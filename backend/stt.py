import speech_recognition as sr
import platform
import subprocess
import os
import sys
from contextlib import contextmanager

"""
    Speech-to-Text module optimized for Raspberry Pi 5
    This uses Google's online recognizer; for offline mode we'll switch to Vosk later.
"""

# Suppress ALSA warnings at the system level
# Set ALSA error reporting to only show critical errors (0 = none, 1 = critical only)
os.environ.setdefault('ALSA_CARD', '')
os.environ.setdefault('ALSA_PCM_CARD', '')
# Note: ALSA doesn't have a direct env var to suppress warnings, so we use stderr redirection


# Global devnull file handle to keep it open during suppression
_devnull_fd = None

def _get_devnull():
    """Get or create a devnull file descriptor."""
    global _devnull_fd
    if _devnull_fd is None:
        _devnull_fd = open(os.devnull, 'w')
    return _devnull_fd

@contextmanager
def suppress_stderr():
    """
    Context manager to suppress stderr output at the file descriptor level.
    Used to hide harmless ALSA/JACK warnings from PyAudio initialization.
    This redirects stderr at the OS level, catching C library output.
    """
    # Save original stderr file descriptor
    original_stderr_fd = sys.stderr.fileno()
    # Create a duplicate of the original stderr
    saved_stderr_fd = os.dup(original_stderr_fd)
    
    try:
        # Get devnull file descriptor (keep it open)
        devnull = _get_devnull()
        # Redirect stderr file descriptor to devnull
        os.dup2(devnull.fileno(), original_stderr_fd)
        try:
            yield
        finally:
            # Restore original stderr
            os.dup2(saved_stderr_fd, original_stderr_fd)
    finally:
        # Close the saved file descriptor
        os.close(saved_stderr_fd)


class STT:
    """
    Speech-to-Text class for handling audio input and transcription.
    
    This class encapsulates the speech recognition functionality, allowing
    for easy configuration and reuse across different parts of the application.
    Optimized for Raspberry Pi 5 with better audio device handling.
    """
    
    def __init__(self, 
                 timeout=5, 
                 phrase_time_limit=5, 
                 ambient_noise_duration=0.5,
                 microphone_index=None):
        """
        Initialize the STT module.
        
        Args:
            timeout (int): Maximum seconds to wait for speech to start (default: 5)
            phrase_time_limit (int): Maximum seconds for a phrase (default: 5)
            ambient_noise_duration (float): Seconds to calibrate ambient noise (default: 0.5)
            microphone_index (int, optional): Specific microphone device index to use
        """
        # On Raspberry Pi/Linux, check audio configuration
        self._is_linux = platform.system() == "Linux"
        if self._is_linux:
            self._check_audio_config()
        
        # Initialize recognizer and microphone (suppress ALSA/JACK warnings during initialization)
        # These warnings are harmless - PyAudio probes for audio backends that may not exist
        try:
            with suppress_stderr():
                self.recognizer = sr.Recognizer()
                if microphone_index is not None:
                    self.mic = sr.Microphone(device_index=microphone_index)
                else:
                    self.mic = sr.Microphone()
        except Exception as e:
            print(f"Warning: Error initializing microphone: {e}")
            print("Attempting to list available microphones...")
            self._list_microphones()
            # Try default microphone anyway
            try:
                with suppress_stderr():
                    self.mic = sr.Microphone()
            except Exception as e2:
                print(f"Error: Could not initialize microphone: {e2}")
                print("Please check your audio configuration.")
                raise
        
        self.timeout = timeout
        self.phrase_time_limit = phrase_time_limit
        self.ambient_noise_duration = ambient_noise_duration
        self._ambient_noise_adjusted = False
    
    def _check_audio_config(self):
        """Check and provide guidance on audio configuration for Raspberry Pi."""
        missing_deps = []
        
        # Check if ALSA is available
        try:
            result = subprocess.run(
                ["which", "arecord"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                missing_deps.append("alsa-utils")
        except Exception:
            pass
        
        # Check if FLAC is available (required for speech_recognition)
        try:
            result = subprocess.run(
                ["which", "flac"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                missing_deps.append("flac")
        except Exception:
            pass
        
        # Check if PulseAudio is running (common on Raspberry Pi OS)
        try:
            result = subprocess.run(
                ["pgrep", "-x", "pulseaudio"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                print("Info: PulseAudio not running. This is normal for some configurations.")
        except Exception:
            pass
        
        # Report missing dependencies
        if missing_deps:
            print(f"Warning: Missing required audio dependencies: {', '.join(missing_deps)}")
            print("Install with: sudo apt-get install " + " ".join(missing_deps))
            if "flac" in missing_deps:
                print("  Note: FLAC is required for speech recognition to work!")
    
    def _list_microphones(self):
        """List available microphone devices for debugging."""
        try:
            mic_list = sr.Microphone.list_microphone_names()
            print(f"Available microphones ({len(mic_list)}):")
            for i, name in enumerate(mic_list):
                print(f"  [{i}] {name}")
        except Exception as e:
            print(f"Could not list microphones: {e}")
    
    def calibrate(self):
        """
        Calibrate the microphone for ambient noise.
        This should be called once before first use for better accuracy.
        """
        if not self._ambient_noise_adjusted:
            print("Calibrating microphone...")
            with suppress_stderr():
                with self.mic as source:
                    self.recognizer.adjust_for_ambient_noise(
                        source, 
                        duration=self.ambient_noise_duration
                    )
            self._ambient_noise_adjusted = True
            print("Ready! Listening...")
    
    def listen_and_transcribe(self, auto_calibrate=True):
        """
        Listen to microphone input and transcribe speech to text.
        
        Args:
            auto_calibrate (bool): Automatically calibrate on first use (default: True)
        
        Returns:
            str: Transcribed text in lowercase, or empty string on error
        """
        try:
            # Suppress ALSA/JACK warnings during all microphone operations
            with suppress_stderr():
                with self.mic as source:
                    # Only adjust for ambient noise once at startup (saves 1-2 seconds per interaction)
                    if auto_calibrate and not self._ambient_noise_adjusted:
                        print("Calibrating microphone...")
                        try:
                            self.recognizer.adjust_for_ambient_noise(
                                source, 
                                duration=self.ambient_noise_duration
                            )
                            self._ambient_noise_adjusted = True
                            print("Ready! Listening...")
                        except Exception as e:
                            print(f"Warning: Could not calibrate microphone: {e}")
                            print("Continuing without calibration...")
                            self._ambient_noise_adjusted = True  # Mark as attempted
                    else:
                        print("\nListening...")
                    
                    # Listen for audio input
                    try:
                        audio = self.recognizer.listen(
                            source, 
                            timeout=self.timeout, 
                            phrase_time_limit=self.phrase_time_limit
                        )
                    except sr.WaitTimeoutError:
                        print("No speech detected within timeout period.")
                        return ""
                    except OSError as e:
                        print(f"Audio device error: {e}")
                        if self._is_linux:
                            print("On Raspberry Pi, you may need to:")
                            print("  1. Check microphone permissions")
                            print("  2. Verify audio device: arecord -l")
                            print("  3. Test recording: arecord -d 3 test.wav")
                        return ""
        except Exception as e:
            print(f"Error accessing microphone: {e}")
            if self._is_linux:
                print("Troubleshooting tips for Raspberry Pi:")
                print("  - Ensure microphone is connected and recognized")
                print("  - Check audio permissions in /etc/group (audio group)")
                print("  - Try: sudo usermod -a -G audio $USER (then logout/login)")
            return ""

        try:
            text = self.recognizer.recognize_google(audio)
            print(f"You said: {text}")
            return text.lower()
        except OSError as e:
            # Handle FLAC missing error specifically
            if "FLAC" in str(e) or "flac" in str(e).lower():
                print("Error: FLAC conversion utility not available.")
                print("Install with: sudo apt-get install flac")
                if self._is_linux:
                    print("After installing, restart the application.")
            else:
                print(f"Audio processing error: {e}")
            return ""
        except sr.UnknownValueError:
            print("Sorry, could not understand the audio.")
            return ""
        except sr.RequestError as e:
            print(f"Could not connect to Google API: {e}")
            print("Check your internet connection.")
            return ""
    
    def reset_calibration(self):
        """
        Reset the ambient noise calibration.
        Useful if microphone conditions change significantly.
        """
        self._ambient_noise_adjusted = False


# Backward compatibility: Create a default instance and expose the function
_default_stt = STT()

def listen_and_transcribe():
    """
    Backward-compatible function wrapper.
    Uses the default STT instance.
    
    Returns:
        str: Transcribed text in lowercase, or empty string on error
    """
    return _default_stt.listen_and_transcribe()
