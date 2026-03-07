# Vision Module

Real-time person detection for Raspberry Pi, with two model targets:

- `NCNN` for on-device inference in this project (`object_detection.py`)
- `IMX` for Raspberry Pi AI Camera (Sony IMX500) export (`.rpk`)

## Workflow A: NCNN runtime for this project

Use this when you want AuraBot camera detection running through `object_detection.py`.

### 1. Install runtime dependencies

From project root:

```bash
pip install -r requirements.txt
```

### 2. Export/download NCNN model (first time)

```bash
cd backend/vision
python setup_model.py
```

This creates `yolo26n_ncnn_model/`.

### 3. Run detection

From project root:

```bash
python backend/vision/object_detection.py
```

## Workflow B: IMX export for Raspberry Pi AI Camera

Use this when you only need model export to IMX500 `.rpk` (not full project runtime install).

From project root:

```bash
./scripts/setup_pi_venv.sh .venv
source .venv/bin/activate
python backend/vision/setup_imx_model.py --model yolo11n.pt --imgsz 640
```

Detailed setup/deploy notes: `backend/vision/IMX_PI_AI_CAMERA_SETUP.md`.

## Benchmark (NCNN runtime path)

Measure YOLO inference latency from project root:

```bash
# Synthetic frame (no camera)
python -m backend.vision.benchmark_yolo_latency --warmup 10 --runs 50

# Live camera
python -m backend.vision.benchmark_yolo_latency --camera --warmup 5 --runs 30

# Mean ms + FPS only
python -m backend.vision.benchmark_yolo_latency --quiet
```
