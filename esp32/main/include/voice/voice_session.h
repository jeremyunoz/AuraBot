/**
 * @file voice_session.h
 * @brief Voice session: capture PCM, encode Opus, send/receive over WebSocket to Pi5.
 *
 * When a voice session is active, PCM from the mic is buffered, encoded with Opus
 * (60 ms frames, 16 kHz mono), and sent to Pi5 over WebSocket. Incoming binary
 * (TTS) is decoded and played via the speaker.
 */
#ifndef VOICE_SESSION_H
#define VOICE_SESSION_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 16 kHz mono PCM; call from the single I2S reader (e.g. wakeword feed_task) when in session */
void voice_session_push_pcm(const int16_t *pcm, size_t samples);

/** True when session is active (capture → Opus → WebSocket and receive TTS) */
bool voice_session_is_active(void);

/** True while the conversation layer can accept captured mic PCM (CONNECTING or LISTEN). */
bool voice_session_capture_enabled(void);

/** Start voice session: connect WebSocket, send hello, start capture/encoder path */
esp_err_t voice_session_start(void);

/** Stop voice session: close WebSocket, stop capture; wakeword resumes feeding AFE */
void voice_session_stop(void);

/** Propagate local speech/silence changes to the active voice session. */
void voice_session_notify_vad(bool speaking);

/** Finalize the current user turn after device-side VAD detects end-of-speech. */
void voice_session_commit_turn(void);

/** Set event queue to post voice session lifecycle events for the main state task. */
void voice_session_set_event_queue(QueueHandle_t queue);

#ifdef __cplusplus
}
#endif

#endif /* VOICE_SESSION_H */
