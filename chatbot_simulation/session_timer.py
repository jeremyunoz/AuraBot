"""
Session timer module for AuraBot.
Tracks user sitting time sessions with pause/resume capabilities.
"""

import threading
import time
import json
import os
from typing import Dict, List, Optional
from uuid import uuid4
from datetime import datetime


class SessionTimer:
    """
    Tracks user sitting time sessions.
    
    Monitors when user is present (sitting) and accumulates time.
    Pauses when user leaves and resumes when they return.
    Saves session data for later reference.
    """
    
    # Session states
    STATE_IDLE = "idle"      # Not started
    STATE_ACTIVE = "active"  # Currently tracking time
    STATE_PAUSED = "paused"  # User left, timer paused
    
    def __init__(self, session_data_file: Optional[str] = None):
        """
        Initialize the SessionTimer.
        
        Args:
            session_data_file: Path to JSON file for saving session history
                              If None, uses default location in logs directory
        """
        # Determine session data file path
        if session_data_file:
            self.session_data_file = session_data_file
        else:
            # Default to logs directory
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            logs_dir = os.path.join(project_root, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            self.session_data_file = os.path.join(logs_dir, "sitting_sessions.json")
        
        # Thread-safe state management
        self._lock = threading.Lock()
        
        # Current session state
        self._state = self.STATE_IDLE
        self._session_start_time: Optional[float] = None
        self._last_resume_time: Optional[float] = None
        self._accumulated_time = 0.0  # Total seconds accumulated in current session
        
        # Session history
        self._sessions: List[Dict] = []
        self._load_session_history()
    
    def start(self) -> bool:
        """
        Start tracking sitting time (user detected in area).
        Can be called when transitioning from idle or paused.
        
        Returns:
            bool: True if started successfully, False if already active
        """
        with self._lock:
            if self._state == self.STATE_ACTIVE:
                return False  # Already active
            
            current_time = time.time()
            
            if self._state == self.STATE_IDLE:
                # Starting a new session
                self._session_start_time = current_time
                self._accumulated_time = 0.0
            elif self._state == self.STATE_PAUSED:
                # Resuming from pause - don't reset accumulated time
                pass
            
            self._last_resume_time = current_time
            self._state = self.STATE_ACTIVE
            return True
    
    def pause(self) -> bool:
        """
        Pause tracking (user left the area).
        Accumulates time since last resume.
        
        Returns:
            bool: True if paused successfully, False if not active
        """
        with self._lock:
            if self._state != self.STATE_ACTIVE:
                return False  # Not active, can't pause
            
            # Accumulate time since last resume
            if self._last_resume_time:
                current_time = time.time()
                elapsed = current_time - self._last_resume_time
                self._accumulated_time += elapsed
                self._last_resume_time = None
            
            self._state = self.STATE_PAUSED
            return True
    
    def stop(self) -> Optional[Dict]:
        """
        Stop the current session and save it.
        Returns session data for the completed session.
        
        Returns:
            Optional[Dict]: Session data if session was active, None otherwise
        """
        with self._lock:
            if self._state == self.STATE_IDLE:
                return None  # No active session
            
            # Finalize accumulated time
            if self._state == self.STATE_ACTIVE and self._last_resume_time:
                current_time = time.time()
                elapsed = current_time - self._last_resume_time
                self._accumulated_time += elapsed
            
            # Create session record
            session_data = {
                "session_id": f"session_{uuid4().hex[:8]}",
                "start_time": self._session_start_time,
                "end_time": time.time(),
                "total_seconds": self._accumulated_time,
                "start_datetime": datetime.fromtimestamp(self._session_start_time).isoformat() if self._session_start_time else None,
                "end_datetime": datetime.now().isoformat(),
                "formatted_duration": self._format_duration(self._accumulated_time)
            }
            
            # Save to history
            self._sessions.append(session_data)
            self._save_session_history()
            
            # Reset state
            self._state = self.STATE_IDLE
            self._session_start_time = None
            self._last_resume_time = None
            self._accumulated_time = 0.0
            
            return session_data
    
    def get_current_session_time(self) -> float:
        """
        Get the total accumulated time for the current session.
        Includes time from all active periods.
        
        Returns:
            float: Total seconds accumulated in current session
        """
        with self._lock:
            if self._state == self.STATE_IDLE:
                return 0.0
            
            current_accumulated = self._accumulated_time
            
            # If currently active, add time since last resume
            if self._state == self.STATE_ACTIVE and self._last_resume_time:
                current_time = time.time()
                current_accumulated += (current_time - self._last_resume_time)
            
            return current_accumulated
    
    def get_state(self) -> str:
        """
        Get the current state of the session timer.
        
        Returns:
            str: Current state (idle, active, or paused)
        """
        with self._lock:
            return self._state
    
    def is_active(self) -> bool:
        """
        Check if timer is currently tracking time.
        
        Returns:
            bool: True if active, False otherwise
        """
        with self._lock:
            return self._state == self.STATE_ACTIVE
    
    def get_session_history(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Get saved session history.
        
        Args:
            limit: Optional limit on number of sessions to return (most recent first)
        
        Returns:
            List[Dict]: List of session records
        """
        with self._lock:
            sessions = self._sessions.copy()
            if limit:
                return sessions[-limit:] if limit > 0 else []
            return sessions
    
    def get_total_sitting_time(self) -> float:
        """
        Get total sitting time across all saved sessions.
        
        Returns:
            float: Total seconds across all sessions
        """
        with self._lock:
            return sum(session.get("total_seconds", 0) for session in self._sessions)
    
    def _format_duration(self, seconds: float) -> str:
        """
        Format duration as human-readable string.
        
        Args:
            seconds: Duration in seconds
        
        Returns:
            str: Formatted duration string
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
        if secs > 0 and hours == 0:
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")
        
        if not parts:
            return "less than a second"
        
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        else:
            return f"{', '.join(parts[:-1])}, and {parts[-1]}"
    
    def _load_session_history(self):
        """Load session history from file."""
        if not os.path.exists(self.session_data_file):
            self._sessions = []
            return
        
        try:
            with open(self.session_data_file, 'r') as f:
                self._sessions = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading session history: {e}")
            self._sessions = []
    
    def _save_session_history(self):
        """Save session history to file."""
        try:
            with open(self.session_data_file, 'w') as f:
                json.dump(self._sessions, f, indent=2)
        except IOError as e:
            print(f"Error saving session history: {e}")

