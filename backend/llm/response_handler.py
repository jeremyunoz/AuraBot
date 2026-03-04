"""
Response handler module.
Handles bot response generation based on user input.

Priority chain:
  1. Exit commands  (deterministic, instant)
  2. Timer commands (deterministic, parsed by TimerParser)
  3. LLM conversation (Gemini-powered natural language, if available)
  4. Keyword fallback (legacy static responses, used when LLM is unavailable)
"""

from typing import Optional, Dict, Tuple

from backend.timer.timer_parser import TimerParser


class ResponseHandler:
    """Handles bot response generation based on user input."""
    
    def __init__(self, responses: Optional[Dict[str, str]] = None,
                 timer_manager=None, llm_client=None):
        """
        Initialize the response handler.
        
        Args:
            responses: Optional custom response dictionary
            timer_manager: Optional TimerManager instance for timer commands
            llm_client: Optional LLMClient instance for AI-powered responses.
                        When provided, general conversation is handled by the LLM
                        instead of the static keyword map.
        """
        self.responses = responses or self._default_responses()
        self.timer_manager = timer_manager
        self.timer_parser = TimerParser() if timer_manager else None
        self.llm_client = llm_client
    
    def _default_responses(self) -> Dict[str, str]:
        """Get default response mappings."""
        return {
            "tired": "Let's take a two-minute break to relax your body.",
            "hello": "Hi there! How are you feeling today?",
            "hi": "Hi there! How are you feeling today?",
            "reminder": "You've been sitting for a while. Time to move a bit!",
            "exit": "Goodbye! Remember to stretch often.",
            "quit": "Goodbye! Remember to stretch often.",
        }
    
    def get_response(self, user_text: str) -> Tuple[str, bool]:
        """
        Get bot response for user input.

        Priority:
          1. Exit commands  — deterministic, immediate
          2. Timer commands — deterministic, parsed by TimerParser
          3. LLM response   — natural conversation via Gemini (if available)
          4. Keyword fallback — legacy static map (when LLM is unavailable)
        
        Args:
            user_text: User's input text (lowercase)
        
        Returns:
            tuple: (response_text, should_exit)
        """
        user_text_lower = user_text.lower()
        
        # 1. Exit commands (deterministic, fast)
        if "exit" in user_text_lower or "quit" in user_text_lower:
            return self.responses.get("exit", "Goodbye! Remember to stretch often."), True
        
        # 2. Timer commands (deterministic, fast)
        if self.timer_manager and self.timer_parser:
            timer_response = self._handle_timer_command(user_text)
            if timer_response:
                return timer_response, False
        
        # 3. LLM-powered conversation
        if self.llm_client is not None:
            try:
                llm_response = self.llm_client.generate_response(user_text)
                return llm_response, False
            except Exception as e:
                # If the LLM call fails, fall through to keyword fallback
                print(f"LLM call failed, falling back to keywords: {e}")
        
        # 4. Keyword fallback (used when LLM is unavailable or errored)
        for keyword, response in self.responses.items():
            if keyword in user_text_lower and keyword not in ["exit", "quit"]:
                return response, False
        
        # Default response
        return f"You said {user_text}. I'm here to keep you active!", False
    
    def _handle_timer_command(self, user_text: str) -> Optional[str]:
        """
        Handle timer-related commands using TimerParser and TimerManager.
        
        Args:
            user_text: User's input text
        
        Returns:
            Optional[str]: Response message if timer command was processed, None otherwise
        """
        if not self.timer_manager or not self.timer_parser:
            return None
        
        # Parse the timer command
        parsed = self.timer_parser.parse_timer_command(user_text)
        
        action = parsed.get("action")
        
        # If no action detected, this is not a timer command
        if not action:
            return None
        
        # If action is "set" but parsing failed (no duration), return error message
        if action == "set" and not parsed.get("success"):
            return "I couldn't understand that duration. Please specify a time like '5 minutes'."
        
        # If action detected but success is False (for non-set actions), return None
        if not parsed.get("success"):
            return None
        
        try:
            if action == "set":
                duration = parsed.get("duration_seconds")
                if not duration:
                    return "I didn't catch the duration. Could you try something like '5 minutes' or '30 seconds'?"
                
                name = parsed.get("name")
                timer_id = self.timer_manager.set_timer(duration, name)
                
                # Format response
                duration_str = self.timer_parser.format_duration(duration)
                if name:
                    return f"Got it! I've set a {name.lower()} timer for {duration_str}. I'll let you know when it's up."
                else:
                    return f"Sure! Timer set for {duration_str}. I'll remind you when it's done."
            
            elif action == "cancel":
                name = parsed.get("name")
                active_timers = self.timer_manager.get_active_timers()
                
                if not active_timers:
                    return "You don't have any active timers right now."
                
                # If name specified, try to find and cancel that timer
                if name:
                    canceled = False
                    for timer in active_timers:
                        if timer["name"].lower() == name.lower():
                            if self.timer_manager.cancel_timer(timer["id"]):
                                canceled = True
                                return f"Okay, I've canceled your {timer['name'].lower()} timer."
                    
                    if not canceled:
                        return f"Sorry, I couldn't find a timer named '{name}'."
                else:
                    # Cancel first timer
                    if self.timer_manager.cancel_timer():
                        timer_name = active_timers[0]["name"]
                        return f"Okay, I've canceled your {timer_name.lower()} timer."
                    else:
                        return "Sorry, I couldn't cancel that timer."
            
            elif action == "query":
                active_timers = self.timer_manager.get_active_timers()
                
                if not active_timers:
                    return "You don't have any active timers right now."
                
                if len(active_timers) == 1:
                    timer = active_timers[0]
                    time_str = self.timer_manager.format_time_remaining(timer["time_remaining"])
                    timer_name = timer['name'].lower() if timer['name'] != 'Timer' else 'timer'
                    return f"You have {time_str} left on your {timer_name}."
                else:
                    parts = []
                    for timer in active_timers:
                        time_str = self.timer_manager.format_time_remaining(timer["time_remaining"])
                        timer_name = timer['name'].lower() if timer['name'] != 'Timer' else 'timer'
                        parts.append(f"{timer_name} has {time_str} left")
                    
                    return f"You have {len(active_timers)} active timers: {', '.join(parts)}."
            
            elif action == "list":
                active_timers = self.timer_manager.get_active_timers()
                
                if not active_timers:
                    return "You don't have any active timers right now."
                
                if len(active_timers) == 1:
                    timer = active_timers[0]
                    time_str = self.timer_manager.format_time_remaining(timer["time_remaining"])
                    timer_name = timer['name'].lower() if timer['name'] != 'Timer' else 'timer'
                    return f"You have one timer running: your {timer_name} has {time_str} remaining."
                else:
                    parts = []
                    for timer in active_timers:
                        time_str = self.timer_manager.format_time_remaining(timer["time_remaining"])
                        timer_name = timer['name'].lower() if timer['name'] != 'Timer' else 'timer'
                        parts.append(f"{timer_name} with {time_str}")
                    
                    return f"You have {len(active_timers)} timers running: {', '.join(parts)}."
        
        except ValueError as e:
            # Handle maximum timer limit error
            if "maximum" in str(e).lower() or "max" in str(e).lower():
                return "You have too many active timers. Please cancel one first, then try again."
            return str(e)
        except Exception as e:
            return f"Sorry, something went wrong. Please try again."
        
        return None
    
    def add_response(self, keyword: str, response: str):
        """
        Add or update a response for a keyword.
        
        Args:
            keyword: Keyword to match in user input
            response: Response text for that keyword
        """
        self.responses[keyword.lower()] = response
    
    def remove_response(self, keyword: str) -> bool:
        """
        Remove a response for a keyword.
        
        Args:
            keyword: Keyword to remove
        
        Returns:
            bool: True if keyword was found and removed, False otherwise
        """
        keyword_lower = keyword.lower()
        if keyword_lower in self.responses:
            del self.responses[keyword_lower]
            return True
        return False
