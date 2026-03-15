# Raspberry Pi 5 Setup for AuraBot

Backend runs on the Pi: voice WebSocket server (ASR/LLM/TTS), dashboard, optional MQTT, PIR GPIO, and vision. TTS on Pi uses espeak-ng (or gTTS if configured). Voice input is typically from ESP32 over WebSocket; optional local mic uses the packages below.

## System dependencies

**Audio (capture and TTS):**

```bash
sudo apt-get update
sudo apt-get install -y \
    flac \
    alsa-utils \
    portaudio19-dev \
    python3-pyaudio \
    espeak-ng
```

| Package | Purpose |
|---------|--------|
| flac | Required by speech_recognition for Google API |
| alsa-utils | arecord, device management |
| portaudio19-dev | PyAudio build |
| python3-pyaudio | PyAudio bindings |
| espeak-ng | TTS on Pi (default when no gTTS) |

**Optional — vision (Pi AI Camera / libcamera):**  
See [backend/vision/README.md](backend/vision/README.md) and [backend/vision/IMX_PI_AI_CAMERA_SETUP.md](backend/vision/IMX_PI_AI_CAMERA_SETUP.md). For IMX on-sensor inference: `sudo apt install imx500-all`, aitrios modlib (pip), and use `run_backend_imx.sh` so libcamera comes from `/usr/local`.

## Installation

1. **System packages (above).**

2. **Python deps (from project root):**
   ```bash
   cd /path/to/AuraBot
   python3 -m venv aurabot-env
   source aurabot-env/bin/activate
   pip install -r requirements.txt
   ```

3. **Config:** Copy or edit `backend/.env` (MQTT, `ENABLE_PIR_GPIO`, `ENABLE_VISION`, LLM, etc.). See root [README.md](README.md) env table.

4. **Audio (optional local mic):** Ensure user is in `audio` group:  
   `sudo usermod -a -G audio $USER` then log out and back in.  
   List devices: `arecord -l`. Test: `arecord -d 3 test.wav`.

## Running

From **project root**:

```bash
source aurabot-env/bin/activate
python -m backend
```

- Dashboard: http://localhost:8000 (or `DASHBOARD_PORT`).
- Voice WebSocket: `ws://<pi-ip>:8765/voice` (or `VOICE_WS_PORT`).

**With IMX AI camera (libcamera from /usr/local):**

```bash
ENABLE_VISION=true ./scripts/run_backend_imx.sh
```

**Local PIR + vision, no MQTT:**

```bash
ENABLE_MQTT=false ENABLE_PIR_GPIO=true ENABLE_VISION=true python -m backend
```

## Troubleshooting

**ALSA/JACK warnings**  
From PyAudio probing backends and non‑existent ALSA devices. Harmless; `stt.py` suppresses them during capture where possible.

**FLAC:** `OSError: FLAC conversion utility not available` → `sudo apt-get install flac`.

**No audio capture:** `arecord -l`; `arecord -d 3 test.wav`; confirm user in `audio` group.

**MQTT "Not authorized"**  
Broker requires auth. Use same credentials as in `backend/.env`:

```bash
mosquitto_pub -h localhost -t "aurabot/sensors" -m '{"motion":1}' -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD"
```

Backend reads `MQTT_USERNAME` and `MQTT_PASSWORD` from `backend/.env` automatically.

**Vision / libcamera:** If using Pi AI Camera and IMX, set `MODLIB_LIBCAMERA=LOCAL` and use `scripts/run_backend_imx.sh` so `LD_LIBRARY_PATH` and `PYTHONPATH` point at `/usr/local` libcamera and modlib.
