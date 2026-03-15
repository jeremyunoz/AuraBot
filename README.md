# AuraBot

Voice-activated wellness chatbot: STT/TTS, session timers, presence-aware tracking. Supports ESP32+MQTT sensor transport or fully local Raspberry Pi (PIR GPIO + vision). Voice I/O via WebSocket (ESP32 streams Opus; Pi runs ASR/LLM/TTS and returns TTS Opus).

## Features

- **Voice**: WebSocket server (port 8765, path `/voice`). ESP32 sends Opus 60 ms @ 16 kHz mono; Pi runs ASR → LLM/timers → TTS → Opus back. Turn endpoint: VAD, commit timeouts, latency logging.
- **LLM**: Gemini (default) or Ollama; optional hybrid (primary + fallback) with `LLM_PRIMARY_BACKEND` / `LLM_FALLBACK_BACKEND`.
- **Timers**: Multiple named timers, NL parsing, TTS on expiry; session timer with pause/resume.
- **Wellness**: Auto wellness breaks after sitting threshold; optional break-compliance (leave-desk detection, violation count, ESP32 movement e.g. swing).
- **Presence**: ESP32 PIR over MQTT and/or local Pi PIR GPIO + vision. Fusion: camera-dominant with PIR fallback and optional PIR-complement sanity checks.
- **Vision**: Person detection — IMX500 on-sensor (preferred), picamera2 + CPU YOLO, or OpenCV V4L2.
- **Dashboard**: FastAPI on port 8000 — `/api/status`, `/api/sessions`, `/api/control`, `/api/config`; static UI; request log to file (high-freq `/api/status` skipped).

## Requirements

- Python 3.7+
- Microphone (for local STT; or use ESP32 for capture)
- Optional: MQTT broker (e.g. Mosquitto), ESP32 with PIR; Raspberry Pi 5 for vision/PIR GPIO

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/AuraBot.git
cd AuraBot
python3 -m venv aurabot-env
source aurabot-env/bin/activate   # Windows: aurabot-env\Scripts\activate
pip install -r requirements.txt
```

## Usage

**Run backend (from project root):**

```bash
python -m backend
```

Starts: voice WebSocket server, local sensor/session API, optional MQTT client, dashboard. Config via `backend/.env`.

**Key env (`backend/.env`):**

| Env | Description |
|-----|-------------|
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD` | Broker connection |
| `ENABLE_MQTT` | `false` = no broker; local PIR/vision still drive session |
| `ENABLE_PIR_GPIO` | `true` = read PIR from Pi GPIO (e.g. BCM 17) |
| `ENABLE_VISION` | `true` = camera person detection into presence |
| `PIR_GPIO_PIN`, `PIR_POLL_INTERVAL_SECONDS`, `PIR_HEARTBEAT_SECONDS`, `PIR_WARMUP_SECONDS` | PIR tuning |
| `VOICE_WS_PORT` | Voice WebSocket port (default 8765) |
| `DASHBOARD_PORT` | Dashboard HTTP port (default 8000) |
| `LLM_PRIMARY_BACKEND` | `gemini` or `ollama` |
| `LLM_FALLBACK_BACKEND` | Fallback when hybrid (e.g. `ollama`) |
| `LLM_DISABLE_FALLBACK` | `true` = no hybrid, primary only |
| `GEMINI_API_KEY`, `OLLAMA_MODEL`, `OLLAMA_HOST` | LLM backends |
| `LOCAL_LLM_WARM_ON_START` | `true` = one-shot warm-up to local LLM |
| `VISION_CAPTURE` | `auto` \| `imx` \| `picamera2` \| `opencv` |
| `VISION_IMX_MODEL_DIR`, `VISION_MODEL`, `VISION_FALLBACK_MODEL` | Vision model paths |
| `VOICE_LATENCY_LOG` | `1` or path → append per-turn latency to file |

**Local PIR + vision (no MQTT):**

```bash
cd backend
ENABLE_MQTT=false ENABLE_PIR_GPIO=true ENABLE_VISION=true python sim_loop.py
```

**Run with IMX AI camera (libcamera from /usr/local):**

```bash
ENABLE_VISION=true ./scripts/run_backend_imx.sh
```

**Presence simulation (MQTT):**

```bash
./scripts/presence_sim.sh
DISTANCE_CM=60 MOTION=0 CAMERA_CONFIRMED=0 ./scripts/presence_sim.sh  # absent
```

**Standalone voice server (echo test, no AuraBot):**

```bash
python -m backend.voice.voice_ws_server
```

**ESP32:** Build and flash with ESP-IDF; set WiFi/MQTT and optional voice WebSocket URI in `idf.py menuconfig` or `sdkconfig.defaults`.

## Project structure

```
AuraBot/
├── backend/
│   ├── __main__.py           # Entry: python -m backend → sim_loop.main()
│   ├── sim_loop.py           # AuraBot class, service startup (MQTT, PIR, vision, dashboard, voice WS)
│   ├── core/logger.py        # Conversation and MQTT logging
│   ├── llm/                  # Gemini, Ollama, HybridLLMClient; response_handler (keywords, timers, exit, LLM)
│   ├── timer/                # session_timer, timer_parser, timer_manager, wellness_timer_trigger
│   ├── voice/                # stt, tts, voice_ws_server, turn_endpoint (VAD/commit)
│   ├── mqtt/                 # mqtt_api (sensors, presence, control, wellness, break-compliance), mqtt_integration
│   ├── pir/                  # pir_integration (GPIO polling → sensor API)
│   ├── api/dashboard_api.py  # FastAPI status/sessions/control/config + static dashboard
│   └── vision/               # object_detection (imx/picamera2/opencv), vision_integration, setup_model, setup_imx_model, benchmarks
├── scripts/                  # presence_sim.sh, run_backend_imx.sh, setup_pi_venv.sh, temp_check.sh
├── dashboard/                # index.html, app.js, styles.css
├── esp32/main/               # main.c, mqtt, pir, wifi_connect, speaker, voice_ws, voice_session, voice_conversation, wakeword, action, servo, usonic, lcd_lvgl, robot_eyes
├── requirements.txt
├── RASPBERRY_PI_SETUP.md
└── backend/vision/README.md
```

## Modules 

| Area | Module | Role |
|------|--------|------|
| Entry | `sim_loop` | AuraBot init, start_services (PIR/vision/MQTT), dashboard thread, voice WS server |
| Core | `core/logger` | Logging by category |
| LLM | `llm/llm_client` | Gemini, Ollama, Hybrid; system prompt from env |
| LLM | `llm/response_handler` | Keywords, timer routing, exit, LLM fallback |
| Timer | `timer/*` | Session time, NL parser, multi-timer manager, wellness trigger |
| Voice | `voice/voice_ws_server` | WebSocket /voice, Opus in/out, ASR→LLM→TTS pipeline, latency log |
| Voice | `voice/turn_endpoint` | Turn segmentation (min/max/idle/stalled ms) |
| MQTT | `mqtt/mqtt_api` | Sensor/control handlers, presence debounce, wellness trigger, break-compliance, camera-dominant fusion |
| MQTT | `mqtt/mqtt_integration` | Client lifecycle, topic subscribe (TTS-over-MQTT deprecated) |
| API | `api/dashboard_api` | FastAPI; request log to file; /api/status not logged |
| PIR | `pir/pir_integration` | GPIO PIR → local sensor API |
| Vision | `vision/object_detection` | IMX / picamera2 / OpenCV; person detection |
| Vision | `vision/vision_integration` | Detection loop → server-side sensor API |

## Dashboard API

- `GET /api/status` — session state, current session time, timer counts
- `GET /api/sessions` — sitting session history (query param `limit`)
- `POST /api/control` — `cmd`: start_session, pause_session, resume_session, stop_session, trigger_wellness, move (action); move requires MQTT
- `GET /api/config` — wellness, debounce, presence_fusion, camera_dominant_presence, pir_complement

## Logs

- `logs/aurabot_conversation.log` — conversation log
- `logs/sitting_sessions.json` — session history
- `logs/dashboard_requests.log` — dashboard API requests (excluding /api/status)
- `logs/voice_pipeline_latency.log` — when `VOICE_LATENCY_LOG=1`

## Docs

- [RASPBERRY_PI_SETUP.md](RASPBERRY_PI_SETUP.md) — Pi 5 audio, deps, MQTT auth
- [backend/vision/README.md](backend/vision/README.md) — YOLO, NCNN, IMX export, benchmarks
- [backend/vision/IMX_PI_AI_CAMERA_SETUP.md](backend/vision/IMX_PI_AI_CAMERA_SETUP.md) — IMX500 export and runtime
