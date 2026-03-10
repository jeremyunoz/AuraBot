"""
Real-time person detection using YOLO.
Supports:
  - Raspberry Pi AI Camera (Sony IMX500): on-sensor inference via aitrios modlib (preferred when available).
  - Picamera2 (Pi 5 libcamera) + CPU YOLO.
  - OpenCV V4L2 + CPU YOLO.
Used standalone or via run_detection_loop() for AuraBot.
"""
from __future__ import annotations

import argparse
import glob
import os
import time
import threading
from pathlib import Path
from typing import Callable, Optional

import cv2

# YOLO (ultralytics) used only for CPU/NCNN inference path; IMX path uses on-sensor model
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None  # type: ignore[misc, assignment]

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DEFAULT_FRAME_SIZE = (640, 480)
OPENCV_PROBE_TIMEOUT_MS = 500
PERSON_CLASS_NAME = "person"
# IMX500 (Raspberry Pi AI Camera): default model dir name under backend/vision
DEFAULT_IMX_MODEL_DIR = "yolo11n_imx_model"
IMX_PACKER_ZIP = "packerOut.zip"
IMX_LABELS_FILE = "labels.txt"
# Frame rate for AI Camera: keep close to on-sensor DPS to avoid request/detection mismatch (modlib warns if RPS >> DPS).
IMX_OBJECT_DETECTION_FPS = 10
# Minimum confidence for person detections from IMX
IMX_PERSON_CONFIDENCE_THRESHOLD = 0.55

# Prefer libcamera from /usr/local (v0.7+) when present so modlib uses it instead of apt's v0.5.
# modlib loads /usr/lib's Python libcamera by default, which reports v0.5 and fails the v0.6 check.
if os.environ.get("MODLIB_LIBCAMERA", "").upper() != "LOCAL":
    os.environ["MODLIB_LIBCAMERA"] = "LOCAL"
    _local_lib = "/usr/local/lib/aarch64-linux-gnu"
    if os.path.isdir(_local_lib):
        _prev = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = f"{_local_lib}:{_prev}" if _prev else _local_lib


def _empty_person_info():
    """Standard empty detection result."""
    return {"detected": False, "count": 0, "boxes": []}


def _get_vision_dir() -> Path:
    """Return backend/vision directory (where IMX model dirs and this script live)."""
    return Path(__file__).resolve().parent


def _imx_available(vision_dir: Optional[Path] = None, model_dir_name: str = DEFAULT_IMX_MODEL_DIR) -> bool:
    """
    Return True if Raspberry Pi AI Camera (IMX500) stack is available and model is present.
    Requires: imx500-all (apt), aitrios-rpi-application-module-library (pip), and packerOut.zip + labels.txt.
    """
    vision_dir = vision_dir or _get_vision_dir()
    model_dir = vision_dir / model_dir_name
    if not (model_dir / IMX_PACKER_ZIP).is_file() or not (model_dir / IMX_LABELS_FILE).is_file():
        return False
    try:
        from modlib.devices import AiCamera  # noqa: F401
        from modlib.models import COLOR_FORMAT, MODEL_TYPE, Model  # noqa: F401
        from modlib.models.post_processors import pp_od_yolo_ultralytics  # noqa: F401
    except Exception:
        return False
    return True


def _imx_detections_to_person_info(detections, labels_list: list, confidence_threshold: float = IMX_PERSON_CONFIDENCE_THRESHOLD):
    """
    Convert frame.detections from aitrios (post-processed YOLO) to person_info dict.
    labels_list: list of class names from labels.txt; we filter by PERSON_CLASS_NAME index.
    """
    person_info = _empty_person_info()
    if detections is None or not len(detections):
        return person_info
    try:
        person_idx = next((i for i, L in enumerate(labels_list) if str(L).strip().lower() == PERSON_CLASS_NAME.lower()), None)
        if person_idx is None:
            return person_info
        # frame.detections: often has .confidence, .class_id, and .xyxy or box data (see Ultralytics IMX500 docs)
        conf = getattr(detections, "confidence", None)
        class_ids = getattr(detections, "class_id", None)
        if conf is None or class_ids is None:
            return person_info
        import numpy as np
        conf = np.asarray(conf).ravel()
        class_ids = np.asarray(class_ids).ravel()
        n = min(len(conf), len(class_ids))
        xyxy = getattr(detections, "xyxy", None) or getattr(detections, "boxes", None) or getattr(detections, "bbox", None)
        for i in range(n):
            if float(conf[i]) < confidence_threshold or int(class_ids[i]) != person_idx:
                continue
            person_info["detected"] = True
            person_info["count"] += 1
            if xyxy is not None:
                arr = np.asarray(xyxy)
                if arr.ndim >= 2 and i < len(arr):
                    box = arr[i].ravel()
                else:
                    box = np.zeros(4, dtype=np.float64)
            else:
                box = np.zeros(4, dtype=np.float64)
            person_info["boxes"].append({"box": box, "confidence": float(conf[i])})
    except Exception:
        return _empty_person_info()
    return person_info


def _get_class_name(class_names, class_id: int) -> str:
    """Resolve class id to name; class_names may be dict or list."""
    if isinstance(class_names, dict):
        return class_names.get(class_id, "")
    if isinstance(class_names, (list, tuple)) and 0 <= class_id < len(class_names):
        return class_names[class_id]
    return ""


def check_person_detection(result):
    """
    Check if a person is detected in the YOLO results.

    Returns:
        dict: {
            'detected': bool,
            'count': int,
            'boxes': list of dicts with 'box' and 'confidence'
        }
    """
    person_info = _empty_person_info()
    if result is None or result.boxes is None or len(result.boxes) == 0:
        return person_info
    class_names = result.names
    if class_names is None:
        return person_info

    try:
        class_ids = result.boxes.cls.cpu().numpy()
        xyxy = result.boxes.xyxy
        conf = result.boxes.conf
        n = len(class_ids)
        for i in range(n):
            if i >= len(xyxy) or i >= len(conf):
                break
            cid = int(class_ids[i])
            if _get_class_name(class_names, cid) != PERSON_CLASS_NAME:
                continue
            person_info["detected"] = True
            person_info["count"] += 1
            person_info["boxes"].append({
                "box": xyxy[i].cpu().numpy(),
                "confidence": float(conf[i].cpu().numpy()),
            })
    except (IndexError, KeyError, AttributeError):
        return _empty_person_info()

    return person_info


def _parse_args():
    parser = argparse.ArgumentParser(description="Headless YOLO object detection (OpenCV headless-friendly).")
    parser.add_argument("--camera", type=int, default=0, help="Camera index for cv2.VideoCapture.")
    parser.add_argument(
        "--capture",
        choices=["auto", "opencv", "picamera2", "imx"],
        default="auto",
        help="Capture backend: imx = Raspberry Pi AI Camera (IMX500) on-sensor inference; picamera2 = libcamera + CPU YOLO.",
    )
    parser.add_argument(
        "--v4l2",
        action="store_true",
        help="Force OpenCV V4L2 backend (only applies to --capture opencv/auto).",
    )
    parser.add_argument(
        "--device",
        default="",
        help="Video device path (e.g. /dev/video0). If set, OpenCV will open this path instead of an index.",
    )
    parser.add_argument("--width", type=int, default=0, help="Requested capture width (0 = default).")
    parser.add_argument("--height", type=int, default=0, help="Requested capture height (0 = default).")
    parser.add_argument("--fps", type=int, default=0, help="Requested capture FPS (0 = default).")
    parser.add_argument(
        "--warmup-frames",
        type=int,
        default=10,
        help="Number of frames to discard at startup to let exposure/stream settle.",
    )
    parser.add_argument(
        "--read-retries",
        type=int,
        default=50,
        help="How many consecutive read failures to tolerate before exiting.",
    )
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after N frames (0 = run until Ctrl+C).")
    parser.add_argument("--log-every", type=int, default=30, help="Print a heartbeat every N frames.")

    parser.add_argument("--model", default="yolo26n_ncnn_model", help="Preferred model path/name (e.g., NCNN model).")
    parser.add_argument("--fallback-model", default="yolo26n.pt", help="Fallback model path/name (auto-download if missing).")
    parser.add_argument(
        "--imx-model-dir",
        default=DEFAULT_IMX_MODEL_DIR,
        help="IMX model directory name under backend/vision (e.g. yolo11n_imx_model). Used when --capture imx.",
    )

    parser.add_argument("--save-dir", default="", help="Directory to save annotated frames (empty disables).")
    parser.add_argument("--save-every", type=int, default=0, help="Save annotated frame every N frames (0 disables).")
    parser.add_argument("--save-on-detect", action="store_true", help="Save an annotated frame when a person is detected.")
    parser.add_argument(
        "--min-save-interval",
        type=float,
        default=2.0,
        help="Minimum seconds between saved frames when using --save-on-detect.",
    )

    parser.add_argument("--output-video", default="", help="Write annotated video to this path (empty disables).")
    return parser.parse_args()


# -----------------------------------------------------------------------------
# Capture backends
# -----------------------------------------------------------------------------


def _open_picamera2(args):
    """Open Pi camera via Picamera2 (libcamera). Returns (next_frame, cleanup, fps_getter)."""
    from picamera2 import Picamera2  # type: ignore
    size = (args.width or DEFAULT_FRAME_SIZE[0], args.height or DEFAULT_FRAME_SIZE[1])
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": size}))
    picam2.start()

    def next_frame():
        rgb = picam2.capture_array()
        if rgb is None:
            return None
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    def cleanup():
        try:
            picam2.stop()
        except Exception:
            pass

    def fps_getter():
        return float(args.fps) if args.fps else 0.0

    return next_frame, cleanup, fps_getter


def _opencv_candidate_devices(args):
    """Return ordered OpenCV device candidates, preferring explicit settings first."""
    if args.device:
        return [args.device]

    candidates = [args.camera]
    if args.camera == 0:
        for path in sorted(glob.glob("/dev/video*")):
            if path not in candidates:
                candidates.append(path)
    return candidates


def _make_opencv_capture(device, args):
    """Create an OpenCV capture triple for one device candidate."""
    api = cv2.CAP_V4L2 if args.v4l2 else cv2.CAP_ANY
    cap = cv2.VideoCapture(device, api)
    if args.width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(args.width))
    if args.height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(args.height))
    if args.fps:
        cap.set(cv2.CAP_PROP_FPS, float(args.fps))

    def next_frame():
        ok, frame = cap.read()
        return frame if ok and frame is not None else None

    def cleanup():
        cap.release()

    def fps_getter():
        try:
            return float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            return 0.0

    return next_frame, cleanup, fps_getter


def _open_opencv(args):
    """Open camera via OpenCV VideoCapture. Returns (next_frame, cleanup, fps_getter)."""
    last_error = None
    for device in _opencv_candidate_devices(args):
        try:
            next_frame, cleanup, fps_getter = _make_opencv_capture(device, args)
            first_frame = _probe_stream_frame(next_frame)
            if first_frame is not None:
                return _prepend_frame(next_frame, first_frame), cleanup, fps_getter
            cleanup()
            last_error = f"no frames from {device}"
        except Exception as exc:
            last_error = f"{device}: {exc}"

    raise RuntimeError(last_error or "no OpenCV camera produced frames")


def _probe_stream_frame(next_frame, attempts: int = 10, delay_s: float = 0.05):
    """Try a few reads and return the first valid frame, else None."""
    for _ in range(attempts):
        frame = next_frame()
        if frame is not None:
            return frame
        time.sleep(delay_s)
    return None


def _prepend_frame(next_frame, first_frame):
    """Return a next_frame() wrapper that yields one buffered frame first."""
    buffered = [first_frame]

    def wrapped():
        if buffered:
            return buffered.pop()
        return next_frame()

    return wrapped


def _open_capture(args):
    """
    Open camera based on args.capture. Returns (next_frame, cleanup, fps_getter).
    next_frame() -> BGR frame or None.
    """
    use_picamera2 = args.capture in ("auto", "picamera2")
    if use_picamera2:
        try:
            next_frame, cleanup, fps_getter = _open_picamera2(args)
            if args.capture == "picamera2":
                return next_frame, cleanup, fps_getter
            first_frame = _probe_stream_frame(next_frame)
            if first_frame is not None:
                return _prepend_frame(next_frame, first_frame), cleanup, fps_getter
            try:
                cleanup()
            except Exception:
                pass
        except Exception:
            if args.capture == "picamera2":
                raise
    return _open_opencv(args)


def _capture_args_from_dict(config: dict):
    """Build an args-like object from a config dict for _open_capture."""
    class _Args:
        pass
    a = _Args()
    a.capture = config.get("capture", "auto")
    a.v4l2 = config.get("v4l2", False)
    a.device = config.get("device", "")
    a.camera = config.get("camera", 0)
    a.width = config.get("width", 0)
    a.height = config.get("height", 0)
    a.fps = config.get("fps", 0)
    a.imx_model_dir = config.get("imx_model_dir", DEFAULT_IMX_MODEL_DIR)
    return a


# -----------------------------------------------------------------------------
# Camera discovery
# -----------------------------------------------------------------------------


def _picamera2_importable() -> bool:
    """Check if Picamera2 can be imported (no camera acquire). Use this to default to Pi camera."""
    try:
        from picamera2 import Picamera2  # type: ignore
        return True
    except Exception:
        return False


def _probe_opencv_device(device, use_v4l2: bool = True) -> bool:
    """Probe OpenCV capture on device (path or index). Returns True if a frame can be read."""
    api = cv2.CAP_V4L2 if use_v4l2 else cv2.CAP_ANY
    cap = cv2.VideoCapture(device, api)
    if not cap.isOpened():
        cap.release()
        return False
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MS, OPENCV_PROBE_TIMEOUT_MS)
    except Exception:
        pass
    ok = cap.read()[0]
    cap.release()
    return ok


def list_available_cameras(skip_opencv_if_picamera2_ok: bool = True, vision_dir: Optional[Path] = None):
    """
    Detect available cameras: IMX500 (AI Camera), Pi 5 libcamera (picamera2), /dev/video* (OpenCV).
    Returns list of dicts: [{"backend", "id", "ok", "error"?}, ...].
    """
    out = []
    vision_dir = vision_dir or _get_vision_dir()
    imx_ok = _imx_available(vision_dir)
    out.append({
        "backend": "imx",
        "id": "ai_camera",
        "ok": imx_ok,
        "error": None if imx_ok else "IMX model or modlib unavailable",
    })
    picamera2_ok = _picamera2_importable()
    out.append({
        "backend": "picamera2",
        "id": "libcamera",
        "ok": picamera2_ok,
        "error": None if picamera2_ok else "picamera2 not importable",
    })
    # Skip slow OpenCV /dev/video* probe when a primary camera (IMX or picamera2) is available
    if skip_opencv_if_picamera2_ok and (imx_ok or picamera2_ok):
        return out
    for path in sorted(glob.glob("/dev/video*")):
        ok = _probe_opencv_device(path)
        out.append({"backend": "opencv", "id": path, "ok": ok, "error": None if ok else "open failed or no frame"})
    ok = _probe_opencv_device(0)
    out.append({"backend": "opencv", "id": "0", "ok": ok, "error": None if ok else "open failed or no frame"})
    return out


# -----------------------------------------------------------------------------
# Detection loop (for AuraBot integration)
# -----------------------------------------------------------------------------


def _run_detection_loop_imx(
    callback,
    stop_event,
    *,
    vision_dir: Optional[Path] = None,
    imx_model_dir_name: str = DEFAULT_IMX_MODEL_DIR,
    warmup_frames: int = 10,
    report_interval_frames: int = 15,
    on_frame: Optional[Callable[[], None]] = None,
    ready_event: Optional[threading.Event] = None,
    confidence_threshold: float = IMX_PERSON_CONFIDENCE_THRESHOLD,
):
    """
    Run object detection using Raspberry Pi AI Camera (IMX500) on-sensor inference.
    Uses aitrios modlib: AiCamera + packerOut.zip model + pp_od_yolo_ultralytics.
    Same callback(person_info) contract as run_detection_loop.
    """
    import numpy as np
    from modlib.devices import AiCamera
    from modlib.models import COLOR_FORMAT, MODEL_TYPE, Model
    from modlib.models.post_processors import pp_od_yolo_ultralytics

    vision_dir = vision_dir or _get_vision_dir()
    model_dir = vision_dir / imx_model_dir_name
    packer_path = model_dir / IMX_PACKER_ZIP
    labels_path = model_dir / IMX_LABELS_FILE
    if not packer_path.is_file() or not labels_path.is_file():
        callback({**_empty_person_info(), "error": f"IMX model not found: {packer_path} or {labels_path}"})
        if ready_event:
            ready_event.set()
        return

    labels_list = np.genfromtxt(str(labels_path), dtype=str, delimiter="\n")
    if labels_list.ndim == 0:
        labels_list = [str(labels_list)]
    else:
        labels_list = [str(x) for x in labels_list.tolist()]

    class YOLOIMX(Model):
        def __init__(self):
            super().__init__(
                model_file=str(packer_path),
                model_type=MODEL_TYPE.CONVERTED,
                color_format=COLOR_FORMAT.RGB,
                preserve_aspect_ratio=False,
            )

        def post_process(self, output_tensors):
            return pp_od_yolo_ultralytics(output_tensors)

    try:
        device = AiCamera(frame_rate=IMX_OBJECT_DETECTION_FPS)
        model = YOLOIMX()
        device.deploy(model, overwrite=False)  # use existing network.rpk, no prompt
    except Exception as e:
        err_msg = str(e)
        if "libcamera" in err_msg and "v0.6" in err_msg:
            err_msg += " Upgrade: sudo apt update && sudo apt full-upgrade, then reboot. If still v0.5.x, upgrade Raspberry Pi OS or see backend/vision/IMX_PI_AI_CAMERA_SETUP.md."
        callback({**_empty_person_info(), "error": f"IMX deploy failed: {err_msg}"})
        if ready_event:
            ready_event.set()
        return

    frame_idx = 0
    last_status = None
    warmup_complete = warmup_frames == 0
    if ready_event and warmup_frames == 0:
        ready_event.set()

    try:
        with device as stream:
            for frame in stream:
                if stop_event.is_set():
                    break
                frame_idx += 1
                if warmup_frames > 0 and frame_idx <= warmup_frames:
                    continue
                if not warmup_complete and ready_event:
                    ready_event.set()
                    warmup_complete = True

                if on_frame:
                    try:
                        on_frame()
                    except Exception:
                        pass

                detections = getattr(frame, "detections", None)
                person_info = _imx_detections_to_person_info(
                    detections, labels_list, confidence_threshold=confidence_threshold
                )
                status = (person_info["detected"], person_info["count"])
                if status != last_status:
                    callback(person_info)
                    last_status = status
                elif report_interval_frames > 0 and (frame_idx % report_interval_frames == 0):
                    callback(person_info)
    except Exception as e:
        callback({**_empty_person_info(), "error": str(e)})
    finally:
        try:
            device.close()
        except Exception:
            pass


def _load_yolo_model(model_name: str, fallback_model: str):
    """Load YOLO model from model_name or fallback_model (auto-download if missing)."""
    if os.path.exists(model_name):
        return YOLO(model_name, task="detect")
    if os.path.exists(fallback_model):
        return YOLO(fallback_model)
    return YOLO(fallback_model)


def run_detection_loop(
    callback,
    stop_event,
    *,
    model_name: str = "yolo26n_ncnn_model",
    fallback_model: str = "yolo26n.pt",
    capture_config: Optional[dict] = None,
    warmup_frames: int = 10,
    read_retries: int = 50,
    report_interval_frames: int = 15,
    on_frame: Optional[Callable[[], None]] = None,
    ready_event: Optional[threading.Event] = None,
):
    """
    Run object detection in a loop and call callback(person_info) on detection updates.
    For AuraBot: callback receives {"detected", "count", "boxes"} or {"error": str}.
    stop_event: threading.Event; when set, the loop exits.
    capture_config: optional dict (capture, camera, device, width, height, fps, v4l2).
    on_frame: optional callable(); called every successful frame after warmup (e.g. for dashboard heartbeat).
    ready_event: optional threading.Event; signaled when model is loaded, camera is open, and warmup is complete.
    """
    capture_args = _capture_args_from_dict(capture_config or {})
    vision_dir = _get_vision_dir()
    use_imx = capture_args.capture == "imx" or (
        capture_args.capture == "auto" and _imx_available(vision_dir, capture_args.imx_model_dir)
    )

    if use_imx:
        _run_detection_loop_imx(
            callback,
            stop_event,
            vision_dir=vision_dir,
            imx_model_dir_name=capture_args.imx_model_dir,
            warmup_frames=warmup_frames,
            report_interval_frames=report_interval_frames,
            on_frame=on_frame,
            ready_event=ready_event,
        )
        return

    # CPU path: Picamera2 or OpenCV + YOLO
    if YOLO is None:
        callback({**_empty_person_info(), "error": "ultralytics not installed (required for CPU capture)"})
        if ready_event:
            ready_event.set()
        return

    model = _load_yolo_model(model_name, fallback_model)

    try:
        next_frame, capture_cleanup, _ = _open_capture(capture_args)
    except Exception as e:
        callback({**_empty_person_info(), "error": str(e)})
        if ready_event:
            ready_event.set()
        return

    if ready_event and warmup_frames == 0:
        ready_event.set()

    try:
        frame_idx = 0
        last_status = None
        consecutive_failures = 0
        warmup_complete = warmup_frames == 0

        while not stop_event.is_set():
            frame = next_frame()
            if frame is None:
                consecutive_failures += 1
                if consecutive_failures >= read_retries:
                    if ready_event and not warmup_complete:
                        ready_event.set()
                    break
                time.sleep(0.05)
                continue
            consecutive_failures = 0
            frame_idx += 1

            if warmup_frames > 0 and frame_idx <= warmup_frames:
                continue

            if not warmup_complete and ready_event:
                ready_event.set()
                warmup_complete = True

            if on_frame:
                try:
                    on_frame()
                except Exception:
                    pass
            results = model(frame, verbose=False)
            person_info = check_person_detection(results[0]) if results else _empty_person_info()
            status = (person_info["detected"], person_info["count"])
            if status != last_status:
                callback(person_info)
                last_status = status
            elif report_interval_frames > 0 and (frame_idx % report_interval_frames == 0):
                callback(person_info)
    except Exception as e:
        callback({**_empty_person_info(), "error": str(e)})
    finally:
        try:
            capture_cleanup()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# CLI (standalone script)
# -----------------------------------------------------------------------------


def _main_setup_outputs(args):
    """Prepare save_dir and output_video_path from args. Returns (save_dir, output_video_path)."""
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving annotated frames to: {save_dir}")
    output_video_path = Path(args.output_video) if args.output_video else None
    if output_video_path:
        output_video_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Writing annotated video to: {output_video_path}")
    return save_dir, output_video_path


def _main_process_frame(args, model, frame, frame_idx, last_status, last_save_time, save_dir, output_video_path, video_writer, fps_getter):
    """Run detection on one frame; update and return (person_info, annotated_frame, new_status, new_save_time, video_writer)."""
    results = model(frame, verbose=False)
    if not results:
        person_info = _empty_person_info()
        annotated_frame = frame
    else:
        person_info = check_person_detection(results[0])
        annotated_frame = results[0].plot()
    status = (person_info["detected"], person_info["count"])

    if status != last_status:
        if person_info["detected"]:
            print(f"Person detected! Count: {person_info['count']}")
            for idx, p in enumerate(person_info["boxes"], 1):
                box, conf = p["box"], p["confidence"]
                print(f"  Person {idx}: confidence={conf:.2f}, bbox=[{box[0]:.0f}, {box[1]:.0f}, {box[2]:.0f}, {box[3]:.0f}]")
        else:
            print("No person detected")
    elif args.log_every > 0 and (frame_idx % args.log_every == 0):
        print(f"Heartbeat: frame={frame_idx}, detected={person_info['detected']}, count={person_info['count']}")

    vw = video_writer
    if output_video_path and vw is None:
        h, w = annotated_frame.shape[:2]
        fps = float(fps_getter() or 0.0) or 20.0
        vw = cv2.VideoWriter(str(output_video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if vw is not None:
        vw.write(annotated_frame)

    now = time.time()
    new_save_time = last_save_time
    if save_dir:
        save_periodic = args.save_every > 0 and (frame_idx % args.save_every == 0)
        save_on_detect = args.save_on_detect and person_info["detected"] and (now - last_save_time) >= args.min_save_interval
        if save_periodic or save_on_detect:
            cv2.imwrite(str(save_dir / f"frame_{frame_idx:06d}.jpg"), annotated_frame)
            new_save_time = now

    return person_info, annotated_frame, status, new_save_time, vw


def _main_imx_loop(args, save_dir: Optional[Path], output_video_path: Optional[Path]):
    """CLI loop using Raspberry Pi AI Camera (IMX500) on-sensor inference."""
    import numpy as np
    from modlib.apps import Annotator
    from modlib.devices import AiCamera
    from modlib.models import COLOR_FORMAT, MODEL_TYPE, Model
    from modlib.models.post_processors import pp_od_yolo_ultralytics

    vision_dir = _get_vision_dir()
    model_dir = vision_dir / args.imx_model_dir
    packer_path = model_dir / IMX_PACKER_ZIP
    labels_path = model_dir / IMX_LABELS_FILE
    if not packer_path.is_file() or not labels_path.is_file():
        print(f"Error: IMX model not found. Expected {packer_path} and {labels_path}")
        print("Export with: python backend/vision/setup_imx_model.py --model yolo11n.pt --imgsz 640")
        return

    labels_list = np.genfromtxt(str(labels_path), dtype=str, delimiter="\n")
    if labels_list.ndim == 0:
        labels_list = [str(labels_list)]
    else:
        labels_list = [str(x) for x in labels_list.tolist()]

    class YOLOIMX(Model):
        def __init__(self):
            super().__init__(
                model_file=str(packer_path),
                model_type=MODEL_TYPE.CONVERTED,
                color_format=COLOR_FORMAT.RGB,
                preserve_aspect_ratio=False,
            )

        def post_process(self, output_tensors):
            return pp_od_yolo_ultralytics(output_tensors)

    try:
        device = AiCamera(frame_rate=IMX_OBJECT_DETECTION_FPS)
        model = YOLOIMX()
        device.deploy(model, overwrite=False)  # use existing network.rpk, no prompt
    except Exception as e:
        err_msg = str(e)
        if "libcamera" in err_msg and "v0.6" in err_msg:
            err_msg += "\nUpgrade libcamera: sudo apt update && sudo apt full-upgrade, then reboot. If still v0.5.x, see backend/vision/IMX_PI_AI_CAMERA_SETUP.md."
        print(f"Error: IMX deploy failed: {err_msg}")
        return

    save_dir = save_dir if save_dir else None
    output_video_path = output_video_path if output_video_path else None
    print("Starting real-time object detection (Raspberry Pi AI Camera, on-sensor). Press Ctrl+C to stop.")

    frame_idx = 0
    last_status = None
    last_save_time = 0.0
    video_writer = None
    annotator = Annotator()

    try:
        with device as stream:
            for frame in stream:
                frame_idx += 1
                if args.warmup_frames > 0 and frame_idx <= args.warmup_frames:
                    continue

                detections = getattr(frame, "detections", None)
                person_info = _imx_detections_to_person_info(
                    detections, labels_list, confidence_threshold=IMX_PERSON_CONFIDENCE_THRESHOLD
                )
                status = (person_info["detected"], person_info["count"])

                # Annotate: frame.image is RGB; draw boxes for detections above threshold (Ultralytics IMX500 example style)
                detections_filtered = detections
                if detections is not None and hasattr(detections, "confidence"):
                    mask = np.asarray(detections.confidence).ravel() >= IMX_PERSON_CONFIDENCE_THRESHOLD
                    if hasattr(detections, "__getitem__") and np.any(mask):
                        detections_filtered = detections[mask]
                    elif not np.any(mask):
                        detections_filtered = None
                if detections_filtered is not None and len(detections_filtered):
                    try:
                        labels_str = [
                            f"{labels_list[int(c)]}: {s:.2f}"
                            for _, s, c, _ in detections_filtered
                        ]
                    except (ValueError, TypeError):
                        conf = getattr(detections_filtered, "confidence", None)
                        cid = getattr(detections_filtered, "class_id", None)
                        labels_str = [
                            f"{labels_list[int(c)]}: {s:.2f}"
                            for c, s in zip(np.asarray(cid).ravel(), np.asarray(conf).ravel())
                        ] if (conf is not None and cid is not None) else []
                    try:
                        annotator.annotate_boxes(frame, detections_filtered, labels=labels_str, alpha=0.3, corner_radius=10)
                    except Exception:
                        pass
                img_bgr = cv2.cvtColor(frame.image, cv2.COLOR_RGB2BGR)

                if status != last_status:
                    if person_info["detected"]:
                        print(f"Person detected! Count: {person_info['count']}")
                        for idx, p in enumerate(person_info["boxes"], 1):
                            box, conf = p["box"], p["confidence"]
                            print(f"  Person {idx}: confidence={conf:.2f}, bbox=[{box[0]:.0f}, {box[1]:.0f}, {box[2]:.0f}, {box[3]:.0f}]")
                    else:
                        print("No person detected")
                elif args.log_every > 0 and (frame_idx % args.log_every == 0):
                    print(f"Heartbeat: frame={frame_idx}, detected={person_info['detected']}, count={person_info['count']}")

                if output_video_path and video_writer is None:
                    h, w = img_bgr.shape[:2]
                    video_writer = cv2.VideoWriter(
                        str(output_video_path), cv2.VideoWriter_fourcc(*"mp4v"), float(IMX_OBJECT_DETECTION_FPS), (w, h)
                    )
                if video_writer is not None:
                    video_writer.write(img_bgr)

                now = time.time()
                if save_dir:
                    save_periodic = args.save_every > 0 and (frame_idx % args.save_every == 0)
                    save_on_detect = args.save_on_detect and person_info["detected"] and (now - last_save_time) >= args.min_save_interval
                    if save_periodic or save_on_detect:
                        cv2.imwrite(str(save_dir / f"frame_{frame_idx:06d}.jpg"), img_bgr)
                        last_save_time = now

                last_status = status

                if args.max_frames > 0 and frame_idx >= args.max_frames:
                    print(f"Reached max frames ({args.max_frames}). Stopping.")
                    break
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C).")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        try:
            device.close()
        except Exception:
            pass
        if video_writer is not None:
            video_writer.release()
        print("Detection stopped.")


def main():
    args = _parse_args()

    if args.capture == "imx":
        vision_dir = _get_vision_dir()
        if not _imx_available(vision_dir, args.imx_model_dir):
            print("Error: IMX (Raspberry Pi AI Camera) not available.")
            print("Install: sudo apt install imx500-all, pip install git+https://github.com/SonySemiconductorSolutions/aitrios-rpi-application-module-library.git")
            print("Export model: python backend/vision/setup_imx_model.py --model yolo11n.pt --imgsz 640")
            return
        save_dir, output_video_path = _main_setup_outputs(args)
        _main_imx_loop(args, save_dir, output_video_path)
        return

    model = _load_yolo_model(args.model, args.fallback_model)
    if not os.path.exists(args.model) and not os.path.exists(args.fallback_model):
        print(f"Model not found. Using default (will auto-download): {args.fallback_model}")
        print("For better Pi5 performance, run: python setup_model.py")

    try:
        next_frame, capture_cleanup, fps_getter = _open_capture(args)
    except Exception as e:
        print(f"Error: Could not initialize capture backend ({args.capture}): {e}")
        print("Tip (Raspberry Pi): sudo apt install python3-picamera2 then run with --capture picamera2")
        return

    save_dir, output_video_path = _main_setup_outputs(args)
    print("Starting real-time object detection (headless). Press Ctrl+C to stop.")

    frame_idx = 0
    last_status = None
    last_save_time = 0.0
    consecutive_failures = 0
    video_writer = None

    try:
        while True:
            frame = next_frame()
            if frame is None:
                consecutive_failures += 1
                if consecutive_failures == 1 or consecutive_failures % 10 == 0:
                    print(f"Warning: could not read frame (failures={consecutive_failures}/{args.read_retries})")
                if consecutive_failures >= args.read_retries:
                    print("Error: Could not read frame (retry limit reached).")
                    break
                time.sleep(0.05)
                continue
            consecutive_failures = 0
            frame_idx += 1
            if args.warmup_frames > 0 and frame_idx <= args.warmup_frames:
                continue

            person_info, _, last_status, last_save_time, video_writer = _main_process_frame(
                args, model, frame, frame_idx, last_status, last_save_time,
                save_dir, output_video_path, video_writer, fps_getter
            )

            if args.max_frames > 0 and frame_idx >= args.max_frames:
                print(f"Reached max frames ({args.max_frames}). Stopping.")
                break
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C).")
    finally:
        try:
            capture_cleanup()
        except Exception:
            pass
        if video_writer is not None:
            video_writer.release()
        print("Detection stopped.")


if __name__ == "__main__":
    main()

