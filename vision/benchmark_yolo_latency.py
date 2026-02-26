#!/usr/bin/env python3
"""
Benchmark YOLO inference latency for the vision pipeline (e.g. on Raspberry Pi 5).

Uses the same NCNN model as in practice (vision/yolo26n_ncnn_model) and, when run with
--camera, the AI Pi camera via Picamera2 (libcamera).

Measures per-frame inference time in ms so you can report:
  "YOLO-based vision pipeline achieved X ms inference latency per frame on Raspberry Pi 5,
   enabling real-time detection."

Usage (from project root):
  python -m vision.benchmark_yolo_latency [options]

With AI Pi camera (Picamera2) and vision-dir NCNN model:
  python -m vision.benchmark_yolo_latency --camera --warmup 5 --runs 30
"""

import argparse
import os
import statistics
import sys
import time
from datetime import datetime

# Allow running as script from repo root or from vision/
_VISION_DIR = os.path.dirname(os.path.abspath(__file__))
if __name__ == "__main__" and __package__ is None:
    _root = os.path.abspath(os.path.join(_VISION_DIR, ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)

import numpy as np

from vision.object_detection import (
    DEFAULT_FRAME_SIZE,
    _load_yolo_model,
    check_person_detection,
    _empty_person_info,
)
from vision.object_detection import _capture_args_from_dict, _open_capture

# NCNN model dir name used in practice (see vision/setup_model.py, vision_integration.py)
NCNN_MODEL_DIR = "yolo26n_ncnn_model"
FALLBACK_PT_MODEL = "yolo26n.pt"

# Default log file for inference latency (inside vision/)
DEFAULT_LATENCY_LOG = "yolo_inference_latency.log"


def _resolve_ncnn_model_path(model_arg: str, fallback_arg: str):
    """
    Resolve model path to the NCNN model used in practice.
    Checks: vision directory (where we keep the model), then current directory, then model_arg as-is.
    Returns (model_path, fallback_path) for _load_yolo_model.
    """
    vision_model = os.path.join(_VISION_DIR, NCNN_MODEL_DIR)
    vision_fallback = os.path.join(_VISION_DIR, FALLBACK_PT_MODEL)
    cwd_model = os.path.join(os.getcwd(), NCNN_MODEL_DIR)
    cwd_fallback = os.path.join(os.getcwd(), FALLBACK_PT_MODEL)
    if os.path.isabs(model_arg) and os.path.exists(model_arg):
        return model_arg, fallback_arg if os.path.isabs(fallback_arg) else os.path.join(_VISION_DIR, fallback_arg)
    if os.path.exists(vision_model):
        return vision_model, vision_fallback
    if os.path.exists(cwd_model):
        return cwd_model, cwd_fallback if os.path.exists(cwd_fallback) else vision_fallback
    if os.path.exists(model_arg):
        return model_arg, fallback_arg
    if os.path.exists(vision_fallback):
        return vision_model, vision_fallback  # load will use fallback
    return model_arg, fallback_arg


def _synthetic_frame(width: int = None, height: int = None):
    """Return a BGR frame (numpy uint8) of given size for reproducible benchmark."""
    w = width or DEFAULT_FRAME_SIZE[0]
    h = height or DEFAULT_FRAME_SIZE[1]
    return np.zeros((h, w, 3), dtype=np.uint8)


def _run_inference_latency_benchmark(
    model,
    frame,
    warmup_runs: int = 10,
    timed_runs: int = 50,
    verbose: bool = True,
):
    """
    Run warmup then timed inference; return list of per-frame latencies in milliseconds.
    """
    # Warmup
    for _ in range(warmup_runs):
        model(frame, verbose=False)

    latencies_ms = []
    for _ in range(timed_runs):
        t0 = time.perf_counter()
        results = model(frame, verbose=False)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

    return latencies_ms


def _stats_dict(latencies_ms: list):
    """Return dict with mean, min, max, std, fps, n. Empty if no samples."""
    if not latencies_ms:
        return None
    n = len(latencies_ms)
    mean_ms = statistics.mean(latencies_ms)
    return {
        "n": n,
        "mean_ms": mean_ms,
        "min_ms": min(latencies_ms),
        "max_ms": max(latencies_ms),
        "std_ms": statistics.stdev(latencies_ms) if n > 1 else 0.0,
        "fps": 1000.0 / mean_ms if mean_ms > 0 else 0.0,
    }


def _print_stats(latencies_ms: list, label: str = "Inference"):
    if not latencies_ms:
        print(f"{label}: no samples")
        return None
    s = _stats_dict(latencies_ms)
    print(f"  {label} (n={s['n']}):")
    print(f"    Mean: {s['mean_ms']:.2f} ms  →  {s['fps']:.1f} FPS")
    print(f"    Min:  {s['min_ms']:.2f} ms")
    print(f"    Max:  {s['max_ms']:.2f} ms")
    print(f"    Std:  {s['std_ms']:.2f} ms")
    return s["mean_ms"]


def _write_latency_log(
    log_path: str,
    latencies_ms: list,
    *,
    model_path: str,
    frame_size: tuple,
    camera: bool,
    capture: str,
    warmup: int,
    runs: int,
) -> None:
    """Append one benchmark run to the latency log file in vision/."""
    if not log_path or not latencies_ms:
        return
    s = _stats_dict(latencies_ms)
    if not s:
        return
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.isdir(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    frame_src = f"camera({capture})" if camera else "synthetic"
    lines = [
        "",
        f"[{ts}] inference_latency",
        f"  model={model_path} frame={frame_size[0]}x{frame_size[1]} source={frame_src} warmup={warmup} runs={runs}",
        f"  mean_ms={s['mean_ms']:.2f} min_ms={s['min_ms']:.2f} max_ms={s['max_ms']:.2f} std_ms={s['std_ms']:.2f} fps={s['fps']:.1f} n={s['n']}",
    ]
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print(f"Warning: could not write latency log to {log_path}: {e}", file=sys.stderr)


def _parse_args():
    p = argparse.ArgumentParser(
        description="Measure YOLO per-frame inference latency (for Pi 5 / vision pipeline)."
    )
    p.add_argument(
        "--model",
        default=NCNN_MODEL_DIR,
        help=f"Model path/name (default: {NCNN_MODEL_DIR}; resolved from vision/ or cwd)",
    )
    p.add_argument(
        "--fallback-model",
        default=FALLBACK_PT_MODEL,
        help=f"Fallback .pt model if NCNN not found (default: {FALLBACK_PT_MODEL})",
    )
    p.add_argument(
        "--camera",
        action="store_true",
        help="Use live frame from AI Pi camera (Picamera2) for real Pi 5 measurement",
    )
    p.add_argument(
        "--capture",
        choices=["auto", "opencv", "picamera2"],
        default="picamera2",
        help="Capture backend when using --camera (default: picamera2 = AI Pi camera via libcamera)",
    )
    p.add_argument(
        "--warmup",
        type=int,
        default=10,
        help="Warmup inference runs (default: 10)",
    )
    p.add_argument(
        "--runs",
        type=int,
        default=50,
        help="Timed inference runs (default: 50)",
    )
    p.add_argument(
        "--width",
        type=int,
        default=0,
        help="Frame width (0 = use default 640; only for synthetic or camera)",
    )
    p.add_argument(
        "--height",
        type=int,
        default=0,
        help="Frame height (0 = use default 480)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Only print mean ms and FPS (one line)",
    )
    p.add_argument(
        "--log-file",
        default="",
        help=f"Append inference latency results to this file (default: vision/{DEFAULT_LATENCY_LOG}). Use 'none' to disable.",
    )
    return p.parse_args()


def main():
    args = _parse_args()
    width = args.width or DEFAULT_FRAME_SIZE[0]
    height = args.height or DEFAULT_FRAME_SIZE[1]

    # Resolve model to vision/ or cwd NCNN model used in practice
    model_path, fallback_path = _resolve_ncnn_model_path(args.model, args.fallback_model)
    if not args.quiet:
        print("Loading YOLO model...")
        print(f"  Model: {model_path}")
        if model_path != args.model or fallback_path != args.fallback_model:
            print(f"  (resolved from vision/ or cwd; fallback: {fallback_path})")
    model = _load_yolo_model(model_path, fallback_path)
    if not args.quiet:
        print(f"Frame size: {width}x{height}")
        if args.camera:
            cam_label = "AI Pi camera (Picamera2/libcamera)" if args.capture == "picamera2" else args.capture
            print(f"Camera: {cam_label}")
        else:
            print("Using synthetic frame (no camera).")

    if args.camera:
        capture_config = {
            "capture": args.capture,
            "width": width if args.width else 0,
            "height": height if args.height else 0,
        }
        cap_args = _capture_args_from_dict(capture_config)
        try:
            next_frame, capture_cleanup, _ = _open_capture(cap_args)
        except Exception as e:
            print(f"Error opening camera: {e}", file=sys.stderr)
            sys.exit(1)
        frame = next_frame()
        try:
            capture_cleanup()
        except Exception:
            pass
        if frame is None:
            print("Error: could not read a frame from camera.", file=sys.stderr)
            sys.exit(1)
    else:
        frame = _synthetic_frame(width, height)

    if not args.quiet:
        print(f"Running warmup ({args.warmup}) then timed runs ({args.runs})...")
    latencies_ms = _run_inference_latency_benchmark(
        model, frame, warmup_runs=args.warmup, timed_runs=args.runs, verbose=not args.quiet
    )

    # Resolve log file path (default: vision/yolo_inference_latency.log)
    log_path = None
    if args.log_file and args.log_file.lower() != "none":
        log_path = os.path.abspath(args.log_file) if os.path.isabs(args.log_file) else os.path.join(_VISION_DIR, args.log_file)
    elif not args.log_file or args.log_file.lower() != "none":
        log_path = os.path.join(_VISION_DIR, DEFAULT_LATENCY_LOG)
    if log_path and latencies_ms:
        _write_latency_log(
            log_path,
            latencies_ms,
            model_path=model_path,
            frame_size=(width, height),
            camera=args.camera,
            capture=args.capture,
            warmup=args.warmup,
            runs=args.runs,
        )
        if not args.quiet:
            print(f"\nLogged to {log_path}")

    if args.quiet:
        mean_ms = statistics.mean(latencies_ms)
        fps = 1000.0 / mean_ms if mean_ms > 0 else 0.0
        print(f"{mean_ms:.2f} ms  {fps:.1f} FPS")
        return

    print()
    print("Results (per-frame inference latency):")
    mean_ms = _print_stats(latencies_ms, "Inference")
    print()
    print("Summary for README:")
    if mean_ms is not None:
        fps = 1000.0 / mean_ms
        print(
            f"  YOLO-based vision pipeline achieved {mean_ms:.0f} ms inference latency "
            f"per frame on Raspberry Pi 5, enabling real-time detection ({fps:.0f} FPS)."
        )


if __name__ == "__main__":
    main()
