import speech_recognition as sr
import platform
import subprocess
import os
import sys
import pyaudio
import numpy as np
import threading
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
                 timeout=8, 
                 phrase_time_limit=8, 
                 ambient_noise_duration=1.5,
                 microphone_index=None,
                 energy_threshold=None):
        """
        Initialize the STT module with PyAudio for ReSpeaker device.
        
        Args:
            timeout (int): Maximum seconds to wait for speech to start (default: 8)
            phrase_time_limit (int): Maximum seconds for a phrase (default: 8)
            ambient_noise_duration (float): Seconds to calibrate ambient noise (default: 1.5)
            microphone_index (int, optional): Specific microphone device index to use (default: 0 for ReSpeaker)
            energy_threshold (float, optional): Manual energy threshold for speech detection
        """
        # On Raspberry Pi/Linux, check audio configuration
        self._is_linux = platform.system() == "Linux"
        if self._is_linux:
            self._check_audio_config()
        
        # ReSpeaker configuration
        self.RESPEAKER_RATE = 16000
        self.RESPEAKER_CHANNELS = 2
        self.RESPEAKER_WIDTH = 2
        self.RESPEAKER_INDEX = microphone_index if microphone_index is not None else 0  # Default to ReSpeaker index 0
        self.CHUNK = 1024
        
        # Thread safety lock for PyAudio operations
        self._pyaudio_lock = threading.Lock()
        
        # Initialize recognizer (suppress ALSA/JACK warnings during initialization)
        try:
            with suppress_stderr():
                self.recognizer = sr.Recognizer()
        except Exception as e:
            print(f"Warning: Error initializing recognizer: {e}")
            raise
        
        # Initialize PyAudio instance (will be reused, not closed until cleanup)
        try:
            with suppress_stderr():
                self.pyaudio_instance = pyaudio.PyAudio()
        except Exception as e:
            print(f"Error: Could not initialize PyAudio: {e}")
            print("Please check your audio configuration.")
            raise
        
        self.timeout = timeout
        self.phrase_time_limit = phrase_time_limit
        self.ambient_noise_duration = ambient_noise_duration
        self.energy_threshold = energy_threshold
        self._ambient_noise_adjusted = False
        self._stream = None  # Persistent stream that stays open between recordings
        self._stream_initialized = False  # Track if stream has been successfully initialized
    
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
            with suppress_stderr():
                p = pyaudio.PyAudio()
                print(f"Available audio input devices:")
                for i in range(p.get_device_count()):
                    info = p.get_device_info_by_index(i)
                    if info['maxInputChannels'] > 0:
                        print(f"  [{i}] {info['name']} (channels: {info['maxInputChannels']})")
                p.terminate()
        except Exception as e:
            print(f"Could not list microphones: {e}")
    
    def _ensure_stream_open(self):
        """
        Ensure the audio stream is open and ready. Reuses existing stream if available.
        This eliminates the need to reopen the stream for each recording, reducing delays.
        """
        # Check if stream already exists and is active
        if self._stream is not None:
            try:
                if self._stream.is_active():
                    # Stream is already open and active, no need to recreate
                    return True
                else:
                    # Stream exists but is not active, close it and recreate
                    try:
                        with suppress_stderr():
                            self._stream.stop_stream()
                            self._stream.close()
                    except:
                        pass
                    self._stream = None
                    self._stream_initialized = False
            except:
                # Stream object exists but is invalid, clear it
                self._stream = None
                self._stream_initialized = False
        
        # Need to create a new stream - try immediately, retry with increasing delays
        attempt_rate = self.RESPEAKER_RATE  # 16000 Hz
        
        # More retries and longer delays for first initialization
        if not self._stream_initialized:
            max_retries = 6
            retry_delays = [0.2, 0.5, 1.0, 1.5, 2.0]  # Increasing delays for first init
        else:
            max_retries = 3
            retry_delays = [0.5, 0.5, 0.5]  # Shorter delays for re-initialization
        
        last_error = None
        for retry_attempt in range(max_retries):
            try:
                # Create stream (matching official ReSpeaker example)
                # Suppress stderr during stream creation to hide ALSA warnings
                with suppress_stderr():
                    self._stream = self.pyaudio_instance.open(
                        rate=attempt_rate,
                        format=self.pyaudio_instance.get_format_from_width(self.RESPEAKER_WIDTH),
                        channels=self.RESPEAKER_CHANNELS,
                        input=True,
                        input_device_index=self.RESPEAKER_INDEX,
                        frames_per_buffer=self.CHUNK
                    )
                self._stream_initialized = True
                return True
                
            except Exception as e:
                last_error = e
                # Ensure stream is closed even on error
                try:
                    if self._stream is not None:
                        with suppress_stderr():
                            self._stream.stop_stream()
                            self._stream.close()
                        self._stream = None
                except:
                    pass
                
                # Wait before retry with increasing delays
                if retry_attempt < max_retries - 1:
                    retry_delay = retry_delays[retry_attempt] if retry_attempt < len(retry_delays) else retry_delays[-1]
                    sleep(retry_delay)
        
        # If all retries failed, raise the last error
        self._stream_initialized = False
        raise last_error if last_error else Exception(f"Could not open audio stream at {attempt_rate} Hz after {max_retries} attempts")
    
    def _record_audio_pyaudio(self, duration=None):
        """
        Record audio using PyAudio with ReSpeaker device.
        Extracts channel 0 from 2-channel input.
        Reuses persistent stream to avoid initialization delays between recordings.
        
        Args:
            duration (float, optional): Recording duration in seconds. 
                                      If None, uses phrase_time_limit.
        
        Returns:
            bytes: Raw audio data (mono, 16-bit PCM)
        """
        if duration is None:
            duration = self.phrase_time_limit
        
        # Use lock to prevent race conditions
        with self._pyaudio_lock:
            # Ensure stream is open (reuses existing stream if available)
            self._ensure_stream_open()
            
            attempt_rate = self.RESPEAKER_RATE  # 16000 Hz
            
            try:
                frames = []
                num_chunks = int(attempt_rate / self.CHUNK * duration)
                
                for i in range(num_chunks):
                    data = self._stream.read(self.CHUNK, exception_on_overflow=False)
                    # Convert bytes to numpy array
                    a = np.frombuffer(data, dtype=np.int16)
                    
                    # Extract channel 0 data from 2 channels (as per official ReSpeaker example)
                    # [0::2] means start at index 0, take every 2nd element (channel 0)
                    if self.RESPEAKER_CHANNELS == 2:
                        a = a[0::2]
                    
                    frames.append(a.tobytes())
                
                # Keep stream open for next recording (don't close it)
                return b''.join(frames)
                
            except Exception as e:
                # If recording fails, mark stream as invalid so it will be recreated next time
                try:
                    if self._stream is not None:
                        with suppress_stderr():
                            self._stream.stop_stream()
                            self._stream.close()
                except:
                    pass
                self._stream = None
                self._stream_initialized = False
                raise
    
    def _wav_bytes_to_audiodata(self, wav_bytes, sample_rate=16000, sample_width=2, channels=1):
        """
        Convert WAV bytes to speech_recognition AudioData object.
        
        Args:
            wav_bytes (bytes): Raw PCM audio data
            sample_rate (int): Sample rate in Hz
            sample_width (int): Sample width in bytes
            channels (int): Number of channels
        
        Returns:
            sr.AudioData: AudioData object for speech_recognition
        """
        return sr.AudioData(wav_bytes, sample_rate, sample_width)
    
    def calibrate(self):
        """
        Calibrate the microphone for ambient noise.
        This should be called once before first use for better accuracy.
        Note: Calibration with PyAudio direct recording is simplified.
        Calibration is non-critical - if it fails, we use default values.
        """
        if not self._ambient_noise_adjusted:
            print("Calibrating microphone...")
            try:
                # Record a short sample for calibration
                # Device initialization delay is handled in _ensure_stream_open()
                audio_data = self._record_audio_pyaudio(duration=self.ambient_noise_duration)
                audio = self._wav_bytes_to_audiodata(audio_data)
                
                # Simple threshold estimation (we'll use a default reasonable value)
                if self.energy_threshold is None:
                    self.recognizer.energy_threshold = 300  # Default reasonable threshold
                
                self._ambient_noise_adjusted = True
                print("Calibration complete. Ready! Listening...")
            except Exception as e:
                # Check if it's a stream initialization error (device not ready yet)
                error_msg = str(e)
                if "Invalid sample rate" in error_msg or "Could not open audio stream" in error_msg:
                    # Device not ready yet - this is expected on first use
                    # Stream will be initialized on first actual recording
                    print("Device initializing... using default settings.")
                else:
                    print(f"Warning: Could not calibrate microphone: {e}")
                    print("Using default settings - calibration is optional and non-critical.")
                
                # Set default threshold anyway
                if self.energy_threshold is None:
                    self.recognizer.energy_threshold = 300
                self._ambient_noise_adjusted = True
                print("Ready! Listening...")
    
    def listen_and_transcribe(self, auto_calibrate=True, max_retries=3):
        """
        Listen to microphone input and transcribe speech to text using PyAudio.
        
        Args:
            auto_calibrate (bool): Automatically calibrate on first use (default: True)
            max_retries (int): Maximum number of retry attempts on failure (default: 3)
        
        Returns:
            str: Transcribed text in lowercase, or empty string on error
        """
        # Calibrate on first use if needed
        if auto_calibrate and not self._ambient_noise_adjusted:
            self.calibrate()
        
        for attempt in range(max_retries + 1):
            try:
                # Set manual energy threshold if provided
                if self.energy_threshold is not None:
                    self.recognizer.energy_threshold = self.energy_threshold
                
                print("\nListening...")
                
                # Record audio using PyAudio (thread-safe)
                # Note: stderr suppression is handled inside _record_audio_pyaudio()
                try:
                    audio_data = self._record_audio_pyaudio(duration=self.phrase_time_limit)
                except Exception as e:
                    print(f"Audio recording error: {e}")
                    if self._is_linux:
                        print("On Raspberry Pi, you may need to:")
                        print("  1. Check microphone permissions")
                        print("  2. Verify audio device: arecord -l")
                        print("  3. Test recording: arecord -d 3 test.wav")
                        print(f"  4. Check ReSpeaker device index: {self.RESPEAKER_INDEX}")
                    if attempt < max_retries:
                        sleep(0.5)  # Brief delay before retry
                        continue
                    return ""
                
                # Convert to AudioData format for speech_recognition
                try:
                    audio = self._wav_bytes_to_audiodata(
                        audio_data, 
                        sample_rate=self.RESPEAKER_RATE,
                        sample_width=self.RESPEAKER_WIDTH,
                        channels=1
                    )
                except Exception as e:
                    print(f"Audio conversion error: {e}")
                    if attempt < max_retries:
                        sleep(0.5)
                        continue
                    return ""
                
            except Exception as e:
                print(f"Error accessing microphone: {e}")
                if self._is_linux:
                    print("Troubleshooting tips for Raspberry Pi:")
                    print("  - Ensure microphone is connected and recognized")
                    print("  - Check audio permissions in /etc/group (audio group)")
                    print("  - Try: sudo usermod -a -G audio $USER (then logout/login)")
                    print(f"  - Verify ReSpeaker device index: {self.RESPEAKER_INDEX}")
                if attempt < max_retries:
                    sleep(0.5)
                    continue
                return ""

            try:
                # Use Google's recognizer with language hint for better accuracy
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
        
        try:
            # Record ambient noise
            # Note: stderr suppression is handled inside _record_audio_pyaudio()
            audio_data = self._record_audio_pyaudio(duration=1.0)
            audio = self._wav_bytes_to_audiodata(audio_data)
                
            # Check audio energy
            import audioop
            rms = audioop.rms(audio.get_raw_data(), audio.sample_width)
            print(f"Ambient noise RMS energy: {rms:.1f}")
            
            # Test with speech
            print("Now speak a sentence...")
            audio_data = self._record_audio_pyaudio(duration=5.0)
            audio = self._wav_bytes_to_audiodata(audio_data)
            rms = audioop.rms(audio.get_raw_data(), audio.sample_width)
            print(f"Speech RMS energy: {rms:.1f}")
            suggested_threshold = max(rms * 0.3, 200)  # Reasonable default
            print(f"Suggested threshold: {suggested_threshold:.1f}")
        except Exception as e:
            print(f"Error during microphone test: {e}")
    
    def cleanup(self):
        """
        Clean up PyAudio resources.
        Call this when done with the STT instance to free resources.
        """
        with self._pyaudio_lock:
            try:
                # Close persistent stream if it exists
                if self._stream is not None:
                    try:
                        with suppress_stderr():
                            if self._stream.is_active():
                                self._stream.stop_stream()
                            self._stream.close()
                    except:
                        pass
                    self._stream = None
                    self._stream_initialized = False
            except Exception as e:
                print(f"Error closing stream during cleanup: {e}")
            
            try:
                if hasattr(self, 'pyaudio_instance') and self.pyaudio_instance:
                    self.pyaudio_instance.terminate()
                    self.pyaudio_instance = None
            except Exception as e:
                print(f"Error during PyAudio cleanup: {e}")
    
    def __del__(self):
        """Cleanup on object destruction."""
        try:
            self.cleanup()
        except:
            pass  # Ignore errors during cleanup


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
