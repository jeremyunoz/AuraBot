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

static void post_system_event(sys_event_id_t id)
{
    if (s_evt_queue) {
        sys_event_t evt = { .id = id };
        (void)xQueueSend(s_evt_queue, &evt, pdMS_TO_TICKS(10));
    }
}

static void on_conversation_event(voice_conversation_event_t event, void *arg)
{
    (void)arg;

    switch (event) {
    case VOICE_CONVERSATION_EVENT_BACKEND_READY:
        post_system_event(SYS_EVT_PI5_READY);
        break;
    case VOICE_CONVERSATION_EVENT_LISTENING:
        post_system_event(SYS_EVT_VOICE_LISTENING);
        break;
    case VOICE_CONVERSATION_EVENT_SPEAKING:
        post_system_event(SYS_EVT_VOICE_SPEAKING);
        break;
    case VOICE_CONVERSATION_EVENT_SESSION_ENDED:
        post_system_event(SYS_EVT_SESSION_END);
        break;
    default:
        ESP_LOGW(TAG, "Unhandled conversation event %d", (int)event);
        break;
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

bool voice_session_capture_enabled(void)
{
    return voice_conversation_capture_enabled();
}

void voice_session_notify_vad(bool speaking)
{
    voice_conversation_notify_vad(speaking);
}

void voice_session_commit_turn(void)
{
    voice_conversation_commit_turn();
}

esp_err_t voice_session_start(void)
{
    const char *uri = CONFIG_VOICE_WS_URI;
    if (!uri || uri[0] == '\0') {
        ESP_LOGE(TAG, "CONFIG_VOICE_WS_URI not set");
        return ESP_ERR_INVALID_ARG;
    }
    voice_conversation_set_event_callback(on_conversation_event, NULL);
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

bool voice_session_capture_enabled(void)
{
    return false;
}

void voice_session_notify_vad(bool speaking)
{
    (void)speaking;
}

void voice_session_commit_turn(void)
{
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
