"""
Dashboard API for AuraBot development.
Serves status, session history, control, and config over HTTP.
"""

import os
import threading
import time
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Resolve project root for dashboard static files
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
_DASHBOARD_DIR = os.path.join(_PROJECT_ROOT, "dashboard")


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
    else:
        out["esp32_online"] = False

    # Camera: online if vision enabled and frames received recently (within 60s)
    camera_enabled = getattr(bot, "_enable_vision", False)
    out["camera_enabled"] = camera_enabled
    last_camera = getattr(bot, "last_camera_activity", None)
    out["camera_online"] = (
        camera_enabled
        and last_camera is not None
        and (time.time() - last_camera) <= 60.0
    )

    return out


def create_app(aurabot: Optional[Any] = None) -> FastAPI:
    """Create FastAPI app for the dashboard. Optionally attach AuraBot to state."""
    app = FastAPI(title="AuraBot Dashboard", version="0.1.0")
    if aurabot is not None:
        app.state.aurabot = aurabot

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
        cfg = {"wellness": {}, "debounce": {}, "presence_fusion": False}
        if bot.mqtt_api:
            cfg["wellness"] = bot.mqtt_api.wellness_trigger.get_config()
            cfg["debounce"] = bot.mqtt_api.get_debounce_config()
            cfg["presence_fusion"] = bot.mqtt_api.get_presence_fusion()
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
        uvicorn.run(app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
