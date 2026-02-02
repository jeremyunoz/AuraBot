"""
Real-time person detection using YOLO.
Supports Picamera2 (Pi 5 libcamera) and OpenCV V4L2. Used standalone or via run_detection_loop() for AuraBot.
"""
from ultralytics import YOLO
import cv2
import argparse
import os
import time
import glob
from pathlib import Path
from typing import Callable, Optional

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DEFAULT_FRAME_SIZE = (640, 480)
OPENCV_PROBE_TIMEOUT_MS = 500
PERSON_CLASS_NAME = "person"


def _empty_person_info():
    """Standard empty detection result."""
    return {"detected": False, "count": 0, "boxes": []}


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
        choices=["auto", "opencv", "picamera2"],
        default="auto",
        help="Frame capture backend. Use picamera2 for Raspberry Pi CSI/AI Camera via libcamera.",
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


def _open_opencv(args):
    """Open camera via OpenCV VideoCapture. Returns (next_frame, cleanup, fps_getter)."""
    api = cv2.CAP_V4L2 if args.v4l2 else cv2.CAP_ANY
    device = args.device if args.device else args.camera
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


def _open_capture(args):
    """
    Open camera based on args.capture. Returns (next_frame, cleanup, fps_getter).
    next_frame() -> BGR frame or None.
    """
    use_picamera2 = args.capture in ("auto", "picamera2")
    if use_picamera2:
        try:
            return _open_picamera2(args)
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


def list_available_cameras(skip_opencv_if_picamera2_ok: bool = True):
    """
    Detect available cameras (Pi 5 libcamera or /dev/video*).
    Returns list of dicts: [{"backend", "id", "ok", "error"?}, ...].
    Picamera2 is reported from import only (no camera acquire) to avoid double-acquire.
    """
    out = []
    picamera2_ok = _picamera2_importable()
    out.append({
        "backend": "picamera2",
        "id": "libcamera",
        "ok": picamera2_ok,
        "error": None if picamera2_ok else "picamera2 not importable",
    })
    if skip_opencv_if_picamera2_ok and picamera2_ok:
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
):
    """
    Run object detection in a loop and call callback(person_info) on detection updates.
    For AuraBot: callback receives {"detected", "count", "boxes"} or {"error": str}.
    stop_event: threading.Event; when set, the loop exits.
    capture_config: optional dict (capture, camera, device, width, height, fps, v4l2).
    on_frame: optional callable(); called every successful frame after warmup (e.g. for dashboard heartbeat).
    """
    capture_args = _capture_args_from_dict(capture_config or {})
    model = _load_yolo_model(model_name, fallback_model)

    try:
        next_frame, capture_cleanup, _ = _open_capture(capture_args)
    except Exception as e:
        callback({**_empty_person_info(), "error": str(e)})
        return

    try:
        frame_idx = 0
        last_status = None
        consecutive_failures = 0
        while not stop_event.is_set():
            frame = next_frame()
            if frame is None:
                consecutive_failures += 1
                if consecutive_failures >= read_retries:
                    break
                time.sleep(0.05)
                continue
            consecutive_failures = 0
            frame_idx += 1
            if warmup_frames > 0 and frame_idx <= warmup_frames:
                continue
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


def main():
    args = _parse_args()
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

