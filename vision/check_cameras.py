#!/usr/bin/env python3
"""
List available cameras (e.g. Pi 5 libcamera or /dev/video*).
Run from project root: python vision/check_cameras.py  or  python -m vision.check_cameras
"""
import sys
import os

if __name__ == "__main__":
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

from vision.object_detection import list_available_cameras


def main():
    print("Checking cameras (Pi 5: picamera2/libcamera or /dev/video*)...")
    cameras = list_available_cameras()
    for c in cameras:
        status = "OK" if c.get("ok") else "FAIL"
        err = f" - {c['error']}" if c.get("error") else ""
        print(f"  {c['backend']}:{c['id']} -> {status}{err}")
    available = [c for c in cameras if c.get("ok")]
    if not available:
        print("No camera available. On Pi 5: sudo apt install python3-picamera2")
        sys.exit(1)
    print("Use capture_config={'capture': 'picamera2'} for Pi 5 libcamera.")
    sys.exit(0)


if __name__ == "__main__":
    main()
