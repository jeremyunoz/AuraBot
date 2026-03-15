# Vision Module

Person detection for Raspberry Pi: NCNN (on-device in this repo) or IMX export for Raspberry Pi AI Camera (Sony IMX500) on-sensor inference.

## Capture backends

- **IMX** — Pi AI Camera + aitrios modlib; on-sensor YOLO (preferred when model and stack available).
- **picamera2** — libcamera on Pi; frames to CPU YOLO (NCNN or Ultralytics).
- **opencv** — V4L2; CPU YOLO.

`--capture auto` chooses imx → picamera2 → opencv in that order.

## Workflow A: NCNN (this project)

For detection via `object_detection.py` using NCNN.

```bash
pip install -r requirements.txt
cd backend/vision
python setup_model.py    # creates yolo26n_ncnn_model/
python check_cameras.py
python object_detection.py --capture auto
```

Overrides:

```bash
python object_detection.py --capture picamera2
python object_detection.py --capture opencv --device /dev/video0
python object_detection.py --capture imx --imx-model-dir yolo11n_imx_model
```

## Workflow B: IMX export only

Export YOLO to IMX500 `.rpk`; no full project runtime.

```bash
./scripts/setup_pi_venv.sh .venv
source .venv/bin/activate
python backend/vision/setup_imx_model.py --model yolo11n.pt --imgsz 640
```

See `IMX_PI_AI_CAMERA_SETUP.md` for deploy and runtime.

## Workflow C: AuraBot with vision

```bash
ENABLE_VISION=true ./scripts/run_backend_imx.sh
```

Launcher sets `MODLIB_LIBCAMERA=LOCAL`, and prepends `/usr/local/lib/aarch64-linux-gnu` to `LD_LIBRARY_PATH` and `/usr/local/lib/python3/dist-packages` to `PYTHONPATH` so libcamera and modlib use the same stack.

Env (e.g. in `backend/.env`):

| Env | Description |
|-----|-------------|
| `VISION_CAPTURE` | `auto` \| `imx` \| `picamera2` \| `opencv` |
| `VISION_IMX_MODEL_DIR` | IMX model dir under backend/vision (e.g. `yolo11n_imx_model`) |
| `VISION_MODEL` | NCNN model dir (e.g. `yolo26n_ncnn_model`) |
| `VISION_FALLBACK_MODEL` | Fallback YOLO weights (e.g. `yolo26n.pt`) |
| `VISION_WARMUP_FRAMES`, `VISION_READ_RETRIES`, `VISION_REPORT_INTERVAL_FRAMES` | Runtime tuning |

## Benchmarks

Shared options and logging in `benchmark_common.py`. Run from project root.

**NCNN / CPU path (synthetic or camera):**

```bash
python -m backend.vision.benchmark_yolo_latency --warmup 10 --runs 50
python -m backend.vision.benchmark_yolo_latency --camera --warmup 5 --runs 30
python -m backend.vision.benchmark_yolo_latency --quiet
```

**IMX path (Pi AI Camera + on-sensor inference):**

```bash
python -m backend.vision.benchmark_imx_latency [options]
```

Requires: imx500-all (apt), aitrios modlib (pip), and IMX model dir with `packerOut.zip` and `labels.txt`.
