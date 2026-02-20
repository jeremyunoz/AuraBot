"""
Voice WebSocket server for Pi5: receive Opus from ESP32 → decode → ASR → AuraBot response → TTS Opus → ESP32.

When integrated with AuraBot (app.state.aurabot set), transcripts are passed to the bot's response handler
(LLM, timers, exit) and the reply is sent as TTS over the same WebSocket. Otherwise echoes "You said: ...".

Matches the protocol in esp32/main/voice_session.c:
- ESP32 connects to ws://<host>:8765/voice
- Client sends text hello; server replies with {"type":"hello",...}
- ESP32 sends binary Opus frames (60 ms, 16 kHz mono) only in LISTEN phase.
- Alternation (no congestion): LISTEN = ESP32 sends mic, ignores TTS; SPEAK = ESP32 plays TTS, does not send mic.
- Server sends {"type":"tts_start"} before each TTS burst, then binary Opus frames, then {"type":"tts_end"}.

Run standalone: python voice_ws_server.py
Integrated: sim_loop sets app.state.aurabot and runs this server by default (ENABLE_VOICE_WS=true; voice capture on ESP32). Set ENABLE_VOICE_WS=false to use Pi mic instead.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Opus: 60 ms @ 16 kHz mono (match ESP32 voice_session.c)
SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_MS = 60
FRAME_SAMPLES = (FRAME_MS * SAMPLE_RATE) // 1000  # 960
FRAME_BYTES = FRAME_SAMPLES * 2  # 1920 (16-bit)
# How much PCM to collect before running ASR (~2.5 s)
UTTERANCE_BYTES = int(SAMPLE_RATE * 2.5 * 2)  # 80_000 bytes

# Optional: opuslib for decode/encode (requires libopus: sudo apt-get install libopus0 libopus-dev)
try:
    from opuslib import Decoder, Encoder
    _OPUS_AVAILABLE = True
except Exception as e:
    logger.warning("opuslib not available (install libopus-dev and opuslib): %s", e)
    _OPUS_AVAILABLE = False

# Speech recognition (from PCM buffer, not Pi mic)
import speech_recognition as sr

app = FastAPI(title="AuraBot Voice WS", version="0.2.0")

HELLO_RESPONSE = {
    "type": "hello",
    "version": 1,
    "transport": "websocket",
}

# Phase control for ESP32 listen/speak alternation (voice_session.c):
# tts_start → ESP32 enters SPEAK (stops sending mic, plays TTS); tts_end → back to LISTEN.
TTS_START_MSG = json.dumps({"type": "tts_start"})
TTS_END_MSG = json.dumps({"type": "tts_end"})

# Thread pool for blocking ASR/TTS
_executor = ThreadPoolExecutor(max_workers=2)


def _tts_to_pcm_16k(text: str) -> bytes:
    """Synthesize text to 16 kHz mono 16-bit PCM using espeak-ng + sox/ffmpeg."""
    if not text or not text.strip():
        return b""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    try:
        # espeak-ng to WAV (Linux/Pi)
        try:
            subprocess.run(
                ["espeak-ng", "-w", wav_path, "-s", "180", text],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                subprocess.run(["espeak", "-w", wav_path, "-s", "180", text], check=True, capture_output=True, timeout=30)
            except (FileNotFoundError, subprocess.CalledProcessError):
                logger.warning("espeak-ng/espeak not available for TTS")
                return b""
        # Convert to 16 kHz mono raw PCM
        try:
            out = subprocess.run(
                ["sox", wav_path, "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw", "-"],
                check=True,
                capture_output=True,
                timeout=15,
            )
            return out.stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                out = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", wav_path,
                        "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-"
                    ],
                    check=True,
                    capture_output=True,
                    timeout=15,
                )
                return out.stdout
            except (FileNotFoundError, subprocess.CalledProcessError):
                logger.warning("sox/ffmpeg not available for TTS resample")
                return b""
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
    return b""


def _pcm_to_opus_frames(pcm_bytes: bytes, encoder) -> list:
    """Chunk PCM into 60 ms frames and encode to Opus. Returns list of bytes (each frame)."""
    if not pcm_bytes or not encoder:
        return []
    frames = []
    n = 0
    while n + FRAME_BYTES <= len(pcm_bytes):
        chunk = pcm_bytes[n : n + FRAME_BYTES]
        n += FRAME_BYTES
        try:
            opus_frame = encoder.encode(chunk, FRAME_SAMPLES)
            frames.append(opus_frame)
        except Exception as e:
            logger.debug("opus encode skip: %s", e)
    return frames


def _run_asr_only(pcm_bytes: bytes, recognizer) -> str:
    """Run ASR on PCM; return transcript or empty string. No LLM."""
    if not pcm_bytes or len(pcm_bytes) < 16000:  # need at least ~0.5 s
        return ""
    try:
        audio = sr.AudioData(pcm_bytes, SAMPLE_RATE, 2)
        text = recognizer.recognize_google(audio, language="en-US")
        return (text or "").strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        logger.warning("ASR request error: %s", e)
        return ""
    except Exception as e:
        logger.warning("ASR error: %s", e)
        return ""


@app.websocket("/voice")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()
    frame_count = 0
    pcm_buffer = bytearray()
    loop = asyncio.get_event_loop()
    app = getattr(websocket, "app", None) or websocket.scope.get("app")

    if not _OPUS_AVAILABLE:
        logger.error("opuslib not available; cannot decode/encode Opus")
        await websocket.close(code=1011, reason="Opus not available")
        return

    # Client hello first — reply immediately so ESP32 sees "connected" faster.
    # Defer heavy init (decoder, encoder, recognizer) until after hello.
    try:
        msg = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        if msg and msg.strip().startswith("{"):
            await websocket.send_text(json.dumps(HELLO_RESPONSE))
            logger.info("Voice WS: sent server hello")
    except asyncio.TimeoutError:
        logger.warning("Voice WS: no client hello within 5s")

    # Now create decoder/encoder/recognizer (avoids delaying hello response)
    decoder = Decoder(SAMPLE_RATE, CHANNELS)
    encoder = Encoder(SAMPLE_RATE, CHANNELS, "voip")
    recognizer = sr.Recognizer()

    # Optional: send greeting when integrated with AuraBot (voice capture on ESP32)
    bot = getattr(app.state, "aurabot", None) if app else None
    if bot is not None and getattr(bot, "greeting", None):
        def do_greeting_tts():
            pcm = _tts_to_pcm_16k(bot.greeting)
            return _pcm_to_opus_frames(pcm, encoder)
        try:
            opus_frames = await loop.run_in_executor(_executor, do_greeting_tts)
            if opus_frames:
                await websocket.send_text(TTS_START_MSG)
                for opus_frame in opus_frames:
                    await websocket.send_bytes(opus_frame)
                    await asyncio.sleep(FRAME_MS / 1000.0)
                await websocket.send_text(TTS_END_MSG)
        except Exception as e:
            logger.debug("Greeting TTS send: %s", e)

    # Queue for TTS: main loop enqueues; drain task sends tts_start + frames + tts_end
    pending_tts_queue = asyncio.Queue()
    drain_task = None
    exit_requested = False

    async def drain_tts_queue():
        """Background task: send queued TTS Opus frames to ESP32.
        Wraps each batch with tts_start/tts_end so ESP32 alternates: LISTEN (sends mic)
        vs SPEAK (plays TTS, ignores mic). Avoids congestion on the wire."""
        while True:
            try:
                frames = await pending_tts_queue.get()
                if not frames:
                    continue
                try:
                    await websocket.send_text(TTS_START_MSG)
                except Exception:
                    return
                for opus_frame in frames:
                    try:
                        await websocket.send_bytes(opus_frame)
                        await asyncio.sleep(FRAME_MS / 1000.0)
                    except Exception:
                        return
                try:
                    await websocket.send_text(TTS_END_MSG)
                except Exception:
                    return
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("drain_tts_queue: %s", e)

    try:
        drain_task = asyncio.create_task(drain_tts_queue())

        while True:
            data = await websocket.receive_bytes()
            frame_count += 1
            if frame_count <= 20 or frame_count % 100 == 0:
                logger.debug("Voice WS: Opus frame #%d, %d bytes", frame_count, len(data))

            # Decode Opus → PCM
            try:
                pcm_chunk = decoder.decode(data, FRAME_SAMPLES, False)
                pcm_buffer.extend(pcm_chunk)
            except Exception as e:
                logger.debug("opus decode error: %s", e)
                continue

            # When we have enough PCM, run ASR then AuraBot response (or echo) via TTS
            if len(pcm_buffer) >= UTTERANCE_BYTES:
                to_process = bytes(pcm_buffer[:UTTERANCE_BYTES])
                pcm_buffer = pcm_buffer[UTTERANCE_BYTES:]  # keep remainder

                transcript = await loop.run_in_executor(
                    _executor,
                    _run_asr_only,
                    to_process,
                    recognizer,
                )
                if transcript:
                    logger.info("STT: %s", transcript)

                # Use AuraBot when integrated (app.state.aurabot); otherwise echo
                bot = getattr(app.state, "aurabot", None) if app else None
                if bot is not None and transcript:
                    response_text, should_exit = bot.response_handler.get_response(transcript.lower())
                    try:
                        bot.logger.log_event(transcript, response_text)
                    except Exception as e:
                        logger.debug("log_event: %s", e)
                    if should_exit:
                        response_text = response_text or "Goodbye."
                        exit_requested = True
                        # Send goodbye TTS then break (close this connection)
                        def do_exit_tts():
                            pcm = _tts_to_pcm_16k(response_text)
                            return _pcm_to_opus_frames(pcm, encoder)
                        opus_frames = await loop.run_in_executor(_executor, do_exit_tts)
                        if opus_frames:
                            await websocket.send_text(TTS_START_MSG)
                            for opus_frame in opus_frames:
                                await websocket.send_bytes(opus_frame)
                                await asyncio.sleep(FRAME_MS / 1000.0)
                            await websocket.send_text(TTS_END_MSG)
                        break
                else:
                    response_text = f"You said: {transcript}" if transcript else ""

                if not response_text:
                    continue

                # TTS → PCM → Opus; queue for drain task to send
                def do_tts():
                    pcm = _tts_to_pcm_16k(response_text)
                    return _pcm_to_opus_frames(pcm, encoder)

                opus_frames = await loop.run_in_executor(_executor, do_tts)
                if opus_frames:
                    pending_tts_queue.put_nowait(opus_frames)

    except WebSocketDisconnect:
        logger.info("Voice WS: client disconnected after %d frames", frame_count)
    except Exception as e:
        logger.exception("Voice WS: error after %d frames: %s", frame_count, e)
    finally:
        if drain_task is not None:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
        # Do not call __del__ on decoder/encoder - GC will run destructors; manual call causes double-free in C/opus.

    # User requested exit: run bot shutdown then exit process so the app stops cleanly.
    if exit_requested and bot is not None:
        try:
            bot._shutdown()
        except Exception as e:
            logger.warning("Shutdown error: %s", e)
        await asyncio.sleep(0.5)  # allow WebSocket to flush
        os._exit(0)  # exit process from async task; sys.exit would only raise in this task


def run_voice_server(host: str = "0.0.0.0", port: int = 8765):
    import uvicorn
    from dashboard_api import UVICORN_LOG_CONFIG_NO_ACCESS
    uvicorn.run(
        app, host=host, port=port, log_level="info",
        access_log=False, log_config=UVICORN_LOG_CONFIG_NO_ACCESS,
    )


if __name__ == "__main__":
    run_voice_server()
