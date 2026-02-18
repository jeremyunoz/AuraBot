/**
 * @file voice_session.c
 * @brief Voice session: Opus encode PCM → WebSocket to Pi5; receive TTS Opus → decode → speaker.
 */

#include "voice/voice_session.h"
#include "audio/speaker.h"
#include "sdkconfig.h"

#if CONFIG_VOICE_SESSION_ENABLE

#include <string.h>
#include "esp_log.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"
#include "esp_websocket_client.h"
#include "opus.h"
#include "cJSON.h"

static const char *TAG = "voice_session";

#define SAMPLE_RATE         16000
#define CHANNELS            1
#define FRAME_MS            60
#define FRAME_SAMPLES       ((FRAME_MS * SAMPLE_RATE) / 1000)  /* 960 */
#define FRAME_BYTES         (FRAME_SAMPLES * sizeof(int16_t))  /* 1920 */
#define OPUS_MAX_PACKET     1275
#define PCM_BUF_SIZE        (FRAME_BYTES * 4)
#define PLAYBACK_BUF_SIZE   (FRAME_BYTES * 4)
#define ENCODER_TASK_STACK  4096
#define ENCODER_TASK_PRIO   5
#define PLAYBACK_TASK_STACK 2048
#define PLAYBACK_TASK_PRIO  6
#define HELLO_TIMEOUT_MS    10000

static const char HELLO_JSON[] =
    "{\"type\":\"hello\",\"version\":1,\"transport\":\"websocket\","
    "\"audio_params\":{\"format\":\"opus\",\"sample_rate\":16000,\"channels\":1,\"frame_duration\":60}}";

static volatile bool s_session_active;
static esp_websocket_client_handle_t s_ws_client;
static OpusEncoder *s_opus_enc;
static OpusDecoder *s_opus_dec;
static StreamBufferHandle_t s_pcm_stream;
static StreamBufferHandle_t s_playback_stream;
static TaskHandle_t s_encoder_task_handle;
static TaskHandle_t s_playback_task_handle;
static bool s_hello_acked;

static void ws_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *evt = (esp_websocket_event_data_t *)data;
    (void)arg;
    (void)base;

    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "WebSocket connected");
        s_hello_acked = false;
        if (evt->client) {
            int sent = esp_websocket_client_send_text(s_ws_client, HELLO_JSON, sizeof(HELLO_JSON) - 1, pdMS_TO_TICKS(1000));
            if (sent < 0) {
                ESP_LOGE(TAG, "Failed to send hello");
            }
        }
        break;

    case WEBSOCKET_EVENT_DATA:
        if (!evt->data_ptr || evt->data_len <= 0) break;
        if (evt->op_code == 0x01) {
            /* Text: expect server hello or other JSON */
            if (evt->data_len >= 4 && strncmp(evt->data_ptr, "{\"ty", 4) == 0) {
                cJSON *root = cJSON_ParseWithLength(evt->data_ptr, evt->data_len);
                if (root) {
                    cJSON *type = cJSON_GetObjectItem(root, "type");
                    if (cJSON_IsString(type) && type->valuestring && strcmp(type->valuestring, "hello") == 0) {
                        s_hello_acked = true;
                        ESP_LOGI(TAG, "Server hello received");
                    }
                    cJSON_Delete(root);
                }
            }
        } else if (evt->op_code == 0x02 && s_opus_dec) {
            /* Binary: TTS Opus → decode → playback buffer */
            int16_t pcm_buf[FRAME_SAMPLES];
            int nsamples = opus_decode(s_opus_dec, (const unsigned char *)evt->data_ptr, evt->data_len,
                                       pcm_buf, FRAME_SAMPLES, 0);
            if (nsamples > 0) {
                size_t bytes = (size_t)(nsamples * sizeof(int16_t));
                size_t sent = xStreamBufferSend(s_playback_stream, pcm_buf, bytes, 0);
                if (sent != bytes) {
                    ESP_LOGW(TAG, "Playback buffer full, dropped %u bytes", (unsigned)(bytes - sent));
                }
            }
        }
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGI(TAG, "WebSocket disconnected or error");
        s_session_active = false;
        break;

    default:
        break;
    }
}

static void encoder_task(void *arg)
{
    (void)arg;
    uint8_t opus_buf[OPUS_MAX_PACKET];
    int16_t pcm_frame[FRAME_SAMPLES];

    while (1) {
        if (!s_session_active || !s_ws_client || !esp_websocket_client_is_connected(s_ws_client)) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        if (!s_opus_enc || !s_pcm_stream) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        size_t received = xStreamBufferReceive(s_pcm_stream, pcm_frame, FRAME_BYTES, pdMS_TO_TICKS(100));
        if (received != FRAME_BYTES) {
            continue;
        }

        int len = opus_encode(s_opus_enc, pcm_frame, FRAME_SAMPLES, opus_buf, sizeof(opus_buf));
        if (len < 0) {
            ESP_LOGW(TAG, "opus_encode error %d", len);
            continue;
        }
        int sent = esp_websocket_client_send_bin(s_ws_client, (const char *)opus_buf, len, pdMS_TO_TICKS(200));
        if (sent != len) {
            ESP_LOGW(TAG, "WS send_bin %d/%d", sent, len);
        }
    }
}

static void playback_task(void *arg)
{
    (void)arg;
    uint8_t buf[FRAME_BYTES];

    while (1) {
        size_t received = xStreamBufferReceive(s_playback_stream, buf, sizeof(buf), pdMS_TO_TICKS(100));
        if (received == 0) continue;
        if (!speaker_is_ready()) continue;
        (void)speaker_write(buf, received);
    }
}

static void create_tasks_and_buffers(void)
{
    if (s_pcm_stream != NULL) return;

    s_pcm_stream = xStreamBufferCreate(PCM_BUF_SIZE, FRAME_BYTES);
    s_playback_stream = xStreamBufferCreate(PLAYBACK_BUF_SIZE, 1);
    if (!s_pcm_stream || !s_playback_stream) {
        ESP_LOGE(TAG, "Failed to create stream buffers");
        if (s_pcm_stream) vStreamBufferDelete(s_pcm_stream);
        if (s_playback_stream) vStreamBufferDelete(s_playback_stream);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        return;
    }

    BaseType_t ok = xTaskCreate(encoder_task, "opus_enc", ENCODER_TASK_STACK, NULL, ENCODER_TASK_PRIO, &s_encoder_task_handle);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create encoder task");
        vStreamBufferDelete(s_pcm_stream);
        vStreamBufferDelete(s_playback_stream);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        return;
    }
    ok = xTaskCreate(playback_task, "opus_play", PLAYBACK_TASK_STACK, NULL, PLAYBACK_TASK_PRIO, &s_playback_task_handle);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create playback task");
        vTaskDelete(s_encoder_task_handle);
        vStreamBufferDelete(s_pcm_stream);
        vStreamBufferDelete(s_playback_stream);
        s_pcm_stream = NULL;
        s_playback_stream = NULL;
        s_encoder_task_handle = NULL;
    }
}

void voice_session_push_pcm(const int16_t *pcm, size_t samples)
{
    if (!s_session_active || !s_pcm_stream || !pcm) return;
    size_t bytes = samples * sizeof(int16_t);
    (void)xStreamBufferSend(s_pcm_stream, pcm, bytes, 0);
}

bool voice_session_is_active(void)
{
    return s_session_active;
}

esp_err_t voice_session_start(void)
{
    if (s_session_active) {
        return ESP_OK;
    }

    const char *uri = CONFIG_VOICE_WS_URI;
    if (!uri || uri[0] == '\0') {
        ESP_LOGE(TAG, "CONFIG_VOICE_WS_URI not set");
        return ESP_ERR_INVALID_ARG;
    }

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
    opus_encoder_ctl(s_opus_enc, OPUS_SET_COMPLEXITY(5));

    s_opus_dec = opus_decoder_create(SAMPLE_RATE, CHANNELS, &err);
    if (!s_opus_dec || err != OPUS_OK) {
        ESP_LOGE(TAG, "opus_decoder_create failed %d", err);
        opus_encoder_destroy(s_opus_enc);
        s_opus_enc = NULL;
        return ESP_FAIL;
    }

    esp_websocket_client_config_t ws_cfg = {
        .uri = uri,
        .buffer_size = 2048,
        .task_prio = 6,
        .task_stack = 4096,
        .disable_auto_reconnect = true,
    };

    s_ws_client = esp_websocket_client_init(&ws_cfg);
    if (!s_ws_client) {
        ESP_LOGE(TAG, "WebSocket client init failed");
        opus_encoder_destroy(s_opus_enc);
        opus_decoder_destroy(s_opus_dec);
        s_opus_enc = NULL;
        s_opus_dec = NULL;
        return ESP_FAIL;
    }

    esp_websocket_register_events(s_ws_client, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL);

    esp_err_t ret = esp_websocket_client_start(s_ws_client);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WebSocket start failed %s", esp_err_to_name(ret));
        esp_websocket_client_destroy(s_ws_client);
        s_ws_client = NULL;
        opus_encoder_destroy(s_opus_enc);
        opus_decoder_destroy(s_opus_dec);
        s_opus_enc = NULL;
        s_opus_dec = NULL;
        return ret;
    }

    s_session_active = true;
    s_hello_acked = false;
    ESP_LOGI(TAG, "Voice session started");
    return ESP_OK;
}

void voice_session_stop(void)
{
#if CONFIG_VOICE_SESSION_ENABLE
    s_session_active = false;

    if (s_ws_client) {
        esp_websocket_client_close(s_ws_client, pdMS_TO_TICKS(1000));
        esp_websocket_client_stop(s_ws_client);
        esp_websocket_client_destroy(s_ws_client);
        s_ws_client = NULL;
    }

    if (s_opus_enc) {
        opus_encoder_destroy(s_opus_enc);
        s_opus_enc = NULL;
    }
    if (s_opus_dec) {
        opus_decoder_destroy(s_opus_dec);
        s_opus_dec = NULL;
    }

    ESP_LOGI(TAG, "Voice session stopped");
#endif
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

#endif /* CONFIG_VOICE_SESSION_ENABLE */
