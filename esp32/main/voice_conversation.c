/**
 * @file voice_conversation.c
 * @brief Voice conversation: Opus encode PCM → send via WS; receive TTS Opus → decode → speaker.
 *
 * Listen phase: encode + send mic; ignore incoming TTS.
 * Speak phase: decode + play TTS; don't send mic.
 *
 * Design aligned with xiaozhi-esp32 audio service (github.com/78/xiaozhi-esp32/main/audio):
 * - Separate tasks for encode, decode, playback; decode queue + playback stream.
 * - Opus APPLICATION_AUDIO for encoder (uplink); server uses audio + higher bitrate for TTS (downlink).
 * - Decoder reset and playback flush on tts_start to avoid cross-burst artifacts.
 * - Frame duration in one place (OPUS_FRAME_DURATION_MS); buffer sizes derived like xiaozhi.
 */

#include "voice/voice_conversation.h"
#include "voice/voice_ws.h"
#include "audio/speaker.h"
#include "sdkconfig.h"

#if CONFIG_VOICE_SESSION_ENABLE

#include <string.h>
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"
#include "freertos/idf_additions.h"
#include "opus.h"
#include "cJSON.h"

/*
 * Large voice buffers go to PSRAM to avoid exhausting internal DRAM.
 * xStreamBufferCreateWithCaps / xQueueCreateWithCaps are IDF 5.x extensions
 * that let FreeRTOS objects back their storage with a specific heap capability.
 * Falls back to internal RAM when SPIRAM is not present.
 */
#if CONFIG_SPIRAM
#  define VOICE_ALLOC_CAPS  (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
#else
#  define VOICE_ALLOC_CAPS  (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
#endif

static const char *TAG = "voice_conversation";
static const char VAD_SPEECH_MSG[] = "{\"type\":\"vad\",\"state\":\"speech\"}";
static const char VAD_SILENCE_MSG[] = "{\"type\":\"vad\",\"state\":\"silence\"}";
static const char TURN_END_MSG[] = "{\"type\":\"turn_end\",\"source\":\"vad\"}";

/* Use header definitions; local aliases for implementation. */
#define SAMPLE_RATE         VOICE_SAMPLE_RATE
#define CHANNELS            VOICE_CHANNELS
#define FRAME_MS            VOICE_FRAME_MS
#define FRAME_SAMPLES       VOICE_FRAME_SAMPLES
#define FRAME_BYTES         VOICE_FRAME_BYTES
#define OPUS_MAX_PACKET     VOICE_OPUS_MAX_PACKET
#define PLAYBACK_FRAMES     VOICE_PLAYBACK_FRAMES
#define PLAYBACK_BUF_SIZE   (VOICE_FRAME_BYTES * VOICE_PLAYBACK_FRAMES)
#define DECODER_QUEUE_LEN   VOICE_DECODER_QUEUE_LEN

/* Internal: PCM/stream and task config (not part of public API). */
#define PCM_BACKLOG_DURATION_MS 1200
#define PCM_BUF_FRAMES          (PCM_BACKLOG_DURATION_MS / FRAME_MS)
#define PCM_BUF_SIZE            (FRAME_BYTES * PCM_BUF_FRAMES)
#define ENCODER_TASK_STACK      24576
#define ENCODER_TASK_PRIO       5
#define PLAYBACK_TASK_STACK     2560
#define PLAYBACK_TASK_PRIO      6
#define DECODER_TASK_STACK      12288
#define DECODER_TASK_PRIO       6
#define DECODER_IDLE_TIMEOUT_MS 750
#define IDLE_POLL_MS            20
#define ENCODER_RECV_TICKS      pdMS_TO_TICKS(FRAME_MS)
#define ENCODER_SEND_TICKS      pdMS_TO_TICKS(80)

typedef enum {
    VOICE_PHASE_CONNECTING = 0,
    VOICE_PHASE_LISTEN     = 1,
    VOICE_PHASE_SPEAK      = 2,
} voice_phase_t;

typedef struct {
    uint8_t buf[OPUS_MAX_PACKET];
    size_t len;
} opus_packet_t;

static volatile bool s_session_active;
static volatile bool s_backend_ready;
static volatile bool s_local_vad_speaking;
static volatile bool s_pending_turn_commit;
static volatile voice_phase_t s_phase;
static voice_conversation_event_cb_t s_event_cb;
static void *s_event_cb_arg;
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

static bool is_remote_listening(void)
{
    return s_session_active && s_backend_ready && s_phase == VOICE_PHASE_LISTEN && voice_ws_is_connected();
}

static void send_control_message(const char *msg)
{
    if (!msg || !is_remote_listening()) {
        return;
    }

    int sent = voice_ws_send_text(msg, strlen(msg), pdMS_TO_TICKS(100));
    if (sent < 0) {
        ESP_LOGW(TAG, "Failed to send control message");
    }
}

static void maybe_send_turn_commit(size_t frame_bytes_filled)
{
    if (!s_pending_turn_commit || !s_pcm_stream) {
        return;
    }
    if (!is_remote_listening()) {
        return;
    }
    if (frame_bytes_filled != 0) {
        return;
    }
    if (xStreamBufferBytesAvailable(s_pcm_stream) != 0) {
        return;
    }

    send_control_message(VAD_SILENCE_MSG);
    send_control_message(TURN_END_MSG);
    s_pending_turn_commit = false;
}

static void notify_event(voice_conversation_event_t event)
{
    if (s_event_cb) {
        s_event_cb(event, s_event_cb_arg);
    }
}

static void reset_capture_stream(void)
{
    if (s_pcm_stream) {
        (void)xStreamBufferReset(s_pcm_stream);
    }
}

static void drain_decoder_queue(void)
{
    if (!s_decoder_queue) {
        return;
    }

    opus_packet_t dropped;
    while (xQueueReceive(s_decoder_queue, &dropped, 0) == pdTRUE) {
    }
}

static void reset_tts_output(void)
{
    if (s_opus_dec) {
        (void)opus_decoder_ctl(s_opus_dec, OPUS_RESET_STATE);
    }
    drain_decoder_queue();
    if (s_playback_stream) {
        (void)xStreamBufferReset(s_playback_stream);
    }
}

static void set_phase(voice_phase_t phase, const char *reason)
{
    if (s_phase == phase) {
        return;
    }

    s_phase = phase;
    switch (phase) {
    case VOICE_PHASE_CONNECTING:
        s_pending_turn_commit = false;
        ESP_LOGD(TAG, "Phase -> CONNECTING (%s)", reason);
        break;
    case VOICE_PHASE_LISTEN:
        ESP_LOGD(TAG, "Phase -> LISTEN (%s)", reason);
        notify_event(VOICE_CONVERSATION_EVENT_LISTENING);
        if (s_local_vad_speaking) {
            send_control_message(VAD_SPEECH_MSG);
        }
        break;
    case VOICE_PHASE_SPEAK:
        reset_capture_stream();
        s_local_vad_speaking = false;
        s_pending_turn_commit = false;
        ESP_LOGD(TAG, "Phase -> SPEAK (%s)", reason);
        notify_event(VOICE_CONVERSATION_EVENT_SPEAKING);
        break;
    default:
        break;
    }
}

static void set_backend_ready(void)
{
    if (s_backend_ready) {
        return;
    }
    s_backend_ready = true;
    notify_event(VOICE_CONVERSATION_EVENT_BACKEND_READY);
}

static void on_ws_data(void *arg, const uint8_t *data, size_t len, bool is_binary)
{
    (void)arg;
    if (!data) return;

    if (is_binary) {
        if (!s_decoder_queue) return;
        set_phase(VOICE_PHASE_SPEAK, "binary_tts");
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
                } else if (strcmp(type->valuestring, "ready") == 0) {
                    set_backend_ready();
                    cJSON *phase = cJSON_GetObjectItem(root, "phase");
                    if (cJSON_IsString(phase) && phase->valuestring &&
                        strcmp(phase->valuestring, "speak") == 0) {
                        set_phase(VOICE_PHASE_SPEAK, "ready");
                    } else {
                        set_phase(VOICE_PHASE_LISTEN, "ready");
                    }
                } else if (strcmp(type->valuestring, "tts_start") == 0) {
                    reset_tts_output();
                    set_phase(VOICE_PHASE_SPEAK, "tts_start");
                } else if (strcmp(type->valuestring, "tts_end") == 0) {
                    set_phase(VOICE_PHASE_LISTEN, "tts_end");
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
    s_backend_ready = false;
    s_local_vad_speaking = false;
    s_pending_turn_commit = false;
    s_phase = VOICE_PHASE_CONNECTING;
    notify_event(VOICE_CONVERSATION_EVENT_SESSION_ENDED);
}

static void decoder_task(void *arg)
{
    (void)arg;
    static int16_t decode_pcm[FRAME_SAMPLES];
    opus_packet_t item;

    while (1) {
        BaseType_t got = xQueueReceive(s_decoder_queue, &item, pdMS_TO_TICKS(DECODER_IDLE_TIMEOUT_MS));
        if (got != pdTRUE) {
            if (s_phase == VOICE_PHASE_SPEAK &&
                (!s_playback_stream || xStreamBufferBytesAvailable(s_playback_stream) == 0)) {
                set_phase(VOICE_PHASE_LISTEN, "decoder_idle");
            }
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
    size_t frame_bytes_filled = 0;

    while (1) {
        if (!s_session_active || !voice_ws_is_connected() || !s_backend_ready || s_phase != VOICE_PHASE_LISTEN) {
            frame_bytes_filled = 0;
            vTaskDelay(pdMS_TO_TICKS(IDLE_POLL_MS));
            continue;
        }
        if (!s_opus_enc || !s_pcm_stream) {
            frame_bytes_filled = 0;
            vTaskDelay(pdMS_TO_TICKS(IDLE_POLL_MS));
            continue;
        }

        maybe_send_turn_commit(frame_bytes_filled);

        size_t received = xStreamBufferReceive(
            s_pcm_stream,
            ((uint8_t *)s_encoder_pcm_frame) + frame_bytes_filled,
            FRAME_BYTES - frame_bytes_filled,
            ENCODER_RECV_TICKS
        );
        if (received == 0) {
            if (s_pending_turn_commit && frame_bytes_filled > 0) {
                memset(((uint8_t *)s_encoder_pcm_frame) + frame_bytes_filled, 0, FRAME_BYTES - frame_bytes_filled);
                frame_bytes_filled = FRAME_BYTES;
            } else {
                maybe_send_turn_commit(frame_bytes_filled);
                continue;
            }
        } else {
            frame_bytes_filled += received;
        }

        if (frame_bytes_filled < FRAME_BYTES) {
            continue;
        }

        int len = opus_encode(s_opus_enc, s_encoder_pcm_frame, FRAME_SAMPLES, s_encoder_opus_buf, sizeof(s_encoder_opus_buf));
        frame_bytes_filled = 0;
        if (len < 0) {
            ESP_LOGW(TAG, "opus_encode error %d", len);
            continue;
        }
        int sent = voice_ws_send_bin(s_encoder_opus_buf, len, ENCODER_SEND_TICKS);
        if (sent != len) {
            ESP_LOGW(TAG, "WS send_bin %d/%d", sent, len);
            continue;
        }
        maybe_send_turn_commit(frame_bytes_filled);
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

    s_pcm_stream      = xStreamBufferCreateWithCaps(PCM_BUF_SIZE, FRAME_BYTES, VOICE_ALLOC_CAPS);
    s_playback_stream = xStreamBufferCreateWithCaps(PLAYBACK_BUF_SIZE, 1, VOICE_ALLOC_CAPS);
    s_decoder_queue   = xQueueCreateWithCaps(DECODER_QUEUE_LEN, sizeof(opus_packet_t), VOICE_ALLOC_CAPS);
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
    if (s_phase == VOICE_PHASE_SPEAK) return;
    size_t bytes = samples * sizeof(int16_t);
    (void)xStreamBufferSend(s_pcm_stream, pcm, bytes, 0);
}

bool voice_conversation_is_active(void)
{
    return s_session_active;
}

bool voice_conversation_capture_enabled(void)
{
    return s_session_active && s_phase != VOICE_PHASE_SPEAK;
}

void voice_conversation_notify_vad(bool speaking)
{
    if (s_local_vad_speaking == speaking) {
        return;
    }

    s_local_vad_speaking = speaking;
    if (speaking) {
        s_pending_turn_commit = false;
        send_control_message(VAD_SPEECH_MSG);
    }
}

void voice_conversation_commit_turn(void)
{
    if (!s_session_active) {
        return;
    }
    s_pending_turn_commit = true;
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

    create_tasks_and_buffers();
    if (!s_pcm_stream) {
        return ESP_FAIL;
    }

    (void)xStreamBufferReset(s_pcm_stream);
    if (s_playback_stream) {
        (void)xStreamBufferReset(s_playback_stream);
    }
    drain_decoder_queue();

    int err = 0;
    /* Encoder: APPLICATION_AUDIO + VBR + AUTO bitrate (xiaozhi audio_service.h AS_OPUS_ENC_CONFIG).
     * Speaking quality (playback) is determined by server TTS Opus bitrate (e.g. 96 kbps). */
    s_opus_enc = opus_encoder_create(SAMPLE_RATE, CHANNELS, OPUS_APPLICATION_AUDIO, &err);
    if (!s_opus_enc || err != OPUS_OK) {
        ESP_LOGE(TAG, "opus_encoder_create failed %d", err);
        return ESP_FAIL;
    }
    opus_encoder_ctl(s_opus_enc, OPUS_SET_COMPLEXITY(0));
    opus_encoder_ctl(s_opus_enc, OPUS_SET_VBR(1));
    opus_encoder_ctl(s_opus_enc, OPUS_SET_BITRATE(OPUS_AUTO));

    s_opus_dec = opus_decoder_create(SAMPLE_RATE, CHANNELS, &err);
    if (!s_opus_dec || err != OPUS_OK) {
        ESP_LOGE(TAG, "opus_decoder_create failed %d", err);
        opus_encoder_destroy(s_opus_enc);
        s_opus_enc = NULL;
        return ESP_FAIL;
    }

    voice_ws_set_data_callback(on_ws_data, NULL);
    voice_ws_set_disconnect_callback(on_ws_disconnect, NULL);
    voice_ws_set_connected_callback(NULL, NULL);

    s_backend_ready = false;
    s_local_vad_speaking = false;
    s_pending_turn_commit = false;
    s_phase = VOICE_PHASE_CONNECTING;

    esp_err_t ret = voice_ws_start(uri);
    if (ret != ESP_OK) {
        opus_encoder_destroy(s_opus_enc);
        opus_decoder_destroy(s_opus_dec);
        s_opus_enc = NULL;
        s_opus_dec = NULL;
        return ret;
    }

    s_session_active = true;
    ESP_LOGI(TAG, "Voice conversation started (xiaozhi-style buffered input + duplex phase control)");
    return ESP_OK;
}

void voice_conversation_set_event_callback(voice_conversation_event_cb_t cb, void *arg)
{
    s_event_cb = cb;
    s_event_cb_arg = arg;
}

void voice_conversation_stop(void)
{
    s_session_active = false;
    s_backend_ready = false;
    s_local_vad_speaking = false;
    s_pending_turn_commit = false;
    s_phase = VOICE_PHASE_CONNECTING;

    voice_ws_stop();

    if (s_opus_enc) {
        opus_encoder_destroy(s_opus_enc);
        s_opus_enc = NULL;
    }
    if (s_opus_dec) {
        opus_decoder_destroy(s_opus_dec);
        s_opus_dec = NULL;
    }

    if (s_pcm_stream) {
        (void)xStreamBufferReset(s_pcm_stream);
    }
    if (s_decoder_queue) {
        drain_decoder_queue();
    }
    if (s_playback_stream) {
        (void)xStreamBufferReset(s_playback_stream);
    }

    ESP_LOGI(TAG, "Voice conversation stopped");
}

#else /* !CONFIG_VOICE_SESSION_ENABLE */

void voice_conversation_push_pcm(const int16_t *pcm, size_t samples) { (void)pcm; (void)samples; }
bool voice_conversation_is_active(void) { return false; }
bool voice_conversation_capture_enabled(void) { return false; }
esp_err_t voice_conversation_start(const char *uri) { (void)uri; return ESP_ERR_NOT_SUPPORTED; }
void voice_conversation_stop(void) { }
void voice_conversation_notify_vad(bool speaking) { (void)speaking; }
void voice_conversation_commit_turn(void) { }
void voice_conversation_set_event_callback(voice_conversation_event_cb_t cb, void *arg) { (void)cb; (void)arg; }

#endif /* CONFIG_VOICE_SESSION_ENABLE */
