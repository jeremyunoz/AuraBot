# AuraBot

A voice-activated wellness chatbot that uses speech-to-text and text-to-speech to provide interactive conversations and wellness reminders.

## Features

-  🎤 **Speech Recognition**: Listens to your voice input using Google's speech recognition API
-  🔊 **Text-to-Speech**: Responds with natural voice output (optimized for macOS using native `say` command)
-  💬 **Interactive Conversations**: Engages in natural dialogue with context-aware responses
-  📝 **Conversation Logging**: Automatically logs all interactions with timestamps
-  🏃 **Wellness Reminders**: Provides gentle reminders to stay active and take breaks
-  ⏰ **Timer Management**: Set, query, and cancel timers via voice commands with natural language support
-  ⏱️ **Session Timer**: Tracks sitting time with pause/resume functionality

## Requirements

-  Python 3.7+
-  Microphone access
-  Internet connection (for Google Speech Recognition API)

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
python backend/sim_loop.py
```

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
│   ├── sim_loop.py          # Main simulation loop and AuraBot class
│   ├── stt.py               # Speech-to-Text module
│   ├── tts.py               # Text-to-Speech module
│   ├── logger.py            # Conversation logging
│   ├── response_handler.py  # Response generation and command routing
│   ├── timer_manager.py     # Timer management and notifications
│   ├── timer_parser.py      # Natural language timer command parsing
│   └── session_timer.py     # Session time tracking
├── tests/                   # Test suites
│   ├── test_timer_phase1.py
│   ├── test_timer_parser.py
│   ├── test_timer_integration.py
│   ├── test_timer_phase4.py
│   └── test_session_timer.py
├── logs/                    # Conversation and session logs (auto-generated)
├── out/                     # Output files
├── requirements.txt         # Python dependencies
├── TIMER_FEATURE_DESIGN.md  # Comprehensive timer feature documentation
├── SESSION_TIMER_DOCUMENTATION.md  # Session timer documentation
└── README.md               # This file
```

## Modules

### `sim_loop.py` - Main Loop & AuraBot Class

-  Manages the conversation flow and AuraBot lifecycle
-  Handles user input and bot responses
-  Integrates all modules (STT, TTS, ResponseHandler, TimerManager)
-  Manages activity tracking and wellness reminders

### `stt.py` - Speech-to-Text

-  Uses `speech_recognition` library with Google's online recognizer
-  Automatic microphone calibration for ambient noise
-  Configurable timeout and phrase limits
-  Returns transcribed text in lowercase

### `tts.py` - Text-to-Speech

-  Optimized for macOS using native `say` command
-  Falls back to `pyttsx3` on other platforms or if system command fails
-  Configurable speech rate and volume
-  Thread-safe with lock to prevent concurrent speech
-  Handles audio device handoff gracefully

### `response_handler.py` - Response Generation

-  Keyword-based response system
-  Routes timer commands to TimerManager
-  Handles conversational responses and wellness interactions
-  Supports custom response dictionaries

### `timer_manager.py` - Timer Management

-  Manages multiple concurrent timers (up to 10)
-  Thread-safe timer operations
-  Automatic TTS notifications on timer expiration
-  Supports named timers and timer queries
-  See `TIMER_FEATURE_DESIGN.md` for complete documentation

### `timer_parser.py` - Timer Command Parsing

-  Parses natural language timer commands
-  Extracts durations (minutes, hours, seconds)
-  Extracts timer names/labels from user input
-  Supports various time formats and command patterns

### `session_timer.py` - Session Time Tracking

-  Tracks sitting/activity time with pause/resume
-  Saves session history to JSON
-  Thread-safe state management
-  See `SESSION_TIMER_DOCUMENTATION.md` for complete documentation

### `logger.py` - Conversation Logging

-  Logs all interactions with timestamps
-  Structured logging format
-  Automatic log file management

## Configuration

### Microphone Settings

You can customize microphone settings in `stt.py`:

-  `timeout`: Maximum seconds to wait for speech (default: 5)
-  `phrase_time_limit`: Maximum seconds for a phrase (default: 5)
-  `ambient_noise_duration`: Calibration duration (default: 0.5)

### TTS Settings

TTS settings can be adjusted in `tts.py`:

-  Speech rate: Currently set to 200 words per minute (macOS) or 180 (pyttsx3)
-  Volume: Set to 0.9 (90%) for pyttsx3

## Timer Feature

AuraBot includes a comprehensive timer feature that allows you to:

-  **Set timers**: "Set a timer for 5 minutes" or "Set a coffee timer for 10 minutes"
-  **Query timers**: "How much time is left?" or "What timers are running?"
-  **Cancel timers**: "Cancel timer" or "Cancel the coffee timer"
-  **Multiple timers**: Track up to 10 concurrent timers with names

Timers automatically notify you via TTS when they expire. For complete documentation, see [`TIMER_FEATURE_DESIGN.md`](TIMER_FEATURE_DESIGN.md).

## Logs

All conversations are automatically logged to `logs/stt_tts_test.log` with timestamps:

```
[2024-01-15 10:30:45] USER: hello
[2024-01-15 10:30:45] BOT:  Hi there! How are you feeling today?
[2024-01-15 10:35:20] USER: set timer for 5 minutes
[2024-01-15 10:35:20] BOT:  Sure! Timer set for 5 minutes. I'll remind you when it's done.
```

Session timer data is saved to `logs/sitting_sessions.json` (if session timer is used).

## Dependencies

-  `speechrecognition>=3.10.0` - Speech recognition library
-  `pyttsx3>=2.90` - Text-to-speech engine

## Platform Support

-  **macOS**: Optimized with native `say` command for reliable TTS
-  **Linux/Windows**: Uses `pyttsx3` for cross-platform compatibility

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

-  Check that timer commands are being recognized (see timer documentation)
-  Verify TimerManager is initialized correctly
-  Check logs for timer-related errors

## Documentation

-  **Timer Feature**: See [`TIMER_FEATURE_DESIGN.md`](TIMER_FEATURE_DESIGN.md) for complete timer documentation
-  **Session Timer**: See [`SESSION_TIMER_DOCUMENTATION.md`](SESSION_TIMER_DOCUMENTATION.md) for session timer documentation
-  **Environment**: See [`ENVIRONMENT_OVERVIEW.md`](ENVIRONMENT_OVERVIEW.md) for environment setup details

## Testing

Run the test suites to verify functionality:

```bash
# Test timer parser
python3 tests/test_timer_parser.py

# Test timer functionality
python3 tests/test_timer_phase1.py
python3 tests/test_timer_integration.py
python3 tests/test_timer_phase4.py

# Test session timer
python3 tests/test_session_timer.py
```
