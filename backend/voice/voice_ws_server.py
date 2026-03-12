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
- Server sends {"type":"ready","phase":"listen"} only after STT/TTS init is complete.
- ESP32 sends binary Opus frames (60 ms, 16 kHz mono) only in LISTEN phase.
- ESP32 sends {"type":"vad","state":"speech|silence"} and {"type":"turn_end"} control frames
  so the backend can flush turns on real speech boundaries instead of fixed windows.
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
VOICE_WS_GREETING_ON_CONNECT = os.environ.get("VOICE_WS_GREETING_ON_CONNECT", "false").lower() in ("1", "true", "yes")
VOICE_TURN_MIN_MS = int(os.environ.get("VOICE_TURN_MIN_MS", "500"))
VOICE_TURN_IDLE_COMMIT_MS = int(os.environ.get("VOICE_TURN_IDLE_COMMIT_MS", "850"))
VOICE_TURN_STALLED_SPEECH_MS = int(os.environ.get("VOICE_TURN_STALLED_SPEECH_MS", "1100"))
VOICE_TURN_MAX_MS = int(os.environ.get("VOICE_TURN_MAX_MS", "12000"))
VOICE_RX_POLL_MS = int(os.environ.get("VOICE_RX_POLL_MS", "250"))
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
from backend.voice.turn_endpoint import VoiceTurnAssembler
from backend.voice.tts import TTS

app = FastAPI(title="AuraBot Voice WS", version="0.2.0")

HELLO_RESPONSE = {
    "type": "hello",
    "version": 1,
    "transport": "websocket",
}

# Phase control for ESP32 listen/speak alternation (voice_session.c):
# tts_start → ESP32 enters SPEAK (stops sending mic, plays TTS); tts_end → back to LISTEN.
def _build_ready_msg(phase: str = "listen") -> str:
    return json.dumps({"type": "ready", "phase": phase})


TTS_START_MSG = json.dumps({"type": "tts_start"})
TTS_END_MSG = json.dumps({"type": "tts_end"})

# Thread pool for blocking ASR/TTS
_executor = ThreadPoolExecutor(max_workers=2)

# Voice WebSocket connection state and TTS text queue (for timer/wellness → online TTS)
_voice_client_state = {
    "connected": False,
    "ready": False,
    "phase": "disconnected",
    "vad_state": "silence",
    "turn_state": "idle",
    "buffered_ms": 0,
    "updated_at": None,
}
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
        return bool(_voice_client_state["connected"])


def _derive_voice_client_state(snapshot: dict) -> str:
    if not snapshot.get("connected"):
        return "disconnected"
    if not snapshot.get("ready"):
        return "connecting"
    phase = str(snapshot.get("phase") or "listen").lower()
    if phase == "speak":
        return "speaking"
    return "listening"


def get_voice_client_status() -> dict:
    """Return a snapshot of the current voice WebSocket session state."""
    with _voice_client_connected_lock:
        snapshot = dict(_voice_client_state)
    updated_at = snapshot.get("updated_at")
    snapshot["state"] = _derive_voice_client_state(snapshot)
    snapshot["age_seconds"] = None if updated_at is None else max(0.0, time.time() - updated_at)
    return snapshot


def _set_voice_client_state(*, connected=None, ready=None, phase=None, vad_state=None, turn_state=None, buffered_ms=None) -> None:
    with _voice_client_connected_lock:
        if connected is not None:
            _voice_client_state["connected"] = bool(connected)
        if ready is not None:
            _voice_client_state["ready"] = bool(ready)
        if phase is not None:
            _voice_client_state["phase"] = phase
        if vad_state is not None:
            _voice_client_state["vad_state"] = str(vad_state).lower()
        if turn_state is not None:
            _voice_client_state["turn_state"] = str(turn_state).lower()
        if buffered_ms is not None:
            _voice_client_state["buffered_ms"] = max(0, int(buffered_ms))
        if not _voice_client_state["connected"]:
            _voice_client_state["ready"] = False
            _voice_client_state["phase"] = "disconnected"
            _voice_client_state["vad_state"] = "silence"
            _voice_client_state["turn_state"] = "idle"
            _voice_client_state["buffered_ms"] = 0
        elif not _voice_client_state["ready"] and phase is None:
            _voice_client_state["phase"] = "connecting"
        _voice_client_state["updated_at"] = time.time()


def enqueue_tts_text(text: str) -> bool:
    """Queue text for online TTS over the voice WebSocket. Returns True if queued, False if no client."""
    if not text or not text.strip():
        return False
    with _voice_client_connected_lock:
        if not _voice_client_state["connected"]:
            return False
    _get_pending_text_tts_queue().put_nowait(text)
    return True


try:
    import numpy as np
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False


def _build_tts_encoder():
    """Build a dedicated Opus encoder for TTS."""
    encoder = Encoder(SAMPLE_RATE, CHANNELS, "audio")
    for attr, val in (
        ("bitrate", OPUS_TTS_BITRATE),
        ("vbr", OPUS_TTS_VBR),
        ("inband_fec", OPUS_TTS_INBAND_FEC),
        ("packet_loss_perc", OPUS_TTS_PACKET_LOSS_PERC),
        ("complexity", OPUS_TTS_COMPLEXITY),
    ):
        try:
            setattr(encoder, attr, val)
        except Exception:
            pass
    return encoder


_tts_encoder: "Encoder | None" = None
_tts_encoder_lock = threading.Lock()


def _get_tts_encoder():
    """Return a shared Opus encoder, creating it once."""
    global _tts_encoder
    if _tts_encoder is None:
        with _tts_encoder_lock:
            if _tts_encoder is None:
                _tts_encoder = _build_tts_encoder()
    return _tts_encoder


def _pcm_to_opus_frames(pcm_bytes: bytes) -> list:
    """Batch encode: PCM → list of Opus frames (used by non-streaming callers)."""
    if not pcm_bytes:
        return []
    encoder = _get_tts_encoder()
    remainder = len(pcm_bytes) % FRAME_BYTES
    if remainder:
        pcm_bytes = pcm_bytes + (b"\x00" * (FRAME_BYTES - remainder))
    if _NP_AVAILABLE:
        pcm_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        frames = []
        for i in range(0, len(pcm_arr), FRAME_SAMPLES):
            chunk = pcm_arr[i : i + FRAME_SAMPLES].tobytes()
            try:
                frames.append(encoder.encode(chunk, FRAME_SAMPLES))
            except Exception as e:
                logger.debug("opus encode skip: %s", e)
        return frames
    frames = []
    n = 0
    while n + FRAME_BYTES <= len(pcm_bytes):
        chunk = pcm_bytes[n : n + FRAME_BYTES]
        n += FRAME_BYTES
        try:
            frames.append(encoder.encode(chunk, FRAME_SAMPLES))
        except Exception as e:
            logger.debug("opus encode skip: %s", e)
    return frames


# Sentinel value pushed to the streaming queue to signal "encoding done".
_STREAM_END = object()


def _pcm_to_opus_stream(pcm_bytes: bytes, out_queue: queue.Queue) -> int:
    """Encode PCM to Opus and push each frame to *out_queue* immediately.

    The caller (async drain loop) can start sending frames to the ESP32 while
    encoding continues.  Pushes ``_STREAM_END`` when finished.

    Returns the total number of frames produced.
    """
    if not pcm_bytes:
        out_queue.put(_STREAM_END)
        return 0
    encoder = _get_tts_encoder()
    remainder = len(pcm_bytes) % FRAME_BYTES
    if remainder:
        pcm_bytes = pcm_bytes + (b"\x00" * (FRAME_BYTES - remainder))
    count = 0
    if _NP_AVAILABLE:
        pcm_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        for i in range(0, len(pcm_arr), FRAME_SAMPLES):
            chunk = pcm_arr[i : i + FRAME_SAMPLES].tobytes()
            try:
                out_queue.put(encoder.encode(chunk, FRAME_SAMPLES))
                count += 1
            except Exception as e:
                logger.debug("opus encode skip: %s", e)
    else:
        n = 0
        while n + FRAME_BYTES <= len(pcm_bytes):
            chunk = pcm_bytes[n : n + FRAME_BYTES]
            n += FRAME_BYTES
            try:
                out_queue.put(encoder.encode(chunk, FRAME_SAMPLES))
                count += 1
            except Exception as e:
                logger.debug("opus encode skip: %s", e)
    out_queue.put(_STREAM_END)
    return count


async def _send_tts_frames(websocket: WebSocket, frames: list):
    """Send a pre-built list of Opus frames with prebuffer+paced strategy."""
    pace_sec = TTS_SEND_INTERVAL_MS / 1000.0 if TTS_SEND_INTERVAL_MS > 0 else 0.0
    if TTS_ADAPTIVE_PREFILL:
        prefill_frames = TTS_PREFILL_LONG_FRAMES if len(frames) >= TTS_LONG_TTS_FRAME_THRESHOLD else TTS_PREFILL_SHORT_FRAMES
    else:
        prefill_frames = TTS_PREFILL_FRAMES
    prefill_frames = max(0, prefill_frames)
    loop = asyncio.get_running_loop()
    pacing_origin = loop.time()
    for i, opus_frame in enumerate(frames):
        if pace_sec > 0 and i >= prefill_frames:
            scheduled_at = pacing_origin + ((i - prefill_frames) * pace_sec)
            sleep_for = scheduled_at - loop.time()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        await websocket.send_bytes(opus_frame)


def _drain_frame_queue(frame_queue: queue.Queue):
    """Discard remaining frames so the encoder thread can finish."""
    while True:
        try:
            item = frame_queue.get_nowait()
            if item is _STREAM_END:
                break
        except queue.Empty:
            break


async def _send_tts_frames_streaming(
    websocket: WebSocket,
    frame_queue: queue.Queue,
    poll_interval: float = 0.005,
    on_first_frame=None,
):
    """Send Opus frames from a thread-safe queue as they arrive (streaming).

    Pacing origin is set when the *first post-prefill* frame is sent, not when
    polling starts.  This avoids a burst of catch-up frames after TTS synthesis
    latency, which would overflow the ESP32 playback buffer.

    *on_first_frame*: optional async callable invoked once, right before the
    first Opus frame is sent.  Used to defer ``tts_start`` until audio is
    actually ready (avoids a multi-second gap when using online TTS).
    """
    pace_sec = TTS_SEND_INTERVAL_MS / 1000.0 if TTS_SEND_INTERVAL_MS > 0 else 0.0
    prefill = max(0, TTS_PREFILL_SHORT_FRAMES if TTS_ADAPTIVE_PREFILL else TTS_PREFILL_FRAMES)
    loop = asyncio.get_running_loop()
    pacing_origin: float | None = None
    idx = 0
    try:
        while True:
            try:
                frame = frame_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(poll_interval)
                continue
            if frame is _STREAM_END:
                break
            if idx == 0 and on_first_frame is not None:
                await on_first_frame()
            if pace_sec > 0 and idx >= prefill:
                if pacing_origin is None:
                    pacing_origin = loop.time()
                scheduled_at = pacing_origin + ((idx - prefill) * pace_sec)
                sleep_for = scheduled_at - loop.time()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            await websocket.send_bytes(frame)
            idx += 1
    except (WebSocketDisconnect, Exception):
        _drain_frame_queue(frame_queue)
        raise


@app.websocket("/voice")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()
    frame_count = 0
    client_disconnected = False
    loop = asyncio.get_event_loop()
    app = getattr(websocket, "app", None) or websocket.scope.get("app")
    bot = getattr(app.state, "aurabot", None) if app else None

    if not _OPUS_AVAILABLE:
        logger.error("opuslib not available; cannot decode/encode Opus")
        await websocket.close(code=1011, reason="Opus not available")
        _set_voice_client_state(connected=False, ready=False, phase="disconnected")
        return

    _set_voice_client_state(
        connected=True,
        ready=False,
        phase="connecting",
        vad_state="silence",
        turn_state="idle",
        buffered_ms=0,
    )

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
    turn_assembler = VoiceTurnAssembler(
        sample_rate=SAMPLE_RATE,
        sample_width=2,
        min_turn_ms=VOICE_TURN_MIN_MS,
        idle_commit_ms=VOICE_TURN_IDLE_COMMIT_MS,
        stalled_speech_ms=VOICE_TURN_STALLED_SPEECH_MS,
        max_turn_ms=VOICE_TURN_MAX_MS,
    )

    # Queue for TTS: main loop enqueues; drain task sends tts_start + frames + tts_end
    pending_tts_queue = asyncio.Queue()
    drain_task = None
    drain_text_task = None
    exit_requested = False
    greeting_frames = []

    def publish_turn_snapshot():
        snapshot = turn_assembler.snapshot()
        _set_voice_client_state(
            vad_state=snapshot["vad_state"],
            turn_state=snapshot["turn_state"],
            buffered_ms=snapshot["buffered_ms"],
        )

    def can_dispatch_external_tts() -> bool:
        status = get_voice_client_status()
        return (
            bool(status.get("connected"))
            and bool(status.get("ready"))
            and str(status.get("phase") or "").lower() == "listen"
            and turn_assembler.turn_state == "idle"
            and pending_tts_queue.empty()
        )

    async def send_tts_batch(frames):
        """Send pre-encoded frames (list) to ESP32."""
        if not frames:
            return
        tts_started = False
        try:
            await websocket.send_text(TTS_START_MSG)
            tts_started = True
            _set_voice_client_state(phase="speak", turn_state="replying", buffered_ms=0)
            await _send_tts_frames(websocket, frames)
            await websocket.send_text(TTS_END_MSG)
        except Exception:
            if tts_started:
                try:
                    await websocket.send_text(TTS_END_MSG)
                except Exception:
                    logger.debug("Voice WS: failed to send tts_end during recovery")
                _set_voice_client_state(phase="listen")
                publish_turn_snapshot()
            try:
                await websocket.close(code=1011, reason="tts_send_failed")
            except Exception:
                pass
            raise
        _set_voice_client_state(phase="listen")
        publish_turn_snapshot()

    async def send_tts_streaming(frame_queue: queue.Queue):
        """Stream-send frames from a queue (concurrent with encoding).

        ``tts_start`` is deferred until the first Opus frame is ready so the
        ESP32 stays in LISTEN mode during online-TTS synthesis (~3-5 s for
        gTTS) instead of sitting in SPEAK mode with no audio.

        On disconnect, drains remaining frames so the encoder thread exits.
        """
        tts_started = False

        async def _on_first_frame():
            nonlocal tts_started
            await websocket.send_text(TTS_START_MSG)
            tts_started = True
            _set_voice_client_state(phase="speak", turn_state="replying", buffered_ms=0)

        try:
            await _send_tts_frames_streaming(
                websocket, frame_queue, on_first_frame=_on_first_frame,
            )
            if tts_started:
                await websocket.send_text(TTS_END_MSG)
        except (WebSocketDisconnect, Exception) as exc:
            _drain_frame_queue(frame_queue)
            if tts_started:
                try:
                    await websocket.send_text(TTS_END_MSG)
                except Exception:
                    pass
                _set_voice_client_state(phase="listen")
                publish_turn_snapshot()
            try:
                await websocket.close(code=1011, reason="tts_send_failed")
            except Exception:
                pass
            raise
        _set_voice_client_state(phase="listen")
        publish_turn_snapshot()

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
                    await send_tts_batch(frames)
                except Exception as e:
                    logger.warning("drain_tts_queue: stopping after TTS send failure: %s", e)
                    return
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("drain_tts_queue: %s", e)

    async def drain_text_tts_queue():
        """Drain thread-safe text TTS queue (timer/wellness): TTS → Opus → pending_tts_queue."""
        text_queue = _get_pending_text_tts_queue()
        pending_text = None
        pending_frames = None

        try:
            while True:
                try:
                    if pending_frames is None:
                        if not can_dispatch_external_tts():
                            await asyncio.sleep(0.1)
                            continue
                        try:
                            pending_text = text_queue.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(0.2)
                            continue
                        if not pending_text or not pending_text.strip():
                            pending_text = None
                            continue

                        text_to_speak = pending_text
                        def do_online_tts():
                            pcm = tts_engine.synthesize_pcm(text_to_speak)
                            return _pcm_to_opus_frames(pcm) if pcm else []

                        pending_frames = await loop.run_in_executor(_executor, do_online_tts)
                        if not pending_frames:
                            logger.warning("drain_text_tts_queue: TTS produced no audio for: %s", pending_text[:80])
                            pending_text = None
                            pending_frames = None
                            continue

                    if not can_dispatch_external_tts():
                        await asyncio.sleep(0.1)
                        continue

                    pending_tts_queue.put_nowait(pending_frames)
                    pending_text = None
                    pending_frames = None
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("drain_text_tts_queue: error (retrying): %s", e)
                    pending_text = None
                    pending_frames = None
                    await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            if pending_text:
                text_queue.put_nowait(pending_text)
            return

    async def process_user_turn(flush) -> bool:
        nonlocal exit_requested

        if not flush or not flush.pcm:
            publish_turn_snapshot()
            return False

        logger.info("Voice turn flush: reason=%s duration=%d ms", flush.reason, flush.duration_ms)
        turn_assembler.set_processing(True)
        publish_turn_snapshot()

        try:
            t0 = time.perf_counter()
            transcript = await loop.run_in_executor(
                _executor,
                stt.transcribe,
                flush.pcm,
            )
            t1 = time.perf_counter()
            stt_ms = (t1 - t0) * 1000.0
            if transcript:
                logger.info("STT: %s", transcript)

            bot_ref = getattr(app.state, "aurabot", None) if app else None
            if bot_ref is not None and transcript:
                response_text, should_exit = bot_ref.response_handler.get_response(transcript.lower())
                t2 = time.perf_counter()
                response_ms = (t2 - t1) * 1000.0
                try:
                    bot_ref.logger.log_event(transcript, response_text)
                except Exception as e:
                    logger.debug("log_event: %s", e)
                if should_exit:
                    response_text = response_text or "Goodbye."
                    exit_requested = True

                    def do_exit_tts():
                        pcm = tts_engine.synthesize_pcm(response_text)
                        return _pcm_to_opus_frames(pcm)

                    opus_frames = await loop.run_in_executor(_executor, do_exit_tts)
                    if opus_frames:
                        await send_tts_batch(opus_frames)
                    return True
            else:
                t2 = time.perf_counter()
                response_ms = (t2 - t1) * 1000.0
                response_text = f"You said: {transcript}" if transcript else ""

            if not response_text:
                return False

            # Streaming TTS: synthesize PCM in a worker thread, then encode
            # Opus frames and push them to a queue.  The send coroutine reads
            # from that queue concurrently so the ESP32 hears audio while
            # encoding continues.
            frame_q: queue.Queue = queue.Queue()

            def do_tts_synth():
                """Worker: synthesize PCM then stream-encode to Opus."""
                pcm = tts_engine.synthesize_pcm(response_text)
                _pcm_to_opus_stream(pcm, frame_q)

            encode_future = loop.run_in_executor(_executor, do_tts_synth)
            send_error = None
            try:
                await send_tts_streaming(frame_q)
            except (WebSocketDisconnect, Exception) as exc:
                send_error = exc
                _drain_frame_queue(frame_q)
            # Always let the encoder thread finish before moving on.
            try:
                await encode_future
            except Exception:
                pass
            if send_error is not None:
                raise send_error

            t3 = time.perf_counter()
            tts_ms = (t3 - t2) * 1000.0
            total_ms = (t3 - t0) * 1000.0
            logger.info(
                "Voice pipeline latency: stt=%.0f ms response=%.0f ms tts=%.0f ms total=%.0f ms",
                stt_ms, response_ms, tts_ms, total_ms,
            )
            _write_voice_latency_log(stt_ms, response_ms, tts_ms, total_ms, transcript or "")
            return False
        finally:
            turn_assembler.reset()
            publish_turn_snapshot()

    try:
        if VOICE_WS_GREETING_ON_CONNECT and bot is not None and getattr(bot, "greeting", None):
            def do_greeting_tts():
                pcm = tts_engine.synthesize_pcm(bot.greeting)
                return _pcm_to_opus_frames(pcm)
            greeting_frames = await loop.run_in_executor(_executor, do_greeting_tts)

        ready_phase = "speak" if greeting_frames else "listen"
        await websocket.send_text(_build_ready_msg(ready_phase))
        _set_voice_client_state(connected=True, ready=True, phase=ready_phase)
        publish_turn_snapshot()

        drain_task = asyncio.create_task(drain_tts_queue())
        drain_text_task = asyncio.create_task(drain_text_tts_queue())

        if greeting_frames:
            await send_tts_batch(greeting_frames)

        receive_poll_sec = max(0.05, VOICE_RX_POLL_MS / 1000.0)
        while True:
            flush = turn_assembler.maybe_flush_timeout()
            if flush and await process_user_turn(flush):
                break

            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=receive_poll_sec)
            except asyncio.TimeoutError:
                continue

            if message.get("type") == "websocket.disconnect":
                client_disconnected = True
                break

            data = message.get("bytes")
            if data is not None:
                frame_count += 1
                if frame_count <= 20 or frame_count % 100 == 0:
                    logger.debug("Voice WS: Opus frame #%d, %d bytes", frame_count, len(data))

                try:
                    pcm_chunk = decoder.decode(data, FRAME_SAMPLES, False)
                except Exception as e:
                    logger.debug("opus decode error: %s", e)
                    continue

                flush = turn_assembler.append_pcm(pcm_chunk)
                publish_turn_snapshot()
                if flush and await process_user_turn(flush):
                    break
                continue

            text = message.get("text")
            if not text:
                continue
            if not text.strip().startswith("{"):
                continue

            try:
                payload = json.loads(text)
            except json.JSONDecodeError as e:
                logger.debug("Voice WS: ignored invalid control frame: %s", e)
                continue

            msg_type = str(payload.get("type") or "").lower()
            if msg_type == "vad":
                flush = turn_assembler.note_vad(str(payload.get("state") or "silence"))
                publish_turn_snapshot()
                if flush and await process_user_turn(flush):
                    break
            elif msg_type == "turn_end":
                flush = turn_assembler.commit_turn(str(payload.get("source") or "turn_end"))
                publish_turn_snapshot()
                if flush and await process_user_turn(flush):
                    break

        if client_disconnected:
            logger.info("Voice WS: client disconnected after %d frames", frame_count)
    except WebSocketDisconnect:
        logger.warning("Voice WS: client disconnected (mid-send) after %d frames", frame_count)
    except Exception as e:
        logger.exception("Voice WS: error after %d frames: %s", frame_count, e)
    finally:
        _set_voice_client_state(connected=False, ready=False, phase="disconnected")
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
