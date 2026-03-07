#!/usr/bin/env python3
"""
Export a YOLO model to Sony IMX500 format for Raspberry Pi AI Camera.

Example:
    python backend/vision/setup_imx_model.py --model yolo11n.pt --imgsz 640
"""

from __future__ import annotations

import argparse
from pathlib import Path
from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export YOLO model to IMX format (IMX500).")
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO model name/path (e.g., yolo11n.pt)")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size used during export")
    parser.add_argument("--half", action="store_true", help="Enable FP16 export when supported")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("Exporting model for Raspberry Pi AI Camera (Sony IMX500)...")
    print(f"Model: {args.model}")
    print(f"Image size: {args.imgsz}")

    model = YOLO(args.model)

    export_kwargs = {
        "format": "imx",
        "imgsz": args.imgsz,
    }
    if args.half:
        export_kwargs["half"] = True

    output = model.export(**export_kwargs)
    print(f"Export complete: {output}")

    output_path = Path(str(output))
    if output_path.is_file() and output_path.suffix.lower() == ".rpk":
        print("Detected IMX package (.rpk):")
        print(f"  {output_path.resolve()}")
        print("Copy this file to the Raspberry Pi and run with Picamera2 IMX500 runtime.")
    else:
        print("Export succeeded. If .rpk is inside an output directory, inspect:")
        print(f"  {output_path.resolve()}")


if __name__ == "__main__":
    main()
