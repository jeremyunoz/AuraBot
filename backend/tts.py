import platform
import subprocess
import threading
import os
from time import sleep

"""
    Text-to-Speech feature optimized for Raspberry Pi 5
    Uses espeak-ng on Linux/Raspberry Pi (more reliable than pyttsx3)
    Uses system 'say' command on macOS
"""

class TTS:
    def __init__(self):
        self._use_system_say = False
        self._use_espeak = False
        self._is_macos = platform.system() == "Darwin"
        self._is_linux = platform.system() == "Linux"
        # Lock to prevent concurrent speech from multiple threads
        self._speak_lock = threading.Lock()
        
        # On macOS, use native 'say' command which is more reliable
        if self._is_macos:
            self._use_system_say = True
            print("TTS: Using macOS 'say' command")
        elif self._is_linux:
            # On Linux/Raspberry Pi, use espeak-ng (more reliable than pyttsx3)
            # Check if espeak-ng is available
            try:
                result = subprocess.run(
                    ["which", "espeak-ng"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0:
                    self._use_espeak = True
                    print("TTS: Using espeak-ng (Raspberry Pi optimized)")
                else:
                    # Try espeak (older version)
                    result = subprocess.run(
                        ["which", "espeak"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if result.returncode == 0:
                        self._use_espeak = True
                        self._espeak_cmd = "espeak"
                        print("TTS: Using espeak (fallback)")
                    else:
                        print("Warning: espeak-ng not found. Install with: sudo apt-get install espeak-ng")
                        raise RuntimeError("espeak-ng not available")
            except Exception as e:
                print(f"Error checking for espeak-ng: {e}")
                raise
        else:
            # For other platforms, try pyttsx3 as fallback
            try:
                import pyttsx3
                self._engine = pyttsx3.init()
                print("TTS: Using pyttsx3 (fallback)")
            except Exception as e:
                print(f"Error initializing TTS engine: {e}")
                raise
    
    def style(self):
        # Style settings are applied per call for espeak and system say
        # Only needed for pyttsx3 fallback
        if hasattr(self, '_engine') and self._engine:
            try:
                self._engine.setProperty("rate", 180)
                self._engine.setProperty("volume", 0.9)
            except Exception as e:
                print(f"Error setting TTS style: {e}")

    def speak(self, message):
        print(f"AuraBot: {message}")
        
        # Use lock to prevent concurrent speech from multiple threads
        with self._speak_lock:
            self._speak_impl(message)
    
    def _speak_impl(self, message):
        """Internal speak implementation (called with lock held)."""
        # On macOS, use system say command
        if self._use_system_say:
            try:
                # Use macOS say command with rate control (words per minute)
                # Rate 180 is roughly equivalent to say -r 200
                subprocess.run(
                    ["say", "-r", "200", message],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return
            except Exception as e:
                print(f"Error using system say: {e}")
                return
        
        # On Linux/Raspberry Pi, use espeak-ng
        if self._use_espeak:
            try:
                # espeak-ng parameters:
                # -s: speed (words per minute), default 175, we use 180
                # -a: amplitude (0-200), default 100, we use 90% = 180
                # -v: voice (optional, can specify language/voice)
                # -g: gap between words (0-10), default 0
                espeak_cmd = getattr(self, '_espeak_cmd', 'espeak-ng')
                subprocess.run(
                    [espeak_cmd, "-s", "180", "-a", "180", message],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return
            except subprocess.CalledProcessError as e:
                print(f"Error using espeak-ng: {e}")
                return
            except FileNotFoundError:
                print("Error: espeak-ng not found. Install with: sudo apt-get install espeak-ng")
                return
        
        # Fallback to pyttsx3 for other platforms
        if hasattr(self, '_engine') and self._engine:
            try:
                self.style()
                self._engine.say(message)
                self._engine.runAndWait()
            except RuntimeError as e:
                # RuntimeError can occur if engine is in use - try to recover
                try:
                    import pyttsx3
                    self._engine = pyttsx3.init()
                    self.style()
                    self._engine.say(message)
                    self._engine.runAndWait()
                except Exception as e2:
                    print(f"Error reinitializing TTS engine: {e2}")
            except Exception as e:
                print(f"Error in speak method: {e}")
        else:
            print("Error: No TTS engine available")
    
    def shutdown_tts(self):
        # Only pyttsx3 needs explicit shutdown
        if hasattr(self, '_engine') and self._engine and not self._use_system_say and not self._use_espeak:
            try:
                self._engine.stop()
                sleep(0.5)
            except Exception as e:
                print(f"Error shutting down TTS: {e}")
        # espeak-ng and system say don't need shutdown