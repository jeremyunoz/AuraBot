/**
 * @file wakeword.c
 * @brief Continuous wake-word detection using ESP-SR AFE + WakeNet
 *
 * Architecture (two FreeRTOS tasks):
 *   feed task  – reads 16 kHz mono PCM from the shared I2S RX channel
 *                and pushes frames into the AFE ring-buffer.
 *   fetch task – pulls processed frames from the AFE, checks the
 *                WakeNet result, and on detection: beeps, resets the
 *                ring-buffer, re-arms WakeNet, then resumes listening.
 */
#include "audio/wakeword.h"

#include <stdlib.h>
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2s_std.h"

#include "audio/speaker.h"
#include "voice/voice_session.h"
#include "model_path.h"
#include "esp_afe_config.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "freertos/queue.h"

#include "system/system_events.h"

static const char *TAG = "wakeword";

#define FEED_STACK_SIZE    8192
#define FETCH_STACK_SIZE   8192
#define TASK_PRIO          5
#define COOLDOWN_MS        1500

/* Shared context passed to both tasks */
typedef struct {
    const esp_afe_sr_iface_t *afe;
    esp_afe_sr_data_t        *afe_data;
    i2s_chan_handle_t          rx_handle;
} wakeword_ctx_t;

static QueueHandle_t s_evt_queue = NULL;

void wakeword_set_event_queue(QueueHandle_t queue)
{
    s_evt_queue = queue;
}

static void wakeword_post_event(sys_event_id_t id)
{
    if (!s_evt_queue) return;
    sys_event_t evt = { .id = id };
    (void)xQueueSend(s_evt_queue, &evt, 0);
}

/* ------------------------------------------------------------------ */
/*  Feed task – mic → AFE                                              */
/* ------------------------------------------------------------------ */
static void feed_task(void *arg)
{
    wakeword_ctx_t *ctx = (wakeword_ctx_t *)arg;
    const esp_afe_sr_iface_t *afe = ctx->afe;
    esp_afe_sr_data_t *afe_data   = ctx->afe_data;

    int chunksize  = afe->get_feed_chunksize(afe_data);
    int channels   = afe->get_feed_channel_num(afe_data);
    size_t frame_bytes = chunksize * channels * sizeof(int16_t);

    int16_t *buf = heap_caps_malloc(frame_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    if (!buf) {
        ESP_LOGE(TAG, "feed: failed to allocate buffer (%u bytes)", (unsigned)frame_bytes);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "feed task running  (chunk=%d, ch=%d, %u bytes/frame)",
             chunksize, channels, (unsigned)frame_bytes);

    while (1) {
        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(ctx->rx_handle, buf, frame_bytes,
                                         &bytes_read, portMAX_DELAY);
        if (ret != ESP_OK || bytes_read != frame_bytes) {
            ESP_LOGW(TAG, "feed: I2S read %s (got %u/%u)",
                     esp_err_to_name(ret), (unsigned)bytes_read, (unsigned)frame_bytes);
            continue;
        }
        if (voice_session_is_active()) {
            /* Voice session: push PCM to Opus/WebSocket path; do not feed AFE */
            size_t samples = bytes_read / sizeof(int16_t);
            voice_session_push_pcm(buf, samples);
        } else {
            afe->feed(afe_data, buf);
        }
    }
}

/* ------------------------------------------------------------------ */
/*  Fetch task – AFE → wake-word check                                 */
/* ------------------------------------------------------------------ */
static void fetch_task(void *arg)
{
    wakeword_ctx_t *ctx = (wakeword_ctx_t *)arg;
    const esp_afe_sr_iface_t *afe = ctx->afe;
    esp_afe_sr_data_t *afe_data   = ctx->afe_data;

    ESP_LOGI(TAG, "fetch task running – listening for wake word");

    while (1) {
        afe_fetch_result_t *res = afe->fetch(afe_data);
        if (!res || res->ret_value != ESP_OK) {
            continue;
        }

        if (res->wakeup_state == WAKENET_DETECTED) {
            ESP_LOGI(TAG, "*** Wake word detected (index=%d) ***",
                     res->wake_word_index);

            wakeword_post_event(SYS_EVT_WAKE_DETECTED);

            /* Re-arm WakeNet so it keeps listening continuously */
            int wn_ret = afe->enable_wakenet(afe_data);
            ESP_LOGI(TAG, "enable_wakenet returned %d", wn_ret);

            /* Brief cooldown to avoid immediate re-trigger */
            vTaskDelay(pdMS_TO_TICKS(COOLDOWN_MS));

            ESP_LOGI(TAG, "Listening again for wake word...");
        }
    }
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */
esp_err_t wakeword_start(void)
{
    /* The speaker driver owns the I2S bus; grab its RX channel */
    i2s_chan_handle_t rx_handle = speaker_get_rx_handle();
    if (!rx_handle) {
        ESP_LOGE(TAG, "No I2S RX handle – call speaker_init() first");
        return ESP_ERR_INVALID_STATE;
    }

    /* Load models from the "model" partition */
    srmodel_list_t *models = esp_srmodel_init("model");
    if (!models) {
        ESP_LOGE(TAG, "Failed to init SR models from partition");
        return ESP_ERR_NOT_FOUND;
    }

    char *model_name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
    if (!model_name) {
        ESP_LOGE(TAG, "No WakeNet model found in partition");
        esp_srmodel_deinit(models);
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGI(TAG, "Using WakeNet model: %s", model_name);

    /* Configure AFE: single-mic, WakeNet only, everything else off */
    afe_config_t *cfg = afe_config_init("M", models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    cfg->aec_init      = false;
    cfg->se_init       = false;
    cfg->ns_init       = false;
    cfg->vad_init      = false;
    cfg->agc_init      = false;
    cfg->wakenet_init  = true;
    cfg->wakenet_mode  = DET_MODE_90;
    cfg->wakenet_model_name = model_name;

    const esp_afe_sr_iface_t *afe = esp_afe_handle_from_config(cfg);
    esp_afe_sr_data_t *afe_data   = afe->create_from_config(cfg);
    afe_config_free(cfg);

    if (!afe_data) {
        ESP_LOGE(TAG, "Failed to create AFE instance");
        return ESP_FAIL;
    }

    /* Print the pipeline so we can verify in the log */
    afe->print_pipeline(afe_data);

    /* Allocate shared context (lives for the lifetime of the app) */
    wakeword_ctx_t *ctx = calloc(1, sizeof(wakeword_ctx_t));
    if (!ctx) {
        ESP_LOGE(TAG, "Failed to allocate context");
        afe->destroy(afe_data);
        return ESP_ERR_NO_MEM;
    }
    ctx->afe       = afe;
    ctx->afe_data  = afe_data;
    ctx->rx_handle = rx_handle;

    /* Launch the two tasks */
    BaseType_t ok;
    ok = xTaskCreate(feed_task, "ww_feed", FEED_STACK_SIZE, ctx, TASK_PRIO, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create feed task");
        free(ctx);
        afe->destroy(afe_data);
        return ESP_ERR_NO_MEM;
    }

    ok = xTaskCreate(fetch_task, "ww_fetch", FETCH_STACK_SIZE, ctx, TASK_PRIO, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create fetch task");
        /* feed task is already running; best-effort */
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "Continuous wake-word detection started");
    return ESP_OK;
}
