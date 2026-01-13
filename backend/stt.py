import speech_recognition as sr

"""
    Speech-to-Text module
    This uses Google's online recognizer; for offline mode we'll switch to Vosk later.
"""


class STT:
    """
    Speech-to-Text class for handling audio input and transcription.
    
    This class encapsulates the speech recognition functionality, allowing
    for easy configuration and reuse across different parts of the application.
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
        self.recognizer = sr.Recognizer()
        if microphone_index is not None:
            self.mic = sr.Microphone(device_index=microphone_index)
        else:
            self.mic = sr.Microphone()
        
        self.timeout = timeout
        self.phrase_time_limit = phrase_time_limit
        self.ambient_noise_duration = ambient_noise_duration
        self._ambient_noise_adjusted = False
    
    def calibrate(self):
        """
        Calibrate the microphone for ambient noise.
        This should be called once before first use for better accuracy.
        """
        if not self._ambient_noise_adjusted:
            print("Calibrating microphone...")
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
        with self.mic as source:
            # Only adjust for ambient noise once at startup (saves 1-2 seconds per interaction)
            if auto_calibrate and not self._ambient_noise_adjusted:
                print("Calibrating microphone...")
                self.recognizer.adjust_for_ambient_noise(
                    source, 
                    duration=self.ambient_noise_duration
                )
                self._ambient_noise_adjusted = True
                print("Ready! Listening...")
            else:
                print("\nListening...")
            
            # Listen for audio input
            audio = self.recognizer.listen(
                source, 
                timeout=self.timeout, 
                phrase_time_limit=self.phrase_time_limit
            )

        try:
            text = self.recognizer.recognize_google(audio)
            print(f"You said: {text}")
            return text.lower()
        except sr.UnknownValueError:
            print("Sorry, could not understand the audio.")
            return ""
        except sr.RequestError:
            print("Could not connect to Google API.")
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
