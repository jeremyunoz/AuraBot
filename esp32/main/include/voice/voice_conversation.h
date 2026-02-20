/**
 * @file voice_conversation.h
 * @brief Voice conversation: Opus encode/decode, listen/speak phase, TTS playback.
 *
 * Consumes PCM from mic (encode + send when in LISTEN), receives TTS (decode + play when in SPEAK).
 * Depends on voice_ws for connection and send; receives data via voice_ws data callback.
 */
#ifndef VOICE_CONVERSATION_H
#define VOICE_CONVERSATION_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Push PCM from mic (16 kHz mono); no-op if session inactive or buffer full */
void voice_conversation_push_pcm(const int16_t *pcm, size_t samples);

/** True when session is active */
bool voice_conversation_is_active(void);

/** Start conversation: create tasks/buffers/Opus, register with voice_ws, start WS */
esp_err_t voice_conversation_start(const char *uri);

/** Stop conversation: stop WS, destroy Opus and tasks */
void voice_conversation_stop(void);

/** Callback when session ends (e.g. WebSocket disconnected). Called from WS task context. */
typedef void (*voice_conversation_session_end_cb_t)(void *arg);
void voice_conversation_set_session_end_callback(voice_conversation_session_end_cb_t cb, void *arg);

#ifdef __cplusplus
}
#endif

#endif /* VOICE_CONVERSATION_H */
