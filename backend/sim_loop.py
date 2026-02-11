"""
Main simulation loop for AuraBot chatbot.
Handles conversation flow, speech recognition, and text-to-speech interactions.
"""

from stt import STT
from tts import TTS
from logger import AuraBotLogger, ConversationLogger
from response_handler import ResponseHandler
from llm_client import LLMClient
from timer_manager import TimerManager
from wellness_timer_trigger import WellnessTimerTrigger
from time import sleep
from typing import Optional, Dict, List
from mqtt_api import MQTTAPI
from mqtt_integration import MQTTIntegration, TTSWithMQTT
from vision_integration import start_vision_integration
from dashboard_api import run_dashboard
from dotenv import load_dotenv
import os
import traceback

# Load environment variables
load_dotenv()


# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "aurabot_conversation.log")
AUDIO_HANDOFF_DELAY = 0.5  # Delay for audio device handoff
SHUTDOWN_DELAY = 0.5  # Delay before shutdown



class AuraBot:
    """Main chatbot class that orchestrates STT, TTS, and conversation logic."""
    
    def __init__(self, 
                 greeting: str = "Hello! I am AuraBot. Let's talk.",
                 log_file: Optional[str] = None,
                 custom_responses: Optional[Dict[str, str]] = None,
                 enable_mqtt: bool = True,
                 enable_vision: bool = False,
                 enable_llm: bool = True,
                 gemini_api_key: Optional[str] = None,
                 gemini_model: str = "gemini-2.5-flash",
                 llm_system_prompt: Optional[str] = None,
                 wellness_threshold_seconds: Optional[int] = None,
                 wellness_break_duration_seconds: Optional[int] = None,
                 wellness_pause_timeout_seconds: Optional[int] = None):
        """
        Initialize the AuraBot chatbot.
        
        Args:
            greeting: Initial greeting message
            log_file: Optional custom log file path
            custom_responses: Optional custom response dictionary
            enable_mqtt: Whether to enable MQTT integration for sensor data
            enable_vision: Whether to use camera for presence (person detection); requires enable_mqtt
            enable_llm: Whether to enable LLM-powered conversation (Gemini)
            gemini_api_key: Google AI API key (or set GEMINI_API_KEY env var)
            gemini_model: Gemini model identifier (default: gemini-2.5-flash)
            llm_system_prompt: Custom system prompt for the LLM personality
            wellness_threshold_seconds: Seconds of sitting before triggering wellness timer
                                      (None uses default from WellnessTimerTrigger)
            wellness_break_duration_seconds: Duration of wellness break timer in seconds
                                           (None uses default from WellnessTimerTrigger)
            wellness_pause_timeout_seconds: Seconds to wait while paused before stopping session
                                            (None uses default from WellnessTimerTrigger)
        """
        self.greeting = greeting
        # Use AuraBotLogger for comprehensive logging with category routing
        self.logger = AuraBotLogger(log_file=log_file or LOG_FILE)

        # Initialize STT (Pi does STT; TTS is on ESP32 via MQTT)
        self.stt = STT()

        # Initialize TTS (raw engine; may wrap with TTSWithMQTT when MQTT is enabled)
        self._tts_engine_raw = TTS()
        self._tts_engine_raw.style()
        self.tts_engine = self._tts_engine_raw

        # Initialize TimerManager (tts_engine may be replaced with wrapper below)
        self.timer_manager = TimerManager(self.tts_engine, self.logger)

        # Initialize LLM client for intelligent conversation (optional)
        llm_client = None
        if enable_llm:
            try:
                llm_client = LLMClient(
                    api_key=gemini_api_key,
                    model=gemini_model,
                    system_prompt=llm_system_prompt,
                )
                self.logger.log_general(
                    f"LLM conversation enabled (model: {gemini_model})", "INFO"
                )
            except Exception as e:
                self.logger.log_error(f"Could not initialize LLM client: {e}")
                self.logger.log_general(
                    "Continuing without LLM — falling back to keyword responses", "WARNING"
                )

        # Initialize ResponseHandler with TimerManager and LLM client
        self.response_handler = ResponseHandler(
            custom_responses, self.timer_manager, llm_client=llm_client
        )

        # Initialize MQTT integration (optional); when enabled, TTS also publishes to aurabot/tts/speak
        self.mqtt_api: Optional[MQTTAPI] = None
        self.mqtt_integration: Optional[MQTTIntegration] = None

        if enable_mqtt:
            try:
                self.mqtt_api = MQTTAPI(
                    self,
                    wellness_threshold_seconds=wellness_threshold_seconds,
                    wellness_break_duration_seconds=wellness_break_duration_seconds,
                    wellness_pause_timeout_seconds=wellness_pause_timeout_seconds,
                    logger=self.logger
                )
                self.mqtt_integration = MQTTIntegration(self.mqtt_api)
                # Pi does STT only; TTS is on ESP32. Wrap TTS so speak() publishes to aurabot/tts/speak (no local playback).
                self.tts_engine = TTSWithMQTT(self._tts_engine_raw, self.mqtt_integration)
                # Point all TTS users at the wrapper: conversation loop, TimerManager, MQTTAPI, WellnessTimerTrigger
                self.timer_manager.tts_engine = self.tts_engine
                self.mqtt_api.tts_engine = self.tts_engine
                self.mqtt_api.wellness_trigger.tts_engine = self.tts_engine
            except Exception as e:
                self.logger.log_error(f"Could not initialize MQTT integration: {e}")
                self.logger.log_general("Continuing without MQTT support...", "WARNING")
        
        self._enable_vision = enable_vision
        self._vision_stop_event = None
        self._vision_ready_event = None
        self._is_running = False
    
    def start(self):
        """Start the chatbot conversation."""
        self._is_running = True
        
        # Start MQTT integration if enabled
        if self.mqtt_integration:
            try:
                self.mqtt_integration.start()
                self.logger.log_mqtt("MQTT integration enabled - sensor data will trigger wellness timers", "INFO")
            except Exception as e:
                self.logger.log_error(f"MQTT integration failed to start: {e}")
        
        # Start vision (camera) integration if enabled; feeds camera_confirmed into MQTT sensor API
        if self._enable_vision and self.mqtt_api:
            try:
                self.mqtt_api.set_presence_fusion(True)  # Require camera AND PIR to infer presence
                self._vision_stop_event, self._vision_ready_event = start_vision_integration(self)
                self.logger.log_general(
                    "Vision integration enabled - presence requires both camera and PIR motion",
                    "INFO",
                )
                # Wait for vision model to load, camera to initialize, and warmup to complete
                if self._vision_ready_event:
                    self.logger.log_general("Waiting for vision model to load and calibrate...", "INFO")
                    self._vision_ready_event.wait(timeout=10.0)  # Wait up to 10 seconds
                    if self._vision_ready_event.is_set():
                        self.logger.log_general("Vision model loaded and calibrated - ready to process", "INFO")
                    else:
                        self.logger.log_general("Vision initialization timeout - continuing anyway", "WARNING")
            except Exception as e:
                self.logger.log_error(f"Vision integration failed to start: {e}")
        
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
            self.logger.log_error(f"Error speaking: {e}")
            traceback.print_exc()
    
    # Timer convenience methods
    def set_timer(self, duration_seconds: int, name: Optional[str] = None) -> str:
        """
        Set a timer (convenience method).
        
        Args:
            duration_seconds: Duration in seconds
            name: Optional timer name
        
        Returns:
            str: Timer ID
        """
        return self.timer_manager.set_timer(duration_seconds, name)
    
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
    
    # Session Timer convenience methods (for object detection integration)
    def start_sitting_timer(self) -> bool:
        """
        Start tracking sitting time (user detected in area).
        To be called by object detection when user is detected.
        
        Returns:
            bool: True if started, False if already active
        """
        return self.timer_manager.session_timer.start()
    
    def pause_sitting_timer(self) -> bool:
        """
        Pause sitting time tracking (user left area).
        To be called by object detection when user leaves.
        
        Returns:
            bool: True if paused, False if not active
        """
        return self.timer_manager.session_timer.pause()
    
    def stop_sitting_timer(self) -> Optional[Dict]:
        """
        Stop current sitting session and save it.
        
        Returns:
            Optional[Dict]: Session data if session existed, None otherwise
        """
        return self.timer_manager.session_timer.stop()
    
    def get_current_sitting_time(self) -> float:
        """
        Get accumulated sitting time for current session.
        
        Returns:
            float: Total seconds in current session
        """
        return self.timer_manager.session_timer.get_current_session_time()
    
    def get_sitting_timer_state(self) -> str:
        """
        Get current state of sitting timer.
        
        Returns:
            str: "idle", "active", or "paused"
        """
        return self.timer_manager.session_timer.get_state()
    
    def get_sitting_session_history(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Get saved sitting session history.
        
        Args:
            limit: Optional limit on number of sessions to return
        
        Returns:
            List[Dict]: List of session records
        """
        return self.timer_manager.session_timer.get_session_history(limit)
    
    def get_total_sitting_time(self) -> float:
        """
        Get total sitting time across all saved sessions.
        
        Returns:
            float: Total seconds across all sessions
        """
        return self.timer_manager.session_timer.get_total_sitting_time()
    
    def _shutdown(self):
        """Clean up resources and shutdown the chatbot."""
        self._is_running = False
        
        # Stop vision (camera) integration if enabled
        if self._vision_stop_event:
            try:
                self._vision_stop_event.set()
                # Give vision thread a moment to clean up camera resources
                sleep(0.2)
            except Exception as e:
                self.logger.log_error(f"Error stopping vision integration: {e}")
        
        # Stop wellness timer monitoring
        if self.mqtt_api and self.mqtt_api.wellness_trigger:
            try:
                self.mqtt_api.wellness_trigger.stop_monitoring()
            except Exception as e:
                self.logger.log_error(f"Error stopping wellness timer monitoring: {e}")
        
        # Stop sensor timeout monitoring
        if self.mqtt_api:
            try:
                self.mqtt_api._stop_timeout_monitoring()
            except Exception as e:
                self.logger.log_error(f"Error stopping sensor timeout monitoring: {e}")
        
        # Stop MQTT integration
        if self.mqtt_integration:
            try:
                self.mqtt_integration.stop()
            except Exception as e:
                self.logger.log_error(f"Error stopping MQTT integration: {e}")
        
        # Stop and save current sitting session if active
        try:
            session_data = self.timer_manager.session_timer.stop()
            if session_data:
                self.logger.log_session(
                    f"Saved sitting session: {session_data.get('formatted_duration', 'N/A')}",
                    "INFO",
                    metadata={"duration_seconds": session_data.get('duration_seconds')}
                )
        except Exception as e:
            self.logger.log_error(f"Error stopping sitting timer: {e}")
        
        # Cancel all active timers
        try:
            canceled_count = self.timer_manager.cancel_all_timers()
            if canceled_count > 0:
                self.logger.log_timer(
                    f"Canceled {canceled_count} active timer(s) on shutdown",
                    "INFO",
                    metadata={"count": canceled_count}
                )
        except Exception as e:
            self.logger.log_error(f"Error canceling timers: {e}")
        
        # Shutdown TTS (wrapper delegates to raw engine)
        try:
            self.tts_engine.shutdown_tts()
        except Exception as e:
            self.logger.log_error(f"Error shutting down TTS: {e}")


def main():
    """
    Main entry point for the chatbot.
    
    Configuration via environment variables:
    - ENABLE_MQTT: Set to "false" to disable MQTT (default: enabled)
    - ENABLE_VISION: Set to "true" to use camera for presence (default: disabled)
    - ENABLE_LLM: Set to "false" to disable Gemini conversation (default: enabled)
    - GEMINI_API_KEY: Google AI API key for Gemini (required when LLM is enabled)
    - GEMINI_MODEL: Gemini model identifier (default: gemini-2.5-flash)
    - WELLNESS_THRESHOLD_SECONDS: Sitting time threshold before wellness timer triggers
    - WELLNESS_BREAK_DURATION_SECONDS: Duration of wellness break timer
    - WELLNESS_PAUSE_TIMEOUT_SECONDS: Seconds to wait while paused before stopping session
    """
    # Check MQTT enable/disable
    enable_mqtt = os.getenv("ENABLE_MQTT", "true").lower() != "false"
    enable_vision = os.getenv("ENABLE_VISION", "false").lower() == "true"
    
    # LLM configuration
    enable_llm = os.getenv("ENABLE_LLM", "true").lower() != "false"
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    
    # Get wellness configuration from environment variables
    env_threshold = os.getenv("WELLNESS_THRESHOLD_SECONDS")
    wellness_threshold = int(env_threshold) if env_threshold else None
    
    env_duration = os.getenv("WELLNESS_BREAK_DURATION_SECONDS")
    break_duration = int(env_duration) if env_duration else None

    env_pause_timeout = os.getenv("WELLNESS_PAUSE_TIMEOUT_SECONDS")
    pause_timeout = int(env_pause_timeout) if env_pause_timeout else None
    
    # Create AuraBot instance
    bot = AuraBot(
        enable_mqtt=enable_mqtt,
        enable_vision=enable_vision,
        enable_llm=enable_llm,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        wellness_threshold_seconds=wellness_threshold,
        wellness_break_duration_seconds=break_duration,
        wellness_pause_timeout_seconds=pause_timeout,
    )
    
    # Log configuration if set
    if enable_mqtt and (wellness_threshold is not None or break_duration is not None or pause_timeout is not None):
        config_msg = "Wellness timer configuration:"
        metadata = {}
        if wellness_threshold is not None:
            hours = wellness_threshold // 3600
            minutes = (wellness_threshold % 3600) // 60
            config_msg += f" Threshold: {wellness_threshold}s ({hours}h {minutes}m)"
            metadata["threshold_seconds"] = wellness_threshold
        if break_duration is not None:
            minutes = break_duration // 60
            config_msg += f" Break duration: {break_duration}s ({minutes}m)"
            metadata["break_duration_seconds"] = break_duration
        if pause_timeout is not None:
            minutes = pause_timeout // 60
            config_msg += f" Pause timeout: {pause_timeout}s ({minutes}m)"
            metadata["pause_timeout_seconds"] = pause_timeout
        bot.logger.log_wellness(config_msg, "INFO", metadata=metadata if metadata else None)
    
    if enable_vision:
        bot.logger.log_general("Vision (camera) enabled for presence detection", "INFO")
    
    # Start dashboard in background thread
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "8000"))
    dashboard_thread = run_dashboard(bot, host="0.0.0.0", port=dashboard_port)
    bot.logger.log_general(
        f"Dashboard started at http://0.0.0.0:{dashboard_port}",
        "INFO",
        metadata={"port": dashboard_port, "local_url": f"http://localhost:{dashboard_port}"}
    )
    
    # Start the main AuraBot conversation loop (blocks)
    bot.start()


if __name__ == "__main__":
    main()