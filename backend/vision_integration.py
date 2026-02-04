"""
Vision integration for AuraBot.
Runs camera-based person detection and feeds camera_confirmed into the MQTT sensor API
so session start/pause and wellness logic use the camera as a presence source.
"""

import os
import sys
import threading
import time
from typing import Any, Optional, Tuple

# Default model names (paths resolved relative to project/vision)
DEFAULT_MODEL_NAME = "yolo26n_ncnn_model"
DEFAULT_FALLBACK_MODEL = "yolo26n.pt"


def _resolve_vision_paths(backend_dir: str, model_name: str, fallback_model: str) -> Tuple[str, str]:
    """Resolve model paths relative to project/vision. Returns (model_path, fallback_path)."""
    project_root = os.path.dirname(backend_dir)
    vision_dir = os.path.join(project_root, "vision")
    model_path = os.path.join(vision_dir, model_name) if not os.path.isabs(model_name) else model_name
    fallback_path = os.path.join(vision_dir, fallback_model) if not os.path.isabs(fallback_model) else fallback_model
    return model_path, fallback_path


def _build_capture_config(capture_config: Optional[dict]) -> dict:
    """Build capture config; default to picamera2 when importable (Pi 5)."""
    effective = dict(capture_config or {})
    if "capture" not in effective:
        try:
            from vision.object_detection import _picamera2_importable
            effective["capture"] = "picamera2" if _picamera2_importable() else "auto"
        except ImportError:
            effective["capture"] = "auto"
    return effective


def _log_available_cameras(aurabot: Any) -> None:
    """Log which cameras are available (picamera2 / opencv)."""
    logger = getattr(aurabot, "logger", None)
    if not logger:
        return
    try:
        from vision.object_detection import list_available_cameras
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


def _send_presence_to_mqtt(aurabot: Any, person_info: dict) -> None:
    """Send camera_confirmed (presence) to MQTT sensor API. Normalizes person_info on error."""
    if "error" in person_info:
        if getattr(aurabot, "logger", None):
            aurabot.logger.log_general(f"Vision error: {person_info['error']}", "WARNING")
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
            aurabot.logger.log_general("Vision integration skipped: MQTT API not available", "WARNING")
        return None, None

    backend_dir = os.path.dirname(os.path.abspath(__file__))
    model_path, fallback_path = _resolve_vision_paths(backend_dir, model_name, fallback_model)
    stop_event = threading.Event()
    ready_event = threading.Event()

    def on_detection(person_info: dict) -> None:
        _send_presence_to_mqtt(aurabot, person_info)

    def on_frame_heartbeat() -> None:
        """Called every successful frame so dashboard camera status updates promptly."""
        setattr(aurabot, "last_camera_activity", time.time())

    def run_vision_thread() -> None:
        project_root = os.path.dirname(backend_dir)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        try:
            from vision.object_detection import run_detection_loop
        except ImportError as e:
            if getattr(aurabot, "logger", None):
                aurabot.logger.log_error(f"Vision module not available: {e}")
            return
        _log_available_cameras(aurabot)
        effective_capture = _build_capture_config(capture_config)
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
