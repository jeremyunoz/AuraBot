"""
MQTT API module for AuraBot.
Structured API for handling MQTT messages and integrating with AuraBot functionality.
"""

import threading
import time
from typing import Dict, Optional
from backend.timer.wellness_timer_trigger import WellnessTimerTrigger
from backend.timer.timer_manager import TimerManager
from backend.core.logger import AuraBotLogger, LogCategory


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
                 wellness_pause_timeout_seconds: Optional[int] = None,
                 logger: Optional[AuraBotLogger] = None):
        """
        Initialize the MQTT API.
        
        Args:
            aurabot: AuraBot instance to integrate with
            wellness_threshold_seconds: Seconds of sitting before triggering wellness timer
                                       (None uses default from WellnessTimerTrigger)
            wellness_break_duration_seconds: Duration of wellness break timer in seconds
                                           (None uses default from WellnessTimerTrigger)
            wellness_pause_timeout_seconds: Seconds to wait while paused before stopping session
                                            (None uses default from WellnessTimerTrigger)
            logger: Optional AuraBotLogger instance (defaults to aurabot.logger if available)
        """
        self.aurabot = aurabot
        self.timer_manager = aurabot.timer_manager
        self.tts_engine = aurabot.tts_engine
        self.logger = logger or getattr(aurabot, 'logger', None)
        
        # Break compliance configuration and state
        # When enabled, AuraBot tracks whether the user actually leaves the desk
        # after a wellness break is requested and can trigger a physical "hit"
        # via ESP32 movement if the user repeatedly ignores breaks.
        self._break_compliance_enabled: bool = True
        # Number of seconds after a wellness prompt during which the user is
        # expected to leave before a violation is counted.
        self._break_leave_grace_seconds: float = 10.0
        # How many ignored breaks before triggering the physical hit.
        self._max_break_violations_before_hit: int = 3
        # ESP32 movement action to use for the hit.
        self._hit_action: str = "swing"
        # Internal runtime state
        self._break_violation_count: int = 0
        self._pending_break: bool = False
        self._pending_break_prompt_time: Optional[float] = None
        self._last_presence_state: Optional[bool] = None
        self._last_hit_time: Optional[float] = None
        # Remaining wellness break time when user returns early (paused break).
        self._paused_wellness_remaining: Optional[float] = None
        # Cooldown between hits to avoid spamming movement.
        self._hit_cooldown_seconds: float = 60.0
        
        # Initialize wellness timer trigger with optional configuration
        # Pass callback to clear debounce counters when wellness timer is created
        self.wellness_trigger = WellnessTimerTrigger(
            timer_manager=self.timer_manager,
            tts_engine=self.tts_engine,
            sitting_threshold_seconds=wellness_threshold_seconds,
            break_duration_seconds=wellness_break_duration_seconds,
            pause_timeout_seconds=wellness_pause_timeout_seconds,
            on_wellness_timer_created=self._on_wellness_timer_created,
            on_pause_timeout=self._handle_wellness_pause_timeout,
            logger=self.logger,
            # Defer creation of the actual wellness timer until presence logic
            # confirms the user has left the desk.
            create_timer_immediately=False,
        )
        
        # Start background monitoring of session time
        # This ensures wellness timer triggers even without continuous sensor data
        self.wellness_trigger.start_monitoring(
            session_time_getter=lambda: self.aurabot.get_current_sitting_time()
        )
        
        # Sensor state tracking
        self._last_motion = None
        self._last_camera_confirmed = None
        self._motion_threshold = 1.0  # Minimum motion value to consider active
        # When True (vision enabled), presence requires BOTH camera AND PIR motion
        self._require_pir_with_camera = False
        # Camera-dominant mode: camera drives presence when valid; PIR only when camera stale
        self._camera_dominant_presence = False
        # Periodic PIR complement: run sanity check every N seconds
        self._pir_complement_interval_seconds = 30.0
        self._pir_absence_threshold_seconds = 60.0
        self._last_pir_complement_check_time: Optional[float] = None
        self._last_pir_motion_time: Optional[float] = None  # When PIR last saw motion (for sanity check)
        # Last PIR/ESP32 state (so vision-only updates don't overwrite it)
        self._last_esp32_motion: Optional[int] = None
        self._last_esp32_time: Optional[float] = None
        self._esp32_state_ttl_seconds = 30.0  # Use last PIR state for this long when fusing
        # Last camera state (so PIR-only updates don't overwrite it)
        self._last_camera_time: Optional[float] = None
        self._camera_state_ttl_seconds = 30.0  # Use last camera state for this long when fusing
        
        # Debounce configuration: require N consecutive readings before state change
        self._presence_stable_count = 2  # Need 2 consecutive "present" readings to start session
        self._absence_stable_count = 2  # Need 2 consecutive "absent" readings to pause session
        self._consecutive_present_count = 0  # Track consecutive present readings
        self._consecutive_absent_count = 0  # Track consecutive absent readings
        
        # Auto-pause timeout: if no sensor data for this duration, auto-pause session
        # This prevents time accumulation if sensor data stops when user leaves
        self._sensor_timeout_seconds = 30.0  # Auto-pause after 30 seconds of no sensor data
        self._last_sensor_time: Optional[float] = None
        # Last time we received aurabot/status with esp32 in topic or payload (dashboard ESP32 status only)
        self._last_esp32_message_time: Optional[float] = None
        # Last reported ESP32 state from aurabot/status payload (e.g. ACTIVE/IDLE)
        self._last_esp32_state: Optional[str] = None
        self._last_esp32_state_time: Optional[float] = None
        # Last TTS ack from ESP32 (aurabot/tts/ack)
        self._last_tts_ack: Optional[Dict] = None
        self._last_tts_ack_time: Optional[float] = None
        self._timeout_check_thread: Optional[threading.Thread] = None
        self._stop_timeout_check = threading.Event()
        # Suppress repeated "session held" logs during wellness breaks
        self._wellness_hold_logged = False
        
        # Start timeout monitoring
        self._start_timeout_monitoring()
    
    def _on_wellness_timer_created(self):
        """
        Callback invoked when a wellness timer is created.
        
        Extends the basic debounce reset with break-compliance tracking so we
        can observe whether the user actually leaves the desk after being
        prompted for a wellness break.
        """
        # Preserve existing behavior: clear presence debounce counters.
        self._clear_debounce_counters()
        
        if not self._break_compliance_enabled:
            return
        
        now = time.time()
        self._pending_break = True
        self._pending_break_prompt_time = now
        # When a new wellness break is requested, we start watching presence;
        # do not immediately clear violation count so repeated failures across
        # a session are still tracked.
        if self.logger:
            self.logger.log_wellness(
                "Wellness break requested; awaiting user to leave desk",
                "INFO",
                metadata={
                    "break_leave_grace_seconds": self._break_leave_grace_seconds,
                    "current_violation_count": self._break_violation_count,
                },
            )
    
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

    def _handle_wellness_pause_timeout(self) -> None:
        """
        Stop session when wellness pause timeout expires and request sleep mode.
        """
        session_state = self.aurabot.get_sitting_timer_state()
        if session_state == "idle":
            return
        session_data = self._handle_stop_session()
        if self.logger:
            self.logger.log_wellness(
                "Session stopped due to wellness pause timeout; entering sleep mode",
                "WARNING",
                metadata={
                    "session_state_before": session_state,
                    "session_seconds": session_data.get("total_seconds") if session_data else None
                }
            )
        if hasattr(self.aurabot, "enter_sleep_mode"):
            try:
                self.aurabot.enter_sleep_mode()
            except Exception as e:
                if self.logger:
                    self.logger.log_error(f"Error entering sleep mode: {e}")
    
    def handle_sensor_data(self, data: dict) -> dict:
        """
        Process sensor data from ESP32.
        
        Expected format:
        {
            "motion": 0 | 1,
            "camera_confirmed": 0 | 1,  # optional; or "camera": 0 | 1
            "ts_us": int (optional),
            "count": int (optional)
        }
        
        Flow:
        1. Detect user presence: when presence_fusion is on (vision enabled), require BOTH
           camera_confirmed AND PIR motion; otherwise camera OR PIR motion. Vision-only
           payloads (motion=0) use last ESP32 PIR state within TTL so camera and PIR are fused.
        2. Start/pause session timer accordingly
        3. Check session time and auto-create wellness timer if needed
        
        Returns:
            dict: Response with status and actions taken
        """
        # Validate data
        if not self._validate_sensor_data(data):
            return {"status": "error", "error": "Invalid sensor data"}
        
        # Extract values
        motion = self._coerce_binary(data.get("motion"))
        # camera_confirmed is optional (PIR-only payloads from ESP32 won't include it)
        camera_raw = data.get("camera_confirmed", data.get("camera"))
        camera_in_payload = camera_raw is not None
        camera_confirmed = self._coerce_binary(camera_raw) if camera_in_payload else None
        
        # Update last sensor time for timeout monitoring
        self._last_sensor_time = time.time()
        
        # Detect vision-only payload (camera sends motion=0); ESP32 sends ts_us/count or motion=1
        from_esp32 = (
            data.get("ts_us") is not None or data.get("count") is not None or motion == 1
        )
        from_camera = camera_in_payload and not from_esp32
        if from_esp32:
            self._last_esp32_motion = motion
            self._last_esp32_time = time.time()
        if from_camera:
            # camera_confirmed is guaranteed non-None when camera_in_payload is True
            self._last_camera_confirmed = camera_confirmed  # type: ignore[assignment]
            self._last_camera_time = time.time()
        
        # Effective PIR motion for presence: use this payload if from ESP32, else last ESP32 state within TTL
        now = time.time()
        esp32_valid = (
            self._last_esp32_time is not None and
            (now - self._last_esp32_time) <= self._esp32_state_ttl_seconds
        )
        if from_esp32:
            effective_motion = motion
        elif esp32_valid:
            effective_motion = self._last_esp32_motion if self._last_esp32_motion is not None else 0
        else:
            effective_motion = 0

        # Effective camera_confirmed for presence: use this payload if from camera, else last camera state within TTL
        cam_valid = (
            self._last_camera_time is not None and
            (now - self._last_camera_time) <= self._camera_state_ttl_seconds
        )
        if from_camera and camera_confirmed is not None:
            effective_camera_confirmed = camera_confirmed
        elif cam_valid:
            effective_camera_confirmed = self._last_camera_confirmed if self._last_camera_confirmed is not None else 0
        else:
            effective_camera_confirmed = 0
        
        # 1. Calculate presence
        pir_present = effective_motion >= self._motion_threshold
        cam_present = effective_camera_confirmed == 1
        # Track last PIR motion time for periodic sanity check (camera-dominant mode)
        if pir_present:
            self._last_pir_motion_time = now

        presence_driven_by: Optional[str] = None
        if self._camera_dominant_presence:
            if cam_valid:
                is_present = cam_present
                presence_driven_by = "camera"
            else:
                is_present = pir_present
                presence_driven_by = "pir"
            # Periodic PIR sanity check: if camera says present but PIR has had no motion for M seconds, override to absent
            if self._camera_dominant_presence and cam_valid and cam_present:
                if self._last_pir_complement_check_time is None or (now - self._last_pir_complement_check_time) >= self._pir_complement_interval_seconds:
                    self._last_pir_complement_check_time = now
                    pir_motion_seconds_ago = (now - self._last_pir_motion_time) if self._last_pir_motion_time is not None else float("inf")
                    if pir_motion_seconds_ago > self._pir_absence_threshold_seconds:
                        is_present = False
        elif self._require_pir_with_camera:
            # Fusion mode: require BOTH camera AND PIR motion.
            is_present = cam_present and pir_present
        else:
            # Non-fusion mode: either source can indicate presence.
            is_present = cam_present or pir_present

        pir_confirms = bool(cam_present and pir_present)

        # Check if wellness timer is active - if so, don't resume session timer
        # User should complete the break before session resumes
        active_wellness_timers = self.timer_manager.get_active_timers(
            timer_type=TimerManager.TIMER_TYPE_WELLNESS
        )
        wellness_timer_active = len(active_wellness_timers) > 0
        # Treat a paused wellness break (user returned early) as still active
        # for the purpose of blocking session restarts until the full break
        # duration has been taken.
        effective_wellness_active = wellness_timer_active or (
            self._paused_wellness_remaining is not None
        )
        if not effective_wellness_active:
            # Reset log suppression after wellness break ends (including when
            # a paused break is fully completed).
            self._wellness_hold_logged = False
        
        # Build response with current state
        response = {
            "status": "processed",
            "motion": motion,
            # Raw camera field for debugging; may be None on PIR-only payloads.
            "camera_confirmed": camera_confirmed if camera_confirmed is not None else 0,
            "effective_camera_confirmed": effective_camera_confirmed,
            "presence": is_present,
            "presence_fusion": self._require_pir_with_camera,
            "camera_dominant_presence": self._camera_dominant_presence,
            "effective_motion": effective_motion,
            "pir_present": pir_present,
            "camera_present": cam_present,
            "pir_confirms": pir_confirms,
            "sources": {
                "from_esp32": from_esp32,
                "from_camera": from_camera,
                "camera_in_payload": camera_in_payload,
                "esp32_state_valid": esp32_valid,
                "camera_state_valid": cam_valid,
            },
            "presence_driven_by": presence_driven_by,
            "wellness_timer_active": wellness_timer_active,
            "debounce": {
                "consecutive_present": self._consecutive_present_count,
                "consecutive_absent": self._consecutive_absent_count,
                "presence_stable_count": self._presence_stable_count,
                "absence_stable_count": self._absence_stable_count
            },
            "actions": []
        }
        
        # Track presence transitions for break-compliance decisions
        previous_presence = self._last_presence_state
        self._last_presence_state = is_present
        
        # Break compliance and break countdown: monitor what happens after a
        # wellness break is requested. The actual wellness timer (countdown)
        # is only created once presence confirms the user has left the desk,
        # and it is paused if the user returns early.
        if self._break_compliance_enabled:
            # Case 1: new wellness break requested, waiting for first absence.
            if self._pending_break:
                if previous_presence is not False and not is_present:
                    # User finally left the desk after a wellness prompt.
                    # Create the wellness timer countdown now and pause the session.
                    duration = self.wellness_trigger.break_duration_seconds
                    try:
                        timer_id = self.timer_manager.set_timer(
                            duration_seconds=duration,
                            name=self.wellness_trigger.break_timer_name,
                            timer_type=TimerManager.TIMER_TYPE_WELLNESS,
                        )
                        if self.aurabot.get_sitting_timer_state() == "active":
                            was_active = self.aurabot.pause_sitting_timer()
                            if was_active and self.logger:
                                self.logger.log_wellness(
                                    "Session timer paused for wellness break (user left desk)",
                                    "INFO",
                                    metadata={"timer_id": timer_id},
                                )
                        if self.logger:
                            self.logger.log_wellness(
                                "Wellness timer started after confirmed absence",
                                "INFO",
                                metadata={"timer_id": timer_id, "duration_seconds": duration},
                            )
                    except Exception as e:
                        if self.logger:
                            self.logger.log_error(f"Error creating deferred wellness timer: {e}")

                    # Clear pending break and reset violations to reward compliance.
                    self._pending_break = False
                    self._pending_break_prompt_time = None
                    if self._break_violation_count > 0 and self.logger:
                        self.logger.log_wellness(
                            "User left desk after wellness prompt; resetting violation counter",
                            "INFO",
                            metadata={"previous_violation_count": self._break_violation_count},
                        )
                    self._break_violation_count = 0
                else:
                    # User is still present; if they stay too long after the prompt,
                    # record a violation and potentially trigger the hit action.
                    if (
                        self._pending_break_prompt_time is not None
                        and now - self._pending_break_prompt_time >= self._break_leave_grace_seconds
                        and is_present
                    ):
                        self._record_break_violation()
                        # This pending break has been fully handled.
                        self._pending_break = False
                        self._pending_break_prompt_time = None
            # Case 2: user previously left and a wellness break started, but they
            # returned early and we paused the break. When they leave again,
            # resume the remaining break time.
            elif (
                self._paused_wellness_remaining is not None
                and previous_presence is not False
                and not is_present
            ):
                remaining = max(0.0, self._paused_wellness_remaining)
                if remaining > 0:
                    try:
                        timer_id = self.timer_manager.set_timer(
                            duration_seconds=int(remaining),
                            name=self.wellness_trigger.break_timer_name,
                            timer_type=TimerManager.TIMER_TYPE_WELLNESS,
                        )
                        if self.aurabot.get_sitting_timer_state() == "active":
                            was_active = self.aurabot.pause_sitting_timer()
                            if was_active and self.logger:
                                self.logger.log_wellness(
                                    "Session timer paused to resume wellness break (user left again)",
                                    "INFO",
                                    metadata={"timer_id": timer_id, "remaining_seconds": remaining},
                                )
                        if self.logger:
                            self.logger.log_wellness(
                                "Wellness timer resumed after user left again",
                                "INFO",
                                metadata={"timer_id": timer_id, "remaining_seconds": remaining},
                            )
                    except Exception as e:
                        if self.logger:
                            self.logger.log_error(f"Error resuming wellness timer: {e}")
                self._paused_wellness_remaining = None
        
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
                not effective_wellness_active):
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
            elif effective_wellness_active:
                # User is present while a wellness break is active or paused:
                # either hold or pause the break so they still owe the full
                # break duration before a new sitting session can begin.
                if wellness_timer_active:
                    # Pause the active wellness timer and remember remaining time.
                    first = active_wellness_timers[0]
                    remaining = float(first.get("time_remaining", 0.0))
                    self._paused_wellness_remaining = max(0.0, remaining)
                    timer_id = first.get("id")
                    if timer_id:
                        self.timer_manager.cancel_timer(timer_id=timer_id)
                    if self.logger:
                        self.logger.log_wellness(
                            "Wellness break paused because user returned early",
                            "INFO",
                            metadata={"remaining_seconds": self._paused_wellness_remaining},
                        )
                # Counter is not incremented toward session start while the
                # user still owes a wellness break.
                response["actions"].append("session_held_for_wellness_break")
                if self.logger and not self._wellness_hold_logged:
                    self.logger.log_wellness(
                        "Session resume blocked: wellness break active or paused",
                        "INFO",
                        metadata={"presence_stable_count": self._presence_stable_count}
                    )
                    self._wellness_hold_logged = True
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
        self._last_motion = motion
        # Keep the last camera_confirmed we actually observed in a camera payload
        if camera_confirmed is not None:
            self._last_camera_confirmed = camera_confirmed
        
        return response
    
    def record_esp32_message_received(self, state: Optional[str] = None) -> None:
        """
        Record that we received an ESP32 status message (aurabot/status with esp32).

        Args:
            state: Optional ESP32 state from payload (e.g. ACTIVE/IDLE/WAKING/SLEEPING).
        """
        self._last_esp32_message_time = time.time()
        if state:
            normalized = str(state).strip().upper()
            if normalized:
                self._last_esp32_state = normalized
                self._last_esp32_state_time = self._last_esp32_message_time
                if self.logger:
                    self.logger.log_mqtt(
                        f"Recorded ESP32 state: {normalized}",
                        "INFO",
                    )

    def get_esp32_last_seen(self) -> Optional[float]:
        """Return Unix timestamp of last ESP32 status (aurabot/status with esp32), or None if never received."""
        return self._last_esp32_message_time

    def is_esp32_online(self, within_seconds: float = 60.0) -> bool:
        """Return True if we received aurabot/status with esp32 within the given seconds."""
        t = self.get_esp32_last_seen()
        if t is None:
            return False
        return (time.time() - t) <= within_seconds

    def get_esp32_state(self, within_seconds: Optional[float] = None) -> Optional[str]:
        """
        Return last known ESP32 state.

        Args:
            within_seconds: Optional freshness window. When None, return the most
            recent known state regardless of age.
        """
        if self._last_esp32_state_time is None or self._last_esp32_state is None:
            return None
        if within_seconds is not None and (time.time() - self._last_esp32_state_time) > within_seconds:
            return None
        return self._last_esp32_state

    def is_esp32_user_control_enabled(self) -> bool:
        """
        User movement commands are enabled on ESP32 only while the voice session is live.
        """
        return self.is_esp32_online() and self.get_esp32_state() in {"LISTENING", "SPEAKING"}
    
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

    def handle_tts_ack(self, data: dict) -> dict:
        """
        Process TTS acknowledgements from ESP32.

        Expected format:
        {
            "device": "esp32",
            "type": "tts",
            "status": "queued" | "error",
            "len": int
        }
        """
        if not isinstance(data, dict):
            return {"status": "error", "error": "Invalid ack payload"}

        device = data.get("device")
        ack_type = data.get("type")
        status = data.get("status")
        length = data.get("len")

        if device != "esp32" or ack_type != "tts":
            return {"status": "error", "error": "Unsupported ack payload"}

        if status not in ("queued", "error"):
            return {"status": "error", "error": f"Unknown TTS ack status: {status}"}

        if not isinstance(length, int) or length <= 0:
            return {"status": "error", "error": "Invalid TTS ack length"}

        self._last_tts_ack = {
            "device": device,
            "type": ack_type,
            "status": status,
            "len": length
        }
        self._last_tts_ack_time = time.time()

        return {
            "status": "success",
            "ack": self._last_tts_ack
        }
    
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
        tts_ack_age_seconds = None
        if self._last_tts_ack_time is not None:
            tts_ack_age_seconds = max(0.0, time.time() - self._last_tts_ack_time)
        
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
            "wellness_config": self.wellness_trigger.get_config(),
            "tts": {
                "last_ack": self._last_tts_ack,
                "last_ack_age_seconds": tts_ack_age_seconds
            },
            "break_compliance": self.get_break_compliance_status(),
        }
    
    def get_break_compliance_status(self) -> dict:
        """
        Return current break-compliance configuration and runtime state.
        """
        return {
            "enabled": self._break_compliance_enabled,
            "break_leave_grace_seconds": self._break_leave_grace_seconds,
            "max_break_violations_before_hit": self._max_break_violations_before_hit,
            "hit_action": self._hit_action,
            "hit_cooldown_seconds": self._hit_cooldown_seconds,
            "violation_count": self._break_violation_count,
            "pending_break": self._pending_break,
            "pending_break_prompt_time": self._pending_break_prompt_time,
            "last_hit_time": self._last_hit_time,
            "paused_wellness_remaining_seconds": self._paused_wellness_remaining,
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
        if "motion" not in data:
            return False
        
        # Validate types
        motion = self._coerce_binary(data.get("motion"))
        if motion is None:
            return False

        # camera_confirmed is optional; if present it must be a 0/1-like value
        if "camera_confirmed" in data or "camera" in data:
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
    
    def set_presence_fusion(self, require_pir_with_camera: bool) -> None:
        """
        Set whether presence requires BOTH camera AND PIR motion (fusion mode).
        When True (e.g. vision enabled), user is present only if camera sees a person
        AND PIR has seen motion within the ESP32 state TTL.
        """
        self._require_pir_with_camera = require_pir_with_camera
    
    def get_presence_fusion(self) -> bool:
        """Return True if presence fusion (camera + PIR) is enabled."""
        return self._require_pir_with_camera

    def set_camera_dominant_presence(self, enabled: bool) -> None:
        """
        Set camera-dominant presence mode.
        When True, camera drives presence when valid; PIR is fallback when camera stale,
        plus periodic PIR sanity check can override to absent.
        """
        self._camera_dominant_presence = enabled

    def get_camera_dominant_presence(self) -> bool:
        """Return True if camera-dominant presence mode is enabled."""
        return self._camera_dominant_presence

    def get_pir_complement_config(self) -> dict:
        """Return PIR complement (sanity check) configuration."""
        return {
            "pir_complement_interval_seconds": self._pir_complement_interval_seconds,
            "pir_absence_threshold_seconds": self._pir_absence_threshold_seconds,
        }

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

    def _record_break_violation(self) -> None:
        """
        Increment break violation count and, when threshold is reached, trigger
        the configured physical hit action via ESP32 movement.
        """
        self._break_violation_count += 1
        if self.logger:
            self.logger.log_wellness(
                "User remained at desk after wellness prompt; recording break violation",
                "WARNING",
                metadata={
                    "violation_count": self._break_violation_count,
                    "max_before_hit": self._max_break_violations_before_hit,
                },
            )
        if self._break_violation_count >= self._max_break_violations_before_hit:
            self._trigger_break_hit()

    def _trigger_break_hit(self) -> None:
        """
        Trigger the configured physical hit action via ESP32, with safety
        checks and cooldown to avoid spamming movement.
        """
        now = time.time()
        # Cooldown: avoid repeated hits in a short period
        if (
            self._last_hit_time is not None
            and now - self._last_hit_time < self._hit_cooldown_seconds
        ):
            return

        mqtt = getattr(self.aurabot, "mqtt_integration", None)
        if not mqtt or not mqtt.is_connected():
            if self.logger:
                self.logger.log_wellness(
                    "Break hit skipped: MQTT not connected",
                    "INFO",
                )
            return

        if not self.is_esp32_user_control_enabled():
            if self.logger:
                self.logger.log_wellness(
                    "Break hit skipped: ESP32 user control not enabled",
                    "INFO",
                )
            return

        action = (self._hit_action or "swing").strip().lower()
        payload = {"cmd": "move", "action": action}
        published = mqtt.publish(
            "aurabot/control",
            payload,
            qos=1,
            retain=False,
        )
        if published:
            self._last_hit_time = now
            # Reset violation count after a successful hit so the user gets
            # another N chances before the next hit.
            previous_count = self._break_violation_count
            self._break_violation_count = 0
            if self.logger:
                self.logger.log_wellness(
                    "Break hit triggered via ESP32 movement",
                    "WARNING",
                    metadata={
                        "action": action,
                        "previous_violation_count": previous_count,
                    },
                )
        else:
            if self.logger:
                self.logger.log_wellness(
                    "Break hit failed: MQTT publish error",
                    "ERROR",
                    metadata={"action": action},
                )
