"""
Shared benchmark constants and helpers for vision pipeline latency tests.
Ensures benchmark_yolo_latency and benchmark_imx_latency use identical testing parameters
and output format for direct comparison.
"""
from __future__ import annotations

import os
import statistics
from datetime import datetime
from pathlib import Path

# Shared testing parameters (both benchmarks use these defaults)
BENCHMARK_WARMUP_DEFAULT = 10
BENCHMARK_RUNS_DEFAULT = 50
BENCHMARK_FRAME_WIDTH = 640
BENCHMARK_FRAME_HEIGHT = 480
BENCHMARK_FRAME_SIZE = (BENCHMARK_FRAME_WIDTH, BENCHMARK_FRAME_HEIGHT)
THERMAL_ZONE_CPU_TEMP = Path("/sys/class/thermal/thermal_zone0/temp")


def stats_dict(latencies_ms: list) -> dict | None:
    """Compute stats for a list of latencies in ms."""
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


def read_soc_temperature_c() -> float | None:
    """Read Raspberry Pi SoC temperature in Celsius when available."""
    try:
        raw = THERMAL_ZONE_CPU_TEMP.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value / 1000.0 if value > 1000 else value


class TemperatureTracker:
    """Collect start/end/min/max/mean SoC temperature samples during a benchmark."""

    def __init__(self) -> None:
        self.samples_c: list[float] = []

    def sample(self) -> float | None:
        temp_c = read_soc_temperature_c()
        if temp_c is not None:
            self.samples_c.append(temp_c)
        return temp_c

    def summary(self) -> dict | None:
        if not self.samples_c:
            return None
        return {
            "start_c": self.samples_c[0],
            "end_c": self.samples_c[-1],
            "min_c": min(self.samples_c),
            "max_c": max(self.samples_c),
            "mean_c": statistics.mean(self.samples_c),
            "n": len(self.samples_c),
        }


def print_stats(latencies_ms: list, label: str, pipeline_name: str) -> float | None:
    """Print stats in a consistent format. Returns mean_ms or None."""
    if not latencies_ms:
        print(f"  {label}: no samples")
        return None
    s = stats_dict(latencies_ms)
    print(f"  {label} (n={s['n']}):")
    print(f"    Mean: {s['mean_ms']:.2f} ms  ->  {s['fps']:.1f} FPS")
    print(f"    Min:  {s['min_ms']:.2f} ms")
    print(f"    Max:  {s['max_ms']:.2f} ms")
    print(f"    Std:  {s['std_ms']:.2f} ms")
    return s["mean_ms"]


def print_temperature_stats(temperature_stats: dict | None) -> None:
    """Print temperature stats in a consistent format."""
    if not temperature_stats:
        print("  Temperature: unavailable")
        return
    print(f"  Temperature (C) (n={temperature_stats['n']}):")
    print(f"    Start: {temperature_stats['start_c']:.2f}")
    print(f"    End:   {temperature_stats['end_c']:.2f}")
    print(f"    Min:   {temperature_stats['min_c']:.2f}")
    print(f"    Max:   {temperature_stats['max_c']:.2f}")
    print(f"    Mean:  {temperature_stats['mean_c']:.2f}")


def write_latency_log(
    log_path: str,
    latencies_ms: list,
    *,
    pipeline: str,
    model_path: str,
    frame_size: tuple,
    warmup: int,
    runs: int,
    source: str = "",
    temperature_stats: dict | None = None,
) -> None:
    """Append benchmark result to log file in a consistent format."""
    if not log_path or not latencies_ms:
        return
    s = stats_dict(latencies_ms)
    if not s:
        return
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.isdir(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    w, h = frame_size[0], frame_size[1]
    src = f" source={source}" if source else ""
    lines = [
        "",
        f"[{ts}] vision_latency pipeline={pipeline}",
        f"  model={model_path} frame={w}x{h} warmup={warmup} runs={runs}{src}",
        f"  mean_ms={s['mean_ms']:.2f} min_ms={s['min_ms']:.2f} max_ms={s['max_ms']:.2f} std_ms={s['std_ms']:.2f} fps={s['fps']:.1f} n={s['n']}",
    ]
    if temperature_stats:
        lines.append(
            "  "
            f"temp_c_start={temperature_stats['start_c']:.2f} "
            f"temp_c_end={temperature_stats['end_c']:.2f} "
            f"temp_c_min={temperature_stats['min_c']:.2f} "
            f"temp_c_max={temperature_stats['max_c']:.2f} "
            f"temp_c_mean={temperature_stats['mean_c']:.2f} "
            f"temp_samples={temperature_stats['n']}"
        )
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        import sys
        print(f"Warning: could not write latency log to {log_path}: {e}", file=sys.stderr)


def get_vision_dir() -> Path:
    """Return backend/vision directory."""
    return Path(__file__).resolve().parent


def add_common_benchmark_args(parser, runs_default=BENCHMARK_RUNS_DEFAULT, warmup_default=BENCHMARK_WARMUP_DEFAULT):
    """Add --warmup and --runs to an ArgumentParser with shared defaults."""
    parser.add_argument("--warmup", type=int, default=warmup_default, help=f"Warmup frames (default: {warmup_default})")
    parser.add_argument("--runs", type=int, default=runs_default, help=f"Timed runs (default: {runs_default})")
