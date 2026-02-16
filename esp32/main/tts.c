#include "audio/tts.h"

#include "esp_log.h"
#include "picotts.h"
#include "audio/speaker.h"
#include <string.h>

static const char *TAG = "tts";

#define TTS_TASK_PRIORITY  5
#define TTS_CORE           1
#define TTS_SAMPLE_RATE    16000

static volatile bool s_busy;
static bool s_initialized;

/* ---------- SAMPLE CALLBACK ---------- */
static void tts_sample_cb(int16_t *buf, unsigned count)
{
    (void)speaker_write(buf, (size_t)(count * 2));
}

/* ---------- DONE CALLBACK ---------- */
static void tts_done_cb(void)
{
    s_busy = false;
}

/* ---------- ERROR CALLBACK ---------- */
static void tts_error_cb(void)
{
    ESP_LOGE(TAG, "picoTTS error");
    s_busy = false;
}

/* ---------- INIT ---------- */
esp_err_t tts_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    if (!speaker_is_ready()) {
        ESP_LOGE(TAG, "TTS init: speaker not ready");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t ret = speaker_open(TTS_SAMPLE_RATE, 1);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "TTS init speaker_open failed: %s", esp_err_to_name(ret));
        return ret;
    }

    if (!picotts_init(TTS_TASK_PRIORITY, tts_sample_cb, TTS_CORE)) {
        ESP_LOGE(TAG, "TTS init picotts_init failed");
        speaker_close();
        return ESP_FAIL;
    }

    picotts_set_idle_notify(tts_done_cb);
    picotts_set_error_notify(tts_error_cb);

    s_initialized = true;
    s_busy = false;
    return ESP_OK;
}

/* ---------- DEINIT ---------- */
esp_err_t tts_deinit(void)
{
    if (!s_initialized) {
        return ESP_OK;
    }

    picotts_shutdown();
    speaker_close();
    s_initialized = false;
    s_busy = false;
    return ESP_OK;
}

/* ---------- SPEAK ---------- */
esp_err_t tts_speak(const char *text)
{
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    if (text == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_busy = true;
    picotts_add(text, (unsigned)(strlen(text) + 1));
    return ESP_OK;
}

/* ---------- IS BUSY ---------- */
bool tts_is_busy(void)
{
    return s_busy;
}
