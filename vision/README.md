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
cd vision
python setup_model.py
```

This will:
- Download the YOLO26n model (latest version)
- Convert it to NCNN format (optimized for Pi5)
- Save it as `yolo26n_ncnn_model/`

### 3. Run Detection

```bash
python object_detection.py
```

Press 'q' to quit.

## Files

- `object_detection.py` - Main detection script
- `setup_model.py` - Model setup script (run once)
- `convert_model.py` - Manual model conversion script
- `check_person_detection()` - Helper function to check for person detection

## Model Files (Not in Git)

These files are excluded from git (see `.gitignore`):
- `*.pt` - PyTorch model files
- `*_ncnn_model/` - NCNN converted models
- `*.onnx` - ONNX model files

They will be downloaded/generated on the Pi5 when you run `setup_model.py`.

## Usage Example

```python
from object_detection import check_person_detection
from ultralytics import YOLO

model = YOLO("yolo26n_ncnn_model", task="detect")
results = model(frame)
person_info = check_person_detection(results[0])

if person_info['detected']:
    print(f"Found {person_info['count']} person(s)")
```
