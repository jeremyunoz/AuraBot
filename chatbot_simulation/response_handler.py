"""
Response handler module.
Handles bot response generation based on user input.
"""

from typing import Optional, Dict, Tuple


class ResponseHandler:
    """Handles bot response generation based on user input."""
    
    def __init__(self, responses: Optional[Dict[str, str]] = None):
        """
        Initialize the response handler.
        
        Args:
            responses: Optional custom response dictionary
        """
        self.responses = responses or self._default_responses()
    
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
        
        Args:
            user_text: User's input text (lowercase)
        
        Returns:
            tuple: (response_text, should_exit)
        """
        user_text_lower = user_text.lower()
        
        # Check for exit commands
        if "exit" in user_text_lower or "quit" in user_text_lower:
            return self.responses.get("exit", "Goodbye! Remember to stretch often."), True
        
        # Check for keyword matches
        for keyword, response in self.responses.items():
            if keyword in user_text_lower and keyword not in ["exit", "quit"]:
                return response, False
        
        # Default response
        return f"You said {user_text}. I'm here to keep you active!", False
    
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

