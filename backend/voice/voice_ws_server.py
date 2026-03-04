"""
Voice WebSocket server for Pi5: receive Opus from ESP32 → decode → ASR → AuraBot response → TTS Opus → ESP32.

TTS strategy:
- When ESP32 is connected (voice WebSocket up): use online TTS (gTTS) and send audio frames over the WebSocket.
- When the Pi5–ESP32 connection is down: TTS is sent as text via MQTT (aurabot/tts/speak) so the ESP32 can
  speak using offline TTS (picoTTS) when it reconnects. Offline TTS (espeak) is only used as fallback if
  online TTS fails (e.g. no network) or when the voice client is disconnected.

When integrated with AuraBot (app.state.aurabot set), transcripts are passed to the bot's response handler
(LLM, timers, exit) and the reply is sent as TTS over the same WebSocket. Otherwise echoes "You said: ...".

Matches the protocol in esp32/main/voice_session.c:
- ESP32 connects to ws://<host>:8765/voice
- Client sends text hello; server replies with {"type":"hello",...}
- ESP32 sends binary Opus frames (60 ms, 16 kHz mono) only in LISTEN phase.
- Alternation (no congestion): LISTEN = ESP32 sends mic, ignores TTS; SPEAK = ESP32 plays TTS, does not send mic.
- Server sends {"type":"tts_start"} before each TTS burst, then binary Opus frames, then {"type":"tts_end"}.

TTS pipeline aligned with xiaozhi-esp32 audio approach (github.com/78/xiaozhi-esp32/main/audio): Opus application
"audio" + 64 kbps for playback quality; espeak-ng with softer params; PCM pad and decoder reset on burst start on ESP32.

Run standalone: python voice_ws_server.py
Integrated: sim_loop sets app.state.aurabot and runs this server (ESP32 voice capture).
"""

import asyncio
import json
import logging
import os
import queue
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

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
# TTS Opus defaults tuned for ESP32 WebSocket playback stability.
# 64 kbps is a good quality/bandwidth balance for 16 kHz mono speech.
OPUS_TTS_BITRATE = int(os.environ.get("VOICE_OPUS_TTS_BITRATE", "64000"))
OPUS_TTS_VBR = os.environ.get("VOICE_OPUS_TTS_VBR", "false").lower() in ("1", "true", "yes")
OPUS_TTS_INBAND_FEC = os.environ.get("VOICE_OPUS_TTS_INBAND_FEC", "true").lower() in ("1", "true", "yes")
OPUS_TTS_PACKET_LOSS_PERC = int(os.environ.get("VOICE_OPUS_TTS_PACKET_LOSS_PERC", "10"))
OPUS_TTS_COMPLEXITY = int(os.environ.get("VOICE_OPUS_TTS_COMPLEXITY", "5"))
# TTS send pacing:
# - send first N frames immediately (prebuffer on ESP32)
# - then pace near frame rate to avoid decoder queue overflow on long responses
TTS_PREFILL_FRAMES = int(os.environ.get("VOICE_TTS_PREFILL_FRAMES", "3"))
TTS_ADAPTIVE_PREFILL = os.environ.get("VOICE_TTS_ADAPTIVE_PREFILL", "true").lower() in ("1", "true", "yes")
TTS_PREFILL_SHORT_FRAMES = int(os.environ.get("VOICE_TTS_PREFILL_SHORT_FRAMES", "4"))
TTS_PREFILL_LONG_FRAMES = int(os.environ.get("VOICE_TTS_PREFILL_LONG_FRAMES", "3"))
TTS_LONG_TTS_FRAME_THRESHOLD = int(os.environ.get("VOICE_TTS_LONG_TTS_FRAME_THRESHOLD", "16"))
# Slightly faster-than-realtime pacing keeps a small cushion against scheduler/network jitter.
_RAW_TTS_SEND_INTERVAL_MS = float(os.environ.get("VOICE_TTS_SEND_INTERVAL_MS", "55"))
_TTS_MIN_SEND_INTERVAL_MS = float(os.environ.get("VOICE_TTS_MIN_SEND_INTERVAL_MS", "35"))
_TTS_MAX_SEND_INTERVAL_MS = float(os.environ.get("VOICE_TTS_MAX_SEND_INTERVAL_MS", "90"))
if _RAW_TTS_SEND_INTERVAL_MS <= 0:
    TTS_SEND_INTERVAL_MS = 0.0
else:
    TTS_SEND_INTERVAL_MS = max(
        _TTS_MIN_SEND_INTERVAL_MS,
        min(_RAW_TTS_SEND_INTERVAL_MS, _TTS_MAX_SEND_INTERVAL_MS),
    )
    if TTS_SEND_INTERVAL_MS != _RAW_TTS_SEND_INTERVAL_MS:
        logger.warning(
            "VOICE_TTS_SEND_INTERVAL_MS=%s clamped to %.1f ms (range %.1f..%.1f)",
            _RAW_TTS_SEND_INTERVAL_MS,
            TTS_SEND_INTERVAL_MS,
            _TTS_MIN_SEND_INTERVAL_MS,
            _TTS_MAX_SEND_INTERVAL_MS,
        )
# Gentle gain for TTS PCM (1.0 = no change). Slightly < 1 reduces sharp/harsh output.
TTS_PCM_GAIN = float(os.environ.get("VOICE_TTS_PCM_GAIN", "0.90"))
# Optional: log per-turn pipeline latency. Set to "1" or path to enable (default: backend/logs/voice_pipeline_latency.log).
# Read this at runtime (not import time) so .env load order cannot disable logging accidentally.
def _get_voice_latency_log_setting() -> str:
    return os.environ.get("VOICE_LATENCY_LOG", "")

# When run from backend/voice/voice_ws_server.py, backend root is parent of voice
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_BACKEND_DIR, "logs")
_DEFAULT_LATENCY_LOG_PATH = os.path.join(_LOGS_DIR, "voice_pipeline_latency.log")


def _write_voice_latency_log(stt_ms: float, response_ms: float, tts_ms: float, total_ms: float, transcript: str = ""):
    """Append one pipeline turn to the latency log when VOICE_LATENCY_LOG is set."""
    voice_latency_log = _get_voice_latency_log_setting()
    if not voice_latency_log:
        return
    log_path = _DEFAULT_LATENCY_LOG_PATH if voice_latency_log.lower() in ("1", "true", "yes") else voice_latency_log
    if os.path.isabs(log_path):
        path = log_path
    else:
        path = os.path.join(_BACKEND_DIR, log_path)
    try:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        line = (
            f"[{ts}] pipeline_latency stt_ms={stt_ms:.0f} response_ms={response_ms:.0f} "
            f"tts_ms={tts_ms:.0f} total_ms={total_ms:.0f} transcript={transcript!r}\n"
        )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.debug("Could not write voice latency log: %s", e)

# Optional: opuslib for decode/encode (requires libopus: sudo apt-get install libopus0 libopus-dev)
try:
    from opuslib import Decoder, Encoder
    _OPUS_AVAILABLE = True
except Exception as e:
    logger.warning("opuslib not available (install libopus-dev and opuslib): %s", e)
    _OPUS_AVAILABLE = False

from backend.voice.stt import STT
from backend.voice.tts import TTS

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

# Voice WebSocket connection state and TTS text queue (for timer/wellness → online TTS)
_voice_client_connected = False
_voice_client_connected_lock = threading.Lock()
_pending_text_tts_queue = None


def _get_pending_text_tts_queue():
    """Lazy-init thread-safe queue for TTS text from external callers (timer, wellness)."""
    global _pending_text_tts_queue
    if _pending_text_tts_queue is None:
        _pending_text_tts_queue = queue.Queue()
    return _pending_text_tts_queue


def is_voice_client_connected() -> bool:
    """True when an ESP32 is connected via the voice WebSocket."""
    with _voice_client_connected_lock:
        return _voice_client_connected


def enqueue_tts_text(text: str) -> bool:
    """Queue text for online TTS over the voice WebSocket. Returns True if queued, False if no client."""
    if not text or not text.strip():
        return False
    with _voice_client_connected_lock:
        if not _voice_client_connected:
            return False
    _get_pending_text_tts_queue().put_nowait(text)
    return True


def _apply_tts_gain(pcm_bytes: bytes, gain: float) -> bytes:
    """Apply linear gain to 16-bit mono PCM. Clips to int16 range."""
    if not pcm_bytes or gain == 1.0:
        return pcm_bytes
    samples = list(struct.unpack(f"<{len(pcm_bytes) // 2}h", pcm_bytes))
    scaled = [max(-32768, min(32767, int(s * gain))) for s in samples]
    return struct.pack(f"<{len(scaled)}h", *scaled)


def _build_tts_encoder():
    """Build a dedicated Opus encoder for one TTS job."""
    encoder = Encoder(SAMPLE_RATE, CHANNELS, "audio")
    try:
        encoder.bitrate = OPUS_TTS_BITRATE
    except Exception:
        pass
    try:
        encoder.vbr = OPUS_TTS_VBR
    except Exception:
        pass
    try:
        encoder.inband_fec = OPUS_TTS_INBAND_FEC
    except Exception:
        pass
    try:
        encoder.packet_loss_perc = OPUS_TTS_PACKET_LOSS_PERC
    except Exception:
        pass
    try:
        encoder.complexity = OPUS_TTS_COMPLEXITY
    except Exception:
        pass
    return encoder


def _pcm_to_opus_frames(pcm_bytes: bytes) -> list:
    """Chunk PCM into 60 ms frames and encode to Opus. Returns list of bytes (each frame).
    Pads trailing bytes with silence so the last frame is complete (avoids abrupt cut)."""
    if not pcm_bytes:
        return []
    encoder = _build_tts_encoder()
    pcm_bytes = _apply_tts_gain(pcm_bytes, TTS_PCM_GAIN)
    # Pad to a multiple of FRAME_BYTES so we don't drop the tail (avoids harsh cut at end)
    remainder = len(pcm_bytes) % FRAME_BYTES
    if remainder:
        pcm_bytes = pcm_bytes + (b"\x00" * (FRAME_BYTES - remainder))
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


async def _send_tts_frames(websocket: WebSocket, frames: list):
    """Send Opus frames with prebuffer+paced strategy for smooth ESP32 playback."""
    pace_sec = TTS_SEND_INTERVAL_MS / 1000.0 if TTS_SEND_INTERVAL_MS > 0 else 0.0
    if TTS_ADAPTIVE_PREFILL:
        # Short replies can tolerate extra prefill to reduce startup jitter; long replies should prefill less.
        prefill_frames = TTS_PREFILL_LONG_FRAMES if len(frames) >= TTS_LONG_TTS_FRAME_THRESHOLD else TTS_PREFILL_SHORT_FRAMES
    else:
        prefill_frames = TTS_PREFILL_FRAMES
    prefill_frames = max(0, prefill_frames)
    loop = asyncio.get_running_loop()
    pacing_origin = loop.time()
    for i, opus_frame in enumerate(frames):
        # Prebuffer initial frames immediately; then use absolute-time pacing to reduce jitter drift.
        if pace_sec > 0 and i >= prefill_frames:
            scheduled_at = pacing_origin + ((i - prefill_frames) * pace_sec)
            now = loop.time()
            sleep_for = scheduled_at - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        await websocket.send_bytes(opus_frame)


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

    # Now create decoder/STT/TTS (avoids delaying hello response)
    decoder = Decoder(SAMPLE_RATE, CHANNELS)
    stt = STT()
    tts_engine = TTS()

    # Optional: send greeting when integrated with AuraBot (voice capture on ESP32)
    bot = getattr(app.state, "aurabot", None) if app else None
    if bot is not None and getattr(bot, "greeting", None):
        def do_greeting_tts():
            pcm = tts_engine.synthesize_pcm(bot.greeting)
            return _pcm_to_opus_frames(pcm)
        try:
            opus_frames = await loop.run_in_executor(_executor, do_greeting_tts)
            if opus_frames:
                await websocket.send_text(TTS_START_MSG)
                await _send_tts_frames(websocket, opus_frames)
                await websocket.send_text(TTS_END_MSG)
        except Exception as e:
            logger.debug("Greeting TTS send: %s", e)

    # Queue for TTS: main loop enqueues; drain task sends tts_start + frames + tts_end
    pending_tts_queue = asyncio.Queue()
    drain_task = None
    drain_text_task = None
    exit_requested = False

    def set_voice_connected(connected: bool):
        global _voice_client_connected
        with _voice_client_connected_lock:
            _voice_client_connected = connected

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
                try:
                    await _send_tts_frames(websocket, frames)
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

    async def drain_text_tts_queue():
        """Drain thread-safe text TTS queue (timer/wellness): online TTS → Opus → pending_tts_queue."""
        text_queue = _get_pending_text_tts_queue()
        while True:
            try:
                try:
                    text = text_queue.get_nowait()
                except queue.Empty:
                    text = None
                if not text:
                    await asyncio.sleep(0.2)
                    continue
                def do_online_tts():
                    pcm = tts_engine.synthesize_pcm(text)
                    return _pcm_to_opus_frames(pcm) if pcm else []
                opus_frames = await loop.run_in_executor(_executor, do_online_tts)
                if opus_frames:
                    pending_tts_queue.put_nowait(opus_frames)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("drain_text_tts_queue: %s", e)

    try:
        set_voice_connected(True)
        drain_task = asyncio.create_task(drain_tts_queue())
        drain_text_task = asyncio.create_task(drain_text_tts_queue())

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

                t0 = time.perf_counter()
                transcript = await loop.run_in_executor(
                    _executor,
                    stt.transcribe,
                    to_process,
                )
                t1 = time.perf_counter()
                stt_ms = (t1 - t0) * 1000.0
                if transcript:
                    logger.info("STT: %s", transcript)

                # Use AuraBot when integrated (app.state.aurabot); otherwise echo
                bot = getattr(app.state, "aurabot", None) if app else None
                if bot is not None and transcript:
                    response_text, should_exit = bot.response_handler.get_response(transcript.lower())
                    t2 = time.perf_counter()
                    response_ms = (t2 - t1) * 1000.0
                    try:
                        bot.logger.log_event(transcript, response_text)
                    except Exception as e:
                        logger.debug("log_event: %s", e)
                    if should_exit:
                        response_text = response_text or "Goodbye."
                        exit_requested = True
                        # Send goodbye TTS then break (close this connection)
                        def do_exit_tts():
                            pcm = tts_engine.synthesize_pcm(response_text)
                            return _pcm_to_opus_frames(pcm)
                        opus_frames = await loop.run_in_executor(_executor, do_exit_tts)
                        if opus_frames:
                            await websocket.send_text(TTS_START_MSG)
                            await _send_tts_frames(websocket, opus_frames)
                            await websocket.send_text(TTS_END_MSG)
                        break
                else:
                    t2 = time.perf_counter()
                    response_ms = (t2 - t1) * 1000.0
                    response_text = f"You said: {transcript}" if transcript else ""

                if not response_text:
                    continue

                # TTS → PCM → Opus; queue for drain task to send
                def do_tts():
                    pcm = tts_engine.synthesize_pcm(response_text)
                    return _pcm_to_opus_frames(pcm)

                opus_frames = await loop.run_in_executor(_executor, do_tts)
                t3 = time.perf_counter()
                tts_ms = (t3 - t2) * 1000.0
                total_ms = (t3 - t0) * 1000.0
                logger.info(
                    "Voice pipeline latency: stt=%.0f ms response=%.0f ms tts=%.0f ms total=%.0f ms",
                    stt_ms, response_ms, tts_ms, total_ms,
                )
                _write_voice_latency_log(stt_ms, response_ms, tts_ms, total_ms, transcript or "")
                if opus_frames:
                    pending_tts_queue.put_nowait(opus_frames)

    except WebSocketDisconnect:
        logger.info("Voice WS: client disconnected after %d frames", frame_count)
    except Exception as e:
        logger.exception("Voice WS: error after %d frames: %s", frame_count, e)
    finally:
        set_voice_connected(False)
        if drain_text_task is not None:
            drain_text_task.cancel()
            try:
                await drain_text_task
            except asyncio.CancelledError:
                pass
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
    from backend.api.dashboard_api import UVICORN_LOG_CONFIG_NO_ACCESS
    uvicorn.run(
        app, host=host, port=port, log_level="info",
        access_log=False, log_config=UVICORN_LOG_CONFIG_NO_ACCESS,
    )


if __name__ == "__main__":
    run_voice_server()
