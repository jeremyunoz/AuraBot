"""
Main simulation loop for AuraBot chatbot.
Handles conversation flow, speech recognition, and text-to-speech interactions.
"""
import os
import sys

# Ensure project root is on path so "backend" package resolves (e.g. when run as python sim_loop.py from backend/)
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_backend_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from time import sleep
from typing import Optional, Dict, List

from dotenv import load_dotenv

from backend.core.logger import AuraBotLogger, ConversationLogger
from backend.llm import (
    ResponseHandler,
    LLMClient,
    OllamaLLMClient,
    HybridLLMClient,
    build_gemini_profile_from_env,
    build_ollama_profile_from_env,
)
from backend.mqtt.mqtt_api import MQTTAPI
from backend.mqtt.mqtt_integration import MQTTIntegration, TTSWithMQTT
from backend.pir.pir_integration import start_pir_integration
from backend.timer import TimerManager, WellnessTimerTrigger
from backend.vision.vision_integration import start_vision_integration
from backend.voice.tts import TTS
import traceback

# Load environment variables
load_dotenv()


# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "aurabot_conversation.log")
SHUTDOWN_DELAY = 0.5  # Delay before shutdown



class AuraBot:
    """Main chatbot class that orchestrates STT, TTS, and conversation logic."""
    
    def __init__(
        self,
        greeting: str = "Hello! I am AuraBot. Let's talk.",
        log_file: Optional[str] = None,
        custom_responses: Optional[Dict[str, str]] = None,
        enable_mqtt: bool = True,
        enable_vision: bool = False,
        enable_pir_gpio: bool = False,
        pir_gpio_pin: int = 17,
        pir_poll_interval_seconds: float = 0.2,
        pir_heartbeat_seconds: float = 15.0,
        enable_llm: bool = True,
        llm_backend: str = "gemini",
        gemini_api_key: Optional[str] = None,
        gemini_model: str = "gemini-2.5-flash",
        ollama_model: str = "lfm2.5-thinking",
        ollama_host: str = "http://127.0.0.1:11434",
        llm_system_prompt: Optional[str] = None,
        wellness_threshold_seconds: Optional[int] = None,
        wellness_break_duration_seconds: Optional[int] = None,
        wellness_pause_timeout_seconds: Optional[int] = None,
        llm_primary_backend: Optional[str] = None,
        llm_fallback_backend: Optional[str] = None,
        llm_disable_fallback: bool = False,
        local_llm_warm_on_start: bool = False,
    ):
        """
        Initialize the AuraBot chatbot.
        
        Args:
            greeting: Initial greeting message
            log_file: Optional custom log file path
            custom_responses: Optional custom response dictionary
            enable_mqtt: Whether to enable MQTT integration for sensor data
            enable_vision: Whether to use camera for presence (person detection); requires enable_mqtt
            enable_pir_gpio: Whether to read a local PIR sensor from Raspberry Pi GPIO
            pir_gpio_pin: Raspberry Pi BCM GPIO pin connected to PIR OUT
            pir_poll_interval_seconds: PIR polling interval in seconds
            pir_heartbeat_seconds: Max time between PIR publishes when state is unchanged
            enable_llm: Whether to enable LLM-powered conversation
            llm_backend: Deprecated alias for the primary LLM backend ("gemini" or "ollama").
            gemini_api_key: Google AI API key (or set GEMINI_API_KEY env var)
            gemini_model: Gemini model identifier (default: gemini-2.5-flash)
            ollama_model: Ollama model name when llm_backend=ollama (default: lfm2.5-thinking)
            ollama_host: Ollama server URL when llm_backend=ollama (default: http://127.0.0.1:11434)
            llm_system_prompt: Custom system prompt for the LLM personality
            wellness_threshold_seconds: Seconds of sitting before triggering wellness timer
                                      (None uses default from WellnessTimerTrigger)
            wellness_break_duration_seconds: Duration of wellness break timer in seconds
                                           (None uses default from WellnessTimerTrigger)
            wellness_pause_timeout_seconds: Seconds to wait while paused before stopping session
                                            (None uses default from WellnessTimerTrigger)
            llm_primary_backend: Optional explicit primary backend ("gemini" or "ollama").
                                 Falls back to llm_backend when not provided.
            llm_fallback_backend: Optional fallback backend ("gemini" or "ollama") used by HybridLLMClient.
            llm_disable_fallback: When True, always use a single backend (no hybrid routing).
            local_llm_warm_on_start: When True and a local Ollama backend is configured, send a
                                     tiny warm-up request on startup to reduce first-response latency.
        """
        self.greeting = greeting
        # Use AuraBotLogger for comprehensive logging with category routing
        self.logger = AuraBotLogger(log_file=log_file or LOG_FILE)

        # Initialize TTS (raw engine; may wrap with deprecated TTSWithMQTT when MQTT is enabled)
        self._tts_engine_raw = TTS()
        self._tts_engine_raw.style()
        self.tts_engine = self._tts_engine_raw

        # Initialize TimerManager (tts_engine may be replaced with wrapper below)
        self.timer_manager = TimerManager(self.tts_engine, self.logger)

        # Initialize LLM client for intelligent conversation (optional)
        llm_client = None
        self._llm_backend_primary = (llm_primary_backend or llm_backend or "gemini").lower()
        self._llm_backend_fallback = (llm_fallback_backend or "").lower() if llm_fallback_backend else None
        self._llm_disable_fallback = llm_disable_fallback

        if enable_llm:
            try:
                primary = self._llm_backend_primary
                fallback = self._llm_backend_fallback

                use_hybrid = (
                    not self._llm_disable_fallback
                    and fallback
                    and fallback != primary
                )

                if not use_hybrid:
                    if primary == "ollama":
                        llm_client = OllamaLLMClient(
                            model=ollama_model,
                            host=ollama_host,
                            system_prompt=llm_system_prompt,
                        )
                        self.logger.log_general(
                            f"LLM conversation enabled (Ollama, model: {ollama_model})", "INFO"
                        )
                    else:
                        llm_client = LLMClient(
                            api_key=gemini_api_key,
                            model=gemini_model,
                            system_prompt=llm_system_prompt,
                        )
                        self.logger.log_general(
                            f"LLM conversation enabled (Gemini, model: {gemini_model})", "INFO"
                        )
                else:
                    # Build profiles using explicit constructor values and environment-driven defaults.
                    gemini_profile = build_gemini_profile_from_env(
                        model=gemini_model,
                        system_prompt=llm_system_prompt,
                    )
                    ollama_profile = build_ollama_profile_from_env(
                        model=ollama_model,
                        host=ollama_host,
                        system_prompt=llm_system_prompt,
                    )

                    if primary == "ollama":
                        primary_client = OllamaLLMClient(
                            model=ollama_profile.model,
                            host=ollama_profile.host,
                            system_prompt=ollama_profile.system_prompt,
                            max_history_turns=ollama_profile.max_history_turns,
                            temperature=ollama_profile.temperature,
                            max_output_tokens=ollama_profile.max_output_tokens,
                            timeout_seconds=ollama_profile.timeout_seconds,
                        )
                        fallback_client = LLMClient(
                            api_key=gemini_api_key,
                            model=gemini_profile.model,
                            system_prompt=gemini_profile.system_prompt,
                            max_history_turns=gemini_profile.max_history_turns,
                            temperature=gemini_profile.temperature,
                            max_output_tokens=gemini_profile.max_output_tokens,
                        )
                    else:
                        primary_client = LLMClient(
                            api_key=gemini_api_key,
                            model=gemini_profile.model,
                            system_prompt=gemini_profile.system_prompt,
                            max_history_turns=gemini_profile.max_history_turns,
                            temperature=gemini_profile.temperature,
                            max_output_tokens=gemini_profile.max_output_tokens,
                        )
                        fallback_client = OllamaLLMClient(
                            model=ollama_profile.model,
                            host=ollama_profile.host,
                            system_prompt=ollama_profile.system_prompt,
                            max_history_turns=ollama_profile.max_history_turns,
                            temperature=ollama_profile.temperature,
                            max_output_tokens=ollama_profile.max_output_tokens,
                            timeout_seconds=ollama_profile.timeout_seconds,
                        )

                    llm_client = HybridLLMClient(
                        primary_client=primary_client,
                        fallback_client=fallback_client,
                        primary_name=primary,
                        fallback_name=fallback,
                        logger=self.logger,
                    )
                    self.logger.log_general(
                        f"LLM conversation enabled (Hybrid primary={primary}, fallback={fallback})",
                        "INFO",
                        metadata={
                            "primary_backend": primary,
                            "fallback_backend": fallback,
                            "gemini_model": gemini_profile.model,
                            "ollama_model": ollama_profile.model,
                        },
                    )

                # Optional warm-up for local Ollama backend to reduce first response latency.
                if local_llm_warm_on_start and isinstance(llm_client, HybridLLMClient):
                    try:
                        # Fire-and-forget tiny prompt; ignore content.
                        llm_client.generate_response("hi")
                        self.logger.log_general(
                            "Local LLM warm-up completed via HybridLLMClient.",
                            "INFO",
                            metadata={"backend": "hybrid"},
                        )
                    except Exception as e:
                        self.logger.log_error(f"Local LLM warm-up failed: {e}")

            except Exception as e:
                self.logger.log_error(f"Could not initialize LLM client: {e}")
                self.logger.log_general(
                    "Continuing without LLM — falling back to keyword responses", "WARNING"
                )

        # Initialize ResponseHandler with TimerManager and LLM client
        self.response_handler = ResponseHandler(
            custom_responses, self.timer_manager, llm_client=llm_client
        )

        # Initialize MQTT integration (optional). DEPRECATED: TTS-over-MQTT (aurabot/tts/speak) is no longer used; voice uses WebSocket TTS.
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
                # DEPRECATED: TTSWithMQTT publishes to aurabot/tts/speak; prefer Voice WebSocket for TTS.
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
        self._enable_pir_gpio = enable_pir_gpio
        self._pir_gpio_pin = pir_gpio_pin
        self._pir_poll_interval_seconds = pir_poll_interval_seconds
        self._pir_heartbeat_seconds = pir_heartbeat_seconds
        self._pir_integration = None
        self._pir_stop_event = None
        self._pir_ready_event = None
        self._is_running = False
    
    def start_services(self):
        """Start MQTT and vision only (no Pi mic / no conversation loop). Use when voice input is from ESP32 via WebSocket."""
        self._is_running = True

        if self.mqtt_integration:
            try:
                self.mqtt_integration.start()
                self.logger.log_mqtt("MQTT integration enabled - sensor data will trigger wellness timers", "INFO")
            except Exception as e:
                self.logger.log_error(f"MQTT integration failed to start: {e}")

        # Start local PIR GPIO integration if enabled; feeds motion into MQTT sensor API.
        if self._enable_pir_gpio and self.mqtt_api:
            try:
                (
                    self._pir_integration,
                    self._pir_stop_event,
                    self._pir_ready_event,
                ) = start_pir_integration(
                    self,
                    gpio_pin=self._pir_gpio_pin,
                    poll_interval_seconds=self._pir_poll_interval_seconds,
                    heartbeat_seconds=self._pir_heartbeat_seconds,
                )
                if self._pir_ready_event:
                    self._pir_ready_event.wait(timeout=3.0)
            except Exception as e:
                self.logger.log_error(f"PIR GPIO integration failed to start: {e}")
        elif self._enable_pir_gpio:
            self.logger.log_general(
                "PIR GPIO integration requested but MQTT API is disabled",
                "WARNING",
            )
        
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

        # Stop local PIR integration if enabled
        if self._pir_integration:
            try:
                self._pir_integration.stop()
            except Exception as e:
                self.logger.log_error(f"Error stopping PIR integration: {e}")
        
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
    - ENABLE_PIR_GPIO: Set to "true" to read PIR sensor from Raspberry Pi GPIO (default: disabled)
    - PIR_GPIO_PIN: BCM GPIO pin number for local PIR sensor (default: 17)
    - PIR_POLL_INTERVAL_SECONDS: PIR polling interval (default: 0.2)
    - PIR_HEARTBEAT_SECONDS: Re-publish PIR state if unchanged after this many seconds (default: 15)
    - VOICE_WS_PORT: Port for the voice WebSocket server (default: 8765)
    - ENABLE_LLM: Set to "false" to disable LLM conversation (default: enabled)
    - LLM_BACKEND: Deprecated alias for primary backend; prefer LLM_PRIMARY_BACKEND
    - LLM_PRIMARY_BACKEND: "gemini" or "ollama" (default: value of LLM_BACKEND or "gemini")
    - LLM_FALLBACK_BACKEND: Optional fallback backend when hybrid routing is enabled (default: "ollama")
    - LLM_DISABLE_FALLBACK: Set to "true" to disable hybrid routing (default: false)
    - LOCAL_LLM_WARM_ON_START: Set to "true" to send a one-time warm-up prompt to the local LLM on startup
    - GEMINI_API_KEY: Google AI API key for Gemini (required when LLM_BACKEND=gemini)
    - GEMINI_MODEL: Gemini model identifier (default: gemini-2.5-flash)
    - OLLAMA_MODEL: Ollama model name when LLM_BACKEND=ollama (default: lfm2.5-thinking)
    - OLLAMA_HOST: Ollama server URL when LLM_BACKEND=ollama (default: http://127.0.0.1:11434)
    - WELLNESS_THRESHOLD_SECONDS: Sitting time threshold before wellness timer triggers
    - WELLNESS_BREAK_DURATION_SECONDS: Duration of wellness break timer
    - WELLNESS_PAUSE_TIMEOUT_SECONDS: Seconds to wait while paused before stopping session
    """
    # Check MQTT enable/disable
    enable_mqtt = os.getenv("ENABLE_MQTT", "true").lower() != "false"
    enable_vision = os.getenv("ENABLE_VISION", "false").lower() == "true"
    enable_pir_gpio = os.getenv("ENABLE_PIR_GPIO", "false").lower() == "true"
    pir_gpio_pin = int(os.getenv("PIR_GPIO_PIN", "17"))
    pir_poll_interval_seconds = float(os.getenv("PIR_POLL_INTERVAL_SECONDS", "0.2"))
    pir_heartbeat_seconds = float(os.getenv("PIR_HEARTBEAT_SECONDS", "15"))
    voice_ws_port = int(os.getenv("VOICE_WS_PORT", "8765"))

    # LLM configuration
    enable_llm = os.getenv("ENABLE_LLM", "true").lower() != "false"
    # Backwards-compatible primary backend selection
    legacy_backend = os.getenv("LLM_BACKEND", "gemini").lower()
    llm_primary_backend = os.getenv("LLM_PRIMARY_BACKEND", legacy_backend).lower()
    llm_fallback_backend = os.getenv("LLM_FALLBACK_BACKEND", "ollama").lower()
    llm_disable_fallback = os.getenv("LLM_DISABLE_FALLBACK", "false").lower() == "true"
    local_llm_warm_on_start = os.getenv("LOCAL_LLM_WARM_ON_START", "false").lower() == "true"

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    ollama_model = os.getenv("OLLAMA_MODEL", "lfm2.5-thinking")
    ollama_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    
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
        enable_pir_gpio=enable_pir_gpio,
        pir_gpio_pin=pir_gpio_pin,
        pir_poll_interval_seconds=pir_poll_interval_seconds,
        pir_heartbeat_seconds=pir_heartbeat_seconds,
        enable_llm=enable_llm,
        llm_backend=llm_primary_backend,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        ollama_model=ollama_model,
        ollama_host=ollama_host,
        llm_primary_backend=llm_primary_backend,
        llm_fallback_backend=llm_fallback_backend,
        llm_disable_fallback=llm_disable_fallback,
        local_llm_warm_on_start=local_llm_warm_on_start,
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
    if enable_pir_gpio:
        bot.logger.log_general(
            "PIR GPIO enabled for local motion detection",
            "INFO",
            metadata={
                "pin": pir_gpio_pin,
                "poll_interval_seconds": pir_poll_interval_seconds,
                "heartbeat_seconds": pir_heartbeat_seconds,
            },
        )

    # Start dashboard in background thread
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "8000"))
    try:
        from backend.api.dashboard_api import run_dashboard
    except ModuleNotFoundError as e:
        if "fastapi" in str(e).lower() or "uvicorn" in str(e).lower():
            raise SystemExit(
                "Dashboard requires fastapi and uvicorn. Install with:\n"
                "  pip install fastapi 'uvicorn[standard]'\n"
                "Or install all deps: pip install -r requirements.txt"
            ) from e
        raise
    dashboard_thread = run_dashboard(bot, host="0.0.0.0", port=dashboard_port)
    bot.logger.log_general(
        f"Dashboard started at http://0.0.0.0:{dashboard_port}",
        "INFO",
        metadata={"port": dashboard_port, "local_url": f"http://localhost:{dashboard_port}"}
    )

    from backend.voice.voice_ws_server import app as voice_ws_app, run_voice_server
    bot.start_services()
    voice_ws_app.state.aurabot = bot
    bot.logger.log_general(
        f"Voice WebSocket server started at ws://0.0.0.0:{voice_ws_port}/voice",
        "INFO",
        metadata={"port": voice_ws_port},
    )
    run_voice_server(host="0.0.0.0", port=voice_ws_port)


if __name__ == "__main__":
    main()
