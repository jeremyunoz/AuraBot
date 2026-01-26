"""
MQTT API module for AuraBot.
Structured API for handling MQTT messages and integrating with AuraBot functionality.
"""

import threading
import time
from typing import Dict, Optional
from wellness_timer_trigger import WellnessTimerTrigger
from timer_manager import TimerManager
from logger import AuraBotLogger, LogCategory


class MQTTAPI:
    """
    Structured API for handling MQTT messages and integrating with AuraBot.
    
    Provides clean separation between MQTT message handling and AuraBot logic,
    making it easier to test and maintain.
    """
    
    def __init__(self, 
                 aurabot,
                 wellness_threshold_seconds: Optional[int] = None,
                 wellness_break_duration_seconds: Optional[int] = None,
                 logger: Optional[AuraBotLogger] = None):
        """
        Initialize the MQTT API.
        
        Args:
            aurabot: AuraBot instance to integrate with
            wellness_threshold_seconds: Seconds of sitting before triggering wellness timer
                                       (None uses default from WellnessTimerTrigger)
            wellness_break_duration_seconds: Duration of wellness break timer in seconds
                                           (None uses default from WellnessTimerTrigger)
            logger: Optional AuraBotLogger instance (defaults to aurabot.logger if available)
        """
        self.aurabot = aurabot
        self.timer_manager = aurabot.timer_manager
        self.tts_engine = aurabot.tts_engine
        self.logger = logger or getattr(aurabot, 'logger', None)
        
        # Initialize wellness timer trigger with optional configuration
        # Pass callback to clear debounce counters when wellness timer is created
        self.wellness_trigger = WellnessTimerTrigger(
            timer_manager=self.timer_manager,
            tts_engine=self.tts_engine,
            sitting_threshold_seconds=wellness_threshold_seconds,
            break_duration_seconds=wellness_break_duration_seconds,
            on_wellness_timer_created=self._clear_debounce_counters,
            logger=self.logger
        )
        
        # Start background monitoring of session time
        # This ensures wellness timer triggers even without continuous sensor data
        self.wellness_trigger.start_monitoring(
            session_time_getter=lambda: self.aurabot.get_current_sitting_time()
        )
        
        # Sensor state tracking
        self._last_distance = None
        self._last_motion = None
        self._last_camera_confirmed = None
        self._presence_threshold_cm = 50.0  # User considered present if distance < 50cm
        self._motion_threshold = 1.0  # Minimum motion value to consider active
        
        # Debounce configuration: require N consecutive readings before state change
        self._presence_stable_count = 2  # Need 2 consecutive "present" readings to start session
        self._absence_stable_count = 2  # Need 2 consecutive "absent" readings to pause session
        self._consecutive_present_count = 0  # Track consecutive present readings
        self._consecutive_absent_count = 0  # Track consecutive absent readings
        
        # Auto-pause timeout: if no sensor data for this duration, auto-pause session
        # This prevents time accumulation if sensor data stops when user leaves
        self._sensor_timeout_seconds = 30.0  # Auto-pause after 30 seconds of no sensor data
        self._last_sensor_time: Optional[float] = None
        self._timeout_check_thread: Optional[threading.Thread] = None
        self._stop_timeout_check = threading.Event()
        
        # Start timeout monitoring
        self._start_timeout_monitoring()
    
    def _clear_debounce_counters(self):
        """
        Clear debounce counters when wellness break starts.
        
        This ensures that after the break ends, we require fresh
        consecutive sensor readings before resuming the session.
        """
        self._consecutive_present_count = 0
        self._consecutive_absent_count = 0
        if self.logger:
            self.logger.log_wellness("Debounce counters cleared for wellness break", "INFO")
    
    def _handle_stop_session(self) -> Optional[Dict]:
        """
        Handle session stop command and reset wellness timer trigger state.
        
        Returns:
            Optional[Dict]: Session data if session existed, None otherwise
        """
        result = self.aurabot.stop_sitting_timer()
        # Reset wellness timer trigger state when session is stopped
        # This ensures new sessions start fresh
        if result is not None:  # Session was actually stopped (not already idle)
            self.wellness_trigger.reset_trigger_state()
            # Reset debounce counters for clean state
            self._consecutive_present_count = 0
            self._consecutive_absent_count = 0
        return result
    
    def handle_sensor_data(self, data: dict) -> dict:
        """
        Process sensor data from ESP32.
        
        Expected format:
        {
            "motion": 0 | 1,
            "camera_confirmed": 0 | 1,  # or "camera": 0 | 1
            "distance_cm": float,
            "ts_us": int (optional),
            "count": int (optional)
        }
        
        Flow:
        1. Detect user presence based on camera/motion/distance
        2. Start/pause session timer accordingly
        3. Check session time and auto-create wellness timer if needed
        
        Returns:
            dict: Response with status and actions taken
        """
        # Validate data
        if not self._validate_sensor_data(data):
            return {"status": "error", "error": "Invalid sensor data"}
        
        # Extract values
        distance = float(data.get("distance_cm", 0))
        motion = self._coerce_binary(data.get("motion"))
        camera_confirmed = self._coerce_binary(
            data.get("camera_confirmed", data.get("camera"))
        )
        
        # Update last sensor time for timeout monitoring
        self._last_sensor_time = time.time()
        
        # 1. Calculate presence based on camera/motion/distance
        is_present = (
            camera_confirmed == 1 or
            (distance < self._presence_threshold_cm and motion >= self._motion_threshold)
        )
        
        # Check if wellness timer is active - if so, don't resume session timer
        # User should complete the break before session resumes
        active_wellness_timers = self.timer_manager.get_active_timers(
            timer_type=TimerManager.TIMER_TYPE_WELLNESS
        )
        wellness_timer_active = len(active_wellness_timers) > 0
        
        # Build response with current state
        response = {
            "status": "processed",
            "distance_cm": distance,
            "motion": motion,
            "camera_confirmed": camera_confirmed,
            "presence": is_present,
            "wellness_timer_active": wellness_timer_active,
            "debounce": {
                "consecutive_present": self._consecutive_present_count,
                "consecutive_absent": self._consecutive_absent_count,
                "presence_stable_count": self._presence_stable_count,
                "absence_stable_count": self._absence_stable_count
            },
            "actions": []
        }
        
        # Debounce logic: track consecutive readings before changing state
        if is_present:
            # Only increment counter if wellness timer is NOT active
            # During wellness break, we don't count readings - they'll be reset when break starts
            # and we'll require fresh consecutive readings after break ends
            if not wellness_timer_active:
                # Increment present counter, reset absent counter
                self._consecutive_present_count += 1
                self._consecutive_absent_count = 0
            # Update response with current counter values
            response["debounce"]["consecutive_present"] = self._consecutive_present_count
            response["debounce"]["consecutive_absent"] = self._consecutive_absent_count
            
            # Only trigger state change if we have enough consecutive present readings
            # AND no wellness timer is active (user must complete break first)
            if (self._consecutive_present_count >= self._presence_stable_count and 
                not wellness_timer_active):
                # Check if starting a new session (from IDLE state)
                session_state_before = self.aurabot.get_sitting_timer_state()
                was_idle = self.aurabot.start_sitting_timer()
                if was_idle:
                    response["actions"].append("session_started")
                    if self.logger:
                        self.logger.log_session(
                            f"Session started: user detected (stable for {self._consecutive_present_count} readings)",
                            "INFO",
                            metadata={
                                "distance_cm": distance,
                                "motion": motion,
                                "camera_confirmed": camera_confirmed,
                                "consecutive_present": self._consecutive_present_count
                            }
                        )
                    
                    # Reset wellness timer trigger state for new session
                    if session_state_before == "idle":
                        self.wellness_trigger.reset_trigger_state()
                        # Ensure monitoring is active for new session
                        self.wellness_trigger.resume_monitoring()
                
                # Resume wellness timer monitoring if user returned (from paused state)
                if session_state_before == "paused":
                    self.wellness_trigger.resume_monitoring()
            elif wellness_timer_active:
                # User is present but wellness timer is active - don't resume session
                # Counter is not incremented during break, so we'll need fresh readings after break ends
                response["actions"].append("session_held_for_wellness_break")
                if self.logger:
                    self.logger.log_wellness(
                        "Session resume blocked: wellness break active",
                        "INFO",
                        metadata={"presence_stable_count": self._presence_stable_count}
                    )
        else:
            # Increment absent counter, reset present counter
            self._consecutive_absent_count += 1
            self._consecutive_present_count = 0
            # Update response with current counter values
            response["debounce"]["consecutive_present"] = self._consecutive_present_count
            response["debounce"]["consecutive_absent"] = self._consecutive_absent_count
            
            # Only trigger state change if we have enough consecutive absent readings
            if self._consecutive_absent_count >= self._absence_stable_count:
                # User absent - pause session
                was_active = self.aurabot.pause_sitting_timer()
                if was_active:
                    response["actions"].append("session_paused")
                    if self.logger:
                        self.logger.log_session(
                            f"Session paused: user left (stable for {self._consecutive_absent_count} readings)",
                            "INFO",
                            metadata={
                                "distance_cm": distance,
                                "motion": motion,
                                "camera_confirmed": camera_confirmed,
                                "consecutive_absent": self._consecutive_absent_count
                            }
                        )
                    
                    # Pause wellness timer monitoring when user leaves
                    self.wellness_trigger.pause_monitoring()
        
        # 2. Update response with current session time
        # Note: Wellness timer creation is handled by background monitoring thread
        # to avoid duplicate checks from both sensor data and background monitoring
        current_session_time = self.aurabot.get_current_sitting_time()
        response["session_time_seconds"] = current_session_time
        
        # Update last sensor readings
        self._last_distance = distance
        self._last_motion = motion
        self._last_camera_confirmed = camera_confirmed
        
        return response
    
    def _start_timeout_monitoring(self):
        """Start background thread to monitor sensor data timeout and auto-pause."""
        def timeout_check_loop():
            """Background thread that checks for sensor data timeout."""
            while not self._stop_timeout_check.is_set():
                try:
                    current_time = time.time()
                    
                    # Check if sensor data timeout occurred
                    if (self._last_sensor_time is not None and 
                        current_time - self._last_sensor_time > self._sensor_timeout_seconds):
                        
                        # Check if session is currently active
                        session_state = self.aurabot.get_sitting_timer_state()
                        if session_state == "active":
                            # Auto-pause session due to sensor timeout
                            was_active = self.aurabot.pause_sitting_timer()
                            if was_active:
                                if self.logger:
                                    self.logger.log_sensor(
                                        f"Session auto-paused: no sensor data for {self._sensor_timeout_seconds:.0f} seconds",
                                        "WARNING",
                                        metadata={"timeout_seconds": self._sensor_timeout_seconds}
                                    )
                                self.wellness_trigger.pause_monitoring()
                                # Reset last sensor time to prevent repeated auto-pause
                                self._last_sensor_time = None
                
                except Exception as e:
                    if self.logger:
                        self.logger.log_error(f"Error in sensor timeout monitoring: {e}")
                
                # Check every 5 seconds
                self._stop_timeout_check.wait(timeout=5.0)
        
        self._stop_timeout_check.clear()
        self._timeout_check_thread = threading.Thread(target=timeout_check_loop, daemon=True)
        self._timeout_check_thread.start()
    
    def _stop_timeout_monitoring(self):
        """Stop sensor timeout monitoring."""
        if self._timeout_check_thread and self._timeout_check_thread.is_alive():
            self._stop_timeout_check.set()
            self._timeout_check_thread.join(timeout=2.0)
            self._timeout_check_thread = None
    
    def handle_control_command(self, data: dict) -> dict:
        """
        Process control commands (from MQTT publisher or ESP32).
        
        Expected format:
        {
            "cmd": "start_session" | "pause_session" | "resume_session" | "stop_session",
            "params": {...}  # optional
        }
        
        Returns:
            dict: Response with status and result
        """
        cmd = data.get("cmd")
        
        if not cmd:
            return {"status": "error", "error": "Missing 'cmd' field"}
        
        # Check if wellness timer is active before allowing session start/resume
        active_wellness_timers = self.timer_manager.get_active_timers(
            timer_type=TimerManager.TIMER_TYPE_WELLNESS
        )
        wellness_timer_active = len(active_wellness_timers) > 0
        
        # Define handlers with wellness timer check
        def start_session_handler():
            if wellness_timer_active:
                return {"error": "Cannot start session during wellness break"}
            return self.aurabot.start_sitting_timer()
        
        def resume_session_handler():
            if wellness_timer_active:
                return {"error": "Cannot resume session during wellness break"}
            return self.aurabot.start_sitting_timer()
        
        handlers = {
            "start_session": start_session_handler,
            "pause_session": lambda: self.aurabot.pause_sitting_timer(),
            "resume_session": resume_session_handler,
            "stop_session": lambda: self._handle_stop_session(),
        }
        
        handler = handlers.get(cmd)
        if not handler:
            return {"status": "error", "error": f"Unknown command: {cmd}"}
        
        try:
            result = handler()
            # Check if handler returned an error dict
            if isinstance(result, dict) and "error" in result:
                return {
                    "status": "error",
                    "command": cmd,
                    "error": result["error"]
                }
            return {
                "status": "success",
                "command": cmd,
                "result": result
            }
        except Exception as e:
            return {
                "status": "error",
                "command": cmd,
                "error": str(e)
            }
    
    def handle_timer_command(self, data: dict) -> dict:
        """
        Process timer creation commands via MQTT.
        
        Expected format:
        {
            "cmd": "set_timer",
            "duration_seconds": int,
            "name": str (optional),
            "timer_type": "user" | "wellness" (optional, default: "user")
        }
        
        Returns:
            dict: Response with timer ID and status
        """
        cmd = data.get("cmd")
        
        if cmd != "set_timer":
            return {"status": "error", "error": f"Invalid timer command: {cmd}"}
        
        duration = data.get("duration_seconds")
        if not duration or duration <= 0:
            return {"status": "error", "error": "Invalid duration_seconds"}
        
        name = data.get("name")
        timer_type = data.get("timer_type", TimerManager.TIMER_TYPE_USER)
        
        try:
            timer_id = self.timer_manager.set_timer(
                duration_seconds=duration,
                name=name,
                timer_type=timer_type
            )
            return {
                "status": "success",
                "timer_id": timer_id,
                "duration_seconds": duration,
                "name": name,
                "timer_type": timer_type
            }
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": f"Unexpected error: {str(e)}"}
    
    def get_status(self) -> dict:
        """
        Get current AuraBot status.
        
        Returns:
            dict: Status information including timers and session
        """
        active_timers = self.timer_manager.get_active_timers()
        user_timers = self.timer_manager.get_active_timers(
            timer_type=TimerManager.TIMER_TYPE_USER
        )
        wellness_timers = self.timer_manager.get_active_timers(
            timer_type=TimerManager.TIMER_TYPE_WELLNESS
        )
        
        return {
            "status": "ok",
            "session": {
                "state": self.aurabot.get_sitting_timer_state(),
                "current_time_seconds": self.aurabot.get_current_sitting_time(),
            },
            "timers": {
                "total": len(active_timers),
                "user": len(user_timers),
                "wellness": len(wellness_timers),
                "active_timers": [
                    {
                        "id": t["id"],
                        "name": t["name"],
                        "type": t.get("timer_type", "user"),
                        "time_remaining": t["time_remaining"]
                    }
                    for t in active_timers
                ]
            },
            "wellness_config": self.wellness_trigger.get_config()
        }
    
    def _validate_sensor_data(self, data: dict) -> bool:
        """
        Validate sensor data structure.
        
        Args:
            data: Sensor data dictionary
        
        Returns:
            bool: True if valid, False otherwise
        """
        if not isinstance(data, dict):
            return False
        
        # Check for required fields
        if "distance_cm" not in data or "motion" not in data:
            return False
        
        if "camera_confirmed" not in data and "camera" not in data:
            return False
        
        # Validate types
        try:
            distance = float(data["distance_cm"])
            if distance < 0:
                return False
        except (ValueError, TypeError):
            return False
        
        motion = self._coerce_binary(data.get("motion"))
        if motion is None:
            return False
        
        camera_confirmed = self._coerce_binary(
            data.get("camera_confirmed", data.get("camera"))
        )
        if camera_confirmed is None:
            return False
        
        return True
    
    def _coerce_binary(self, value) -> Optional[int]:
        """
        Normalize a 0/1-like value to int 0/1.
        
        Returns None for invalid values.
        """
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value if value in (0, 1) else None
        if isinstance(value, float):
            return int(value) if value in (0.0, 1.0) else None
        if isinstance(value, str):
            value = value.strip()
            if value in ("0", "1"):
                return int(value)
        return None
    
    def update_presence_threshold(self, threshold_cm: float):
        """
        Update the distance threshold for presence detection.
        
        Args:
            threshold_cm: New threshold in centimeters
        """
        if threshold_cm > 0:
            self._presence_threshold_cm = threshold_cm
    
    def get_presence_threshold(self) -> float:
        """
        Get current presence detection threshold.
        
        Returns:
            float: Threshold in centimeters
        """
        return self._presence_threshold_cm
    
    def update_debounce_config(self, 
                                presence_stable_count: Optional[int] = None,
                                absence_stable_count: Optional[int] = None):
        """
        Update debounce configuration for presence detection.
        
        Args:
            presence_stable_count: Number of consecutive "present" readings required to start session
                                 (None to keep current value, must be >= 1)
            absence_stable_count: Number of consecutive "absent" readings required to pause session
                                 (None to keep current value, must be >= 1)
        """
        if presence_stable_count is not None and presence_stable_count >= 1:
            self._presence_stable_count = presence_stable_count
            # Reset counter when config changes
            self._consecutive_present_count = 0
        
        if absence_stable_count is not None and absence_stable_count >= 1:
            self._absence_stable_count = absence_stable_count
            # Reset counter when config changes
            self._consecutive_absent_count = 0
    
    def get_debounce_config(self) -> dict:
        """
        Get current debounce configuration.
        
        Returns:
            dict: Configuration with presence_stable_count and absence_stable_count
        """
        return {
            "presence_stable_count": self._presence_stable_count,
            "absence_stable_count": self._absence_stable_count
        }

