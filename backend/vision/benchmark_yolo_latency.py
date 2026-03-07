#!/usr/bin/env python3
"""
Benchmark YOLO inference latency for the vision pipeline (e.g. on Raspberry Pi 5).

Usage (from project root):
  python -m backend.vision.benchmark_yolo_latency [options]
"""

import argparse
import os
import statistics
import sys
import time
from datetime import datetime

import numpy as np

from backend.vision.object_detection import (
    DEFAULT_FRAME_SIZE,
    _capture_args_from_dict,
    _load_yolo_model,
    _open_capture,
)

# Directory for backend vision assets.
_VISION_DIR = os.path.dirname(os.path.abspath(__file__))

NCNN_MODEL_DIR = "yolo26n_ncnn_model"
FALLBACK_PT_MODEL = "yolo26n.pt"
DEFAULT_LATENCY_LOG = "yolo_inference_latency.log"


def _resolve_ncnn_model_path(model_arg: str, fallback_arg: str):
    """Resolve model path to backend/vision, then cwd, then raw args."""
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
        return vision_model, vision_fallback
    return model_arg, fallback_arg


def _synthetic_frame(width: int = None, height: int = None):
    w = width or DEFAULT_FRAME_SIZE[0]
    h = height or DEFAULT_FRAME_SIZE[1]
    return np.zeros((h, w, 3), dtype=np.uint8)


def _run_inference_latency_benchmark(model, frame, warmup_runs: int = 10, timed_runs: int = 50):
    for _ in range(warmup_runs):
        model(frame, verbose=False)

    latencies_ms = []
    for _ in range(timed_runs):
        t0 = time.perf_counter()
        model(frame, verbose=False)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)
    return latencies_ms


def _stats_dict(latencies_ms: list):
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
    print(f"    Mean: {s['mean_ms']:.2f} ms  ->  {s['fps']:.1f} FPS")
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
    p = argparse.ArgumentParser(description="Measure YOLO per-frame inference latency.")
    p.add_argument("--model", default=NCNN_MODEL_DIR)
    p.add_argument("--fallback-model", default=FALLBACK_PT_MODEL)
    p.add_argument("--camera", action="store_true")
    p.add_argument("--capture", choices=["auto", "opencv", "picamera2"], default="picamera2")
    p.add_argument("--warmup", type=int, default=10)
    p.add_argument("--runs", type=int, default=50)
    p.add_argument("--width", type=int, default=0)
    p.add_argument("--height", type=int, default=0)
    p.add_argument("--quiet", action="store_true")
    p.add_argument(
        "--log-file",
        default="",
        help=f"Append results to this file (default: backend/vision/{DEFAULT_LATENCY_LOG}). Use 'none' to disable.",
    )
    return p.parse_args()


def main():
    args = _parse_args()
    width = args.width or DEFAULT_FRAME_SIZE[0]
    height = args.height or DEFAULT_FRAME_SIZE[1]

    model_path, fallback_path = _resolve_ncnn_model_path(args.model, args.fallback_model)
    model = _load_yolo_model(model_path, fallback_path)

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

    latencies_ms = _run_inference_latency_benchmark(model, frame, warmup_runs=args.warmup, timed_runs=args.runs)

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

    if args.quiet:
        mean_ms = statistics.mean(latencies_ms)
        fps = 1000.0 / mean_ms if mean_ms > 0 else 0.0
        print(f"{mean_ms:.2f} ms  {fps:.1f} FPS")
        return

    print("Results (per-frame inference latency):")
    mean_ms = _print_stats(latencies_ms, "Inference")
    if mean_ms is not None:
        fps = 1000.0 / mean_ms
        print(
            f"YOLO-based vision pipeline achieved {mean_ms:.0f} ms inference latency "
            f"per frame on Raspberry Pi 5, enabling real-time detection ({fps:.0f} FPS)."
        )


if __name__ == "__main__":
    main()
