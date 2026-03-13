"""
Wellness timer trigger module for AuraBot.
Automatically creates wellness timers based on session time thresholds.
"""

import threading
import time
from typing import Optional, Callable

from backend.core.logger import AuraBotLogger
from backend.timer.timer_manager import TimerManager


class WellnessTimerTrigger:
    """
    Automatically creates wellness timers based on accumulated sitting time.
    
    Monitors session time and triggers break reminders when user has been
    sitting for extended periods.
    """
    
    # Default configuration
    DEFAULT_SITTING_THRESHOLD_SECONDS =  60 * 60  # 1 hours
    DEFAULT_BREAK_DURATION_SECONDS = 10 * 60  # 10 minutes
    DEFAULT_BREAK_TIMER_NAME = "Wellness Break"
    DEFAULT_CHECK_INTERVAL_SECONDS = 10  # Check every 10 seconds
    DEFAULT_PAUSE_TIMEOUT_SECONDS = 30 * 60  # 30 minutes
    
    def __init__(self, 
                 timer_manager: TimerManager,
                 tts_engine=None,
                 sitting_threshold_seconds: Optional[int] = None,
                 break_duration_seconds: Optional[int] = None,
                 break_timer_name: Optional[str] = None,
                 check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS,
                 pause_timeout_seconds: Optional[int] = None,
                 on_wellness_timer_created: Optional[Callable] = None,
                 on_pause_timeout: Optional[Callable] = None,
                 logger: Optional[AuraBotLogger] = None,
                 create_timer_immediately: bool = True):
        """
        Initialize the WellnessTimerTrigger.
        
        Args:
            timer_manager: TimerManager instance for creating timers
            tts_engine: Optional TTS engine for announcements
            sitting_threshold_seconds: Seconds of sitting before triggering (default: 1 hour)
            break_duration_seconds: Duration of break timer (default: 10 minutes)
            break_timer_name: Name for wellness break timers (default: "Wellness Break")
            check_interval_seconds: How often to check session time in background (default: 10 seconds)
            pause_timeout_seconds: Seconds to wait while paused before stopping session (default: 30 minutes)
            on_wellness_timer_created: Optional callback called when wellness timer is created
            on_pause_timeout: Optional callback called when pause timeout expires
            logger: Optional AuraBotLogger instance for logging
        """
        self.timer_manager = timer_manager
        self.tts_engine = tts_engine
        self.on_wellness_timer_created = on_wellness_timer_created
        self.logger = logger
        # When False, reaching the sitting threshold will only signal that a
        # wellness break is due; responsibility for creating the actual timer
        # (and pausing the session) is delegated to higher-level policy code.
        self.create_timer_immediately = create_timer_immediately
        
        self.sitting_threshold_seconds = (
            sitting_threshold_seconds or self.DEFAULT_SITTING_THRESHOLD_SECONDS
        )
        self.break_duration_seconds = (
            break_duration_seconds or self.DEFAULT_BREAK_DURATION_SECONDS
        )
        self.break_timer_name = (
            break_timer_name or self.DEFAULT_BREAK_TIMER_NAME
        )
        self.check_interval_seconds = check_interval_seconds
        self.pause_timeout_seconds = (
            pause_timeout_seconds
            if pause_timeout_seconds is not None
            else self.DEFAULT_PAUSE_TIMEOUT_SECONDS
        )
        if self.pause_timeout_seconds is not None and self.pause_timeout_seconds <= 0:
            self.pause_timeout_seconds = None
        
        # Background monitoring
        self._monitoring_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()
        self._pause_monitoring = threading.Event()  # Pause state for when user leaves
        self._session_time_getter: Optional[Callable[[], float]] = None
        self._pause_started_at: Optional[float] = None
        self._pause_timeout_triggered = False
        self._pending_wellness_resume_timeout = False
        self._wellness_break_end_time: Optional[float] = None
        
        # Track last wellness timer trigger to prevent continuous creation
        # This tracks the session time at which we last triggered a wellness timer
        self._last_trigger_session_time: Optional[float] = None
        self._trigger_lock = threading.Lock()
        self._on_pause_timeout = on_pause_timeout
    
    def check_and_trigger_wellness_timer(self, session_time_seconds: float) -> Optional[str]:
        """
        Check if user has been sitting too long and auto-create break timer.
        
        Triggers wellness timer once per threshold period based on session time,
        not just when threshold is reached. This prevents continuous timer creation.
        
        Logic:
        - First trigger: When session_time >= threshold
        - Subsequent triggers: When session_time >= last_trigger_time + threshold
        
        Args:
            session_time_seconds: Current accumulated sitting time in seconds
        
        Returns:
            Optional[str]: Timer ID if created, None otherwise
        """
        with self._trigger_lock:
            # Determine the session time threshold for next trigger
            if self._last_trigger_session_time is None:
                # First trigger: wait until threshold is reached
                next_trigger_threshold = self.sitting_threshold_seconds
            else:
                # Subsequent triggers: wait for another full threshold period
                next_trigger_threshold = self._last_trigger_session_time + self.sitting_threshold_seconds
            
            # Check if we've reached the next trigger threshold
            if session_time_seconds < next_trigger_threshold:
                return None
            
            # Record when we triggered this threshold (based on session time)
            # This ensures we wait for another full threshold period before next trigger
            self._last_trigger_session_time = session_time_seconds

            # If we are not creating the timer immediately, simply notify the
            # callback and return. Higher-level policy (e.g. MQTTAPI) is
            # responsible for starting the actual break countdown once the user
            # has left the desk.
            if not self.create_timer_immediately:
                if self.on_wellness_timer_created:
                    try:
                        self.on_wellness_timer_created()
                    except Exception as e:
                        if self.logger:
                            self.logger.log_error(f"Error in wellness timer created callback: {e}")
                        else:
                            print(f"Error in wellness timer created callback: {e}")
                return None

            # Check if wellness timer already exists (prevent duplicates)
            active_wellness_timers = self.timer_manager.get_active_timers(
                timer_type=TimerManager.TIMER_TYPE_WELLNESS
            )
            
            if active_wellness_timers:
                # Wellness timer already exists, don't create another
                return None
            
            # We've reached the threshold and no active timer exists
            # This means either:
            # 1. First trigger (last_trigger is None)
            # 2. Previous timer expired, and we've reached next threshold period
            
            # Create wellness break timer
            try:
                timer_id = self.timer_manager.set_timer(
                    duration_seconds=self.break_duration_seconds,
                    name=self.break_timer_name,
                    timer_type=TimerManager.TIMER_TYPE_WELLNESS
                )
                
                # Pause session timer during wellness break
                # This prevents session time from accumulating during the break
                if self.timer_manager.session_timer.is_active():
                    self.timer_manager.session_timer.pause()
                    if self.logger:
                        self.logger.log_wellness("Session timer paused for wellness break", "INFO")
                    self._pending_wellness_resume_timeout = True
                    self._wellness_break_end_time = time.time() + self.break_duration_seconds
                    self._pause_started_at = None
                    self._pause_timeout_triggered = False
                
                # Notify callback (e.g., to clear debounce counters)
                if self.on_wellness_timer_created:
                    try:
                        self.on_wellness_timer_created()
                    except Exception as e:
                        if self.logger:
                            self.logger.log_error(f"Error in wellness timer created callback: {e}")
                        else:
                            print(f"Error in wellness timer created callback: {e}")
                
                # Announce via TTS (voice WebSocket → ESP32 speaker only)
                try:
                    hours = int(session_time_seconds // 3600)
                    minutes = int((session_time_seconds % 3600) // 60)
                    if hours > 0:
                        time_str = f"{hours} hour{'s' if hours != 1 else ''}"
                        if minutes > 0:
                            time_str += f" and {minutes} minute{'s' if minutes != 1 else ''}"
                    else:
                        time_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
                    
                    message = (
                        f"You've been sitting for {time_str}. "
                        f"I've set a {self.break_duration_seconds // 60}-minute break timer."
                    )
                    spoken = False
                    try:
                        from backend.voice.voice_ws_server import (
                            is_voice_client_connected,
                            enqueue_tts_text,
                        )
                        if is_voice_client_connected() and enqueue_tts_text(message):
                            spoken = True
                    except ImportError:
                        # Voice WebSocket stack not available; skip MQTT/text fallbacks
                        spoken = False

                    # Intentionally do NOT fall back to MQTT/text-based TTS here.
                    # Wellness break alarms should be delivered as voice frames over
                    # the voice WebSocket only. If no voice client is connected,
                    # we simply log the event without triggering local/MQTT TTS.
                    if self.logger:
                        self.logger.log_wellness(
                            f"Wellness timer created: {message}",
                            "INFO",
                            metadata={
                                "timer_id": timer_id,
                                "session_time_seconds": session_time_seconds,
                                "break_duration_seconds": self.break_duration_seconds
                            }
                        )
                except Exception as e:
                    if self.logger:
                        self.logger.log_error(f"Error announcing wellness timer: {e}")
                    else:
                        print(f"Error announcing wellness timer: {e}")
                
                return timer_id
                
            except ValueError as e:
                # Max timers reached or other error
                if self.logger:
                    self.logger.log_error(f"Error creating wellness timer: {e}")
                else:
                    print(f"Error creating wellness timer: {e}")
                return None
    
    def reset_trigger_state(self):
        """
        Reset the wellness timer trigger state.
        
        Call this when starting a new session to reset the trigger tracking.
        This allows the wellness timer to trigger again from the beginning
        of the new session.
        """
        with self._trigger_lock:
            self._last_trigger_session_time = None
        self._pending_wellness_resume_timeout = False
        self._wellness_break_end_time = None
        self._pause_started_at = None
        self._pause_timeout_triggered = False
    
    def get_config(self) -> dict:
        """
        Get current configuration.
        
        Returns:
            dict: Configuration values
        """
        return {
            "sitting_threshold_seconds": self.sitting_threshold_seconds,
            "break_duration_seconds": self.break_duration_seconds,
            "break_timer_name": self.break_timer_name,
            "pause_timeout_seconds": self.pause_timeout_seconds
        }
    
    def start_monitoring(self, session_time_getter: Callable[[], float]):
        """
        Start background monitoring of session time.
        
        Periodically checks session time and triggers wellness timer when threshold
        is reached, independent of sensor data arrival.
        
        Args:
            session_time_getter: Callable that returns current session time in seconds
        """
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            return
        
        self._session_time_getter = session_time_getter
        self._stop_monitoring.clear()
        self._pause_monitoring.clear()  # Start in active (unpaused) state
        
        def monitor_loop():
            """Background thread that periodically checks session time."""
            while not self._stop_monitoring.is_set():
                now = time.time()
                # Check if monitoring is paused (user left area)
                if self._pause_monitoring.is_set():
                    if self._pause_started_at is None:
                        self._pause_started_at = now
                    self._check_pause_timeout(now)
                    # Wait while paused, but check stop event periodically
                    self._stop_monitoring.wait(timeout=1.0)
                    continue
                
                # If a wellness break ended but session never resumed, apply pause timeout
                if self._pending_wellness_resume_timeout:
                    if self._wellness_break_end_time is not None and now >= self._wellness_break_end_time:
                        active_wellness_timers = self.timer_manager.get_active_timers(
                            timer_type=TimerManager.TIMER_TYPE_WELLNESS
                        )
                        if not active_wellness_timers:
                            session_state = self.timer_manager.session_timer.get_state()
                            if session_state == "paused":
                                if self._pause_started_at is None:
                                    self._pause_started_at = now
                                    self._pause_timeout_triggered = False
                                self._check_pause_timeout(now)
                            else:
                                self._pending_wellness_resume_timeout = False
                                self._wellness_break_end_time = None
                
                try:
                    if self._session_time_getter:
                        session_time = self._session_time_getter()
                        # Only check if session is active (time > 0)
                        if session_time > 0:
                            self.check_and_trigger_wellness_timer(session_time)
                except Exception as e:
                    if self.logger:
                        self.logger.log_error(f"Error in wellness timer monitoring: {e}")
                    else:
                        print(f"Error in wellness timer monitoring: {e}")
                
                # Wait for check interval or stop event
                self._stop_monitoring.wait(timeout=self.check_interval_seconds)
        
        self._monitoring_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitoring_thread.start()

    def _check_pause_timeout(self, now: float) -> None:
        if (self.pause_timeout_seconds is None or
            self._pause_started_at is None or
            self._pause_timeout_triggered):
            return
        elapsed = now - self._pause_started_at
        if elapsed >= self.pause_timeout_seconds:
            self._pause_timeout_triggered = True
            if self.logger:
                self.logger.log_wellness(
                    "Wellness pause timeout reached - stopping session",
                    "WARNING",
                    metadata={"timeout_seconds": self.pause_timeout_seconds}
                )
            if self._on_pause_timeout:
                try:
                    self._on_pause_timeout()
                except Exception as e:
                    if self.logger:
                        self.logger.log_error(f"Error in pause timeout callback: {e}")
                    else:
                        print(f"Error in pause timeout callback: {e}")
    
    def pause_monitoring(self):
        """
        Pause wellness timer monitoring (user left area).
        
        Monitoring will stop checking for wellness timer triggers but
        will preserve trigger state. Call resume_monitoring() when user returns.
        """
        self._pause_monitoring.set()
        self._pause_timeout_triggered = False
    
    def resume_monitoring(self):
        """
        Resume wellness timer monitoring (user returned).
        
        Monitoring will continue checking for wellness timer triggers
        based on the preserved trigger state.
        """
        self._pause_monitoring.clear()
        self._pause_started_at = None
        self._pause_timeout_triggered = False
        self._pending_wellness_resume_timeout = False
        self._wellness_break_end_time = None
    
    def is_paused(self) -> bool:
        """
        Check if monitoring is currently paused.
        
        Returns:
            bool: True if monitoring is paused, False otherwise
        """
        return self._pause_monitoring.is_set()
    
    def stop_monitoring(self):
        """Stop background monitoring."""
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            self._stop_monitoring.set()
            self._pause_monitoring.clear()  # Clear pause state on stop
            self._pause_started_at = None
            self._pause_timeout_triggered = False
            self._pending_wellness_resume_timeout = False
            self._wellness_break_end_time = None
            self._monitoring_thread.join(timeout=2.0)
            self._monitoring_thread = None
    
    def is_monitoring(self) -> bool:
        """
        Check if background monitoring is active.
        
        Returns:
            bool: True if monitoring thread is running
        """
        return self._monitoring_thread is not None and self._monitoring_thread.is_alive()
    
    def update_config(self,
                      sitting_threshold_seconds: Optional[int] = None,
                      break_duration_seconds: Optional[int] = None,
                      break_timer_name: Optional[str] = None,
                      pause_timeout_seconds: Optional[int] = None):
        """
        Update configuration values.
        
        Args:
            sitting_threshold_seconds: New threshold (None to keep current)
            break_duration_seconds: New break duration (None to keep current)
            break_timer_name: New timer name (None to keep current)
            pause_timeout_seconds: New pause timeout (None to keep current)
        """
        if sitting_threshold_seconds is not None:
            self.sitting_threshold_seconds = sitting_threshold_seconds
        if break_duration_seconds is not None:
            self.break_duration_seconds = break_duration_seconds
        if break_timer_name is not None:
            self.break_timer_name = break_timer_name
        if pause_timeout_seconds is not None:
            self.pause_timeout_seconds = pause_timeout_seconds
            if self.pause_timeout_seconds <= 0:
                self.pause_timeout_seconds = None
