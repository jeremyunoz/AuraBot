"""
Dashboard API for AuraBot development.
Serves status, session history, control, and config over HTTP.
Request logging goes to a file only (no console); high-frequency polls (e.g. /api/status) are skipped to reduce noise.
"""

import logging
import os
import threading
import time
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Resolve project root for dashboard static files (api is backend/api/)
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
_DASHBOARD_DIR = os.path.join(_PROJECT_ROOT, "dashboard")
_DASHBOARD_LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")
_DASHBOARD_ACCESS_LOG = os.path.join(_DASHBOARD_LOG_DIR, "dashboard_requests.log")

# Paths we skip logging (high-frequency polling) to avoid repeated lines in the log file
_SKIP_LOG_PATHS = frozenset({"/api/status"})

# Uvicorn log config with no access log (no console spam from GET /api/status etc.)
# Use this for both dashboard and voice server so neither attaches an access handler.
UVICORN_LOG_CONFIG_NO_ACCESS = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "WARNING", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "WARNING", "propagate": False},
        "uvicorn.access": {"handlers": [], "level": "INFO", "propagate": False},
    },
}


class DashboardRequestLogMiddleware(BaseHTTPMiddleware):
    """Log dashboard API requests to a file only. Skips high-frequency paths to reduce noise."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in _SKIP_LOG_PATHS:
            return response
        try:
            os.makedirs(_DASHBOARD_LOG_DIR, exist_ok=True)
            with open(_DASHBOARD_ACCESS_LOG, "a", encoding="utf-8") as f:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                client = request.client.host if request.client else "-"
                f.write(f"{ts} {client} {request.method} {path} {response.status_code}\n")
        except OSError:
            pass
        return response


def _get_aurabot(request: Request):
    """Dependency: get AuraBot instance from app state."""
    bot = getattr(request.app.state, "aurabot", None)
    if bot is None:
        raise HTTPException(status_code=503, detail="AuraBot not attached")
    return bot


def _build_status(bot) -> dict:
    """Build status dict from AuraBot (works with or without MQTT)."""
    tm = bot.timer_manager
    st = tm.session_timer
    active = tm.get_active_timers()
    user_timers = [t for t in active if t.get("timer_type") == "user"]
    wellness_timers = [t for t in active if t.get("timer_type") == "wellness"]

    out = {
        "status": "ok",
        "session": {
            "state": st.get_state(),
            "current_time_seconds": st.get_current_session_time(),
        },
        "timers": {
            "total": len(active),
            "user": len(user_timers),
            "wellness": len(wellness_timers),
            "active_timers": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "type": t.get("timer_type", "user"),
                    "time_remaining": t["time_remaining"],
                }
                for t in active
            ],
        },
        "wellness_config": {},
    }

    if bot.mqtt_api and bot.mqtt_api.wellness_trigger:
        out["wellness_config"] = bot.mqtt_api.wellness_trigger.get_config()
    if bot.mqtt_integration:
        out["mqtt_connected"] = bot.mqtt_integration.is_connected()
    else:
        out["mqtt_connected"] = False

    # ESP32: online if we received aurabot/status with esp32 recently (within 60s)
    if bot.mqtt_api:
        out["esp32_online"] = bot.mqtt_api.is_esp32_online(within_seconds=60.0)
        out["esp32_state"] = bot.mqtt_api.get_esp32_state()
        out["esp32_user_control_enabled"] = bot.mqtt_api.is_esp32_user_control_enabled()
    else:
        out["esp32_online"] = False
        out["esp32_state"] = None
        out["esp32_user_control_enabled"] = False

    try:
        from backend.voice.voice_ws_server import get_voice_client_status
        out["voice"] = get_voice_client_status()
    except Exception:
        out["voice"] = {
            "connected": False,
            "ready": False,
            "phase": "disconnected",
            "state": "disconnected",
            "age_seconds": None,
        }

    # Camera: online if vision enabled and frames received recently (within 60s)
    camera_enabled = getattr(bot, "_enable_vision", False)
    out["camera_enabled"] = camera_enabled
    last_camera = getattr(bot, "last_camera_activity", None)
    out["camera_online"] = (
        camera_enabled
        and last_camera is not None
        and (time.time() - last_camera) <= 60.0
    )

    # PIR: online if local PIR integration enabled and activity received recently.
    pir_enabled = getattr(bot, "_enable_pir_gpio", False)
    out["pir_enabled"] = pir_enabled
    out["pir_warmed_up"] = bool(getattr(bot, "pir_warmed_up", False))
    out["pir_warmed_up_at"] = getattr(bot, "pir_warmed_up_at", None)
    last_pir = getattr(bot, "last_pir_activity", None)
    out["pir_online"] = (
        pir_enabled
        and last_pir is not None
        and (time.time() - last_pir) <= 60.0
    )

    return out


def create_app(aurabot: Optional[Any] = None) -> FastAPI:
    """Create FastAPI app for the dashboard. Optionally attach AuraBot to state."""
    app = FastAPI(title="AuraBot Dashboard", version="0.1.0")
    if aurabot is not None:
        app.state.aurabot = aurabot
    app.add_middleware(DashboardRequestLogMiddleware)

    @app.get("/api/status")
    def api_status(request: Request):
        bot = _get_aurabot(request)
        return _build_status(bot)

    @app.get("/api/sessions")
    def api_sessions(request: Request, limit: Optional[int] = 50):
        bot = _get_aurabot(request)
        sessions = bot.get_sitting_session_history(limit=limit)
        return {"sessions": sessions}

    class ControlBody(BaseModel):
        cmd: str
        params: Optional[dict] = None

    @app.post("/api/control")
    def api_control(request: Request, body: ControlBody):
        bot = _get_aurabot(request)
        data = {"cmd": body.cmd, **(body.params or {})}
        if body.cmd == "move":
            action = (body.params or {}).get("action")
            if not isinstance(action, str) or not action.strip():
                return {"status": "error", "error": "Missing movement action"}
            allowed_actions = {
                "stand", "walk", "back", "lay_down",
                "turn_left", "turn_right", "sit", "wave", "swing",
            }
            normalized_action = action.strip().lower()
            if normalized_action not in allowed_actions:
                return {"status": "error", "error": f"Unsupported movement action: {action}"}
            if not bot.mqtt_integration or not bot.mqtt_integration.is_connected():
                return {"status": "error", "error": "MQTT is not connected"}
            if not bot.mqtt_integration.publish(
                "aurabot/control",
                {"cmd": "move", "action": normalized_action},
                qos=1,
                retain=False,
            ):
                return {"status": "error", "error": "Failed to publish movement command"}
            return {"status": "success", "command": "move", "action": normalized_action}

        if bot.mqtt_api:
            result = bot.mqtt_api.handle_control_command(data)
        else:
            cmd = body.cmd
            # Check if wellness timer is active before allowing session start/resume
            tm = bot.timer_manager
            active_wellness_timers = tm.get_active_timers(
                timer_type=tm.TIMER_TYPE_WELLNESS
            )
            wellness_timer_active = len(active_wellness_timers) > 0
            
            def start_session_handler():
                if wellness_timer_active:
                    raise ValueError("Cannot start session during wellness break")
                return bot.start_sitting_timer()
            
            def resume_session_handler():
                if wellness_timer_active:
                    raise ValueError("Cannot resume session during wellness break")
                return bot.start_sitting_timer()
            
            handlers = {
                "start_session": start_session_handler,
                "pause_session": lambda: bot.pause_sitting_timer(),
                "resume_session": resume_session_handler,
                "stop_session": lambda: bot.stop_sitting_timer(),
            }
            h = handlers.get(cmd)
            if not h:
                return {"status": "error", "error": f"Unknown command: {cmd}"}
            try:
                r = h()
                return {"status": "success", "command": cmd, "result": r}
            except Exception as e:
                return {"status": "error", "command": cmd, "error": str(e)}

        return result

    @app.get("/api/config")
    def api_config(request: Request):
        bot = _get_aurabot(request)
        cfg = {
            "wellness": {},
            "debounce": {},
            "presence_fusion": False,
            "camera_dominant_presence": False,
            "pir_complement": {},
        }
        if bot.mqtt_api:
            cfg["wellness"] = bot.mqtt_api.wellness_trigger.get_config()
            cfg["debounce"] = bot.mqtt_api.get_debounce_config()
            cfg["presence_fusion"] = bot.mqtt_api.get_presence_fusion()
            cfg["camera_dominant_presence"] = bot.mqtt_api.get_camera_dominant_presence()
            cfg["pir_complement"] = bot.mqtt_api.get_pir_complement_config()
        return cfg

    if os.path.isdir(_DASHBOARD_DIR):
        app.mount("/static", StaticFiles(directory=_DASHBOARD_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        path = os.path.join(_DASHBOARD_DIR, "index.html")
        if os.path.isfile(path):
            return FileResponse(path)
        return HTMLResponse(
            "<html><body><p>AuraBot Dashboard. Add dashboard/index.html.</p>"
            '<p><a href="/api/status">/api/status</a></p></body></html>'
        )

    return app


def run_dashboard(
    aurabot: Any,
    host: str = "0.0.0.0",
    port: int = 8000,
) -> threading.Thread:
    """
    Run the dashboard API in a daemon thread.
    Returns the thread; caller starts it then continues with AuraBot.
    """
    app = create_app(aurabot)
    app.state.aurabot = aurabot

    def _run():
        import uvicorn
        uvicorn.run(
            app, host=host, port=port, log_level="warning",
            access_log=False, log_config=UVICORN_LOG_CONFIG_NO_ACCESS,
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
