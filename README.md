# AuraBot

AuraBot is an edge-AI desktop companion designed to help reduce sedentary behavior in desk-based environments. It monitors user presence, tracks sitting time, and delivers wellness reminders through sensing, voice interaction, and robotic feedback.

The system combines a **Raspberry Pi 5** and an **ESP32-based robotic module**, connected via MQTT and WebSocket. The Pi handles vision, speech, and reminder logic; the ESP32 handles sensors, motion, and device feedback. An **IMX500 AI camera** and **PIR sensor** provide presence monitoring; a **speech pipeline** supports voice commands, timers, and conversational reminders. The vision pipeline runs at ~10 FPS on the Pi 5 with IMX500 on-sensor inference and stable thermal performance.

**[Project site](https://projects.eng.uci.edu/projects/2025-2026/aurabot-edge-ai-desk-wellness-companion)** · **[Video demo](https://youtu.be/ghKhDYF8M4M)**

---

## Contents

- [Quick start](#quick-start)
- [What you need](#what-you-need)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [API & logs](#api--logs)
- [Learn more](#learn-more)

---

## Quick start

From a fresh clone, get the backend running in a few steps:

```bash
git clone https://github.com/YOUR_USERNAME/AuraBot.git
cd AuraBot
python3 -m venv aurabot-env
source aurabot-env/bin/activate   # Windows: aurabot-env\Scripts\activate
pip install -r requirements.txt
python -m backend
```

Then open **http://localhost:8000** for the dashboard. Configure options in `backend/.env` (see [Configuration](#configuration)).

> **Tip:** Always run from the **project root** so the `backend` package resolves correctly.

---

## What you need

| You want to… | You need… |
|--------------|-----------|
| Run the backend (dashboard, voice logic) | Python 3.7+, `pip install -r requirements.txt` |
| Use voice (with a device) | ESP32 connected to Pi via WebSocket, or a microphone on the Pi |
| Use presence (motion + camera) | MQTT broker and/or Pi with PIR GPIO + camera |
| Use the robot (motion, feedback) | ESP32 flashed with AuraBot firmware, MQTT broker |
| Use vision (person detection) | Raspberry Pi 5; for IMX500 on-sensor: AI camera + [vision setup](backend/vision/README.md) |

---

## Features

### Voice & conversation

- **WebSocket voice pipeline** (port 8765, path `/voice`): ESP32 streams Opus (60 ms, 16 kHz mono); Pi runs ASR → LLM/timers → TTS and returns TTS as Opus.
- **Turn handling**: VAD, configurable commit timeouts, optional per-turn latency logging.
- **LLM**: Gemini or Ollama; optional **hybrid** (primary + fallback) via `LLM_PRIMARY_BACKEND` / `LLM_FALLBACK_BACKEND`.

### Timers & wellness

- **Timers**: Multiple named timers, natural-language parsing, TTS on expiry; session timer with pause/resume.
- **Wellness**: Auto wellness breaks after a sitting threshold; optional **break-compliance** (leave-desk detection, violation count, ESP32 movement e.g. swing).

### Sensing & presence

- **Presence**: ESP32 PIR over MQTT and/or local Pi PIR GPIO + vision. **Camera-dominant** fusion with PIR fallback and optional PIR-complement checks.
- **Vision**: Person detection — IMX500 on-sensor (preferred), picamera2 + CPU YOLO, or OpenCV V4L2.

### Dashboard & API

- **Web dashboard** (port 8000): Session state, timers, controls, wellness config. Serves `/api/status`, `/api/sessions`, `/api/control`, `/api/config`.

---

## Installation

1. **Clone and enter the repo:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/AuraBot.git
   cd AuraBot
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python3 -m venv aurabot-env
   source aurabot-env/bin/activate   # Windows: aurabot-env\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure (optional):** Copy or edit `backend/.env`. See [Configuration](#configuration) and [RASPBERRY_PI_SETUP.md](RASPBERRY_PI_SETUP.md) for Pi-specific setup.

---

## Usage

### Run the backend

From the **project root** (with venv activated):

```bash
python -m backend
```

This starts the voice WebSocket server, local sensor/session API, optional MQTT client, and dashboard. All behavior is driven by `backend/.env`.

### By setup

**Local PIR + vision, no MQTT:**

```bash
ENABLE_MQTT=false ENABLE_PIR_GPIO=true ENABLE_VISION=true python -m backend
```

**With IMX AI camera** (libcamera from `/usr/local`):

```bash
ENABLE_VISION=true ./scripts/run_backend_imx.sh
```

**Presence simulation over MQTT:**

```bash
./scripts/presence_sim.sh
# Simulate absent:
DISTANCE_CM=60 MOTION=0 CAMERA_CONFIRMED=0 ./scripts/presence_sim.sh
```

**Standalone voice server** (echo test, no AuraBot logic):

```bash
python -m backend.voice.voice_ws_server
```

**ESP32:** Build and flash with ESP-IDF; set WiFi, MQTT, and optional voice WebSocket URI in `idf.py menuconfig` or `sdkconfig.defaults`.

---

## Configuration

Key variables in `backend/.env`:

| Category | Variables | Purpose |
|----------|------------|---------|
| **MQTT** | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD` | Broker connection |
| **Services** | `ENABLE_MQTT`, `ENABLE_PIR_GPIO`, `ENABLE_VISION` | Turn transport and sensors on/off |
| **PIR** | `PIR_GPIO_PIN`, `PIR_POLL_INTERVAL_SECONDS`, `PIR_HEARTBEAT_SECONDS`, `PIR_WARMUP_SECONDS` | GPIO PIR tuning |
| **Ports** | `VOICE_WS_PORT` (default 8765), `DASHBOARD_PORT` (default 8000) | Voice and dashboard |
| **LLM** | `LLM_PRIMARY_BACKEND`, `LLM_FALLBACK_BACKEND`, `LLM_DISABLE_FALLBACK`, `GEMINI_API_KEY`, `OLLAMA_MODEL`, `OLLAMA_HOST` | Conversation backend |
| **Vision** | `VISION_CAPTURE` (`auto` \| `imx` \| `picamera2` \| `opencv`), `VISION_IMX_MODEL_DIR`, `VISION_MODEL`, `VISION_FALLBACK_MODEL` | Camera and model choice |
| **Voice** | `VOICE_LATENCY_LOG` (`1` or path to log per-turn latency) | Debugging |

---

## Project structure

```
AuraBot/
├── backend/
│   ├── __main__.py           # Entry: python -m backend
│   ├── sim_loop.py           # AuraBot, service startup (MQTT, PIR, vision, dashboard, voice WS)
│   ├── core/                 # Logging
│   ├── llm/                  # Gemini, Ollama, hybrid; response routing (keywords, timers, LLM)
│   ├── timer/                # Session timer, parser, manager, wellness trigger
│   ├── voice/                # STT, TTS, WebSocket server, turn endpoint
│   ├── mqtt/                 # Sensor/control API, presence, wellness, break-compliance
│   ├── pir/                  # GPIO PIR → sensor API
│   ├── api/                  # FastAPI dashboard (status, sessions, control, config)
│   └── vision/               # Object detection (IMX / picamera2 / OpenCV), integration, benchmarks
├── scripts/                  # presence_sim.sh, run_backend_imx.sh, setup_pi_venv.sh, temp_check.sh
├── dashboard/                # Web UI (index.html, app.js, styles.css)
├── esp32/                    # Firmware (main, mqtt, PIR, voice, wakeword, motion, etc.)
├── requirements.txt
├── RASPBERRY_PI_SETUP.md
└── backend/vision/README.md
```

---

## API & logs

**Dashboard API**

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | Session state, current session time, timer counts |
| `GET /api/sessions` | Sitting session history (query: `limit`) |
| `POST /api/control` | Commands: `start_session`, `pause_session`, `resume_session`, `stop_session`, `trigger_wellness`, `move` (action; needs MQTT) |
| `GET /api/config` | Wellness, debounce, presence fusion, PIR complement |

**Log files**

| File | Content |
|------|---------|
| `logs/aurabot_conversation.log` | Conversation log |
| `logs/sitting_sessions.json` | Session history |
| `logs/dashboard_requests.log` | Dashboard API requests (excluding /api/status) |
| `logs/voice_pipeline_latency.log` | Per-turn latency when `VOICE_LATENCY_LOG=1` |

---

## Learn more

- **[RASPBERRY_PI_SETUP.md](RASPBERRY_PI_SETUP.md)** — Pi 5 audio, system deps, MQTT auth, optional vision
- **[backend/vision/README.md](backend/vision/README.md)** — YOLO/NCNN/IMX workflows, capture backends, benchmarks
- **[backend/vision/IMX_PI_AI_CAMERA_SETUP.md](backend/vision/IMX_PI_AI_CAMERA_SETUP.md)** — IMX500 export and Pi AI Camera runtime
