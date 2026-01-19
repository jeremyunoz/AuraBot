"""
Wellness timer trigger module for AuraBot.
Automatically creates wellness timers based on session time thresholds.
"""

import threading
import time
from typing import Optional, Callable
from timer_manager import TimerManager


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
    
    def __init__(self, 
                 timer_manager: TimerManager,
                 tts_engine=None,
                 sitting_threshold_seconds: Optional[int] = None,
                 break_duration_seconds: Optional[int] = None,
                 break_timer_name: Optional[str] = None,
                 check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS):
        """
        Initialize the WellnessTimerTrigger.
        
        Args:
            timer_manager: TimerManager instance for creating timers
            tts_engine: Optional TTS engine for announcements
            sitting_threshold_seconds: Seconds of sitting before triggering (default: 1 hour)
            break_duration_seconds: Duration of break timer (default: 10 minutes)
            break_timer_name: Name for wellness break timers (default: "Wellness Break")
            check_interval_seconds: How often to check session time in background (default: 10 seconds)
        """
        self.timer_manager = timer_manager
        self.tts_engine = tts_engine
        
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
        
        # Background monitoring
        self._monitoring_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()
        self._session_time_getter: Optional[Callable[[], float]] = None
        
        # Track last wellness timer trigger to prevent continuous creation
        # This tracks the session time at which we last triggered a wellness timer
        self._last_trigger_session_time: Optional[float] = None
        self._trigger_lock = threading.Lock()
    
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
                
                # Record when we triggered this timer (based on session time)
                # This ensures we wait for another full threshold period before next trigger
                self._last_trigger_session_time = session_time_seconds
                
                # Optional: Announce via TTS
                if self.tts_engine:
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
                        self.tts_engine.speak(message)
                    except Exception as e:
                        print(f"Error announcing wellness timer: {e}")
                
                return timer_id
                
            except ValueError as e:
                # Max timers reached or other error
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
    
    def get_config(self) -> dict:
        """
        Get current configuration.
        
        Returns:
            dict: Configuration values
        """
        return {
            "sitting_threshold_seconds": self.sitting_threshold_seconds,
            "break_duration_seconds": self.break_duration_seconds,
            "break_timer_name": self.break_timer_name
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
        
        def monitor_loop():
            """Background thread that periodically checks session time."""
            while not self._stop_monitoring.is_set():
                try:
                    if self._session_time_getter:
                        session_time = self._session_time_getter()
                        # Only check if session is active (time > 0)
                        if session_time > 0:
                            self.check_and_trigger_wellness_timer(session_time)
                except Exception as e:
                    print(f"Error in wellness timer monitoring: {e}")
                
                # Wait for check interval or stop event
                self._stop_monitoring.wait(timeout=self.check_interval_seconds)
        
        self._monitoring_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitoring_thread.start()
    
    def stop_monitoring(self):
        """Stop background monitoring."""
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            self._stop_monitoring.set()
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
                      break_timer_name: Optional[str] = None):
        """
        Update configuration values.
        
        Args:
            sitting_threshold_seconds: New threshold (None to keep current)
            break_duration_seconds: New break duration (None to keep current)
            break_timer_name: New timer name (None to keep current)
        """
        if sitting_threshold_seconds is not None:
            self.sitting_threshold_seconds = sitting_threshold_seconds
        if break_duration_seconds is not None:
            self.break_duration_seconds = break_duration_seconds
        if break_timer_name is not None:
            self.break_timer_name = break_timer_name

