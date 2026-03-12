import logging
import platform
import subprocess
import tempfile
import threading
import os
from time import perf_counter, sleep

"""
    Text-to-Speech feature optimized for Raspberry Pi 5
    Uses espeak-ng on Linux/Raspberry Pi (more reliable than pyttsx3)
    Uses system 'say' command on macOS

    Two output modes:
      speak(text)            – play audio on local speakers (blocking)
      synthesize_pcm(text)   – return 16 kHz mono 16-bit PCM bytes
                               (for the voice WebSocket Opus pipeline)
"""

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Read at call time (not import time) so that dotenv/.env values are respected
# even when tts.py is imported before load_dotenv() runs.
def _get_tts_pcm_gain() -> float:
    return float(os.environ.get("VOICE_TTS_PCM_GAIN", "0.90"))

def _get_voice_tts_prefer_online() -> bool:
    return os.environ.get(
        "VOICE_TTS_PREFER_ONLINE", "false"
    ).lower() in ("1", "true", "yes")


class TTS:
    def __init__(self):
        self._use_system_say = False
        self._use_espeak = False
        self._is_macos = platform.system() == "Darwin"
        self._is_linux = platform.system() == "Linux"
        self._speak_lock = threading.Lock()
        self._espeak_cmd = "espeak-ng"
        
        if self._is_macos:
            self._use_system_say = True
            print("TTS: Using macOS 'say' command")
        elif self._is_linux:
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
            try:
                import pyttsx3
                self._engine = pyttsx3.init()
                print("TTS: Using pyttsx3 (fallback)")
            except Exception as e:
                print(f"Error initializing TTS engine: {e}")
                raise

    # ------------------------------------------------------------------
    # PCM synthesis (for voice WebSocket / Opus pipeline)
    # ------------------------------------------------------------------

    def synthesize_pcm(
        self,
        text: str,
        prefer_online: bool | None = None,
        gain: float | None = None,
    ) -> bytes:
        """Synthesize *text* to 16 kHz mono 16-bit little-endian PCM bytes.

        *prefer_online*: True → gTTS first, False → offline only, None → read
        VOICE_TTS_PREFER_ONLINE env at call time (default False).
        *gain*: linear volume multiplier applied inside ffmpeg/sox (no Python
        sample loop).  None → read VOICE_TTS_PCM_GAIN env at call time (default 0.90).
        """
        if not text or not text.strip():
            return b""
        if prefer_online is None:
            prefer_online = _get_voice_tts_prefer_online()
        if gain is None:
            gain = _get_tts_pcm_gain()

        started_at = perf_counter()
        if prefer_online:
            pcm = self._online_tts_to_pcm(text, gain=gain)
            if pcm:
                total_ms = (perf_counter() - started_at) * 1000.0
                logger.info("TTS: online (gTTS) succeeded in %.0f ms", total_ms)
                return pcm
            logger.info("TTS: online failed, falling back to offline espeak")
        pcm = self._offline_tts_to_pcm(text, gain=gain)
        total_ms = (perf_counter() - started_at) * 1000.0
        logger.info("TTS: offline (espeak) completed in %.0f ms", total_ms)
        return pcm

    def _online_tts_to_pcm(self, text: str, gain: float = 1.0) -> bytes | None:
        """gTTS → MP3 → ffmpeg (with gain) → 16 kHz mono PCM."""
        try:
            from gtts import gTTS
        except ImportError:
            logger.debug("gTTS not installed – skipping online TTS")
            return None
        mp3_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                mp3_path = f.name
            t0 = perf_counter()
            tts = gTTS(text=text, lang="en", slow=False)
            tts.save(mp3_path)
            t1 = perf_counter()
            cmd = [
                "ffmpeg", "-y", "-i", mp3_path,
                "-af", f"volume={gain}",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-",
            ]
            out = subprocess.run(
                cmd, check=True, capture_output=True, timeout=30,
            )
            t2 = perf_counter()
            logger.info(
                "TTS online: gtts=%.0f ms ffmpeg=%.0f ms total=%.0f ms chars=%d",
                (t1 - t0) * 1000.0,
                (t2 - t1) * 1000.0,
                (t2 - t0) * 1000.0,
                len(text),
            )
            return out.stdout
        except (FileNotFoundError, subprocess.CalledProcessError, Exception) as e:
            logger.warning("Online TTS failed: %s", e)
            return None
        finally:
            if mp3_path:
                try:
                    os.unlink(mp3_path)
                except OSError:
                    pass

    def _offline_tts_to_pcm(self, text: str, gain: float = 1.0) -> bytes:
        """espeak-ng → WAV → sox (or ffmpeg, with gain) → 16 kHz mono PCM."""
        if not text or not text.strip():
            return b""
        wav_path = None
        try:
            t0 = perf_counter()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
            espeak_args = ["-w", wav_path, "-s", "180", text]
            try:
                subprocess.run(
                    [self._espeak_cmd] + espeak_args,
                    check=True, capture_output=True, timeout=30,
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                fallback = "espeak" if self._espeak_cmd != "espeak" else "espeak-ng"
                try:
                    subprocess.run(
                        [fallback] + espeak_args,
                        check=True, capture_output=True, timeout=30,
                    )
                except (FileNotFoundError, subprocess.CalledProcessError):
                    logger.warning("espeak-ng/espeak not available for offline TTS")
                    return b""
            t1 = perf_counter()
            gain_db = 20 * __import__("math").log10(max(gain, 0.001))
            try:
                out = subprocess.run(
                    [
                        "sox", wav_path,
                        "-r", str(SAMPLE_RATE), "-c", "1",
                        "gain", f"{gain_db:.2f}",
                        "-t", "raw", "-",
                    ],
                    check=True, capture_output=True, timeout=15,
                )
                t2 = perf_counter()
                logger.info(
                    "TTS offline: espeak=%.0f ms sox=%.0f ms total=%.0f ms chars=%d",
                    (t1 - t0) * 1000.0, (t2 - t1) * 1000.0, (t2 - t0) * 1000.0, len(text),
                )
                return out.stdout
            except (FileNotFoundError, subprocess.CalledProcessError):
                try:
                    out = subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", wav_path,
                            "-af", f"volume={gain}",
                            "-f", "s16le", "-acodec", "pcm_s16le",
                            "-ar", str(SAMPLE_RATE), "-ac", "1", "-",
                        ],
                        check=True, capture_output=True, timeout=15,
                    )
                    t2 = perf_counter()
                    logger.info(
                        "TTS offline: espeak=%.0f ms ffmpeg=%.0f ms total=%.0f ms chars=%d",
                        (t1 - t0) * 1000.0, (t2 - t1) * 1000.0, (t2 - t0) * 1000.0, len(text),
                    )
                    return out.stdout
                except (FileNotFoundError, subprocess.CalledProcessError):
                    logger.warning("sox/ffmpeg not available for TTS resample")
                    return b""
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Local speaker playback (original interface)
    # ------------------------------------------------------------------

    def style(self):
        if hasattr(self, '_engine') and self._engine:
            try:
                self._engine.setProperty("rate", 180)
                self._engine.setProperty("volume", 0.9)
            except Exception as e:
                print(f"Error setting TTS style: {e}")

    def speak(self, message):
        print(f"AuraBot: {message}")
        with self._speak_lock:
            self._speak_impl(message)
    
    def _speak_impl(self, message):
        """Internal speak implementation (called with lock held)."""
        if self._use_system_say:
            try:
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
        
        if self._use_espeak:
            try:
                subprocess.run(
                    [self._espeak_cmd, "-s", "180", "-a", "180", message],
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
        
        if hasattr(self, '_engine') and self._engine:
            try:
                self.style()
                self._engine.say(message)
                self._engine.runAndWait()
            except RuntimeError as e:
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
        if hasattr(self, '_engine') and self._engine and not self._use_system_say and not self._use_espeak:
            try:
                self._engine.stop()
                sleep(0.5)
            except Exception as e:
                print(f"Error shutting down TTS: {e}")