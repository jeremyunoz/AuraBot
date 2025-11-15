"""
Conversation logging module.
Handles logging of conversation events to files.
"""

from datetime import datetime
import os
from typing import Optional


class ConversationLogger:
    """Handles logging of conversation events."""
    
    def __init__(self, log_file: str):
        """
        Initialize the conversation logger.
        
        Args:
            log_file: Path to the log file
        """
        self.log_file = log_file
        self._ensure_log_directory()
    
    def _ensure_log_directory(self):
        """Ensure the log directory exists."""
        log_dir = os.path.dirname(self.log_file)
        if log_dir:  # Only create if there's a directory path
            os.makedirs(log_dir, exist_ok=True)
    
    def log_event(self, user_text: str, bot_text: str) -> bool:
        """
        Log a conversation event.
        
        Args:
            user_text: User's input text
            bot_text: Bot's response text
        
        Returns:
            bool: True if logging succeeded, False otherwise
        """
        try:
            with open(self.log_file, "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] USER: {user_text}\n")
                f.write(f"[{timestamp}] BOT:  {bot_text}\n\n")
            return True
        except Exception as e:
            print(f"Error logging event: {e}")
            return False

