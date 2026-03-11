"""
Helpers for speech-turn assembly on the voice websocket path.

The ESP32 client now sends VAD control messages, but the backend still needs a
small amount of state to:
- accumulate decoded PCM for the current user turn
- flush the turn immediately on VAD silence / explicit turn_end
- fall back to idle timeouts if control messages are delayed or lost
"""

from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class TurnFlush:
    pcm: bytes
    reason: str
    duration_ms: int


class VoiceTurnAssembler:
    """Collect PCM for one user turn and flush it on VAD/end-of-turn signals."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        sample_width: int = 2,
        min_turn_ms: int = 150,
        idle_commit_ms: int = 850,
        stalled_speech_ms: int = 1100,
        max_turn_ms: int = 12000,
    ) -> None:
        self._bytes_per_second = sample_rate * sample_width
        self._min_turn_bytes = max(1, int(self._bytes_per_second * (min_turn_ms / 1000.0)))
        self._idle_commit_sec = max(0.1, idle_commit_ms / 1000.0)
        self._stalled_speech_sec = max(0.1, stalled_speech_ms / 1000.0)
        self._max_turn_bytes = max(self._min_turn_bytes, int(self._bytes_per_second * (max_turn_ms / 1000.0)))

        self._buffer = bytearray()
        self._speech_active = False
        self._processing = False
        self._turn_started_at = None
        self._last_audio_at = None
        self._last_vad_at = None

    @property
    def buffered_ms(self) -> int:
        if not self._buffer:
            return 0
        return int((len(self._buffer) * 1000) / self._bytes_per_second)

    @property
    def vad_state(self) -> str:
        return "speech" if self._speech_active else "silence"

    @property
    def turn_state(self) -> str:
        if self._processing:
            return "processing"
        if self._speech_active:
            return "speech"
        if self._buffer:
            return "buffered"
        return "idle"

    def snapshot(self) -> dict:
        return {
            "vad_state": self.vad_state,
            "turn_state": self.turn_state,
            "buffered_ms": self.buffered_ms,
            "buffered_bytes": len(self._buffer),
        }

    def set_processing(self, processing: bool) -> None:
        self._processing = bool(processing)

    def reset(self) -> None:
        self._buffer.clear()
        self._speech_active = False
        self._processing = False
        self._turn_started_at = None
        self._last_audio_at = None
        self._last_vad_at = None

    def append_pcm(self, pcm_bytes: bytes) -> TurnFlush | None:
        if not pcm_bytes:
            return None

        now = time.monotonic()
        if self._turn_started_at is None:
            self._turn_started_at = now
        self._last_audio_at = now
        self._buffer.extend(pcm_bytes)

        if len(self._buffer) >= self._max_turn_bytes:
            return self._flush("max_turn", allow_short=True)
        return None

    def note_vad(self, state: str) -> TurnFlush | None:
        now = time.monotonic()
        normalized = "speech" if str(state).lower() == "speech" else "silence"
        self._last_vad_at = now

        if normalized == "speech":
            self._speech_active = True
            if self._turn_started_at is None:
                self._turn_started_at = now
            return None

        self._speech_active = False
        return None

    def commit_turn(self, reason: str = "turn_end") -> TurnFlush | None:
        self._speech_active = False
        return self._flush(reason, allow_short=True)

    def maybe_flush_timeout(self) -> TurnFlush | None:
        if not self._buffer:
            return None

        now = time.monotonic()
        if self._speech_active:
            if self._last_audio_at is not None and (now - self._last_audio_at) >= self._stalled_speech_sec:
                self._speech_active = False
                return self._flush("stalled_speech")
            return None

        if self._last_audio_at is not None and (now - self._last_audio_at) >= self._idle_commit_sec:
            return self._flush("idle_timeout")
        return None

    def _flush(self, reason: str, *, allow_short: bool = False) -> TurnFlush | None:
        if not self._buffer:
            return None

        pcm = bytes(self._buffer)
        duration_ms = int((len(pcm) * 1000) / self._bytes_per_second)

        self._buffer.clear()
        self._turn_started_at = None
        self._last_audio_at = None

        if not allow_short and len(pcm) < self._min_turn_bytes:
            return None

        return TurnFlush(pcm=pcm, reason=reason, duration_ms=duration_ms)
