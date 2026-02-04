# Raspberry Pi 5 Setup Guide for AuraBot

This guide covers audio capture only. Audio output is handled by the ESP32.

## Required System Dependencies (Audio Capture Only)

Install the following packages on your Raspberry Pi 5 for audio capture:

```bash
sudo apt-get update
sudo apt-get install -y \
    flac \
    alsa-utils \
    portaudio19-dev \
    python3-pyaudio
```

## What Each Package Does

- **flac**: Audio codec required by speech_recognition library for Google API
- **alsa-utils**: Audio utilities (arecord) for audio capture device management
- **portaudio19-dev**: Development headers for PyAudio
- **python3-pyaudio**: Python bindings for audio I/O

## Installation Steps

1. **Install system dependencies (audio capture only):**
   ```bash
   sudo apt-get update
   sudo apt-get install -y flac alsa-utils portaudio19-dev python3-pyaudio
   ```

2. **Install Python dependencies:**
   ```bash
   cd /home/jzunoz/projects/AuraBot
   source venv/bin/activate  # if using virtual environment
   pip install -r requirements.txt
   ```

3. **Verify audio capture device:**
   ```bash
   # List audio capture devices
   arecord -l
   
   # Test recording (3 seconds)
   arecord -d 3 test.wav
   ```

4. **Check audio permissions:**
   ```bash
   # Add user to audio group (if not already)
   sudo usermod -a -G audio $USER
   # Logout and login again for changes to take effect
   ```

## Troubleshooting

### ALSA/JACK Warnings - What Causes Them?

**Root Cause:**
These warnings come from **PyAudio's initialization process**, not your code. When PyAudio initializes, it:

1. **Probes all audio backends** - ALSA, JACK, OSS, PulseAudio, etc.
2. **Tries to open various device configurations** - The ALSA config files reference many device types (front, rear, surround, etc.) that don't exist on Raspberry Pi
3. **Attempts to connect to JACK server** - JACK is a professional audio server that's not running on most Pi setups

**Why They Appear:**
- ALSA configuration files (`/usr/share/alsa/alsa.conf`) define many audio device types for desktop systems
- Raspberry Pi typically only has basic audio hardware (HDMI or 3.5mm jack)
- PyAudio tries each configuration and ALSA reports "Unknown PCM" for devices that don't exist
- These are **informational warnings**, not errors

**The Code Now Suppresses Them:**
The updated `stt.py` suppresses these warnings during audio capture initialization and recording, so you'll see cleaner output. The warnings are harmless and don't affect functionality.

### JACK Warnings
JACK server warnings are also **harmless** if you're not using JACK audio. PyAudio is just checking for available backends.

### FLAC Error
If you see: `OSError: FLAC conversion utility not available`
- Install FLAC: `sudo apt-get install flac`

### Audio Capture Device Not Working
1. Check if the capture device is detected: `arecord -l`
2. Test recording: `arecord -d 3 test.wav`
3. Check permissions: `groups` (should include 'audio')
4. If needed: `sudo usermod -a -G audio $USER` (then logout/login)

### MQTT Connection Errors
MQTT "Not authorized" errors mean the broker requires authentication. Use the same credentials as the backend (from `backend/.env`):

- **`mosquitto_pub`**: Pass `-u <MQTT_USERNAME>` and `-P <MQTT_PASSWORD>`:
  ```bash
  mosquitto_pub -h localhost -t "aurabot/sensors" -m '{"motion": 1, "camera_confirmed": 1}' -u user -P YOUR_PASSWORD
  ```
  Replace `user` and `YOUR_PASSWORD` with your `MQTT_USERNAME` and `MQTT_PASSWORD` from `.env`.

- **Backend**: Reads `MQTT_USERNAME` and `MQTT_PASSWORD` from `backend/.env` automatically.

## Running AuraBot

```bash
cd /home/jzunoz/projects/AuraBot/backend
python3 sim_loop.py
```

The application will automatically:
- Use espeak-ng for TTS on Raspberry Pi
- Check for required dependencies on startup
- Provide helpful error messages if something is missing

