# IMX Model Export + Raspberry Pi AI Camera Setup

This guide covers exporting a YOLO model to **Sony IMX500** format for the Raspberry Pi AI Camera.

## 1. Create Python venv on the Pi (export-only)

From your project root:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip

cd /home/jeremyz/Desktop/AuraBot
./scripts/setup_pi_venv.sh .venv
source .venv/bin/activate
```

If you prefer manual setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install "ultralytics>=8.3.0"
```

## 2. Export YOLO to IMX (.rpk)

Run from project root:

```bash
source .venv/bin/activate
python backend/vision/setup_imx_model.py --model yolo11n.pt --imgsz 640
```

Expected result: an exported artifact containing an `.rpk` model package for IMX500.

## 3. Deploy model to Pi AI Camera runtime

On Raspberry Pi OS with AI Camera stack installed, copy the `.rpk` into your model directory, for example:

```bash
mkdir -p ~/imx500-models
cp /path/to/exported_model.rpk ~/imx500-models/
```

## 4. Run inference with Picamera2 IMX500 runtime

Typical runtime flow is:

1. Load IMX500 model package (`.rpk`) with Picamera2 IMX500 helpers.
2. Start camera stream.
3. Read metadata/detections from the camera pipeline.
4. Parse and filter detections (person class if needed).

If you already have an IMX500 runtime script, point it to your exported `.rpk`.

## 5. Quick verification checklist

- `source .venv/bin/activate` works.
- `python -c "from ultralytics import YOLO; print('ok')"` prints `ok`.
- `setup_imx_model.py` finishes without exception.
- Export output contains an `.rpk` file.
- Runtime script receives detection metadata from the AI Camera.

## Notes

- IMX export support is version-sensitive. If export fails, upgrade Ultralytics first:
  `python -m pip install -U ultralytics`
- NCNN (`setup_model.py`) and IMX (`setup_imx_model.py`) are different targets:
  - NCNN: runs on CPU/GPU on Pi.
  - IMX: runs directly on Sony IMX500 camera hardware.
