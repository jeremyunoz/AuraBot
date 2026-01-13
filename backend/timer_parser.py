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
        - "X timer" (where X is a short, meaningful name like "coffee", "break")
        
        Args:
            text: Input text containing potential timer name
        
        Returns:
            Optional[str]: Extracted timer name, or None if not found
        """
        text_lower = text.lower().strip()
        
        # Common conversational words to filter out (based on actual user patterns from logs)
        conversational_words = {
            'can', 'you', 'send', 'me', 'the', 'set', 'a', 'an', 'my', 'please',
            'could', 'would', 'will', 'help', 'create', 'make', 'give', 'cancel',
            'stop', 'delete', 'remove', 'that', 'this', 'another', 'let', 'lets',
            'best', 'way', 'for', 'to', 'do', 'want', 'need', 'get'
        }
        
        # Pattern: "called <name>" or "named <name>" - most reliable
        called_pattern = r'(?:called|named)\s+([a-zA-Z0-9\s]+?)(?:\s+for\s+\d|$)'
        match = re.search(called_pattern, text_lower)
        if match:
            name = match.group(1).strip()
            # Remove trailing "timer" if present
            name = re.sub(r'\s+timer\s*$', '', name)
            if name and len(name) > 0:
                # Limit to reasonable length (1-3 words typically)
                words = name.split()
                if len(words) <= 3:
                    return name.title()
        
        # Pattern: "<name> timer" - but be very conservative
        # Only match short, meaningful names (1-2 words) that aren't conversational phrases
        # Look for patterns like "coffee timer", "break timer", "work timer" etc.
        # Try to match word(s) immediately before "timer"
        name_timer_pattern = r'(?:^|\s)([a-zA-Z]+(?:\s+[a-zA-Z]+)?)\s+timer\b'
        matches = re.finditer(name_timer_pattern, text_lower)
        for match in matches:
            name = match.group(1).strip()
            
            # Remove leading articles (a, an, the)
            name = re.sub(r'^(a|an|the)\s+', '', name, flags=re.IGNORECASE)
            name = name.strip()
            
            if not name:
                continue
                
            words = name.split()
            
            # Only accept 1-2 word names (after removing articles)
            if len(words) > 2:
                continue
            
            # Filter out conversational phrases - check if ALL words are conversational
            if all(word in conversational_words for word in words):
                continue
            
            # Filter out single-word conversational phrases
            if len(words) == 1 and name.lower() in conversational_words:
                continue
            
            # Filter out common command words
            if name.lower() in ['set', 'a', 'an', 'the', 'my', 'timer', 'cancel', 'stop']:
                continue
            
            # Don't extract if it starts with a number
            if re.match(r'^\d', name):
                continue
            
            # Only extract if it's a meaningful name (not too long, not conversational)
            if name and 1 <= len(words) <= 2:
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
            
            # Only try to extract timer name if there's a clear name pattern
            # Don't extract names from "cancel the timer" or "cancel timer" (generic cancel)
            # Only extract from patterns like "cancel the coffee timer" or "cancel coffee timer"
            # Pattern: "cancel (the) <name> timer" - only extract if name exists before "timer"
            cancel_name_pattern = r'(?:cancel|stop|delete|remove)\s+(?:the\s+)?([a-zA-Z]+(?:\s+[a-zA-Z]+)?)\s+timer\b'
            match = re.search(cancel_name_pattern, text_lower)
            if match:
                name = match.group(1).strip()
                # Filter out conversational words and command words
                conversational_words = {'can', 'you', 'send', 'me', 'the', 'set', 'a', 'an', 'my', 'please',
                                      'could', 'would', 'will', 'help', 'create', 'make', 'give', 'cancel',
                                      'stop', 'delete', 'remove', 'that', 'this', 'another', 'let', 'lets',
                                      'best', 'way', 'for', 'to', 'do', 'want', 'need', 'get'}
                words = name.split()
                # Only extract if it's a meaningful name (1-2 words, not all conversational)
                if name and 1 <= len(words) <= 2 and not all(word in conversational_words for word in words):
                    result["name"] = name.title()
        
        # Query patterns - more flexible matching
        query_patterns = [
            r'\b(?:how\s+much\s+time|time\s+left|timer\s+status|timer\s+remaining)',
            r'\bwhat\s+is\s+(?:the\s+)?(?:current\s+)?timer',
            r'\b(?:what\'?s?\s+)(?:the\s+)?(?:current\s+)?timer\s+(?:status|remaining|left)?',
            r'\bhow\s+long\s+(?:is\s+)?(?:my\s+)?(?:the\s+)?timer',
            r'\b(?:what\'?s?\s+|what\s+is\s+)(?:the\s+)?timer\s+(?:status|remaining|left)'
        ]
        # Make sure "what time is it" doesn't match (time query, not timer query)
        if any(re.search(pattern, text_lower) for pattern in query_patterns) and 'what time is it' not in text_lower:
            result["action"] = "query"
            result["success"] = True
        
        # List patterns - matches "list timer", "list the timer", "list my timer", etc.
        list_patterns = [
            r'\blist\s+(?:the\s+)?(?:my\s+)?(?:active\s+)?timer(?:s)?\b',  # list timer, list the timer, list my timer, list active timer, list the active timer
            r'\b(?:what|show)\s+timers?\s+(?:are\s+)?(?:running|active)?',
            r'\b(?:active\s+)?timers?\s+(?:are\s+)?(?:running|active)',
            r'\bshow\s+(?:me\s+)?(?:the\s+)?(?:active\s+)?timers?',
            r'\bwhat\s+timers?\b(?!\s+is\s+it)',  # "what timers" but not "what time is it"
            r'\bactive\s+timers?\b'  # standalone "active timers"
        ]
        if any(re.search(pattern, text_lower) for pattern in list_patterns):
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

