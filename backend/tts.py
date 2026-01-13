import pyttsx3
from time import sleep
import platform
import subprocess
import threading

"""
    Simulate Text-to-Speech feature
"""

class TTS:
    def __init__(self):
        self._use_system_say = False
        self._is_macos = platform.system() == "Darwin"
        # Lock to prevent concurrent speech from multiple threads
        self._speak_lock = threading.Lock()
        
        # On macOS, pyttsx3 often has issues after microphone use (completes but no audio)
        # Use native 'say' command which is more reliable on macOS
        if self._is_macos:
            self._use_system_say = True
            # Still initialize pyttsx3 as fallback, but don't use it by default
            try:
                self._engine = pyttsx3.init()
            except:
                self._engine = None
        else:
            # On other platforms, use pyttsx3
            try:
                self._engine = pyttsx3.init()
            except Exception as e:
                print(f"Error initializing TTS engine: {e}")
                raise
    
    def style(self):
        if not self._use_system_say and self._engine:
            try:
                self._engine.setProperty("rate", 180)
                self._engine.setProperty("volume", 0.9)
            except Exception as e:
                print(f"Error setting TTS style: {e}")
        # For system say, rate is set per call

    def speak(self, message):
        print(f"AuraBot: {message}")
        
        # Use lock to prevent concurrent speech from multiple threads
        with self._speak_lock:
            self._speak_impl(message)
    
    def _speak_impl(self, message):
        """Internal speak implementation (called with lock held)."""
        # On macOS, use system say command if pyttsx3 isn't working reliably
        if self._use_system_say:
            try:
                # Use macOS say command with rate control (words per minute)
                # Rate 180 in pyttsx3 is roughly equivalent to say -r 200
                # Run in background to avoid blocking, but wait for completion
                subprocess.run(["say", "-r", "200", message], check=True, 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception as e:
                print(f"Error using system say: {e}")
                # Fall through to try pyttsx3 as backup
        
        # Only use pyttsx3 if engine is available
        if not self._engine:
            # If no engine and system say failed, try to initialize pyttsx3
            if self._is_macos:
                try:
                    self._engine = pyttsx3.init()
                    self.style()
                except:
                    return
            else:
                return
        
        try:
            # Ensure properties are still set
            try:
                self.style()
            except Exception as prop_error:
                # If properties fail and on macOS, try system say instead
                if self._is_macos:
                    try:
                        subprocess.run(["say", "-r", "200", message], check=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except:
                        pass
            
            # Queue the message and run (no delays needed)
            self._engine.say(message)
            self._engine.runAndWait()
            
        except RuntimeError as e:
            # RuntimeError can occur if engine is in use - try to recover
            try:
                # Reinitialize the engine
                self._engine = pyttsx3.init()
                self.style()
                self._engine.say(message)
                self._engine.runAndWait()
            except Exception as e2:
                # On macOS, fallback to system say command
                if self._is_macos:
                    try:
                        subprocess.run(["say", "-r", "200", message], check=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception as e3:
                        print(f"Error using system say: {e3}")
                else:
                    print(f"Error reinitializing TTS engine: {e2}")
        except Exception as e:
            print(f"Error in speak method: {e}")
            # On macOS, try system say as last resort
            if self._is_macos:
                try:
                    subprocess.run(["say", "-r", "200", message], check=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e2:
                    print(f"Error using system say: {e2}")
    
    def shutdown_tts(self):
        if not self._use_system_say and self._engine:
            try:
                self._engine.stop()
                sleep(0.5)
            except Exception as e:
                print(f"Error shutting down TTS: {e}")
        # System say doesn't need shutdown