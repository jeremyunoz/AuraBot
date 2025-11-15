from stt import STT
from tts import tts
from datetime import datetime
from time import sleep
import os

# Get the project root directory (parent of chatbot_simulation)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "stt_tts_test.log")

# Ensure logs directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log_event(user_text, bot_text):
    try:
        with open(LOG_FILE, "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] USER: {user_text}\n")
            f.write(f"[{timestamp}] BOT:  {bot_text}\n\n")
    except Exception as e:
        print(f"Error logging event: {e}")

def main():
    # Greeting Part
    stt = STT()
    engine = tts()
    engine.style()
    engine.speak("Hello! I am AuraPet. Let's talk.")

    # Main Loop to handle listen and speak
    while True:
        user_text = stt.listen_and_transcribe()
        if not user_text:
            continue
        if "exit" in user_text or "quit" in user_text:
            bot_text = "Goodbye! Remember to stretch often."
            engine.speak(bot_text)
            log_event(user_text, bot_text)
            sleep(0.5)
            engine.shutdown_tts()
            break

        if "tired" in user_text:
            bot_text = "Let's take a two-minute break to relax your body."
        elif "hello" in user_text or "hi" in user_text:
            bot_text = "Hi there! How are you feeling today?"
        elif "reminder" in user_text:
            bot_text = "You've been sitting for a while. Time to move a bit!"
        else:
            bot_text = f"You said {user_text}. I'm here to keep you active!"

        # Minimal delay for audio device handoff (system say is fast and reliable)
        sleep(0.1)
        
        try:
            engine.speak(bot_text)
        except Exception as e:
            print(f"Error speaking: {e}")
            import traceback
            traceback.print_exc()
        
        # Log asynchronously to avoid blocking
        try:
            log_event(user_text, bot_text)
        except Exception as e:
            print(f"Error logging: {e}")
        
    
if __name__ == "__main__":
    main() # main loop function