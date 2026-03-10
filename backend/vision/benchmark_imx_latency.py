#!/usr/bin/env python3
"""
Benchmark IMX500 vision pipeline latency (Raspberry Pi AI Camera + on-sensor YOLO).
Uses same testing parameters as benchmark_yolo_latency for direct comparison.

Measures end-to-end per-frame latency: capture + on-sensor inference (e.g. yolo11n_imx_model
or yolo11n_mix_model). Requires: imx500-all (apt), aitrios modlib (pip), and
packerOut.zip + labels.txt in the IMX model directory.

Usage (from project root):
  python -m backend.vision.benchmark_imx_latency [options]
"""

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

try:
    from backend.vision.benchmark_common import (
        BENCHMARK_RUNS_DEFAULT,
        BENCHMARK_WARMUP_DEFAULT,
        add_common_benchmark_args,
        print_stats,
        write_latency_log,
    )
    from backend.vision.object_detection import (
        DEFAULT_IMX_MODEL_DIR,
        IMX_OBJECT_DETECTION_FPS,
        IMX_PACKER_ZIP,
        _get_vision_dir,
        _imx_available,
    )
except ModuleNotFoundError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.vision.benchmark_common import (
        BENCHMARK_RUNS_DEFAULT,
        BENCHMARK_WARMUP_DEFAULT,
        add_common_benchmark_args,
        print_stats,
        write_latency_log,
    )
    from backend.vision.object_detection import (
        DEFAULT_IMX_MODEL_DIR,
        IMX_OBJECT_DETECTION_FPS,
        IMX_PACKER_ZIP,
        _get_vision_dir,
        _imx_available,
    )

_VISION_DIR = Path(__file__).resolve().parent
DEFAULT_LATENCY_LOG = "vision_latency.log"
PIPELINE_NAME = "imx500"


def _resolve_imx_model_path(model_dir_arg: str):
    """Resolve IMX model directory to backend/vision, then cwd, then raw arg."""
    vision_model = _VISION_DIR / model_dir_arg
    cwd_model = Path(os.getcwd()) / model_dir_arg
    if vision_model.is_dir() and (vision_model / IMX_PACKER_ZIP).is_file():
        return str(vision_model)
    if cwd_model.is_dir() and (cwd_model / IMX_PACKER_ZIP).is_file():
        return str(cwd_model)
    if Path(model_dir_arg).is_dir() and (Path(model_dir_arg) / IMX_PACKER_ZIP).is_file():
        return model_dir_arg
    return str(vision_model)


def _run_imx_latency_benchmark(
    warmup_runs: int = BENCHMARK_WARMUP_DEFAULT,
    timed_runs: int = BENCHMARK_RUNS_DEFAULT,
    imx_model_dir: str = DEFAULT_IMX_MODEL_DIR,
    frame_rate: int = IMX_OBJECT_DETECTION_FPS,
):
    """
    Open IMX pipeline, run warmup frames, then time each frame.
    Returns (latencies_ms, frame_size). Latency = time to get one frame (capture + on-sensor inference).
    """
    from modlib.devices import AiCamera
    from modlib.models import COLOR_FORMAT, MODEL_TYPE, Model
    from modlib.models.post_processors import pp_od_yolo_ultralytics

    vision_dir = _get_vision_dir()
    model_dir = vision_dir / imx_model_dir
    packer_path = model_dir / IMX_PACKER_ZIP
    if not packer_path.is_file():
        raise FileNotFoundError(f"IMX model not found: {packer_path} (use --imx-model-dir)")

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

    device = AiCamera(frame_rate=frame_rate)
    model = YOLOIMX()
    device.deploy(model, overwrite=False)

    frame_size = (0, 0)
    latencies_ms = []
    run_count = 0
    try:
        with device as stream:
            it = iter(stream)
            # Warmup
            for _ in range(warmup_runs):
                frame = next(it, None)
                if frame is None:
                    break
                if hasattr(frame, "image") and frame.image is not None:
                    h, w = frame.image.shape[:2]
                    frame_size = (w, h)
            # Timed runs
            for _ in range(timed_runs):
                t0 = time.perf_counter()
                frame = next(it, None)
                t1 = time.perf_counter()
                if frame is None:
                    break
                latencies_ms.append((t1 - t0) * 1000.0)
                if hasattr(frame, "image") and frame.image is not None and frame_size == (0, 0):
                    h, w = frame.image.shape[:2]
                    frame_size = (w, h)
                run_count += 1
    finally:
        try:
            device.close()
        except Exception:
            pass

    return latencies_ms, frame_size


def _parse_args():
    p = argparse.ArgumentParser(
        description="Measure IMX500 per-frame latency (capture + on-sensor YOLO)."
    )
    p.add_argument(
        "--imx-model-dir",
        default=DEFAULT_IMX_MODEL_DIR,
        help=f"IMX model directory name under backend/vision (default: {DEFAULT_IMX_MODEL_DIR}, e.g. yolo11n_mix_model).",
    )
    add_common_benchmark_args(p)
    p.add_argument(
        "--frame-rate",
        type=int,
        default=IMX_OBJECT_DETECTION_FPS,
        help=f"AI Camera frame rate (default: {IMX_OBJECT_DETECTION_FPS}).",
    )
    p.add_argument("--quiet", action="store_true")
    p.add_argument(
        "--log-file",
        default="",
        help=f"Append results to this file (default: backend/vision/{DEFAULT_LATENCY_LOG}). Use 'none' to disable.",
    )
    return p.parse_args()


def main():
    args = _parse_args()

    vision_dir = _get_vision_dir()
    if not _imx_available(vision_dir, args.imx_model_dir):
        print("Error: IMX500 (Raspberry Pi AI Camera) stack or model not available.", file=sys.stderr)
        print(
            "Install: sudo apt install imx500-all, pip install git+https://github.com/SonySemiconductorSolutions/aitrios-rpi-application-module-library.git",
            file=sys.stderr,
        )
        print(
            f"Export model and place packerOut.zip + labels.txt in backend/vision/{args.imx_model_dir}/",
            file=sys.stderr,
        )
        sys.exit(1)

    model_path = _resolve_imx_model_path(args.imx_model_dir)

    try:
        latencies_ms, frame_size = _run_imx_latency_benchmark(
            warmup_runs=args.warmup,
            timed_runs=args.runs,
            imx_model_dir=args.imx_model_dir,
            frame_rate=args.frame_rate,
        )
    except Exception as e:
        print(f"Error running benchmark: {e}", file=sys.stderr)
        sys.exit(1)

    log_path = None
    if args.log_file and args.log_file.lower() != "none":
        log_path = (
            os.path.abspath(args.log_file)
            if os.path.isabs(args.log_file)
            else str(_VISION_DIR / args.log_file)
        )
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
            source="imx500",
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

    w, h = frame_size[0], frame_size[1]
    print(f"Results (pipeline={PIPELINE_NAME}, warmup={args.warmup} runs={args.runs} frame={w}x{h}):")
    mean_ms = print_stats(latencies_ms, "Frame (capture + inference)", PIPELINE_NAME)
    if mean_ms is not None:
        fps = 1000.0 / mean_ms
        print(
            f"IMX500 vision pipeline achieved {mean_ms:.0f} ms latency per frame "
            f"(model={args.imx_model_dir}), enabling real-time detection ({fps:.0f} FPS)."
        )


if __name__ == "__main__":
    main()
