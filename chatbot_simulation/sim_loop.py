"""
Main simulation loop for AuraPet chatbot.
Handles conversation flow, speech recognition, and text-to-speech interactions.
"""

from stt import STT
from tts import tts
from logger import ConversationLogger
from response_handler import ResponseHandler
from time import sleep
import os
import traceback
from typing import Optional, Dict


# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "stt_tts_test.log")
AUDIO_HANDOFF_DELAY = 0.1  # Delay for audio device handoff
SHUTDOWN_DELAY = 0.5  # Delay before shutdown


class AuraPet:
    """Main chatbot class that orchestrates STT, TTS, and conversation logic."""
    
    def __init__(self, 
                 greeting: str = "Hello! I am AuraPet. Let's talk.",
                 log_file: Optional[str] = None,
                 custom_responses: Optional[Dict[str, str]] = None):
        """
        Initialize the AuraPet chatbot.
        
        Args:
            greeting: Initial greeting message
            log_file: Optional custom log file path
            custom_responses: Optional custom response dictionary
        """
        self.greeting = greeting
        self.logger = ConversationLogger(log_file or LOG_FILE)
        self.response_handler = ResponseHandler(custom_responses)
        
        # Initialize STT and TTS
        self.stt = STT()
        self.tts_engine = tts()
        self.tts_engine.style()
        
        self._is_running = False
    
    def start(self):
        """Start the chatbot conversation."""
        self._is_running = True
        self._speak_safely(self.greeting)
        self._run_conversation_loop()
    
    def _run_conversation_loop(self):
        """Main conversation loop."""
        while self._is_running:
            # Listen for user input
            user_text = self.stt.listen_and_transcribe()
            
            # Skip empty inputs
            if not user_text:
                continue
            
            # Get bot response
            bot_text, should_exit = self.response_handler.get_response(user_text)
            
            # Handle exit
            if should_exit:
                self._speak_safely(bot_text)
                self.logger.log_event(user_text, bot_text)
                sleep(SHUTDOWN_DELAY)
                self._shutdown()
                break
            
            # Process regular response
            sleep(AUDIO_HANDOFF_DELAY)
            self._speak_safely(bot_text)
            self.logger.log_event(user_text, bot_text)
    
    def _speak_safely(self, message: str):
        """
        Safely speak a message with error handling.
        
        Args:
            message: Message to speak
        """
        try:
            self.tts_engine.speak(message)
        except Exception as e:
            print(f"Error speaking: {e}")
            traceback.print_exc()
    
    def _shutdown(self):
        """Clean up resources and shutdown the chatbot."""
        self._is_running = False
        try:
            self.tts_engine.shutdown_tts()
        except Exception as e:
            print(f"Error shutting down TTS: {e}")


def main():
    """Main entry point for the chatbot."""
    chatbot = AuraPet()
    chatbot.start()


if __name__ == "__main__":
    main()