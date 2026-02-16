/**
 * @file speaker.c
 * @brief Speaker driver using esp_codec_dev and ES8311 codec
 *
 * Codec setup follows esp-adf esp_codec_dev test_apps/codec_dev_test/main
 * (test_board.c): I2C/I2S init, then data_if, ctrl_if, gpio_if, codec_if,
 * esp_codec_dev_new. See https://github.com/espressif/esp-adf/tree/master/
 * components/esp_codec_dev and component registry esp_codec_dev.
 * Requires CONFIG_CODEC_ES8311_SUPPORT in esp_codec_dev.
 */
#include "audio/speaker.h"

#if CONFIG_SPEAKER_ENABLE
#include "sdkconfig.h"
#include "esp_idf_version.h"

/* Match codec_dev_test: use I2C master on IDF 5.3+ unless backward compatible */
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 3, 0) && !CONFIG_CODEC_I2C_BACKWARD_COMPATIBLE
#define USE_I2C_MASTER    1
#endif

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#if USE_I2C_MASTER
#include "driver/i2c_master.h"
#else
#include "driver/i2c.h"
#endif
#include "esp_log.h"

#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"

#ifdef CONFIG_CODEC_ES8311_SUPPORT
#include "es8311_codec.h"
#else
#error "Speaker driver requires ES8311. Enable CONFIG_CODEC_ES8311_SUPPORT in esp_codec_dev."
#endif

#include "audio/speaker_config.h"

static const char *TAG = "speaker";

/* --- State --- */
#if USE_I2C_MASTER
static i2c_master_bus_handle_t s_i2c_bus;
#else
static bool s_i2c_installed;
#endif

static i2s_chan_handle_t s_tx_handle;
static i2s_chan_handle_t s_rx_handle;  /* RX channel for mic/ADC input (shared I2S bus) */

static const audio_codec_data_if_t *s_data_if;
static const audio_codec_ctrl_if_t *s_ctrl_if;
static const audio_codec_gpio_if_t *s_gpio_if;
static const audio_codec_if_t *s_codec_if;
static esp_codec_dev_handle_t s_codec_dev;

static bool s_initialized;
static bool s_opened;
static int s_volume = 100;

/* --- I2C --- */
static esp_err_t i2c_init(void)
{
#if USE_I2C_MASTER
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = SPEAKER_I2C_NUM,
        .sda_io_num = SPEAKER_I2C_SDA_GPIO,
        .scl_io_num = SPEAKER_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true, // using internal pullup on the board but better use external pullup resistor around 4.7k to 10k (standard time mode is 100kHz)
    };
    esp_err_t ret = i2c_new_master_bus(&bus_cfg, &s_i2c_bus);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2C master init failed: %s", esp_err_to_name(ret));
        return ret;
    }
#else
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = SPEAKER_I2C_SDA_GPIO,
        .scl_io_num = SPEAKER_I2C_SCL_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 100000,
    };
    esp_err_t ret = i2c_param_config(SPEAKER_I2C_NUM, &conf);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2C param_config failed: %s", esp_err_to_name(ret));
        return ret;
    }
    ret = i2c_driver_install(SPEAKER_I2C_NUM, conf.mode, 0, 0, 0);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2C driver install failed: %s", esp_err_to_name(ret));
        return ret;
    }
    s_i2c_installed = true;
#endif
    return ESP_OK;
}

static void i2c_deinit(void)
{
#if USE_I2C_MASTER
    if (s_i2c_bus) {
        i2c_del_master_bus(s_i2c_bus);
        s_i2c_bus = NULL;
    }
#else
    if (s_i2c_installed) {
        i2c_driver_delete(SPEAKER_I2C_NUM);
        s_i2c_installed = false;
    }
#endif
}

/* --- I2S --- */
static esp_err_t i2s_init(void)
{
    /* Match working i2s_es8311 example: auto_clear so DMA buffer has no legacy garbage */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;

    /* Create both TX (speaker) and RX (mic/ADC) on the same I2S bus */
    esp_err_t ret = i2s_new_channel(&chan_cfg, &s_tx_handle, &s_rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S new channel failed: %s", esp_err_to_name(ret));
        return ret;
    }

    /* Same slot/clk pattern as working i2s_es8311 example (16 bit stereo + MCLK multiple) */
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(16, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = SPEAKER_I2S_MCK_GPIO,
            .bclk = SPEAKER_I2S_BCK_GPIO,
            .ws = SPEAKER_I2S_WS_GPIO,
            .dout = SPEAKER_I2S_DOUT_GPIO,
            .din = SPEAKER_I2S_DIN_GPIO,
        },
    };
    std_cfg.clk_cfg.mclk_multiple = SPEAKER_I2S_MCLK_MULTIPLE;

    ret = i2s_channel_init_std_mode(s_tx_handle, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S TX std init failed: %s", esp_err_to_name(ret));
        i2s_del_channel(s_tx_handle);
        i2s_del_channel(s_rx_handle);
        return ret;
    }

    /* RX uses the same bus/clock pins; override slot to mono left for mic input */
    i2s_std_config_t rx_cfg = std_cfg;
    rx_cfg.slot_cfg = (i2s_std_slot_config_t)I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(16, I2S_SLOT_MODE_MONO);
    rx_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    ret = i2s_channel_init_std_mode(s_rx_handle, &rx_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX std init failed: %s", esp_err_to_name(ret));
        i2s_del_channel(s_tx_handle);
        i2s_del_channel(s_rx_handle);
        return ret;
    }

    ret = i2s_channel_enable(s_tx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S TX enable failed: %s", esp_err_to_name(ret));
        i2s_del_channel(s_tx_handle);
        i2s_del_channel(s_rx_handle);
        return ret;
    }

    ret = i2s_channel_enable(s_rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX enable failed: %s", esp_err_to_name(ret));
        i2s_channel_disable(s_tx_handle);
        i2s_del_channel(s_tx_handle);
        i2s_del_channel(s_rx_handle);
        return ret;
    }

    return ESP_OK;
}

static void i2s_deinit(void)
{
    if (s_rx_handle) {
        i2s_channel_disable(s_rx_handle);
        i2s_del_channel(s_rx_handle);
        s_rx_handle = NULL;
    }
    if (s_tx_handle) {
        i2s_channel_disable(s_tx_handle);
        i2s_del_channel(s_tx_handle);
        s_tx_handle = NULL;
    }
}

/* --- Codec setup --- */
static void pa_gpio_enable(void)
{
    if (SPEAKER_PA_GPIO < 0) return;

    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << SPEAKER_PA_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf);
    gpio_set_level(SPEAKER_PA_GPIO, 1);
    vTaskDelay(pdMS_TO_TICKS(100));  /* NS4150B amp power-up */
}

static esp_err_t create_codec_device(void)
{
    audio_codec_i2s_cfg_t i2s_cfg = {
        .rx_handle = NULL,
        .tx_handle = s_tx_handle,
    };
    audio_codec_i2c_cfg_t i2c_cfg = { .addr = ES8311_CODEC_DEFAULT_ADDR };
#if USE_I2C_MASTER
    i2c_cfg.bus_handle = s_i2c_bus;
#else
    i2c_cfg.port = SPEAKER_I2C_NUM;
#endif

    s_data_if = audio_codec_new_i2s_data(&i2s_cfg);
    if (!s_data_if) return ESP_FAIL;

    s_ctrl_if = audio_codec_new_i2c_ctrl(&i2c_cfg);
    if (!s_ctrl_if) return ESP_FAIL;

    s_gpio_if = audio_codec_new_gpio();
    if (!s_gpio_if) return ESP_FAIL;

    es8311_codec_cfg_t es8311_cfg = {
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .ctrl_if = s_ctrl_if,
        .gpio_if = s_gpio_if,
        .pa_pin = SPEAKER_PA_GPIO,
        .use_mclk = (SPEAKER_I2S_MCK_GPIO >= 0),
    };
    s_codec_if = es8311_codec_new(&es8311_cfg);
    if (!s_codec_if) return ESP_FAIL;

    esp_codec_dev_cfg_t dev_cfg = {
        .codec_if = s_codec_if,
        .data_if = s_data_if,
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
    };
    s_codec_dev = esp_codec_dev_new(&dev_cfg);
    return s_codec_dev ? ESP_OK : ESP_FAIL;
}

static void cleanup_codec_resources(void)
{
    if (s_codec_dev) {
        esp_codec_dev_delete(s_codec_dev);
        s_codec_dev = NULL;
    }
    if (s_codec_if) {
        audio_codec_delete_codec_if(s_codec_if);
        s_codec_if = NULL;
    }
    if (s_ctrl_if) {
        audio_codec_delete_ctrl_if(s_ctrl_if);
        s_ctrl_if = NULL;
    }
    if (s_gpio_if) {
        audio_codec_delete_gpio_if(s_gpio_if);
        s_gpio_if = NULL;
    }
    if (s_data_if) {
        audio_codec_delete_data_if(s_data_if);
        s_data_if = NULL;
    }
}

/* --- Public API --- */
esp_err_t speaker_init(void)
{
    if (s_initialized) return ESP_OK;

    esp_err_t ret = i2c_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "speaker_init I2C failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2s_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "speaker_init I2S failed: %s", esp_err_to_name(ret));
        i2c_deinit();
        return ret;
    }

    ret = create_codec_device();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "speaker_init codec failed");
        i2s_deinit();
        i2c_deinit();
        return ret;
    }

    if (esp_codec_dev_set_out_vol(s_codec_dev, s_volume) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "Set volume failed");
    }

    pa_gpio_enable();

    s_initialized = true;
    ESP_LOGI(TAG, "Speaker initialized (ES8311, PA_GPIO=%d)", SPEAKER_PA_GPIO);
    return ESP_OK;
}

esp_err_t speaker_deinit(void)
{
    if (!s_initialized) return ESP_OK;

    speaker_close();
    cleanup_codec_resources();
    i2s_deinit();
    i2c_deinit();
    s_initialized = false;
    ESP_LOGI(TAG, "Speaker deinitialized");
    return ESP_OK;
}

bool speaker_is_ready(void)
{
    return s_initialized;
}

/* --- Volume --- */
esp_err_t speaker_set_volume(int volume)
{
    if (volume < SPEAKER_VOL_MIN) volume = SPEAKER_VOL_MIN;
    if (volume > SPEAKER_VOL_MAX) volume = SPEAKER_VOL_MAX;
    s_volume = volume;

    if (!s_codec_dev) return ESP_ERR_INVALID_STATE;
    int ret = esp_codec_dev_set_out_vol(s_codec_dev, volume);
    return (ret == ESP_CODEC_DEV_OK) ? ESP_OK : ESP_FAIL;
}

esp_err_t speaker_get_volume(int *volume)
{
    if (!volume) return ESP_ERR_INVALID_ARG;
    *volume = s_volume;
    if (s_codec_dev) {
        int vol;
        if (esp_codec_dev_get_out_vol(s_codec_dev, &vol) == ESP_CODEC_DEV_OK) {
            *volume = vol;
        }
    }
    return ESP_OK;
}

/* --- Playback --- */
esp_err_t speaker_open(int sample_rate, int channels)
{
    if (!s_initialized || !s_codec_dev) {
        ESP_LOGE(TAG, "speaker_open: invalid state");
        return ESP_ERR_INVALID_STATE;
    }
    if (s_opened) return ESP_OK;

    /* Clamp channels to 1 or 2 */
    if (channels < 1) channels = 1;
    if (channels > 2) channels = 2;

    esp_codec_dev_sample_info_t fs = {
        .sample_rate = sample_rate,
        .channel = channels,
        .bits_per_sample = SPEAKER_BITS_PER_SAMPLE,
    };

    int ret = esp_codec_dev_open(s_codec_dev, &fs);
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "speaker_open failed: %d", ret);
        return ESP_FAIL;
    }
    s_opened = true;
    return ESP_OK;
}

esp_err_t speaker_close(void)
{
    if (!s_opened || !s_codec_dev) return ESP_OK;

    esp_codec_dev_close(s_codec_dev);
    s_opened = false;
    /* Codec layer may disable I2S channels on close. Re-enable both TX and RX
     * so the next speaker_open() works and the mic feed task keeps running.
     * Ignore ESP_ERR_INVALID_STATE if already enabled. */
    if (s_tx_handle) {
        esp_err_t e = i2s_channel_enable(s_tx_handle);
        if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "TX re-enable failed: %s", esp_err_to_name(e));
        }
    }
    if (s_rx_handle) {
        esp_err_t e = i2s_channel_enable(s_rx_handle);
        if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "RX re-enable failed: %s", esp_err_to_name(e));
        }
    }
    return ESP_OK;
}

esp_err_t speaker_write(const void *data, size_t len)
{
    if (!s_codec_dev || !s_opened) {
        ESP_LOGE(TAG, "speaker_write: invalid state");
        return ESP_ERR_INVALID_STATE;
    }
    if (!data || len == 0) {
        ESP_LOGE(TAG, "speaker_write: invalid arg");
        return ESP_ERR_INVALID_ARG;
    }

    int ret = esp_codec_dev_write(s_codec_dev, (void *)data, (int)len);
    if (ret < 0) {
        ESP_LOGE(TAG, "speaker_write failed: %d", ret);
        return ESP_FAIL;
    }
    return ESP_OK;
}

/* --- Beep --- */
/* 440 Hz sine wave, 16-bit mono, 16000 Hz */
static const int16_t s_beep_440[] = {
    0, 831, 1638, 2412, 3135, 3792, 4368, 4852, 5234, 5507, 5665, 5704,
    5622, 5419, 5099, 4667, 4130, 3497, 2778, 1986, 1134, 236, -700, -1649,
    -2578, -3472, -4317, -5100, -5808, -6431, -6961, -7391, -7715, -7930,
    -8034, -8026, -7906, -7678, -7345, -6913, -6389, -5781, -5098, -4351,
    -3551, -2710, -1842, -959, -79, 799, 1664, 2502, 3297, 4037, 4711, 5309,
    5822, 6243, 6566, 6787, 6904, 6916, 6824, 6630, 6338, 5952, 5479, 4925,
    4299, 3610, 2868, 2084, 1270, 439, -397, -1226, -2035, -2813, -3548,
    -4230, -4849, -5397, -5867, -6252, -6548, -6752, -6861, -6874, -6791,
    -6613, -6343, -5985, -5544, -5026, -4438, -3789, -3086, -2340, -1560,
    -756, 63, 878, 1682, 2459, 3198, 3887, 4517, 5079, 5565, 5970, 6289,
    6519, 6656, 6700, 6650, 6508, 6276, 5958, 5558, 5082, 4535, 3925, 3259,
    2547, 1797, 1020, 226, -574, -1368, -2147, -2898, -3612, -4279, -4890,
    -5437, -5914, -6314, -6633, -6866, -7012, -7069, -7036, -6914, -6704,
    -6410, -6035, -5584, -5063, -4478, -3836, -3146, -2415, -1654, -871,
    -83, 706, 1486, 2245, 2972, 3658, 4293, 4870, 5381, 5820, 6182, 6463,
    6660, 6771, 6795, 6733, 6585, 6354, 6043, 5656, 5197, 4672, 4087, 3450,
    2768, 2050, 1305, 542, -229, -994, -1744, -2469, -3160, -3808, -4405,
    -4944, -5418, -5823, -6154, -6408, -6583, -6677, -6690, -6621, -6472,
    -6244, -5940, -5563, -5118, -4610, -4045, -3430, -2772, -2079, -1361,
    -624, 120, 861, 1589, 2293, 2964, 3594, 4175, 4700, 5163, 5559, 5885,
    6136, 6310, 6406, 6423, 6360, 6219, 6001, 5709, 5346, 4916, 4424, 3875,
    3275, 2630, 1948, 1236, 502, -242, -983, -1713, -2423, -3104, -3747,
    -4344, -4887, -5370, -5787, -6133, -6405, -6599, -6714, -6749, -6703,
    -6578, -6374, -6094, -5741, -5318, -4830, -4282, -3680, -3031, -2342,
    -1622, -879, -123, 635, 1385, 2119, 2827, 3501, 4132, 4714, 5240, 5705,
    6103, 6431, 6685, 6863, 6963, 6985, 6929, 6796, 6588, 6307, 5957, 5541,
    5064, 4530, 3946, 3317, 2651, 1955, 1237, 504, -235, -972, -1699,
    -2409, -3092, -3740, -4346, -4902, -5403, -5843, -6217, -6522, -6755,
    -6913, -6995, -7000, -6928, -6780, -6558, -6264, -5902, -5476, -4991,
    -4452, -3866, -3238, -2576, -1887, -1179, -460, 264, 982, 1689, 2375,
    3031, 3648, 4219, 4737, 5196, 5591, 5917, 6172, 6352, 6456, 6483,
    6433, 6307, 6107, 5835, 5495, 5091, 4628, 4112, 3549, 2946, 2311, 1651,
    975, 291, -395, -1076, -1744, -2390, -3006, -3584, -4118, -4601, -5028,
    -5394, -5695, -5928, -6091, -6182, -6200, -6145, -6018, -5820, -5552,
    -5217, -4818, -4360, -3847, -3285, -2680, -2038, -1367, -673, 32, 739,
    1440, 2130, 2798, 3435, 4033, 4585, 5084, 5525, 5903, 6214, 6455,
    6623, 6717, 6735, 6678, 6547, 6343, 6068, 5725, 5318, 4852, 4332,
    3764, 3155, 2511, 1839, 1147, 441, -271, -981, -1680, -2358, -3007,
};

#define BEEP_SAMPLES  (sizeof(s_beep_440) / sizeof(s_beep_440[0]))
#define BEEP_MS       400
#define BEEP_SR       16000
#define BEEP_REPEAT   ((BEEP_SR * BEEP_MS / 1000) / BEEP_SAMPLES)

static void mono_to_stereo_interleaved(const int16_t *mono, int16_t *stereo, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        stereo[i * 2]     = mono[i];
        stereo[i * 2 + 1] = mono[i];
    }
}

esp_err_t speaker_beep(void)
{
    if (SPEAKER_PA_GPIO >= 0) gpio_set_level(SPEAKER_PA_GPIO, 1);

    esp_err_t ret = speaker_open(BEEP_SR, 2);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "speaker_beep open failed: %s", esp_err_to_name(ret));
        return ret;
    }

    esp_codec_dev_set_out_vol(s_codec_dev, 100);
    vTaskDelay(pdMS_TO_TICKS(30));

    int16_t stereo_buf[BEEP_SAMPLES * 2];
    mono_to_stereo_interleaved(s_beep_440, stereo_buf, BEEP_SAMPLES);
    size_t stereo_len = sizeof(stereo_buf);

    for (int i = 0; i < BEEP_REPEAT; i++) {
        ret = speaker_write(stereo_buf, stereo_len);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "speaker_beep write failed: %s", esp_err_to_name(ret));
            speaker_close();
            return ret;
        }
    }

    vTaskDelay(pdMS_TO_TICKS(BEEP_MS + 80));  /* Let DMA drain */
    speaker_close();
    return ESP_OK;
}

i2s_chan_handle_t speaker_get_rx_handle(void)
{
    return s_rx_handle;
}

#else /* !CONFIG_SPEAKER_ENABLE - stub implementation */

esp_err_t speaker_init(void) { return ESP_ERR_NOT_SUPPORTED; }
esp_err_t speaker_deinit(void) { return ESP_OK; }
bool speaker_is_ready(void) { return false; }
esp_err_t speaker_set_volume(int vol) { (void)vol; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t speaker_get_volume(int *vol) { if (vol) *vol = 0; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t speaker_open(int sr, int ch) { (void)sr; (void)ch; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t speaker_close(void) { return ESP_OK; }
esp_err_t speaker_write(const void *d, size_t len) { (void)d; (void)len; return ESP_ERR_NOT_SUPPORTED; }
esp_err_t speaker_beep(void) { return ESP_ERR_NOT_SUPPORTED; }
i2s_chan_handle_t speaker_get_rx_handle(void) { return NULL; }

#endif
