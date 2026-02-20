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

/** Start voice session: connect WebSocket, send hello, start capture/encoder path */
esp_err_t voice_session_start(void);

/** Stop voice session: close WebSocket, stop capture; wakeword resumes feeding AFE */
void voice_session_stop(void);

/** Set event queue to post SYS_EVT_SESSION_END when WebSocket disconnects (e.g. server down). */
void voice_session_set_event_queue(QueueHandle_t queue);

#ifdef __cplusplus
}
#endif

#endif /* VOICE_SESSION_H */
