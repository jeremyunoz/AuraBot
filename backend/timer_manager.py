"""
Timer management module for AuraBot.
Handles setting, tracking, and notifying about timers.
"""

import threading
import time
from typing import Dict, List, Optional
from uuid import uuid4
try:
    from .session_timer import SessionTimer
except ImportError:
    from session_timer import SessionTimer


class TimerManager:
    """
    Manages multiple active timers with expiration notifications.
    
    Each timer runs in its own daemon thread and triggers a TTS notification
    when it expires. Supports multiple concurrent timers.
    """
    
    # Configuration
    MAX_CONCURRENT_TIMERS = 10
    DEFAULT_TIMER_NAME = "Timer"
    DEFAULT_NOTIFICATION_MESSAGE = "Your timer is up!"
    
    # Timer types
    TIMER_TYPE_USER = "user"        # Voice-requested timers
    TIMER_TYPE_WELLNESS = "wellness" # Auto-triggered wellness timers
    
    def __init__(self, tts_engine, logger, max_timers: int = None, session_data_file: Optional[str] = None):
        """
        Initialize the TimerManager.
        
        Args:
            tts_engine: TTS engine instance for timer notifications
            logger: ConversationLogger instance for logging timer events
            max_timers: Maximum concurrent timers (default: MAX_CONCURRENT_TIMERS)
            session_data_file: Optional path for session timer data file
        """
        self.tts_engine = tts_engine
        self.logger = logger
        self.max_timers = max_timers or self.MAX_CONCURRENT_TIMERS
        
        # Thread-safe timer storage
        self._timers: Dict[str, Dict] = {}
        self._timers_lock = threading.Lock()
        
        # Initialize session timer for tracking sitting time
        self.session_timer = SessionTimer(session_data_file)
    
    def set_timer(self, duration_seconds: int, name: Optional[str] = None, timer_type: str = TIMER_TYPE_USER) -> str:
        """
        Set a new timer.
        
        Args:
            duration_seconds: Duration in seconds (must be > 0)
            name: Optional name/label for the timer
            timer_type: Type of timer ("user" for voice-requested, "wellness" for auto-triggered)
        
        Returns:
            str: Timer ID
        
        Raises:
            ValueError: If duration is invalid or max timers reached
        """
        if duration_seconds <= 0:
            raise ValueError("Timer duration must be greater than 0")
        
        with self._timers_lock:
            if len(self._timers) >= self.max_timers:
                raise ValueError(f"Maximum of {self.max_timers} concurrent timers reached")
            
            # Generate unique timer ID
            timer_id = f"timer_{uuid4().hex[:8]}"
            current_time = time.time()
            expiration_time = current_time + duration_seconds
            
            # Create timer data structure
            timer_data = {
                "id": timer_id,
                "name": name or self.DEFAULT_TIMER_NAME,
                "duration_seconds": duration_seconds,
                "expiration_time": expiration_time,
                "created_at": current_time,
                "timer_type": timer_type,  # Track timer type
            }
            
            self._timers[timer_id] = timer_data
            
            # Start timer thread (daemon thread, no need to store reference)
            self._start_timer_thread(timer_id, duration_seconds, timer_data["name"])
        
        return timer_id
    
    def cancel_timer(self, timer_id: Optional[str] = None) -> bool:
        """
        Cancel a timer by ID. If no ID provided, cancels the first timer.
        
        Args:
            timer_id: Timer ID to cancel (optional, cancels first if None)
        
        Returns:
            bool: True if timer was found and canceled, False otherwise
        """
        with self._timers_lock:
            if timer_id:
                if timer_id in self._timers:
                    del self._timers[timer_id]
                    return True
                return False
            else:
                # Cancel first timer if no ID specified
                if self._timers:
                    first_timer_id = next(iter(self._timers))
                    del self._timers[first_timer_id]
                    return True
                return False
    
    def cancel_all_timers(self) -> int:
        """
        Cancel all active timers.
        
        Returns:
            int: Number of timers canceled
        """
        with self._timers_lock:
            count = len(self._timers)
            self._timers.clear()
            return count
    
    def get_active_timers(self, timer_type: Optional[str] = None) -> List[Dict]:
        """
        Get list of all active timers with current status.
        
        Args:
            timer_type: Optional filter by timer type ("user" or "wellness")
        
        Returns:
            List[Dict]: List of timer dictionaries with time_remaining added
        """
        with self._timers_lock:
            active_timers = []
            current_time = time.time()
            
            for timer_id, timer_data in self._timers.items():
                # Filter by type if specified
                if timer_type and timer_data.get("timer_type") != timer_type:
                    continue
                
                # Create a copy to avoid modifying the original
                timer_info = timer_data.copy()
                time_remaining = max(0, timer_data["expiration_time"] - current_time)
                timer_info["time_remaining"] = time_remaining
                active_timers.append(timer_info)
            
            return active_timers
    
    def get_time_remaining(self, timer_id: Optional[str] = None) -> Optional[float]:
        """
        Get time remaining for a specific timer or the first timer.
        
        Args:
            timer_id: Timer ID (optional, uses first timer if None)
        
        Returns:
            Optional[float]: Seconds remaining, or None if timer not found
        """
        with self._timers_lock:
            if timer_id:
                if timer_id in self._timers:
                    timer_data = self._timers[timer_id]
                    time_remaining = timer_data["expiration_time"] - time.time()
                    return max(0, time_remaining)
                return None
            else:
                # Get first timer if no ID specified
                if self._timers:
                    first_timer_data = next(iter(self._timers.values()))
                    time_remaining = first_timer_data["expiration_time"] - time.time()
                    return max(0, time_remaining)
                return None
    
    def get_timer_count(self) -> int:
        """
        Get the number of active timers.
        
        Returns:
            int: Number of active timers
        """
        with self._timers_lock:
            return len(self._timers)
    
    def _start_timer_thread(self, timer_id: str, duration: float, name: str) -> threading.Thread:
        """
        Start a background thread that waits for timer expiration.
        
        Args:
            timer_id: Unique timer identifier
            duration: Duration in seconds
            name: Timer name for notification
        
        Returns:
            threading.Thread: The started thread
        """
        def timer_thread_func():
            """Thread function that waits for timer expiration."""
            time.sleep(duration)
            
            # Check if timer still exists (might have been canceled)
            with self._timers_lock:
                if timer_id not in self._timers:
                    return  # Timer was canceled
            
            # Timer expired - trigger notification
            self._timer_expiration_handler(timer_id, name)
        
        thread = threading.Thread(target=timer_thread_func, daemon=True)
        thread.start()
        return thread
    
    def _timer_expiration_handler(self, timer_id: str, name: str):
        """
        Handle timer expiration: notify user and clean up.
        
        Args:
            timer_id: Timer ID that expired
            name: Timer name for notification message
        """
        # Remove timer from active list
        with self._timers_lock:
            if timer_id in self._timers:
                del self._timers[timer_id]
        
        # Create notification message
        if name and name != self.DEFAULT_TIMER_NAME:
            notification = f"{name} is up! {self.DEFAULT_NOTIFICATION_MESSAGE}"
        else:
            notification = self.DEFAULT_NOTIFICATION_MESSAGE
        
        # Speak notification
        try:
            self.tts_engine.speak(notification)
        except Exception as e:
            print(f"Error speaking timer notification: {e}")
        
        # Log timer expiration
        try:
            self.logger.log_event(
                f"Timer expired: {name}",
                notification
            )
        except Exception as e:
            print(f"Error logging timer expiration: {e}")
    
    def format_time_remaining(self, seconds: float) -> str:
        """
        Format time remaining as a human-readable string.
        
        Args:
            seconds: Time in seconds
        
        Returns:
            str: Formatted time string (e.g., "2 minutes and 30 seconds")
        """
        if seconds <= 0:
            return "0 seconds"
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if secs > 0 and hours == 0:  # Only show seconds if less than an hour
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")
        
        if not parts:
            return "less than a second"
        
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        else:
            return f"{', '.join(parts[:-1])}, and {parts[-1]}"

