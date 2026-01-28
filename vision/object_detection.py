from ultralytics import YOLO
import cv2
import argparse
import os
import time
from pathlib import Path


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
    person_info = {
        'detected': False,
        'count': 0,
        'boxes': []
    }
    
    if result.boxes is not None and len(result.boxes) > 0:
        class_ids = result.boxes.cls.cpu().numpy()
        class_names = result.names
        
        for i, class_id in enumerate(class_ids):
            if class_names[int(class_id)] == 'person':
                person_info['detected'] = True
                person_info['count'] += 1
                
                # Get bounding box and confidence
                box = result.boxes.xyxy[i].cpu().numpy()  # [x1, y1, x2, y2]
                confidence = float(result.boxes.conf[i].cpu().numpy())
                
                person_info['boxes'].append({
                    'box': box,
                    'confidence': confidence
                })
    
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


def _open_capture(args):
    """
    Returns (next_frame_callable, cleanup_callable, fps_getter_callable)
    - next_frame_callable() -> frame (BGR np.ndarray) or None
    """
    if args.capture in ("auto", "picamera2"):
        try:
            from picamera2 import Picamera2  # type: ignore
        except Exception:
            if args.capture == "picamera2":
                raise
        else:
            picam2 = Picamera2()
            config = picam2.create_video_configuration(main={"size": (args.width or 640, args.height or 480)})
            picam2.configure(config)
            picam2.start()

            def _next_frame():
                # Picamera2 returns RGB by default; Ultralytics accepts BGR too, but we keep BGR consistent.
                rgb = picam2.capture_array()
                if rgb is None:
                    return None
                return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            def _cleanup():
                try:
                    picam2.stop()
                except Exception:
                    pass

            def _fps():
                return float(args.fps) if args.fps else 0.0

            return _next_frame, _cleanup, _fps

    # Fallback: OpenCV VideoCapture
    api = cv2.CAP_V4L2 if args.v4l2 else cv2.CAP_ANY
    cap = cv2.VideoCapture(args.device if args.device else args.camera, api)

    if args.width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(args.width))
    if args.height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(args.height))
    if args.fps:
        cap.set(cv2.CAP_PROP_FPS, float(args.fps))

    def _next_frame():
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        return frame

    def _cleanup():
        cap.release()

    def _fps():
        try:
            return float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            return 0.0

    return _next_frame, _cleanup, _fps


def main():
    args = _parse_args()

    # Model configuration - change these if needed
    MODEL_NAME = args.model  # NCNN format for Pi5 (faster)
    FALLBACK_MODEL = args.fallback_model  # PyTorch format (will auto-download)
    
    if os.path.exists(MODEL_NAME):
        print(f"Loading NCNN model: {MODEL_NAME}")
        model = YOLO(MODEL_NAME, task="detect")
    elif os.path.exists(FALLBACK_MODEL):
        print(f"Loading PyTorch model: {FALLBACK_MODEL}")
        model = YOLO(FALLBACK_MODEL)
    else:
        print(f"Model not found. Using default (will auto-download): {FALLBACK_MODEL}")
        print("For better Pi5 performance, run: python setup_model.py")
        model = YOLO(FALLBACK_MODEL)  # Will auto-download
    
    # Initialize capture
    try:
        next_frame, capture_cleanup, fps_getter = _open_capture(args)
    except Exception as e:
        print(f"Error: Could not initialize capture backend ({args.capture}): {e}")
        print("Tip (Raspberry Pi AI Camera): install and use Picamera2/libcamera stack.")
        print("  e.g. `sudo apt install python3-picamera2` then run with `--capture picamera2`.")
        return
    
    save_dir: Path | None = None
    if args.save_dir:
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving annotated frames to: {save_dir}")

    video_writer = None
    output_video_path = Path(args.output_video) if args.output_video else None
    if output_video_path:
        output_video_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Writing annotated video to: {output_video_path}")

    print("Starting real-time object detection (headless). Press Ctrl+C to stop.")

    frame_idx = 0
    last_status = None  # (detected: bool, count: int)
    last_save_time = 0.0
    consecutive_failures = 0

    try:
        while True:
            # Read frame from webcam
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

            # Reset failure counter on success
            consecutive_failures = 0

            frame_idx += 1
            if args.warmup_frames > 0 and frame_idx <= args.warmup_frames:
                continue

            # Run YOLO detection on the frame
            results = model(frame, verbose=False)
            result = results[0]  # one frame -> one result

            # Check for person detection using the helper function
            person_info = check_person_detection(result)

            status = (person_info["detected"], person_info["count"])
            if status != last_status:
                if person_info["detected"]:
                    print(f"Person detected! Count: {person_info['count']}")
                    for idx, person in enumerate(person_info["boxes"], 1):
                        box = person["box"]
                        conf = person["confidence"]
                        print(
                            f"  Person {idx}: confidence={conf:.2f}, "
                            f"bbox=[{box[0]:.0f}, {box[1]:.0f}, {box[2]:.0f}, {box[3]:.0f}]"
                        )
                else:
                    print("No person detected")
                last_status = status
            elif args.log_every > 0 and (frame_idx % args.log_every == 0):
                print(f"Heartbeat: frame={frame_idx}, detected={person_info['detected']}, count={person_info['count']}")

            annotated_frame = result.plot()

            # Lazy-init video writer once we know frame size
            if output_video_path and video_writer is None:
                height, width = annotated_frame.shape[:2]
                fps = float(fps_getter() or 0.0)
                if fps <= 0:
                    fps = 20.0
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                video_writer = cv2.VideoWriter(str(output_video_path), fourcc, fps, (width, height))

            if video_writer is not None:
                video_writer.write(annotated_frame)

            now = time.time()
            should_save_periodic = save_dir is not None and args.save_every > 0 and (frame_idx % args.save_every == 0)
            should_save_on_detect = (
                save_dir is not None
                and args.save_on_detect
                and person_info["detected"]
                and (now - last_save_time) >= args.min_save_interval
            )
            if should_save_periodic or should_save_on_detect:
                out_path = save_dir / f"frame_{frame_idx:06d}.jpg"
                cv2.imwrite(str(out_path), annotated_frame)
                last_save_time = now

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

