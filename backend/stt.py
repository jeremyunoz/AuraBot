"""
Speech-to-Text module for AuraBot.

Core ASR: accepts 16 kHz mono 16-bit PCM bytes and returns a transcript using
Google's online recognizer (speech_recognition).  Matches the receiving pipeline
in voice_ws_server.py where Opus frames from ESP32 are decoded to PCM, buffered,
then transcribed here.

For offline ASR, swap recognizer.recognize_google with a Vosk/Whisper backend.
"""

import logging
import speech_recognition as sr

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit PCM
MIN_PCM_BYTES = SAMPLE_RATE  # ~0.5 s of audio (16000 bytes)


class STT:
    """Lightweight ASR wrapper around speech_recognition."""

    def __init__(self, language: str = "en-US"):
        self.recognizer = sr.Recognizer()
        self.language = language

    def transcribe(self, pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        """
        Run ASR on raw PCM bytes.

        Args:
            pcm_bytes: 16-bit mono PCM audio data.
            sample_rate: Sample rate in Hz (default 16000).

        Returns:
            Transcript string (lowercase, stripped) or empty string on failure.
        """
        if not pcm_bytes or len(pcm_bytes) < MIN_PCM_BYTES:
            return ""
        try:
            audio = sr.AudioData(pcm_bytes, sample_rate, SAMPLE_WIDTH)
            text = self.recognizer.recognize_google(audio, language=self.language)
            return (text or "").strip().lower()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            logger.warning("ASR request error: %s", e)
            return ""
        except Exception as e:
            logger.warning("ASR error: %s", e)
            return ""
