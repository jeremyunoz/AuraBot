/**
 * @file voice_conversation.c
 * @brief Voice conversation: Opus encode PCM → send via WS; receive TTS Opus → decode → speaker.
 *
 * Listen phase: encode + send mic; ignore incoming TTS.
 * Speak phase: decode + play TTS; don't send mic.
 */

#include "voice/voice_conversation.h"
#include "voice/voice_ws.h"
#include "audio/speaker.h"
#include "sdkconfig.h"

#if CONFIG_VOICE_SESSION_ENABLE

#include <string.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"
#include "opus.h"
#include "cJSON.h"

static const char *TAG = "voice_conversation";

#define SAMPLE_RATE         16000
#define CHANNELS            1
#define FRAME_MS            60
#define FRAME_SAMPLES       ((FRAME_MS * SAMPLE_RATE) / 1000)
#define FRAME_BYTES         (FRAME_SAMPLES * sizeof(int16_t))
#define OPUS_MAX_PACKET     1275
#define PCM_BUF_SIZE        (FRAME_BYTES * 2)
#define PLAYBACK_BUF_SIZE   (FRAME_BYTES * 4)
#define ENCODER_TASK_STACK  24576
#define ENCODER_TASK_PRIO   5
#define PLAYBACK_TASK_STACK 2560
#define PLAYBACK_TASK_PRIO  6
#define DECODER_TASK_STACK  12288
#define DECODER_TASK_PRIO   5
#define DECODER_QUEUE_LEN   8
#define SPEAK_TO_LISTEN_MS  500
#define ENCODER_RECV_TICKS  pdMS_TO_TICKS(60)
#define ENCODER_SEND_TICKS  pdMS_TO_TICKS(80)

typedef enum {
    VOICE_PHASE_LISTEN = 0,
    VOICE_PHASE_SPEAK  = 1,
} voice_phase_t;

typedef struct {
    uint8_t buf[OPUS_MAX_PACKET];
    size_t len;
} opus_packet_t;

static volatile bool s_session_active;
static volatile voice_phase_t s_phase;
static voice_conversation_session_end_cb_t s_session_end_cb;
static void *s_session_end_cb_arg;
static bool s_session_end_notified;
static OpusEncoder *s_opus_enc;
static OpusDecoder *s_opus_dec;
static StreamBufferHandle_t s_pcm_stream;
static StreamBufferHandle_t s_playback_stream;
static QueueHandle_t s_decoder_queue;
static TaskHandle_t s_encoder_task_handle;
static TaskHandle_t s_playback_task_handle;
static TaskHandle_t s_decoder_task_handle;
static uint8_t s_playback_buf[FRAME_BYTES];
static uint8_t s_encoder_opus_buf[OPUS_MAX_PACKET];
static int16_t s_encoder_pcm_frame[FRAME_SAMPLES];

static void on_ws_data(void *arg, const uint8_t *data, size_t len, bool is_binary)
{
    (void)arg;
    if (!data) return;

    if (is_binary) {
        if (!s_decoder_queue) return;
        if (s_phase == VOICE_PHASE_LISTEN) {
            s_phase = VOICE_PHASE_SPEAK;
        }
        size_t copy_len = len > OPUS_MAX_PACKET ? OPUS_MAX_PACKET : len;
        opus_packet_t item;
        item.len = copy_len;
        memcpy(item.buf, data, copy_len);
        if (xQueueSend(s_decoder_queue, &item, 0) != pdTRUE) {
            ESP_LOGW(TAG, "Decoder queue full, dropped TTS packet");
        }
        return;
    }

    /* Text: hello ack, tts_start, tts_end */
    if (len >= 4 && memcmp(data, "{\"ty", 4) == 0) {
        cJSON *root = cJSON_ParseWithLength((const char *)data, len);
        if (root) {
            cJSON *type = cJSON_GetObjectItem(root, "type");
            if (cJSON_IsString(type) && type->valuestring) {
                if (strcmp(type->valuestring, "hello") == 0) {
                    ESP_LOGI(TAG, "Server hello received");
                } else if (strcmp(type->valuestring, "tts_start") == 0) {
                    s_phase = VOICE_PHASE_SPEAK;
                    ESP_LOGD(TAG, "Phase -> SPEAK (tts_start)");
                } else if (strcmp(type->valuestring, "tts_end") == 0) {
                    s_phase = VOICE_PHASE_LISTEN;
                    ESP_LOGD(TAG, "Phase -> LISTEN (tts_end)");
                }
            }
            cJSON_Delete(root);
        }
    }
}

static void on_ws_disconnect(void *arg)
{
    (void)arg;
    s_session_active = false;
    if (s_session_end_cb && !s_session_end_notified) {
        s_session_end_notified = true;
        s_session_end_cb(s_session_end_cb_arg);
    }
}

static void decoder_task(void *arg)
{
    (void)arg;
    static int16_t decode_pcm[FRAME_SAMPLES];
    opus_packet_t item;

    while (1) {
        BaseType_t got = xQueueReceive(s_decoder_queue, &item, pdMS_TO_TICKS(SPEAK_TO_LISTEN_MS));
        if (got != pdTRUE) {
            s_phase = VOICE_PHASE_LISTEN;
            continue;
        }
        if (!s_opus_dec || !s_playback_stream || item.len == 0) continue;

        int nsamples = opus_decode(s_opus_dec, item.buf, (opus_int32)item.len, decode_pcm, FRAME_SAMPLES, 0);
        if (nsamples > 0) {
            size_t bytes = (size_t)(nsamples * sizeof(int16_t));
            size_t sent = xStreamBufferSend(s_playback_stream, decode_pcm, bytes, pdMS_TO_TICKS(50));
            if (sent != bytes) {
                ESP_LOGW(TAG, "Playback buffer full, dropped %u bytes", (unsigned)(bytes - sent));
            }
        }
    }
}

static void encoder_task(void *arg)
{
    (void)arg;
    while (1) {
        if (!s_session_active || !voice_ws_is_connected()) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        if (!s_opus_enc || !s_pcm_stream) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        size_t received = xStreamBufferReceive(s_pcm_stream, s_encoder_pcm_frame, FRAME_BYTES, ENCODER_RECV_TICKS);
        if (received != FRAME_BYTES) {
            continue;
        }

        int len = opus_encode(s_opus_enc, s_encoder_pcm_frame, FRAME_SAMPLES, s_encoder_opus_buf, sizeof(s_encoder_opus_buf));
        if (len < 0) {
            ESP_LOGW(TAG, "opus_encode error %d", len);
            continue;
        }
        if (s_phase == VOICE_PHASE_LISTEN) {
            int sent = voice_ws_send_bin(s_encoder_opus_buf, len, ENCODER_SEND_TICKS);
            if (sent != len) {
                ESP_LOGW(TAG, "WS send_bin %d/%d", sent, len);
            }
        }
    }
}

static void playback_task(void *arg)
{
    (void)arg;
    while (1) {
        size_t received = xStreamBufferReceive(s_playback_stream, s_playback_buf, sizeof(s_playback_buf), pdMS_TO_TICKS(100));
        if (received == 0) continue;
        if (!speaker_is_ready()) continue;
        (void)speaker_write(s_playback_buf, received);
    }
}

static void create_tasks_and_buffers(void)
{
    if (s_pcm_stream != NULL) return;

    s_pcm_stream = xStreamBufferCreate(PCM_BUF_SIZE, FRAME_BYTES);
    s_playback_stream = xStreamBufferCreate(PLAYBACK_BUF_SIZE, 1);
    s_decoder_queue = xQueueCreate(DECODER_QUEUE_LEN, sizeof(opus_packet_t));
    if (!s_pcm_stream || !s_playback_stream || !s_decoder_queue) {
        ESP_LOGE(TAG, "Failed to create stream buffers or decoder queue");
        if (s_pcm_stream) vStreamBufferDelete(s_pcm_stream);
        if (s_playback_stream) vStreamBufferDelete(s_playback_stream);
        if (s_decoder_queue) vQueueDelete(s_decoder_queue);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        s_decoder_queue = NULL;
        return;
    }

    BaseType_t ok = xTaskCreate(encoder_task, "opus_enc", ENCODER_TASK_STACK, NULL, ENCODER_TASK_PRIO, &s_encoder_task_handle);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create encoder task");
        vStreamBufferDelete(s_pcm_stream);
        vStreamBufferDelete(s_playback_stream);
        vQueueDelete(s_decoder_queue);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        s_decoder_queue = NULL;
        return;
    }
    ok = xTaskCreate(decoder_task, "opus_dec", DECODER_TASK_STACK, NULL, DECODER_TASK_PRIO, &s_decoder_task_handle);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create decoder task");
        vTaskDelete(s_encoder_task_handle);
        vStreamBufferDelete(s_pcm_stream);
        vStreamBufferDelete(s_playback_stream);
        vQueueDelete(s_decoder_queue);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        s_decoder_queue = NULL;
        s_encoder_task_handle = NULL;
        return;
    }
    ok = xTaskCreate(playback_task, "opus_play", PLAYBACK_TASK_STACK, NULL, PLAYBACK_TASK_PRIO, &s_playback_task_handle);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create playback task");
        vTaskDelete(s_encoder_task_handle);
        vTaskDelete(s_decoder_task_handle);
        vStreamBufferDelete(s_pcm_stream);
        vStreamBufferDelete(s_playback_stream);
        vQueueDelete(s_decoder_queue);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        s_decoder_queue = NULL;
        s_encoder_task_handle = NULL;
        s_decoder_task_handle = NULL;
        return;
    }
}

void voice_conversation_push_pcm(const int16_t *pcm, size_t samples)
{
    if (!s_session_active || !s_pcm_stream || !pcm) return;
    size_t bytes = samples * sizeof(int16_t);
    (void)xStreamBufferSend(s_pcm_stream, pcm, bytes, 0);
}

bool voice_conversation_is_active(void)
{
    return s_session_active;
}

esp_err_t voice_conversation_start(const char *uri)
{
    if (s_session_active) {
        return ESP_OK;
    }
    if (!uri || uri[0] == '\0') {
        ESP_LOGE(TAG, "Voice WS URI not set");
        return ESP_ERR_INVALID_ARG;
    }

    s_session_end_notified = false;
    create_tasks_and_buffers();
    if (!s_pcm_stream) {
        return ESP_FAIL;
    }

    int err = 0;
    s_opus_enc = opus_encoder_create(SAMPLE_RATE, CHANNELS, OPUS_APPLICATION_VOIP, &err);
    if (!s_opus_enc || err != OPUS_OK) {
        ESP_LOGE(TAG, "opus_encoder_create failed %d", err);
        return ESP_FAIL;
    }
    opus_encoder_ctl(s_opus_enc, OPUS_SET_COMPLEXITY(0));

    s_opus_dec = opus_decoder_create(SAMPLE_RATE, CHANNELS, &err);
    if (!s_opus_dec || err != OPUS_OK) {
        ESP_LOGE(TAG, "opus_decoder_create failed %d", err);
        opus_encoder_destroy(s_opus_enc);
        s_opus_enc = NULL;
        return ESP_FAIL;
    }

    voice_ws_set_data_callback(on_ws_data, NULL);
    voice_ws_set_disconnect_callback(on_ws_disconnect, NULL);

    esp_err_t ret = voice_ws_start(uri);
    if (ret != ESP_OK) {
        opus_encoder_destroy(s_opus_enc);
        opus_decoder_destroy(s_opus_dec);
        s_opus_enc = NULL;
        s_opus_dec = NULL;
        return ret;
    }

    s_session_active = true;
    s_phase = VOICE_PHASE_LISTEN;
    ESP_LOGI(TAG, "Voice conversation started (listen/speak alternation)");
    return ESP_OK;
}

void voice_conversation_set_session_end_callback(voice_conversation_session_end_cb_t cb, void *arg)
{
    s_session_end_cb = cb;
    s_session_end_cb_arg = arg;
}

void voice_conversation_stop(void)
{
    s_session_active = false;
    s_session_end_notified = false;
    s_session_end_cb = NULL;
    s_session_end_cb_arg = NULL;

    voice_ws_stop();

    if (s_opus_enc) {
        opus_encoder_destroy(s_opus_enc);
        s_opus_enc = NULL;
    }
    if (s_opus_dec) {
        opus_decoder_destroy(s_opus_dec);
        s_opus_dec = NULL;
    }

    ESP_LOGI(TAG, "Voice conversation stopped");
}

#else /* !CONFIG_VOICE_SESSION_ENABLE */

void voice_conversation_push_pcm(const int16_t *pcm, size_t samples) { (void)pcm; (void)samples; }
bool voice_conversation_is_active(void) { return false; }
esp_err_t voice_conversation_start(const char *uri) { (void)uri; return ESP_ERR_NOT_SUPPORTED; }
void voice_conversation_stop(void) { }
void voice_conversation_set_session_end_callback(voice_conversation_session_end_cb_t cb, void *arg) { (void)cb; (void)arg; }

#endif /* CONFIG_VOICE_SESSION_ENABLE */
