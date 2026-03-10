"""
Vision integration for AuraBot.
Runs camera-based person detection and feeds camera_confirmed into the server-side
sensor API so session start/pause and wellness logic use the camera as a presence source.
"""

import os
import threading
import time
from typing import Any, Optional, Tuple

# Default model names
DEFAULT_MODEL_NAME = "yolo26n_ncnn_model"
DEFAULT_FALLBACK_MODEL = "yolo26n.pt"


def _resolve_vision_paths(backend_dir: str, model_name: str, fallback_model: str) -> Tuple[str, str]:
    """
    Resolve model paths under backend/vision.
    """
    backend_vision_dir = os.path.join(backend_dir, "vision")

    def _resolve(name: str) -> str:
        if os.path.isabs(name):
            return name
        return os.path.join(backend_vision_dir, name)

    model_path = _resolve(model_name)
    fallback_path = _resolve(fallback_model)
    return model_path, fallback_path


def _build_capture_config(capture_config: Optional[dict]) -> dict:
    """Build capture config aligned with object_detection: prefer IMX when available, else picamera2 (Pi 5), else auto.
    Supports same keys as object_detection._capture_args_from_dict: capture, imx_model_dir, camera, device, width, height, fps, v4l2."""
    effective = dict(capture_config or {})
    try:
        from backend.vision.object_detection import DEFAULT_IMX_MODEL_DIR, _get_vision_dir, _imx_available, _picamera2_importable
    except ImportError:
        effective.setdefault("capture", "auto")
        return effective
    vision_dir = _get_vision_dir()
    imx_model_dir = effective.get("imx_model_dir", DEFAULT_IMX_MODEL_DIR)
    if "capture" not in effective:
        if _imx_available(vision_dir, imx_model_dir):
            effective["capture"] = "imx"
        else:
            effective["capture"] = "picamera2" if _picamera2_importable() else "auto"
    return effective


def _log_available_cameras(aurabot: Any, capture_config: Optional[dict] = None) -> None:
    """Log which cameras are available without poisoning IMX libcamera imports."""
    logger = getattr(aurabot, "logger", None)
    if not logger:
        return
    try:
        from backend.vision.object_detection import (
            DEFAULT_IMX_MODEL_DIR,
            _get_vision_dir,
            _imx_available,
            list_available_cameras,
        )
        effective_capture = dict(capture_config or {})
        requested_capture = effective_capture.get("capture", "auto")
        imx_model_dir = effective_capture.get("imx_model_dir", DEFAULT_IMX_MODEL_DIR)
        vision_dir = _get_vision_dir()

        # Avoid importing picamera2 before IMX startup. A failed picamera2 import can leave
        # the system libcamera extension (/usr/lib, v0.5.x) loaded in-process, which then
        # causes modlib's later IMX libcamera check to report the wrong version.
        if requested_capture == "imx" or (
            requested_capture == "auto" and _imx_available(vision_dir, imx_model_dir)
        ):
            cameras = [{"backend": "imx", "id": "ai_camera", "ok": True}]
        else:
            cameras = list_available_cameras()
        available = [c for c in cameras if c.get("ok")]
        if available:
            cam_str = ", ".join(f"{c['backend']}:{c['id']}" for c in available)
            logger.log_general(f"Vision cameras: {cam_str}", "INFO")
        else:
            for c in cameras:
                logger.log_general(
                    f"Vision camera {c.get('backend', '?')}:{c.get('id', '?')} - {c.get('error', 'unavailable')}",
                    "WARNING",
                )
    except Exception as e:
        logger.log_general(f"Vision camera check failed: {e}", "WARNING")


def _send_presence_to_sensor_api(aurabot: Any, person_info: dict) -> None:
    """Send camera_confirmed (presence) to server-side sensor API."""
    if "error" in person_info:
        if getattr(aurabot, "logger", None):
            err = person_info["error"]
            # Known env limitation: libcamera too old for IMX; PIR fallback is expected
            if "libcamera" in err and "v0.6" in err:
                aurabot.logger.log_general(
                    "IMX camera unavailable (libcamera upgrade required). Using PIR fallback. See backend/vision/IMX_PI_AI_CAMERA_SETUP.md. "
                    "To use the AI camera, start the backend with LD_LIBRARY_PATH and PYTHONPATH set before Python (e.g. scripts/run_backend_imx.sh).",
                    "INFO",
                )
            else:
                aurabot.logger.log_general(f"Vision error: {err}", "WARNING")
        person_info = {"detected": False, "count": 0, "boxes": []}
    camera_confirmed = 1 if person_info.get("detected") else 0
    try:
        aurabot.mqtt_api.handle_sensor_data({
            "camera_confirmed": camera_confirmed,
            "motion": 0,
        })
    except Exception as e:
        if getattr(aurabot, "logger", None):
            aurabot.logger.log_error(f"Vision callback error: {e}")


def start_vision_integration(
    aurabot: Any,
    *,
    model_name: str = DEFAULT_MODEL_NAME,
    fallback_model: str = DEFAULT_FALLBACK_MODEL,
    capture_config: Optional[dict] = None,
    warmup_frames: int = 10,
    read_retries: int = 50,
    report_interval_frames: int = 15,
) -> Tuple[Optional[threading.Event], Optional[threading.Event]]:
    """
    Start camera-based person detection in a daemon thread and feed presence into AuraBot.

    When a person is detected, calls aurabot.mqtt_api.handle_sensor_data() with
    camera_confirmed=1 so session/wellness logic runs as with hardware sensors.

    Requires aurabot.mqtt_api. Returns (stop_event, ready_event) tuple, or (None, None) if vision could not be started.
    ready_event is signaled when model is loaded, camera is open, and warmup is complete.
    """
    if not getattr(aurabot, "mqtt_api", None):
        if getattr(aurabot, "logger", None):
            aurabot.logger.log_general("Vision integration skipped: sensor API not available", "WARNING")
        return None, None

    # backend/vision/vision_integration.py -> backend root is parent of vision
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_path, fallback_path = _resolve_vision_paths(backend_dir, model_name, fallback_model)
    stop_event = threading.Event()
    ready_event = threading.Event()

    def on_detection(person_info: dict) -> None:
        _send_presence_to_sensor_api(aurabot, person_info)

    def on_frame_heartbeat() -> None:
        """Called every successful frame so dashboard camera status updates promptly."""
        setattr(aurabot, "last_camera_activity", time.time())

    def run_vision_thread() -> None:
        try:
            from backend.vision.object_detection import run_detection_loop
        except ImportError as e:
            if getattr(aurabot, "logger", None):
                aurabot.logger.log_error(f"Vision module not available: {e}")
            return
        effective_capture = _build_capture_config(capture_config)
        _log_available_cameras(aurabot, effective_capture)
        run_detection_loop(
            on_detection,
            stop_event,
            model_name=model_path,
            fallback_model=fallback_path,
            capture_config=effective_capture,
            warmup_frames=warmup_frames,
            read_retries=read_retries,
            report_interval_frames=report_interval_frames,
            on_frame=on_frame_heartbeat,
            ready_event=ready_event,
        )

    thread = threading.Thread(target=run_vision_thread, daemon=True)
    thread.start()
    return stop_event, ready_event
