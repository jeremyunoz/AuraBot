# Vision Module - Object Detection

Real-time object detection using YOLO for Raspberry Pi 5.

## Setup on Pi5

### 1. Install Dependencies

```bash
pip install -r ../requirements.txt
```

### 2. Setup Model (First Time Only)

The model files are too large for git. Run this once on your Pi5:

```bash
cd backend/vision
python setup_model.py
```

This will:
- Download the YOLO26n model (latest version)
- Convert it to NCNN format (optimized for Pi5)
- Save it as `yolo26n_ncnn_model/`

### 3. Run Detection

```bash
python backend/vision/object_detection.py
```

### 4. Measure inference latency (benchmark)

To measure per-frame inference latency (e.g. for reporting "X ms on Raspberry Pi 5"):

From the project root (`AuraBot/`):

```bash
# Synthetic frame (no camera); quick and reproducible
python -m backend.vision.benchmark_yolo_latency --warmup 10 --runs 50

# Live camera (real-world Pi 5 latency)
python -m backend.vision.benchmark_yolo_latency --camera --warmup 5 --runs 30

# One-line output (mean ms and FPS only)
python -m backend.vision.benchmark_yolo_latency --quiet
```

Output includes mean/min/max/std of inference time (ms) and equivalent FPS. Use the mean ms value for the README claim.
