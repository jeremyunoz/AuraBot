/**
 * @file voice_session.c
 * @brief Voice session: public API; delegates to voice_ws (connection) and voice_conversation (Opus/TTS).
 *
 * When a voice session is active, PCM from the mic is encoded with Opus and sent over WebSocket;
 * incoming TTS is decoded and played via the speaker. Listen/speak phases alternate.
 */

#include "voice/voice_session.h"
#include "voice/voice_conversation.h"
#include "system/system_events.h"
#include "sdkconfig.h"

#if CONFIG_VOICE_SESSION_ENABLE

#include "esp_log.h"
#include "freertos/queue.h"

static const char *TAG = "voice_session";
static QueueHandle_t s_evt_queue = NULL;

static void on_session_end(void *arg)
{
    (void)arg;
    if (s_evt_queue) {
        sys_event_t evt = { .id = SYS_EVT_SESSION_END };
        (void)xQueueSend(s_evt_queue, &evt, 0);
    }
}

void voice_session_set_event_queue(QueueHandle_t queue)
{
    s_evt_queue = queue;
}

void voice_session_push_pcm(const int16_t *pcm, size_t samples)
{
    voice_conversation_push_pcm(pcm, samples);
}

bool voice_session_is_active(void)
{
    return voice_conversation_is_active();
}

esp_err_t voice_session_start(void)
{
    const char *uri = CONFIG_VOICE_WS_URI;
    if (!uri || uri[0] == '\0') {
        ESP_LOGE(TAG, "CONFIG_VOICE_WS_URI not set");
        return ESP_ERR_INVALID_ARG;
    }
    voice_conversation_set_session_end_callback(on_session_end, NULL);
    return voice_conversation_start(uri);
}

void voice_session_stop(void)
{
    voice_conversation_stop();
}

#else /* !CONFIG_VOICE_SESSION_ENABLE */

void voice_session_push_pcm(const int16_t *pcm, size_t samples)
{
    (void)pcm;
    (void)samples;
}

bool voice_session_is_active(void)
{
    return false;
}

esp_err_t voice_session_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

void voice_session_stop(void)
{
}

void voice_session_set_event_queue(QueueHandle_t queue)
{
    (void)queue;
}

#endif /* CONFIG_VOICE_SESSION_ENABLE */
