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

## 4. Run inference with the AI Camera (on-sensor)

This project uses the **Sony Aitrios Raspberry Pi application module library** for IMX500 deployment (see [Ultralytics IMX500 docs](https://docs.ultralytics.com/integrations/sony-imx500/#software-prerequisites)).

**Software on the Pi (Raspberry Pi OS Bookworm):**

- **libcamera ≥ 0.6** is required by the Aitrios modlib. The system must have libcamera v0.6 or newer (v0.5.x will fail with "Modlib requires libcamera v0.6 or newer").
- Upgrade the system first so libcamera can be updated, then install the IMX stack:

```bash
sudo apt update && sudo apt full-upgrade
sudo reboot
# After reboot, install IMX firmware and the application library:
sudo apt install imx500-all
sudo reboot
pip install git+https://github.com/SonySemiconductorSolutions/aitrios-rpi-application-module-library.git
```

If you still have libcamera v0.5.x after `apt full-upgrade`, your Raspberry Pi OS release may not yet ship v0.6. Follow the steps below to build and install libcamera (and rpicam-apps) from source.

### Upgrading libcamera from source (to v0.6+)

Build and install [Raspberry Pi’s libcamera](https://github.com/raspberrypi/libcamera) and [rpicam-apps](https://github.com/raspberrypi/rpicam-apps) from source. You must build **rpicam-apps after libcamera** (libcamera has no stable binary interface).

**1. Install build dependencies (Raspberry Pi OS / Debian):**

```bash
sudo apt install -y git build-essential meson ninja-build pkg-config
sudo apt install -y libboost-dev libgnutls28-dev openssl libtiff-dev
sudo apt install -y libyaml-dev python3-yaml python3-ply python3-jinja2
sudo apt install -y pybind11-dev libpython3-dev
# Optional: for GStreamer plugin (omit if you don’t need it)
sudo apt install -y libglib2.0-dev libgstreamer-plugins-base1.0-dev
```

**2. Build and install libcamera:**

```bash
cd ~
git clone https://github.com/raspberrypi/libcamera.git
cd libcamera
# Use a release tag for 0.6+ (e.g. v0.6.0 or newer), or main for latest
git checkout v0.6.0   # or: git checkout main
meson setup build --buildtype=release -Dpycamera=enabled
ninja -C build
sudo ninja -C build install
```

On 1GB RAM devices, use `ninja -C build -j 1` to avoid OOM.

**3. Build and install rpicam-apps (required after libcamera):**

```bash
cd ~
git clone https://github.com/raspberrypi/rpicam-apps.git
cd rpicam-apps
meson setup build --buildtype=release
ninja -C build
sudo ninja -C build install
```

**4. Refresh library cache and verify:**

```bash
sudo ldconfig
rpicam-hello --version
```

You should see libcamera at v0.6 or above. Then reboot and continue with the IMX stack install (`sudo apt install imx500-all`, etc.).

After exporting the model (step 2), the export produces a directory `yolo11n_imx_model` (or similar) containing `packerOut.zip` and `labels.txt`. Place that directory under `backend/vision/`. Then:

- **Standalone:** `python backend/vision/object_detection.py --capture imx`
- **AuraBot:** Vision integration automatically uses the AI Camera when available (capture `imx`); no config change needed.

Inference runs **on the camera sensor** (IMX500), so the Pi CPU is not used for YOLO.

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
