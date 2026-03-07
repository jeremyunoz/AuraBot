# AuraBot

A voice-activated wellness chatbot that uses speech-to-text and text-to-speech for interactive conversations, wellness reminders, and presence-aware session tracking. Combines ESP32 sensors, Raspberry Pi vision, and MQTT for a multi-device setup.

## Features

### Voice & Conversation
-  🎤 **Speech Recognition**: Listens to voice input using Google's speech recognition API
-  🔊 **Text-to-Speech**: Natural voice output (macOS `say`, espeak-ng on Raspberry Pi, pyttsx3 fallback)
-  💬 **Interactive Conversations**: Natural dialogue with context-aware responses
-  📝 **Conversation Logging**: Logs all interactions with timestamps

### Wellness & Timers
-  ⏰ **Timer Management**: Set, query, and cancel timers via voice (natural language support)
-  ⏱️ **Session Timer**: Tracks sitting time with pause/resume (presence-aware)
-  🏃 **Wellness Timer Trigger**: Automatically suggests breaks after extended sitting
-  👁️ **Presence Detection**: PIR sensor (ESP32) + optional camera (Pi) drive session start/pause

### Hardware & Integration
-  📡 **ESP32 + MQTT**: PIR motion sensor publishes to `aurabot/sensors`
-  📷 **Vision Module**: YOLO person detection on Raspberry Pi 5 (camera_confirmed)
-  📊 **Web Dashboard**: Status, session controls, timers, wellness config (port 8000)
-  🧪 **Presence Simulator**: `presence_sim.sh` for testing without hardware

## Requirements

-  Python 3.7+
-  Microphone access
-  Internet connection (for Google Speech Recognition API)
-  MQTT broker (e.g. Mosquitto) for sensor integration
-  Optional: ESP32 with PIR sensor; Raspberry Pi 5 for vision

## Installation

1. Clone this repository:

   ```bash
   git clone https://github.com/YOUR_USERNAME/AuraBot.git
   cd AuraBot
   ```

2. Create a virtual environment (recommended):

   ```bash
   python3 -m venv aurabot-env
   source aurabot-env/bin/activate  # On macOS/Linux
   # or
   aurabot-env\Scripts\activate  # On Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Run AuraBot (main loop)

From the **project root** (recommended):

```bash
python -m backend
# or
python -m backend.sim_loop
```

From the `backend/` directory:

```bash
cd backend
python sim_loop.py
```

- Starts the voice loop, MQTT integration (if enabled), and web dashboard at http://localhost:8000
- Use `backend/.env` for `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`
- **Voice**: By default the ESP32 records sound (WebSocket to Pi); Pi runs ASR/LLM/TTS and sends TTS Opus back. Set `ENABLE_VOICE_WS=false` to use the Pi microphone instead. See "Voice session" below.
- **LLM backends**: Set `LLM_BACKEND=gemini` (default) for Google Gemini, or `LLM_BACKEND=ollama` for local Ollama on the Pi. For Ollama, install and start Ollama (`curl -fsSL https://ollama.com/install.sh | sh`), then run `ollama pull tinyllama` (or another small model). Use `OLLAMA_MODEL` and `OLLAMA_HOST` in `.env` to configure.

### Presence simulation (no hardware)

```bash
# Simulate user present (starts session)
./presence_sim.sh

# Simulate user absent (pauses session)
DISTANCE_CM=60 MOTION=0 CAMERA_CONFIRMED=0 ./presence_sim.sh
```

If your broker requires auth: `MQTT_USER=user MQTT_PASS=pass ./presence_sim.sh`

### Vision module (Raspberry Pi 5)

```bash
cd backend/vision
python setup_model.py   # First time only
python object_detection.py --capture picamera2
```

### ESP32 firmware

Build and flash with ESP-IDF:

```bash
cd esp32
idf.py build flash monitor
```

Configure WiFi and MQTT via `idf.py menuconfig` (or sdkconfig.defaults).

### ESP32 speaker (optional)

For boards with an ES8311 codec (e.g. **ESP32-P4-WIFI6** from Waveshare):

1. In `idf.py menuconfig` → **AuraBot Configuration** → **Speaker (ES8311 Codec)**:
   - Enable **Enable speaker output**
   - Defaults match ESP32-P4-WIFI6 (I2C: 7/8, I2S: 9–13, PA: 53). Adjust for other boards.
2. Build and flash. On PIR motion, a short beep plays; you can also call `speaker_beep()`, `speaker_write()`, etc. from your code.

### Voice session (Pi5 ↔ ESP32, voice capture on ESP32)

When voice capture is on the ESP32 (not the Pi), the Pi runs a WebSocket voice server: ESP32 sends Opus (60 ms, 16 kHz mono), Pi decodes → ASR → AuraBot (LLM/timers) → TTS → Opus back to ESP32.

1. **On Pi5** (default: voice from ESP32):

   ```bash
   cd backend
   python sim_loop.py
   ```
   This starts MQTT, dashboard, and the voice WebSocket server on port 8765 (or `VOICE_WS_PORT`). Voice input comes from the ESP32; Pi runs ASR/LLM/TTS and sends TTS Opus back. On first ESP32 connection, the bot sends its greeting as TTS over the WebSocket. To use the Pi microphone instead, run with `ENABLE_VOICE_WS=false`.

2. **Standalone voice server** (no AuraBot, echo only):

   From project root:
   ```bash
   python -m backend.voice.voice_ws_server
   ```
   Or from `backend/`: `python -c "from backend.voice.voice_ws_server import run_voice_server; run_voice_server()"`  
   Listens on `0.0.0.0:8765`, path `/voice`; replies "You said: …" for testing.

3. **On ESP32**: Enable voice session and set the Pi5 URI:

   - `idf.py menuconfig` → **AuraBot Configuration** → **Voice session (online TTS / Pi5 WebSocket)**:
     - **Enable voice session (Opus + WebSocket to Pi5)** = Yes
     - **WebSocket URI for Pi5 voice server** = `ws://pi5.local:8765/voice` (or your Pi5 hostname/IP)
   - Or in `esp32/sdkconfig.defaults`: set `CONFIG_VOICE_SESSION_ENABLE=y` and `CONFIG_VOICE_WS_URI="ws://<host>:8765/voice"`.

4. **Test**: Put the device in ACTIVE state (e.g. wake + start session). ESP32 connects, gets server hello and the greeting TTS, then streams mic Opus. Pi runs ASR → LLM/timers → TTS Opus back to ESP32.

**Measuring end-to-end speech pipeline (STT → LLM → TTS) response time**

- Each voice turn is timed on the Pi: **STT** (ASR), **response** (LLM or keyword), **TTS** (synthesis), and **total** (utterance start to TTS ready).
- **Live interaction:** Run the bot as above (`python sim_loop.py` with ESP32 or Pi mic). Every turn logs one line to the console, e.g.  
  `Voice pipeline latency: stt=420 ms response=890 ms tts=1100 ms total=2410 ms`
- **Log file:** To append per-turn latency to a file (e.g. for reporting or averaging), set `VOICE_LATENCY_LOG=1` (writes to `backend/logs/voice_pipeline_latency.log`) or `VOICE_LATENCY_LOG=/path/to/file.log`:
  ```bash
  VOICE_LATENCY_LOG=1 python -m backend
  ```
  Each line in the log looks like:  
  `[2026-02-26T12:00:00Z] pipeline_latency stt_ms=420 response_ms=890 tts_ms=1100 total_ms=2410 transcript='hello'`
- Use the **total** value as the end-to-end response time (e.g. “achieved ~2400 ms response time during live interaction tests”). Average over multiple turns for a stable number.

### How it works

1. **Startup**: AuraBot greets you with "Hello! I am AuraBot. Let's talk."
2. **Listening**: The bot listens for your voice input (5-second timeout)
3. **Response**: Based on your input, AuraBot responds appropriately:
   -  Say "tired" → Suggests a two-minute break
   -  Say "hello" or "hi" → Friendly greeting
   -  Say "reminder" → Encourages movement
   -  Say "set timer for 5 minutes" → Sets a timer
   -  Say "how much time is left" → Shows remaining time on timers
   -  Say "cancel timer" → Cancels active timers
   -  Say anything else → Acknowledges your message with encouragement
4. **Exit**: Say "exit" or "quit" to end the conversation

### Example Conversation

```
AuraBot: Hello! I am AuraBot. Let's talk.
[Listening...]
You: Hello
AuraBot: Hi there! How are you feeling today?
[Listening...]
You: I'm tired
AuraBot: Let's take a two-minute break to relax your body.
[Listening...]
You: Set a timer for 5 minutes
AuraBot: Sure! Timer set for 5 minutes. I'll remind you when it's done.
[Listening...]
You: How much time is left?
AuraBot: You have 3 minutes and 45 seconds remaining on your timer.
[5 minutes later]
AuraBot: Your timer is up! (TTS notification)
[Listening...]
You: exit
AuraBot: Goodbye! Remember to stretch often.
```

## Project Structure

```
AuraBot/
├── backend/
│   ├── sim_loop.py              # Main loop, AuraBot class, MQTT + dashboard startup
│   ├── core/                    # Shared utilities
│   │   └── logger.py            # Conversation and MQTT logging
│   ├── llm/                     # Conversation & response routing
│   │   ├── llm_client.py       # Gemini / Ollama clients
│   │   └── response_handler.py # Exit, timer, LLM, keyword routing
│   ├── timer/                   # Timers and wellness
│   │   ├── session_timer.py     # Session time tracking
│   │   ├── timer_parser.py      # Natural language timer parsing
│   │   ├── timer_manager.py     # Timer management and notifications
│   │   └── wellness_timer_trigger.py # Auto wellness breaks
│   ├── voice/                   # Speech and WebSocket
│   │   ├── stt.py               # Speech-to-Text
│   │   ├── tts.py               # Text-to-Speech
│   │   └── voice_ws_server.py  # Voice WebSocket (ESP32 ↔ Pi)
│   ├── mqtt/                    # MQTT integration
│   │   ├── mqtt_api.py          # Message handling, presence, control
│   │   └── mqtt_integration.py # Client lifecycle and routing
│   ├── api/                     # HTTP API
│   │   └── dashboard_api.py     # FastAPI dashboard (status, sessions, control)
│   └── vision/                  # Camera presence and YOLO utilities
│       ├── vision_integration.py # Person detection → MQTT
│       ├── object_detection.py  # YOLO person detection runtime
│       └── setup_model.py       # Model download and NCNN conversion
├── dashboard/
│   ├── index.html               # Web dashboard UI
│   ├── app.js                   # Dashboard logic
│   └── styles.css               # Dashboard styles
├── esp32/
│   ├── sdkconfig.defaults       # ESP-IDF default config
│   └── main/
│       ├── main.c               # App entry, WiFi, MQTT task
│       ├── mqtt.c/h             # MQTT publish
│       ├── pir.c/h              # PIR motion sensor (GPIO)
│       ├── speaker.c/h          # Speaker (ES8311 via esp_codec_dev)
│       └── wifi_connect.c/h     # WiFi STA
├── presence_sim.sh              # MQTT presence test script
├── RASPBERRY_PI_SETUP.md        # Pi 5 audio and environment setup
├── requirements.txt
└── README.md
```

## Modules

### Backend (by feature)

| Package / module | Description |
|------------------|-------------|
| `sim_loop.py` | Main loop, AuraBot class; starts MQTT, dashboard, and voice flow |
| **core** | |
| `core/logger.py` | Conversation and MQTT logging with category routing |
| **llm** | |
| `llm/llm_client.py` | Gemini / Ollama LLM clients |
| `llm/response_handler.py` | Keyword responses, timer routing, exit, LLM fallback |
| **timer** | |
| `timer/timer_manager.py` | Multiple concurrent timers, TTS notifications, named timers |
| `timer/timer_parser.py` | Natural language timer parsing (durations, names) |
| `timer/session_timer.py` | Sitting time with pause/resume, JSON session history |
| `timer/wellness_timer_trigger.py` | Auto wellness breaks when sitting exceeds threshold |
| **voice** | |
| `voice/stt.py` | Speech-to-Text (Google API, ambient noise calibration) |
| `voice/tts.py` | Text-to-Speech (macOS `say`, espeak-ng on Pi, pyttsx3 fallback) |
| `voice/voice_ws_server.py` | Voice WebSocket server (ESP32 ↔ Pi, Opus) |
| **mqtt** | MQTT client, API, and sensor routing |
| **api** | FastAPI dashboard (status, sessions, control) |
| **vision** | Camera person detection feeding presence into MQTT |

### MQTT & Dashboard

**Note:** The **Voice/TTS-over-MQTT** path (`aurabot/tts/speak`) is **deprecated**. Voice output now uses the Voice WebSocket (Pi synthesizes TTS and sends Opus to ESP32). MQTT remains used for sensors, control, and dashboard.

| Module | Description |
|--------|-------------|
| `mqtt_api.py` | Handles `aurabot/sensors` and `aurabot/control`; presence debounce; wellness trigger |
| `mqtt_integration.py` | MQTT client factory, lifecycle, topic subscription (TTSWithMQTT / `aurabot/tts/speak` deprecated) |
| `dashboard_api.py` | FastAPI: `/api/status`, `/api/sessions`, `/api/control`, `/api/config` |

### ESP32

| Component | Description |
|-----------|-------------|
| `main.c` | WiFi STA, publisher task, PIR interrupt, optional speaker init |
| `pir.c/h` | PIR motion sensor on GPIO with event groups |
| `mqtt.c/h` | Publish JSON to `aurabot/sensors` (motion, count, ts_us, placeholders for camera/distance) |
| `speaker.c/h` | ES8311 codec output (beep, TTS); optional via Kconfig |
| `wifi_connect.c/h` | WiFi station connection |

## Configuration

### Environment (`backend/.env`)

```env
MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_USERNAME=       # Optional
MQTT_PASSWORD=       # Optional
```

### Microphone (stt.py)

- `timeout`: Max seconds to wait for speech (default: 5)
- `phrase_time_limit`: Max phrase length (default: 5)
- `ambient_noise_duration`: Calibration duration (default: 0.5)

### TTS (tts.py)

- macOS: native `say`; Raspberry Pi: espeak-ng; fallback: pyttsx3
- Speech rate and volume configurable in module

## Timer Feature

- **Set**: "Set a timer for 5 minutes" or "Set a coffee timer for 10 minutes"
- **Query**: "How much time is left?" or "What timers are running?"
- **Cancel**: "Cancel timer" or "Cancel the coffee timer"
- **Multiple timers**: Up to 10 concurrent named timers; TTS on expiry

## Logs

- `logs/stt_tts_test.log` – conversation log
- `logs/sitting_sessions.json` – session history
- MQTT events logged via `AuraBotLogger` when configured

## Dependencies

- `SpeechRecognition` – speech recognition
- `PyAudio` – microphone capture
- `paho-mqtt` – MQTT client
- `python-dotenv` – env config
- `fastapi`, `uvicorn` – dashboard API
- `ultralytics`, `opencv-python-headless` – vision (Raspberry Pi)

## Platform Support

- **macOS**: Native `say` for TTS
- **Raspberry Pi 5**: espeak-ng for TTS; `backend/vision/` for YOLO person detection
- **Linux/Windows**: pyttsx3 fallback for TTS

## Troubleshooting

### Microphone not working

-  Ensure microphone permissions are granted
-  Check that your microphone is connected and working
-  Try specifying a microphone index in `STT.__init__()`

### No audio output

-  On macOS, the system `say` command should work automatically
-  On other platforms, ensure `pyttsx3` is properly installed
-  Check system audio settings

### Speech recognition errors

-  Ensure you have an internet connection (Google API requires internet)
-  Check microphone input levels
-  Try adjusting ambient noise calibration duration

### Timer not working

- Check that timer commands are being recognized
- Verify TimerManager is initialized correctly
- Check logs for timer-related errors

### MQTT not connecting

- Ensure Mosquitto (or another broker) is running
- Set `MQTT_HOST` and `MQTT_PORT` in `backend/.env`
- For auth: set `MQTT_USERNAME` and `MQTT_PASSWORD` (same as broker config)

## Documentation

- **[RASPBERRY_PI_SETUP.md](RASPBERRY_PI_SETUP.md)** – Pi 5 audio capture, dependencies, MQTT auth
- **[backend/vision/README.md](backend/vision/README.md)** – YOLO setup and object detection on Pi 5
