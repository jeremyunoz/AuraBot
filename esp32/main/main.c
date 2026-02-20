#include <stdio.h>

#include "sdkconfig.h"
#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"

#include "audio/speaker.h"
#include "audio/tts.h"
#include "audio/wakeword.h"
#include "network/wifi_connect.h"
#include "network/mqtt.h"
#include "sensors/pir.h"
#include "driver/gpio.h"
#include "motion/action.h"
#include "motion/servo.h"

#include "system/system_events.h"
#include "display/lcd_lvgl.h"
#include "display/robot_eyes.h"
#include "voice/voice_session.h"
#include "voice/voice_ws.h"

static const char *TAG = "main";

typedef enum {
    SYS_STATE_IDLE = 0,
    SYS_STATE_WAKING,
    SYS_STATE_ACTIVE,
    SYS_STATE_SLEEPING
} sys_state_t;

#define STATE_TASK_STACK_SIZE    4096
#define PIR_TASK_STACK_SIZE     4096
#define PIR_WARMUP_TASK_STACK   1536
#define EVT_QUEUE_LEN           10
#define PIR_EVENT_BIT           BIT0
#define READY_PIR_BIT           BIT1
#define READY_WS_BIT            BIT2
#define READY_BOTH_TIMEOUT_MS   20000

static QueueHandle_t s_evt_queue = NULL;
static EventGroupHandle_t s_pir_evt = NULL;
static EventGroupHandle_t s_ready_evt = NULL;  /* used in enter_waking: PIR + WS ready */
static sys_state_t s_state = SYS_STATE_IDLE;
static bool s_pir_configured = false;

static const char *state_to_str(sys_state_t state)
{
    switch (state) {
    case SYS_STATE_IDLE:
        return "IDLE";
    case SYS_STATE_WAKING:
        return "WAKING";
    case SYS_STATE_ACTIVE:
        return "ACTIVE";
    case SYS_STATE_SLEEPING:
        return "SLEEPING";
    default:
        return "UNKNOWN";
    }
}

static void publish_state(sys_state_t state)
{
    char msg[128];
    snprintf(msg, sizeof(msg), "{\"src\":\"esp32\",\"state\":\"%s\"}", state_to_str(state));
    (void)mqtt_publish("aurabot/status", msg, 1, 1);
}

static void publish_pir_status(const char *status)
{
    char msg[128];
    snprintf(msg, sizeof(msg), "{\"src\":\"esp32\",\"pir\":\"%s\"}", status);
    (void)mqtt_publish("aurabot/sensors", msg, 1, 0);
}

static roboeyes_state_t sys_to_eye_state(sys_state_t s)
{
    switch (s) {
    case SYS_STATE_WAKING:   return EYE_STATE_WAKING;
    case SYS_STATE_ACTIVE:   return EYE_STATE_ACTIVE;
    case SYS_STATE_SLEEPING: return EYE_STATE_SLEEPING;
    default:                 return EYE_STATE_IDLE;
    }
}

static action_id_t sys_to_action(sys_state_t s)
{
    switch (s) {
    case SYS_STATE_IDLE:     return ACTION_STAND;
    case SYS_STATE_WAKING:   return ACTION_WAVE;
    case SYS_STATE_ACTIVE:   return ACTION_STAND;
    case SYS_STATE_SLEEPING: return ACTION_LAY_DOWN;
    default:                 return ACTION_SIT;
    }
}

static void set_state(sys_state_t state)
{
    s_state = state;
    roboeyes_set_state(sys_to_eye_state(state));
    action_post(sys_to_action(state));
    action_set_user_control(state == SYS_STATE_ACTIVE);
    publish_state(state);
    /* Voice session is started in enter_waking() (parallel with PIR warmup); not here */
    ESP_LOGI(TAG, "State -> %s", state_to_str(state));
}

static void pir_task(void *arg)
{
    (void)arg;
    while (1) {
        if (!s_pir_evt) {
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        xEventGroupWaitBits(
            s_pir_evt,
            PIR_EVENT_BIT,
            pdTRUE,
            pdFALSE,
            portMAX_DELAY
        );

        if (s_state == SYS_STATE_ACTIVE && mqtt_is_connected()) {
            char msg[128];
            uint32_t count = pir_get_count();
            snprintf(msg, sizeof(msg), "{\"src\":\"esp32\",\"motion\":1,\"count\":%u}", (unsigned)count);
            (void)mqtt_publish("aurabot/sensors", msg, 1, 0);
        }
    }
}

static void enter_sleeping(void)
{
    voice_session_stop();
    set_state(SYS_STATE_SLEEPING); // announce over MQTT while still connected

    /* Give the servos time to reach the lay-down pose before teardown. */
    vTaskDelay(pdMS_TO_TICKS(1500));

#if CONFIG_MQTT_ENABLE
    mqtt_stop();
#endif
    wifi_disconnect_sta();

    /* Connectivity is down -- update local state and pose to idle / sit. */
    s_state = SYS_STATE_IDLE;
    roboeyes_set_state(EYE_STATE_IDLE);
    action_post(ACTION_SIT);
    action_set_user_control(false);
    ESP_LOGI(TAG, "State -> %s", state_to_str(s_state));
}

#if CONFIG_VOICE_SESSION_ENABLE
static void ws_ready_cb(void *arg)
{
    (void)arg;
    if (s_ready_evt) {
        xEventGroupSetBits(s_ready_evt, READY_WS_BIT);
    }
}
#endif

static void pir_warmup_task(void *arg)
{
    EventGroupHandle_t ready = (EventGroupHandle_t)arg;
    vTaskDelay(pdMS_TO_TICKS(CONFIG_PIR_WARMUP_MS));
    if (ready) {
        xEventGroupSetBits(ready, READY_PIR_BIT);
    }
    vTaskDelete(NULL);
}

static void enter_waking(void)
{
    set_state(SYS_STATE_WAKING);

    ESP_LOGI(TAG, "Starting WiFi station");
    wifi_sta_cfg_t cfg = {
        .ssid = CONFIG_ESP_WIFI_SSID,
        .password = CONFIG_ESP_WIFI_PASSWORD,
        .max_retry = CONFIG_ESP_MAXIMUM_RETRY,
    };

    if (wifi_connect_sta(&cfg) != ESP_OK) {
        ESP_LOGE(TAG, "WiFi connect failed");
        enter_sleeping();
        return;
    }
    ESP_LOGI(TAG, "WiFi connected");

    /* Event group for "ready": PIR warmup and WebSocket connected (both in parallel with MQTT) */
    s_ready_evt = xEventGroupCreate();
    if (!s_ready_evt) {
        ESP_LOGE(TAG, "Failed to create ready event group");
        enter_sleeping();
        return;
    }
    xEventGroupClearBits(s_ready_evt, READY_PIR_BIT | READY_WS_BIT);

    /* PIR hardware + start warmup task (runs in parallel from here) */
    if (!s_pir_evt) {
        s_pir_evt = xEventGroupCreate();
    }
    if (s_pir_evt && !s_pir_configured) {
        pir_t pir = { .pin = (gpio_num_t)CONFIG_PIR_GPIO };
        if (pir_int_interrupt(&pir, s_pir_evt, PIR_EVENT_BIT) == ESP_OK) {
            s_pir_configured = true;
        }
    }
    xTaskCreate(pir_warmup_task, "pir_warmup", PIR_WARMUP_TASK_STACK, s_ready_evt, 4, NULL);
    ESP_LOGI(TAG, "PIR warmup started (parallel)");

#if CONFIG_MQTT_ENABLE
    if (mqtt_start() != ESP_OK) {
        ESP_LOGE(TAG, "MQTT start failed");
        vEventGroupDelete(s_ready_evt);
        s_ready_evt = NULL;
        enter_sleeping();
        return;
    }

    int wait_ms = 0;
    while (!mqtt_is_connected() && wait_ms < 10000) {
        vTaskDelay(pdMS_TO_TICKS(200));
        wait_ms += 200;
    }
    if (!mqtt_is_connected()) {
        ESP_LOGE(TAG, "MQTT connect timeout");
        vEventGroupDelete(s_ready_evt);
        s_ready_evt = NULL;
        enter_sleeping();
        return;
    }
    ESP_LOGI(TAG, "MQTT connected (waited %d ms)");

    (void)mqtt_publish(
        "aurabot/status",
        "{\"src\":\"esp32\",\"state\":\"WAKING\",\"wifi\":\"up\",\"mqtt\":\"up\",\"pir\":\"warming\"}",
        1,
        1
    );
#else
    ESP_LOGI(TAG, "MQTT disabled (voice-session-only test)");
#endif

#if CONFIG_VOICE_SESSION_ENABLE
    voice_ws_set_connected_callback(ws_ready_cb, NULL);
    esp_err_t start_err = voice_session_start();
    if (start_err != ESP_OK) {
        ESP_LOGE(TAG, "voice_session_start failed %s", esp_err_to_name(start_err));
        vEventGroupDelete(s_ready_evt);
        s_ready_evt = NULL;
        enter_sleeping();
        return;
    }
    ESP_LOGI(TAG, "voice_session_start done, waiting for PIR + WebSocket ready");

    EventBits_t bits = xEventGroupWaitBits(
        s_ready_evt,
        READY_PIR_BIT | READY_WS_BIT,
        pdTRUE,
        pdTRUE,
        pdMS_TO_TICKS(READY_BOTH_TIMEOUT_MS)
    );
    vEventGroupDelete(s_ready_evt);
    s_ready_evt = NULL;

    if ((bits & (READY_PIR_BIT | READY_WS_BIT)) != (READY_PIR_BIT | READY_WS_BIT)) {
        ESP_LOGE(TAG, "Timeout waiting for PIR warmup + WebSocket (got 0x%lx)", (unsigned long)bits);
        voice_session_stop();
        enter_sleeping();
        return;
    }
    ESP_LOGI(TAG, "PIR warmup + WebSocket ready, going ACTIVE");
#else
    /* No voice session: wait only for PIR warmup */
    EventBits_t bits = xEventGroupWaitBits(
        s_ready_evt,
        READY_PIR_BIT,
        pdTRUE,
        pdTRUE,
        pdMS_TO_TICKS(CONFIG_PIR_WARMUP_MS + 2000)
    );
    vEventGroupDelete(s_ready_evt);
    s_ready_evt = NULL;
    if ((bits & READY_PIR_BIT) == 0) {
        ESP_LOGE(TAG, "Timeout waiting for PIR warmup");
        enter_sleeping();
        return;
    }
    ESP_LOGI(TAG, "PIR warmup ready, going ACTIVE");
#endif

    pir_reset_count();
    publish_pir_status("warm");
    set_state(SYS_STATE_ACTIVE);
}

static void state_task(void *arg)
{
    (void)arg;
    sys_event_t evt;

    while (1) {
        if (xQueueReceive(s_evt_queue, &evt, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        switch (s_state) {
        case SYS_STATE_IDLE:
            if (evt.id == SYS_EVT_WAKE_DETECTED || evt.id == SYS_EVT_FORCE_WAKE) {
                enter_waking();
            }
            break;

        case SYS_STATE_ACTIVE:
            if (evt.id == SYS_EVT_SESSION_END) {
                enter_sleeping();
            }
            break;

        case SYS_STATE_WAKING:
        case SYS_STATE_SLEEPING:
        default:
            break;
        }
    }
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    /* ---- LCD + LVGL display ---- */
    lv_display_t *disp = lcd_lvgl_init();
    if (disp) {
        lcd_lvgl_lock();
        roboeyes_init(disp);
        lcd_lvgl_unlock();
        ESP_LOGI(TAG, "RoboEyes UI loaded");

        /* Start in IDLE; other subsystems call roboeyes_set_state()
           when the system state changes (WAKING → ACTIVE → SLEEPING). */
        roboeyes_set_state(EYE_STATE_IDLE);
    } else {
        ESP_LOGE(TAG, "LCD/LVGL init failed");
    }

    /* ---- Servo / action subsystem ---- */
    action_task_start();

    /* Initialize speaker (also sets up I2S RX for mic input) */
#if CONFIG_SPEAKER_ENABLE
    ret = speaker_init();
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Speaker initialized");
        ret = tts_init();
        if (ret == ESP_OK) {
            ESP_LOGI(TAG, "TTS initialized");
            tts_speak("Hello, I am Aurabot.");
        } else {
            ESP_LOGE(TAG, "TTS init failed: %s", esp_err_to_name(ret));
        }
    } else {
        ESP_LOGE(TAG, "Speaker init failed: %s", esp_err_to_name(ret));
    }
#endif

    s_evt_queue = xQueueCreate(EVT_QUEUE_LEN, sizeof(sys_event_t));
    if (!s_evt_queue) {
        ESP_LOGE(TAG, "Failed to create event queue");
        return;
    }
    wakeword_set_event_queue(s_evt_queue);
#if CONFIG_MQTT_ENABLE
    mqtt_set_event_queue(s_evt_queue);
#endif

    s_pir_evt = xEventGroupCreate();
    if (!s_pir_evt) {
        ESP_LOGW(TAG, "Failed to create PIR event group");
    } else {
        xTaskCreate(pir_task, "pir_task", PIR_TASK_STACK_SIZE, NULL, 5, NULL);
    }

    /* Start continuous wake-word detection */
    ret = wakeword_start();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Wake word start failed: %s", esp_err_to_name(ret));
    }

    set_state(SYS_STATE_IDLE);

    xTaskCreate(state_task, "state_task", STATE_TASK_STACK_SIZE, NULL, 6, NULL);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
