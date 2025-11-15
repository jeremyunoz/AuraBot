# AuraBot

A voice-activated wellness chatbot that uses speech-to-text and text-to-speech to provide interactive conversations and wellness reminders.

## Features

- 🎤 **Speech Recognition**: Listens to your voice input using Google's speech recognition API
- 🔊 **Text-to-Speech**: Responds with natural voice output (optimized for macOS using native `say` command)
- 💬 **Interactive Conversations**: Engages in natural dialogue with context-aware responses
- 📝 **Conversation Logging**: Automatically logs all interactions with timestamps
- 🏃 **Wellness Reminders**: Provides gentle reminders to stay active and take breaks

## Requirements

- Python 3.7+
- Microphone access
- Internet connection (for Google Speech Recognition API)

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/AuraBot.git
   cd AuraBot
   ```

2. Create a virtual environment (recommended):
   ```bash
   python3 -m venv aurapet-env
   source aurapet-env/bin/activate  # On macOS/Linux
   # or
   aurapet-env\Scripts\activate  # On Windows
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Run the main simulation loop:

```bash
python chatbot_simulation/sim_loop.py
```

### How it works

1. **Startup**: AuraPet greets you with "Hello! I am AuraPet. Let's talk."
2. **Listening**: The bot listens for your voice input (5-second timeout)
3. **Response**: Based on your input, AuraPet responds appropriately:
   - Say "tired" → Suggests a two-minute break
   - Say "hello" or "hi" → Friendly greeting
   - Say "reminder" → Encourages movement
   - Say anything else → Acknowledges your message with encouragement
4. **Exit**: Say "exit" or "quit" to end the conversation

### Example Conversation

```
AuraPet: Hello! I am AuraPet. Let's talk.
[Listening...]
You: Hello
AuraPet: Hi there! How are you feeling today?
[Listening...]
You: I'm tired
AuraPet: Let's take a two-minute break to relax your body.
[Listening...]
You: exit
AuraPet: Goodbye! Remember to stretch often.
```

## Project Structure

```
AuraBot/
├── chatbot_simulation/
│   ├── sim_loop.py      # Main simulation loop and conversation handler
│   ├── stt.py           # Speech-to-Text module
│   └── tts.py           # Text-to-Speech module
├── logs/                 # Conversation logs (auto-generated)
├── out/                  # Output files
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

## Modules

### `stt.py` - Speech-to-Text
- Uses `speech_recognition` library with Google's online recognizer
- Automatic microphone calibration for ambient noise
- Configurable timeout and phrase limits
- Returns transcribed text in lowercase

### `tts.py` - Text-to-Speech
- Optimized for macOS using native `say` command
- Falls back to `pyttsx3` on other platforms or if system command fails
- Configurable speech rate and volume
- Handles audio device handoff gracefully

### `sim_loop.py` - Main Loop
- Manages the conversation flow
- Handles user input and bot responses
- Logs all interactions to `logs/stt_tts_test.log`
- Implements keyword-based response logic

## Configuration

### Microphone Settings
You can customize microphone settings in `stt.py`:
- `timeout`: Maximum seconds to wait for speech (default: 5)
- `phrase_time_limit`: Maximum seconds for a phrase (default: 5)
- `ambient_noise_duration`: Calibration duration (default: 0.5)

### TTS Settings
TTS settings can be adjusted in `tts.py`:
- Speech rate: Currently set to 200 words per minute (macOS) or 180 (pyttsx3)
- Volume: Set to 0.9 (90%) for pyttsx3

## Logs

All conversations are automatically logged to `logs/stt_tts_test.log` with timestamps:
```
[2024-01-15 10:30:45] USER: hello
[2024-01-15 10:30:45] BOT:  Hi there! How are you feeling today?
```

## Dependencies

- `speechrecognition>=3.10.0` - Speech recognition library
- `pyttsx3>=2.90` - Text-to-speech engine

## Platform Support

- **macOS**: Optimized with native `say` command for reliable TTS
- **Linux/Windows**: Uses `pyttsx3` for cross-platform compatibility

## Troubleshooting

### Microphone not working
- Ensure microphone permissions are granted
- Check that your microphone is connected and working
- Try specifying a microphone index in `STT.__init__()`

### No audio output
- On macOS, the system `say` command should work automatically
- On other platforms, ensure `pyttsx3` is properly installed
- Check system audio settings

### Speech recognition errors
- Ensure you have an internet connection (Google API requires internet)
- Check microphone input levels
- Try adjusting ambient noise calibration duration



