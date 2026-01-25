import speech_recognition as sr
import platform
import subprocess
import os
import sys
from contextlib import contextmanager
from time import sleep

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
                 timeout=15, 
                 phrase_time_limit=15, 
                 ambient_noise_duration=1.5,
                 microphone_index=None,
                 energy_threshold=None):
        """
        Initialize the STT module.
        
        Args:
            timeout (int): Maximum seconds to wait for speech to start (default: 15)
            phrase_time_limit (int): Maximum seconds for a phrase (default: 15)
            ambient_noise_duration (float): Seconds to calibrate ambient noise (default: 1.5)
            microphone_index (int, optional): Specific microphone device index to use
            energy_threshold (float, optional): Manual energy threshold for speech detection
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
        self.energy_threshold = energy_threshold
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
    
    def listen_and_transcribe(self, auto_calibrate=True, max_retries=3):
        """
        Listen to microphone input and transcribe speech to text.
        
        Args:
            auto_calibrate (bool): Automatically calibrate on first use (default: True)
            max_retries (int): Maximum number of retry attempts on failure (default: 3)
        
        Returns:
            str: Transcribed text in lowercase, or empty string on error
        """
        for attempt in range(max_retries + 1):
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
                                # Make threshold slightly more sensitive (reduce by 10%)
                                # This helps catch quieter speech
                                if self.recognizer.energy_threshold > 50:
                                    self.recognizer.energy_threshold *= 0.9
                                # Log the energy threshold for debugging
                                print(f"Energy threshold set to: {self.recognizer.energy_threshold:.1f}")
                                self._ambient_noise_adjusted = True
                                print("Ready! Listening...")
                            except Exception as e:
                                print(f"Warning: Could not calibrate microphone: {e}")
                                print("Continuing without calibration...")
                                self._ambient_noise_adjusted = True  # Mark as attempted
                        else:
                            print("\nListening...")
                        
                        # Set manual energy threshold if provided
                        if self.energy_threshold is not None:
                            self.recognizer.energy_threshold = self.energy_threshold
                        else:
                            # Dynamically adjust threshold if it seems too high
                            # Lower threshold by 20% if we're on a retry attempt (might be too sensitive)
                            if attempt > 0 and self.recognizer.energy_threshold > 100:
                                adjusted_threshold = self.recognizer.energy_threshold * 0.8
                                self.recognizer.energy_threshold = adjusted_threshold
                        
                        # Listen for audio input
                        try:
                            audio = self.recognizer.listen(
                                source, 
                                timeout=self.timeout, 
                                phrase_time_limit=self.phrase_time_limit
                            )
                        except sr.WaitTimeoutError:
                            if attempt < max_retries:
                                print("No speech detected, retrying...")
                                sleep(0.5)  # Brief pause before retry
                                continue
                            print("No speech detected within timeout period.")
                            return ""
                        except OSError as e:
                            print(f"Audio device error: {e}")
                            if self._is_linux:
                                print("On Raspberry Pi, you may need to:")
                                print("  1. Check microphone permissions")
                                print("  2. Verify audio device: arecord -l")
                                print("  3. Test recording: arecord -d 3 test.wav")
                            if attempt < max_retries:
                                sleep(0.5)  # Brief delay before retry
                                continue
                            return ""
            except Exception as e:
                print(f"Error accessing microphone: {e}")
                if self._is_linux:
                    print("Troubleshooting tips for Raspberry Pi:")
                    print("  - Ensure microphone is connected and recognized")
                    print("  - Check audio permissions in /etc/group (audio group)")
                    print("  - Try: sudo usermod -a -G audio $USER (then logout/login)")
                if attempt < max_retries:
                    sleep(0.5)
                    continue
                return ""

            try:
                # Use Google's recognizer with language hint for better accuracy
                # Also enable show_all=False to get best result, and with_confidence for better handling
                text = self.recognizer.recognize_google(audio, language="en-US")
                print(f"You said: {text}")
                return text.lower()
            except OSError as e:
                # Handle FLAC missing error specifically
                if "FLAC" in str(e) or "flac" in str(e).lower():
                    print("Error: FLAC conversion utility not available.")
                    print("Install with: sudo apt-get install flac")
                    if self._is_linux:
                        print("After installing, restart the application.")
                    return ""
                else:
                    print(f"Audio processing error: {e}")
                    if attempt < max_retries:
                        sleep(0.5)
                        continue
                    return ""
            except sr.UnknownValueError:
                if attempt < max_retries:
                    print("Could not understand audio, retrying...")
                    # Increase pause time with each retry to allow for better audio capture
                    sleep(0.5 + (attempt * 0.2))
                    continue
                print("Sorry, could not understand the audio.")
                print("Tip: Try speaking more clearly or closer to the microphone.")
                return ""
            except sr.RequestError as e:
                print(f"Could not connect to Google API: {e}")
                print("Check your internet connection.")
                if attempt < max_retries:
                    sleep(1.0)  # Longer delay for network issues
                    continue
                return ""
        
        return ""  # All retries exhausted
    
    def reset_calibration(self):
        """
        Reset the ambient noise calibration.
        Useful if microphone conditions change significantly.
        """
        self._ambient_noise_adjusted = False
    
    def set_energy_threshold(self, threshold):
        """
        Manually set the energy threshold for speech detection.
        Lower values = more sensitive (may pick up background noise)
        Higher values = less sensitive (may miss quiet speech)
        
        Args:
            threshold (float): Energy threshold value
        """
        self.recognizer.energy_threshold = threshold
        self.energy_threshold = threshold
        print(f"Energy threshold set to: {threshold:.1f}")
    
    def get_energy_threshold(self):
        """Get the current energy threshold."""
        return self.recognizer.energy_threshold
    
    def test_microphone_sensitivity(self):
        """
        Test microphone and suggest optimal energy threshold.
        Run this to find the best threshold for your environment.
        """
        print("Testing microphone sensitivity...")
        print("Please speak normally for a few seconds...")
        
        with suppress_stderr():
            with self.mic as source:
                # Get ambient noise level
                self.recognizer.adjust_for_ambient_noise(source, duration=1.0)
                ambient_threshold = self.recognizer.energy_threshold
                print(f"Ambient noise threshold: {ambient_threshold:.1f}")
                
                # Test with speech
                print("Now speak a sentence...")
                try:
                    audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=5)
                    # Check audio energy
                    import audioop
                    rms = audioop.rms(audio.get_raw_data(), audio.sample_width)
                    print(f"Speech RMS energy: {rms:.1f}")
                    print(f"Suggested threshold: {max(ambient_threshold * 1.5, rms * 0.5):.1f}")
                except sr.WaitTimeoutError:
                    print("No speech detected during test.")


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
