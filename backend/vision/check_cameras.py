#!/usr/bin/env python3
"""
List available cameras and show which one AuraBot uses by default.
Run from project root:
  python backend/vision/check_cameras.py
  python -m backend.vision.check_cameras
"""

import sys

from backend.vision.object_detection import list_available_cameras
from backend.vision.vision_integration import _build_capture_config


def main():
    # Default selection first (same logic AuraBot uses) — no slow device probe
    effective = _build_capture_config(None)
    default_capture = effective.get("capture", "auto")
    if default_capture == "imx":
        print("Default camera selection: imx (Raspberry Pi AI Camera) — used by AuraBot when vision is enabled.")
    else:
        print(f"Default camera selection: {default_capture} (AI Camera not available or not chosen).")
    print()

    print("Checking cameras (IMX / Pi 5 libcamera / OpenCV)...")
    cameras = list_available_cameras()
    for c in cameras:
        status = "OK" if c.get("ok") else "FAIL"
        err = f" - {c['error']}" if c.get("error") else ""
        print(f"  {c['backend']}:{c['id']} -> {status}{err}")

    available = [c for c in cameras if c.get("ok")]
    if not available:
        print("No camera available. On Pi 5: sudo apt install python3-picamera2")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
