"""
Comprehensive logging module for AuraBot.
Handles logging of different message types with routing to appropriate destinations.
"""

from datetime import datetime
import os
from typing import Optional, Dict, List, Set
from enum import Enum


class LogCategory(Enum):
    """Categories for different types of log messages."""
    CONVERSATION = "conversation"  # User-bot conversations
    MQTT = "mqtt"  # MQTT communication
    TIMER = "timer"  # Timer events
    WELLNESS = "wellness"  # Wellness timer events
    SESSION = "session"  # Session timer events
    SENSOR = "sensor"  # Sensor data processing
    GENERAL = "general"  # General system messages
    ERROR = "error"  # Error messages


class MessageRouter:
    """
    Routes log messages to appropriate destinations based on category.
    
    Supports routing to:
    - Console (stdout)
    - Category-specific log files
    - General log file
    """
    
    def __init__(self, 
                 log_dir: str = "logs",
                 enable_console: bool = True,
                 category_routing: Optional[Dict[LogCategory, str]] = None):
        """
        Initialize the message router.
        
        Args:
            log_dir: Directory for log files
            enable_console: Whether to print messages to console
            category_routing: Optional dict mapping categories to specific log file paths
                            (relative to log_dir or absolute paths)
        """
        self.log_dir = log_dir
        self.enable_console = enable_console
        self.category_routing = category_routing or {}
        self._ensure_log_directory()
    
    def _ensure_log_directory(self):
        """Ensure the log directory exists."""
        if self.log_dir:
            os.makedirs(self.log_dir, exist_ok=True)
    
    def _get_log_file_path(self, category: LogCategory) -> Optional[str]:
        """
        Get the log file path for a category.
        
        Args:
            category: Log category
            
        Returns:
            Optional[str]: Log file path, or None if category should not be logged to file
        """
        # Check if category has specific routing
        if category in self.category_routing:
            path = self.category_routing[category]
            # If absolute path, use as-is; otherwise relative to log_dir
            if os.path.isabs(path):
                return path
            return os.path.join(self.log_dir, path)
        
        # Default: use category name as filename
        return os.path.join(self.log_dir, f"{category.value}.log")
    
    def route_message(self, 
                     category: LogCategory,
                     message: str,
                     level: str = "INFO",
                     metadata: Optional[Dict] = None):
        """
        Route a message to appropriate destinations.
        
        Args:
            category: Message category
            message: Message text
            level: Log level (INFO, WARNING, ERROR, DEBUG)
            metadata: Optional metadata dictionary
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] [{level}] [{category.value.upper()}] {message}"
        
        # Add metadata if provided
        if metadata:
            metadata_str = " | ".join(f"{k}={v}" for k, v in metadata.items())
            formatted_message += f" | {metadata_str}"
        
        # Route to console
        if self.enable_console:
            print(formatted_message)
        
        # Route to category-specific log file
        log_file = self._get_log_file_path(category)
        if log_file:
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(formatted_message + "\n")
            except Exception as e:
                # Fallback: print error to console if file write fails
                if self.enable_console:
                    print(f"[ERROR] Failed to write to log file {log_file}: {e}")


class AuraBotLogger:
    """
    Main logger for AuraBot with category-based routing.
    
    Provides convenience methods for different message categories and
    maintains backward compatibility with ConversationLogger interface.
    """
    
    def __init__(self,
                 log_file: Optional[str] = None,
                 log_dir: str = "logs",
                 enable_console: bool = True,
                 category_routing: Optional[Dict[LogCategory, str]] = None):
        """
        Initialize the AuraBot logger.
        
        Args:
            log_file: Optional path to conversation log file (for backward compatibility)
            log_dir: Directory for log files
            enable_console: Whether to print messages to console
            category_routing: Optional dict mapping categories to specific log file paths
        """
        self.log_file = log_file
        self.router = MessageRouter(log_dir, enable_console, category_routing)
        
        # Set default conversation log file if provided
        if log_file and LogCategory.CONVERSATION not in (category_routing or {}):
            self.router.category_routing[LogCategory.CONVERSATION] = log_file
    
    # Convenience methods for each category
    def log_conversation(self, user_text: str, bot_text: str) -> bool:
        """
        Log a conversation event (backward compatible with ConversationLogger).
        
        Args:
            user_text: User's input text
            bot_text: Bot's response text
        
        Returns:
            bool: True if logging succeeded, False otherwise
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"USER: {user_text} | BOT: {bot_text}"
            self.router.route_message(LogCategory.CONVERSATION, message)
            return True
        except Exception as e:
            self.log_error(f"Error logging conversation: {e}")
            return False
    
    def log_mqtt(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Log an MQTT-related message."""
        self.router.route_message(LogCategory.MQTT, message, level, metadata)
    
    def log_timer(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Log a timer-related message."""
        self.router.route_message(LogCategory.TIMER, message, level, metadata)
    
    def log_wellness(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Log a wellness-related message."""
        self.router.route_message(LogCategory.WELLNESS, message, level, metadata)
    
    def log_session(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Log a session-related message."""
        self.router.route_message(LogCategory.SESSION, message, level, metadata)
    
    def log_sensor(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Log a sensor-related message."""
        self.router.route_message(LogCategory.SENSOR, message, level, metadata)
    
    def log_general(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Log a general system message."""
        self.router.route_message(LogCategory.GENERAL, message, level, metadata)
    
    def log_error(self, message: str, metadata: Optional[Dict] = None):
        """Log an error message."""
        self.router.route_message(LogCategory.ERROR, message, "ERROR", metadata)
    
    def log(self, category: LogCategory, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        """Generic log method for any category."""
        self.router.route_message(category, message, level, metadata)
    
    # Backward compatibility: alias for ConversationLogger interface
    def log_event(self, user_text: str, bot_text: str) -> bool:
        """Backward compatibility alias for log_conversation."""
        return self.log_conversation(user_text, bot_text)


# Backward compatibility: ConversationLogger class
class ConversationLogger:
    """
    Legacy conversation logger (backward compatibility).
    
    This class wraps AuraBotLogger to maintain compatibility with existing code.
    New code should use AuraBotLogger directly.
    """
    
    def __init__(self, log_file: str):
        """
        Initialize the conversation logger.
        
        Args:
            log_file: Path to the log file
        """
        self.log_file = log_file
        self._logger = AuraBotLogger(log_file=log_file)
    
    def log_event(self, user_text: str, bot_text: str) -> bool:
        """
        Log a conversation event.
        
        Args:
            user_text: User's input text
            bot_text: Bot's response text
        
        Returns:
            bool: True if logging succeeded, False otherwise
        """
        return self._logger.log_conversation(user_text, bot_text)
