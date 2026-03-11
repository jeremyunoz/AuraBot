/**
 * @file voice_conversation.h
 * @brief Voice conversation: Opus encode/decode, listen/speak phase, TTS playback.
 *
 * Consumes PCM from mic (encode + send when in LISTEN), receives TTS (decode + play when in SPEAK).
 * Depends on voice_ws for connection and send; receives data via voice_ws data callback.
 *
 * Frame duration and buffer sizes are defined here (xiaozhi-style); single source of truth.
 */
#ifndef VOICE_CONVERSATION_H
#define VOICE_CONVERSATION_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------*
 * Opus / frame configuration (single source of truth; cf. xiaozhi-esp32
 * OPUS_FRAME_DURATION_MS, MAX_DECODE_PACKETS = 2400/FRAME_MS, etc.)
 * ---------------------------------------------------------------------------*/

#define VOICE_SAMPLE_RATE           16000
#define VOICE_CHANNELS              1

/** Opus frame duration in ms. Supported: 5, 10, 20, 40, 60, 80, 100, 120 (Opus spec). */
#define VOICE_OPUS_FRAME_DURATION_MS  60

#define VOICE_FRAME_MS              VOICE_OPUS_FRAME_DURATION_MS
#define VOICE_FRAME_SAMPLES          ((VOICE_FRAME_MS * VOICE_SAMPLE_RATE) / 1000)
#define VOICE_FRAME_BYTES            (VOICE_FRAME_SAMPLES * sizeof(int16_t))

/** Max Opus packet size (bytes) for decode queue item. */
#define VOICE_OPUS_MAX_PACKET       1275

/** Duration enum for frame config (cf. xiaozhi AS_OPUS_GET_FRAME_DRU_ENUM). */
typedef enum {
    VOICE_FRAME_DURATION_5_MS   = 5,
    VOICE_FRAME_DURATION_10_MS  = 10,
    VOICE_FRAME_DURATION_20_MS  = 20,
    VOICE_FRAME_DURATION_40_MS  = 40,
    VOICE_FRAME_DURATION_60_MS  = 60,
    VOICE_FRAME_DURATION_80_MS  = 80,
    VOICE_FRAME_DURATION_100_MS = 100,
    VOICE_FRAME_DURATION_120_MS = 120,
} voice_frame_duration_ms_t;

/** Playback buffer duration (ms) Good to keep for 1200/2400 */
#define VOICE_PLAYBACK_DURATION_MS   2400
/** Decode queue capacity duration (ms); xiaozhi MAX_DECODE_PACKETS = 2400/FRAME_MS. */
#define VOICE_DECODE_QUEUE_DURATION_MS 2400

#define VOICE_PLAYBACK_FRAMES       (VOICE_PLAYBACK_DURATION_MS / VOICE_FRAME_MS)
#define VOICE_DECODER_QUEUE_LEN     (VOICE_DECODE_QUEUE_DURATION_MS / VOICE_FRAME_MS)

/* ---------------------------------------------------------------------------*
 * API
 * ---------------------------------------------------------------------------*/

/** Push PCM from mic (16 kHz mono); no-op if session inactive or buffer full */
void voice_conversation_push_pcm(const int16_t *pcm, size_t samples);

/** True when session is active */
bool voice_conversation_is_active(void);

/** True when the conversation layer can currently accept mic PCM (CONNECTING or LISTEN). */
bool voice_conversation_capture_enabled(void);

/** Start conversation: create tasks/buffers/Opus, register with voice_ws, start WS */
esp_err_t voice_conversation_start(const char *uri);

/** Stop conversation: stop WS, destroy Opus and tasks */
void voice_conversation_stop(void);

/** Notify the backend of local speech/silence transitions when remote LISTEN is active. */
void voice_conversation_notify_vad(bool speaking);

/** Commit the current user turn to the backend (used after VAD returns to silence). */
void voice_conversation_commit_turn(void);

typedef enum {
    VOICE_CONVERSATION_EVENT_BACKEND_READY = 0,
    VOICE_CONVERSATION_EVENT_LISTENING,
    VOICE_CONVERSATION_EVENT_SPEAKING,
    VOICE_CONVERSATION_EVENT_SESSION_ENDED,
} voice_conversation_event_t;

/** Callback for conversation lifecycle updates. Called from WS task context. */
typedef void (*voice_conversation_event_cb_t)(voice_conversation_event_t event, void *arg);
void voice_conversation_set_event_callback(voice_conversation_event_cb_t cb, void *arg);

#ifdef __cplusplus
}
#endif

#endif /* VOICE_CONVERSATION_H */
