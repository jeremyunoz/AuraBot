#!/usr/bin/env python3
"""
Benchmark non-IMX vision pipeline latency (standard camera + CPU YOLO).
Uses same testing parameters and log format as benchmark_imx_latency.py for direct comparison.

Usage (from project root):
  python -m backend.vision.benchmark_yolo_latency [options]
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

try:
    from backend.vision.benchmark_common import (
        BENCHMARK_FRAME_HEIGHT,
        BENCHMARK_FRAME_WIDTH,
        BENCHMARK_RUNS_DEFAULT,
        BENCHMARK_WARMUP_DEFAULT,
        TemperatureTracker,
        add_common_benchmark_args,
        print_stats,
        print_temperature_stats,
        write_latency_log,
    )
    from backend.vision.object_detection import (
        _capture_args_from_dict,
        _load_yolo_model,
        _open_capture,
    )
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.vision.benchmark_common import (
        BENCHMARK_FRAME_HEIGHT,
        BENCHMARK_FRAME_WIDTH,
        BENCHMARK_RUNS_DEFAULT,
        BENCHMARK_WARMUP_DEFAULT,
        TemperatureTracker,
        add_common_benchmark_args,
        print_stats,
        print_temperature_stats,
        write_latency_log,
    )
    from backend.vision.object_detection import (
        _capture_args_from_dict,
        _load_yolo_model,
        _open_capture,
    )

_VISION_DIR = Path(__file__).resolve().parent

NCNN_MODEL_DIR = "yolo11n_ncnn_model"
FALLBACK_PT_MODEL = "yolo11n.pt"
DEFAULT_LATENCY_LOG = "vision_latency.log"
PIPELINE_NAME = "yolo_cpu"


def _resolve_model_path(model_arg: str, fallback_arg: str) -> tuple[str, str]:
    """Resolve model path relative to backend/vision first, then cwd, then raw arg."""
    vision_model = _VISION_DIR / model_arg
    vision_fallback = _VISION_DIR / fallback_arg
    cwd_model = Path.cwd() / model_arg
    cwd_fallback = Path.cwd() / fallback_arg

    if Path(model_arg).is_absolute() and Path(model_arg).exists():
        resolved_fallback = str(Path(fallback_arg)) if Path(fallback_arg).is_absolute() else str(vision_fallback)
        return model_arg, resolved_fallback
    if vision_model.exists():
        return str(vision_model), str(vision_fallback)
    if cwd_model.exists():
        return str(cwd_model), str(cwd_fallback if cwd_fallback.exists() else vision_fallback)
    if Path(model_arg).exists():
        return model_arg, fallback_arg
    if vision_fallback.exists():
        return str(vision_model), str(vision_fallback)
    return model_arg, fallback_arg


def _synthetic_frame(width: int, height: int):
    return np.zeros((height, width, 3), dtype=np.uint8)


def _run_synthetic_latency_benchmark(
    model,
    frame,
    warmup_runs: int,
    timed_runs: int,
    temperature_tracker: TemperatureTracker | None = None,
) -> list[float]:
    """Measure CPU inference latency using a synthetic frame."""
    for _ in range(warmup_runs):
        model(frame, verbose=False)
        if temperature_tracker:
            temperature_tracker.sample()

    latencies_ms: list[float] = []
    for _ in range(timed_runs):
        t0 = time.perf_counter()
        model(frame, verbose=False)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)
        if temperature_tracker:
            temperature_tracker.sample()
    return latencies_ms


def _run_camera_latency_benchmark(
    model,
    *,
    warmup_runs: int,
    timed_runs: int,
    capture: str,
    camera: int,
    device: str,
    width: int,
    height: int,
    fps: int,
    v4l2: bool,
    temperature_tracker: TemperatureTracker | None = None,
) -> tuple[list[float], tuple[int, int]]:
    """
    Measure end-to-end per-frame latency for the non-IMX path.
    Latency = capture + CPU inference.
    """
    capture_config = {
        "capture": capture,
        "camera": camera,
        "device": device,
        "width": width,
        "height": height,
        "fps": fps,
        "v4l2": v4l2,
    }
    cap_args = _capture_args_from_dict(capture_config)
    next_frame, capture_cleanup, _ = _open_capture(cap_args)

    frame_size = (0, 0)
    latencies_ms: list[float] = []
    try:
        for _ in range(warmup_runs):
            frame = next_frame()
            if frame is None:
                break
            model(frame, verbose=False)
            if temperature_tracker:
                temperature_tracker.sample()
            if frame_size == (0, 0):
                h, w = frame.shape[:2]
                frame_size = (w, h)

        for _ in range(timed_runs):
            t0 = time.perf_counter()
            frame = next_frame()
            if frame is None:
                break
            model(frame, verbose=False)
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)
            if temperature_tracker:
                temperature_tracker.sample()
            if frame_size == (0, 0):
                h, w = frame.shape[:2]
                frame_size = (w, h)
    finally:
        try:
            capture_cleanup()
        except Exception:
            pass

    return latencies_ms, frame_size


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Measure non-IMX YOLO latency with either synthetic input or a standard camera."
    )
    parser.add_argument("--model", default=NCNN_MODEL_DIR, help=f"Preferred model path/name (default: {NCNN_MODEL_DIR}).")
    parser.add_argument(
        "--fallback-model",
        default=FALLBACK_PT_MODEL,
        help=f"Fallback model path/name (default: {FALLBACK_PT_MODEL}).",
    )
    parser.add_argument(
        "--camera",
        action="store_true",
        help="Benchmark end-to-end standard camera + CPU YOLO latency instead of synthetic-frame inference only.",
    )
    parser.add_argument(
        "--capture",
        choices=["auto", "opencv", "picamera2"],
        default="picamera2",
        help="Capture backend for --camera (default: picamera2).",
    )
    parser.add_argument("--camera-index", type=int, default=0, help="Camera index for OpenCV capture (default: 0).")
    parser.add_argument("--device", default="", help="Video device path for OpenCV (e.g. /dev/video0).")
    parser.add_argument("--width", type=int, default=BENCHMARK_FRAME_WIDTH, help=f"Frame width (default: {BENCHMARK_FRAME_WIDTH}).")
    parser.add_argument("--height", type=int, default=BENCHMARK_FRAME_HEIGHT, help=f"Frame height (default: {BENCHMARK_FRAME_HEIGHT}).")
    parser.add_argument("--fps", type=int, default=0, help="Requested capture FPS for --camera (default: backend default).")
    parser.add_argument("--v4l2", action="store_true", help="Force OpenCV V4L2 backend when using OpenCV capture.")
    add_common_benchmark_args(parser)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--log-file",
        default="",
        help=f"Append results to this file (default: backend/vision/{DEFAULT_LATENCY_LOG}). Use 'none' to disable.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()

    model_path, fallback_path = _resolve_model_path(args.model, args.fallback_model)
    temperature_tracker = TemperatureTracker()
    temperature_tracker.sample()

    try:
        model = _load_yolo_model(model_path, fallback_path)
    except Exception as e:
        print(f"Error loading model: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.camera:
            latencies_ms, frame_size = _run_camera_latency_benchmark(
                model,
                warmup_runs=args.warmup,
                timed_runs=args.runs,
                capture=args.capture,
                camera=args.camera_index,
                device=args.device,
                width=args.width,
                height=args.height,
                fps=args.fps,
                v4l2=args.v4l2,
                temperature_tracker=temperature_tracker,
            )
            source = args.capture
            label = "Frame (capture + inference)"
        else:
            frame_size = (args.width, args.height)
            frame = _synthetic_frame(*frame_size)
            latencies_ms = _run_synthetic_latency_benchmark(
                model,
                frame,
                warmup_runs=args.warmup,
                timed_runs=args.runs,
                temperature_tracker=temperature_tracker,
            )
            source = "synthetic"
            label = "Inference"
    except Exception as e:
        print(f"Error running benchmark: {e}", file=sys.stderr)
        sys.exit(1)
    temperature_tracker.sample()
    temperature_stats = temperature_tracker.summary()

    log_path = None
    if args.log_file and args.log_file.lower() != "none":
        log_path = os.path.abspath(args.log_file) if os.path.isabs(args.log_file) else str(_VISION_DIR / args.log_file)
    elif not args.log_file or args.log_file.lower() != "none":
        log_path = str(_VISION_DIR / DEFAULT_LATENCY_LOG)
    if log_path and latencies_ms:
        write_latency_log(
            log_path,
            latencies_ms,
            pipeline=PIPELINE_NAME,
            model_path=model_path,
            frame_size=frame_size,
            warmup=args.warmup,
            runs=args.runs,
            source=source,
            temperature_stats=temperature_stats,
        )

    if args.quiet:
        if latencies_ms:
            mean_ms = statistics.mean(latencies_ms)
            fps = 1000.0 / mean_ms if mean_ms > 0 else 0.0
            print(f"{mean_ms:.2f} ms  {fps:.1f} FPS")
        return

    if not latencies_ms:
        print("No frames collected (stream ended early?).")
        return

    w, h = frame_size
    print(f"Results (pipeline={PIPELINE_NAME}, warmup={args.warmup} runs={args.runs} frame={w}x{h} source={source}):")
    mean_ms = print_stats(latencies_ms, label, PIPELINE_NAME)
    print_temperature_stats(temperature_stats)
    if mean_ms is not None:
        fps = 1000.0 / mean_ms
        if args.camera:
            print(
                f"Non-IMX vision pipeline achieved {mean_ms:.0f} ms latency per frame "
                f"(capture={args.capture}, model={args.model}), enabling real-time detection ({fps:.0f} FPS)."
            )
        else:
            print(
                f"Non-IMX YOLO inference achieved {mean_ms:.0f} ms latency per frame "
                f"(model={args.model}) on synthetic input ({fps:.0f} FPS)."
            )


if __name__ == "__main__":
    main()
