"""
Timer parser module for AuraBot.
Parses natural language timer commands and extracts duration and names.
"""

import re
from typing import Optional, Dict, Any


class TimerParser:
    """
    Parses natural language timer commands to extract duration and timer names.
    
    Supports various time formats and natural language patterns for setting timers.
    """
    
    # Time unit patterns (in seconds)
    TIME_UNITS = {
        'second': 1,
        'seconds': 1,
        'sec': 1,
        'secs': 1,
        's': 1,
        'minute': 60,
        'minutes': 60,
        'min': 60,
        'mins': 60,
        'm': 60,
        'hour': 3600,
        'hours': 3600,
        'hr': 3600,
        'hrs': 3600,
        'h': 3600,
    }
    
    # Special time expressions
    SPECIAL_TIMES = {
        'half an hour': 30 * 60,
        'half hour': 30 * 60,
        'half a hour': 30 * 60,
        'quarter hour': 15 * 60,
        'quarter of an hour': 15 * 60,
        'quarter of a hour': 15 * 60,
    }
    
    def parse_duration(self, text: str) -> Optional[int]:
        """
        Parse duration from natural language text.
        
        Supports formats like:
        - "5 minutes" / "5 min" / "5m"
        - "2 hours" / "2 hrs" / "2h"
        - "30 seconds" / "30 sec" / "30s"
        - "half an hour" → 30 minutes
        - "quarter hour" → 15 minutes
        
        Args:
            text: Input text containing duration information
        
        Returns:
            Optional[int]: Duration in seconds, or None if parsing fails
        """
        text_lower = text.lower().strip()
        
        # Check for special time expressions first
        for expression, seconds in self.SPECIAL_TIMES.items():
            if expression in text_lower:
                return seconds
        
        # Pattern to match numbers with time units
        # Matches: "5 minutes", "2.5 hours", "30 sec", "10m", etc.
        pattern = r'(\d+(?:\.\d+)?)\s*([a-zA-Z]+)'
        matches = re.findall(pattern, text_lower)
        
        if not matches:
            # Try to match just numbers (assume minutes if no unit)
            number_pattern = r'\b(\d+(?:\.\d+)?)\b'
            number_match = re.search(number_pattern, text_lower)
            if number_match:
                # Default to minutes if no unit specified
                minutes = float(number_match.group(1))
                return int(minutes * 60)
            return None
        
        total_seconds = 0
        
        for match in matches:
            try:
                value = float(match[0])
                unit = match[1].lower()
                
                # Find matching time unit
                unit_seconds = self.TIME_UNITS.get(unit)
                if unit_seconds:
                    total_seconds += int(value * unit_seconds)
                else:
                    # Try partial match for units like "minute" when we have "min"
                    for unit_key, unit_value in self.TIME_UNITS.items():
                        if unit_key.startswith(unit) or unit.startswith(unit_key):
                            total_seconds += int(value * unit_value)
                            break
            except (ValueError, TypeError):
                continue
        
        return total_seconds if total_seconds > 0 else None
    
    def extract_timer_name(self, text: str) -> Optional[str]:
        """
        Extract timer name/label from user input.
        
        Looks for patterns like:
        - "called X" / "named X"
        - "X timer" (where X is the name)
        - "timer for X" / "timer X"
        
        Args:
            text: Input text containing potential timer name
        
        Returns:
            Optional[str]: Extracted timer name, or None if not found
        """
        text_lower = text.lower().strip()
        
        # Pattern: "called <name>" or "named <name>"
        # Capture everything after "called" or "named" up to end or before another command word
        called_pattern = r'(?:called|named)\s+([a-zA-Z0-9\s]+?)(?:\s+for\s+\d|$)'
        match = re.search(called_pattern, text_lower)
        if match:
            name = match.group(1).strip()
            # Remove trailing "timer" if the name ends with it (e.g., "break timer" becomes "break timer", not "break")
            # But keep it if it's part of the name
            # Actually, if user says "named break timer", we want "Break Timer"
            if name and len(name) > 0:
                return name.title()
        
        # Pattern: "<name> timer" (name before "timer") - but not command words
        # Only match if there's a meaningful name before "timer"
        name_timer_pattern = r'((?:^|\s)(?!set|a|an|the|my|timer|cancel|stop|delete|remove)[a-zA-Z0-9]+(?:\s+[a-zA-Z0-9]+)*?)\s+timer'
        match = re.search(name_timer_pattern, text_lower)
        if match:
            name = match.group(1).strip()
            # Remove common prefixes
            name = re.sub(r'^(set|a|an|the|my|cancel|stop|delete|remove)\s+', '', name, flags=re.IGNORECASE)
            if name and len(name) > 0 and not re.match(r'^\d', name):
                # Make sure we didn't just get a command word
                if name.lower() not in ['set', 'a', 'an', 'the', 'my', 'timer', 'cancel', 'stop']:
                    return name.title()
        
        # Pattern: "timer for <name>" - only if name is not a number/duration and not in cancel context
        if 'cancel' not in text_lower and 'stop' not in text_lower:
            timer_for_pattern = r'timer\s+for\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)*?)(?:\s+for\s+\d|\s*$)'
            match = re.search(timer_for_pattern, text_lower)
            if match:
                name = match.group(1).strip()
                # Don't extract if it looks like a duration or command word
                if name and len(name) > 0 and not re.match(r'^\d', name) and name not in ['set', 'a', 'an', 'the']:
                    return name.title()
        
        return None
    
    def parse_timer_command(self, text: str) -> Dict[str, Any]:
        """
        Parse a complete timer command from user input.
        
        Detects action type (set, cancel, query, list) and extracts relevant information.
        
        Args:
            text: User input text
        
        Returns:
            Dict with keys:
                - action: "set", "cancel", "query", "list", or None
                - duration_seconds: Optional[int] - Parsed duration for "set" action
                - timer_id: Optional[str] - Timer ID for cancel/query (not implemented in Phase 2)
                - name: Optional[str] - Timer name/label
                - success: bool - Whether parsing was successful
        """
        text_lower = text.lower().strip()
        result = {
            "action": None,
            "duration_seconds": None,
            "timer_id": None,
            "name": None,
            "success": False
        }
        
        # Detect action type
        if any(keyword in text_lower for keyword in ["set timer", "set a timer", "timer for", "countdown", "remind me"]):
            result["action"] = "set"
            
            # Parse duration
            duration = self.parse_duration(text_lower)
            if duration:
                result["duration_seconds"] = duration
                result["success"] = True
            
            # Extract timer name
            name = self.extract_timer_name(text_lower)
            if name:
                result["name"] = name
        
        elif any(keyword in text_lower for keyword in ["cancel timer", "stop timer", "delete timer", "remove timer", "cancel the", "stop the"]):
            result["action"] = "cancel"
            result["success"] = True
            
            # Try to extract timer name for cancellation
            name = self.extract_timer_name(text_lower)
            if name:
                result["name"] = name
        
        elif any(keyword in text_lower for keyword in ["time left", "how much time", "timer status", "timer remaining"]):
            result["action"] = "query"
            result["success"] = True
        
        elif any(keyword in text_lower for keyword in ["what timers", "list timers", "show timers", "active timers"]):
            result["action"] = "list"
            result["success"] = True
        
        return result
    
    def format_duration(self, seconds: int) -> str:
        """
        Format duration in seconds to human-readable string.
        Helper method for consistency with TimerManager formatting.
        
        Args:
            seconds: Duration in seconds
        
        Returns:
            str: Formatted duration string (e.g., "5 minutes and 30 seconds")
        """
        if seconds <= 0:
            return "0 seconds"
        
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        parts = []
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if secs > 0:
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")
        
        if not parts:
            return "less than a second"
        
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        else:
            return f"{', '.join(parts[:-1])} and {parts[-1]}"

