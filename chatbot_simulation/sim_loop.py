"""
Main simulation loop for AuraBot chatbot.
Handles conversation flow, speech recognition, and text-to-speech interactions.
"""

from stt import STT
from tts import tts
from logger import ConversationLogger
from response_handler import ResponseHandler
from timer_manager import TimerManager
from time import sleep, time
import os
import traceback
import threading
from typing import Optional, Dict


# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "stt_tts_test.log")
AUDIO_HANDOFF_DELAY = 0.1  # Delay for audio device handoff
SHUTDOWN_DELAY = 0.5  # Delay before shutdown


class AuraBot:
    """Main chatbot class that orchestrates STT, TTS, and conversation logic."""
    
    def __init__(self, 
                 greeting: str = "Hello! I am AuraBot. Let's talk.",
                 log_file: Optional[str] = None,
                 custom_responses: Optional[Dict[str, str]] = None):
        """
        Initialize the AuraBot chatbot.
        
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
        
        # Initialize TimerManager
        self.timer_manager = TimerManager(self.tts_engine, self.logger)
        
        self._is_running = False
        
        # Inactivity timer tracking
        self._last_activity_time = time()
        self._inactivity_timer_thread = None
        self._timer_lock = threading.Lock()
    
    def start(self):
        """Start the chatbot conversation."""
        self._is_running = True
        self._last_activity_time = time()
        self._start_inactivity_timer()
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
            
            # Update activity time
            self._update_activity_time()
            
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
    
    def _start_inactivity_timer(self):
        """Start the background inactivity timer thread."""
        def timer_loop():
            """Background thread that continuously tracks inactivity time."""
            while self._is_running:
                sleep(1)  # Check every second
                with self._timer_lock:
                    elapsed_time = time() - self._last_activity_time
                    # For now, just track the time (reminders will be added in next step)
                    # This timer runs continuously in the background
        
        self._inactivity_timer_thread = threading.Thread(target=timer_loop, daemon=True)
        self._inactivity_timer_thread.start()
    
    def _update_activity_time(self):
        """Update the last activity time to current time."""
        with self._timer_lock:
            self._last_activity_time = time()
    
    def get_inactivity_duration(self) -> float:
        """
        Get the duration of inactivity in seconds.
        
        Returns:
            float: Seconds since last activity
        """
        with self._timer_lock:
            return time() - self._last_activity_time
    
    # Timer convenience methods
    def set_timer(self, duration_seconds: int, name: Optional[str] = None) -> str:
        """
        Set a timer (convenience method that also updates activity time).
        
        Args:
            duration_seconds: Duration in seconds
            name: Optional timer name
        
        Returns:
            str: Timer ID
        """
        self._update_activity_time()
        return self.timer_manager.set_timer(duration_seconds, name)
    
    def get_active_timers(self):
        """Get list of active timers."""
        return self.timer_manager.get_active_timers()
    
    def get_timer_status_message(self) -> str:
        """
        Get a human-readable status message for active timers.
        
        Returns:
            str: Status message
        """
        active_timers = self.timer_manager.get_active_timers()
        
        if not active_timers:
            return "You don't have any active timers."
        
        if len(active_timers) == 1:
            timer = active_timers[0]
            time_str = self.timer_manager.format_time_remaining(timer["time_remaining"])
            return f"You have {time_str} remaining on your {timer['name'].lower()}."
        else:
            parts = []
            for timer in active_timers:
                time_str = self.timer_manager.format_time_remaining(timer["time_remaining"])
                parts.append(f"{timer['name']} with {time_str} left")
            
            return f"You have {len(active_timers)} active timers: {', '.join(parts)}."
    
    def _shutdown(self):
        """Clean up resources and shutdown the chatbot."""
        self._is_running = False
        
        # Cancel all active timers
        try:
            canceled_count = self.timer_manager.cancel_all_timers()
            if canceled_count > 0:
                print(f"Canceled {canceled_count} active timer(s) on shutdown")
        except Exception as e:
            print(f"Error canceling timers: {e}")
        
        # Shutdown TTS
        try:
            self.tts_engine.shutdown_tts()
        except Exception as e:
            print(f"Error shutting down TTS: {e}")


def main():
    """Main entry point for the chatbot."""
    chatbot = AuraBot()
    chatbot.start()
    print("Sitting time: ", chatbot.get_inactivity_duration())


if __name__ == "__main__":
    main()