#include <stdio.h>
#include <stdbool.h>

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
#include "driver/gpio.h"
#include "motion/action.h"
#include "motion/servo.h"

#include "system/system_events.h"
#include "display/lcd_lvgl.h"
#include "display/robot_eyes.h"
#include "sensors/usonic.h"
#include "voice/voice_session.h"
#include "voice/voice_ws.h"

static const char *TAG = "main";

typedef enum {
    SYS_STATE_IDLE = 0,
    SYS_STATE_WAKING,
    SYS_STATE_CONNECTING,
    SYS_STATE_LISTENING,
    SYS_STATE_SPEAKING,
    SYS_STATE_SLEEPING
} sys_state_t;

#define STATE_TASK_STACK_SIZE    4096
#define USONIC_TASK_STACK_SIZE   3072
#define EVT_QUEUE_LEN            24
#define STATUS_PUBLISH_PERIOD_MS 30000

static QueueHandle_t s_evt_queue = NULL;
static volatile sys_state_t s_state = SYS_STATE_IDLE;
static TickType_t  s_active_since = 0;
#define MIN_ACTIVE_FOR_SLEEP_MS  5000

static bool state_is_voice_live(sys_state_t state)
{
    return state == SYS_STATE_LISTENING || state == SYS_STATE_SPEAKING;
}

static const char *state_to_str(sys_state_t state)
{
    switch (state) {
    case SYS_STATE_IDLE:
        return "IDLE";
    case SYS_STATE_WAKING:
        return "WAKING";
    case SYS_STATE_CONNECTING:
        return "CONNECTING";
    case SYS_STATE_LISTENING:
        return "LISTENING";
    case SYS_STATE_SPEAKING:
        return "SPEAKING";
    case SYS_STATE_SLEEPING:
        return "SLEEPING";
    default:
        return "UNKNOWN";
    }
}

static void publish_state(sys_state_t state)
{
    char msg[128];
    const bool voice_ready = state_is_voice_live(state);
    const char *phase =
        (state == SYS_STATE_CONNECTING) ? "connecting" :
        (state == SYS_STATE_LISTENING) ? "listen" :
        (state == SYS_STATE_SPEAKING) ? "speak" :
        (state == SYS_STATE_WAKING) ? "waking" :
        (state == SYS_STATE_SLEEPING) ? "sleeping" : "idle";
    snprintf(
        msg,
        sizeof(msg),
        "{\"src\":\"esp32\",\"state\":\"%s\",\"voice_phase\":\"%s\",\"voice_ready\":%s}",
        state_to_str(state),
        phase,
        voice_ready ? "true" : "false"
    );
    (void)mqtt_publish("aurabot/status", msg, 1, 1);
}

static roboeyes_state_t sys_to_eye_state(sys_state_t s)
{
    switch (s) {
    case SYS_STATE_WAKING:   return EYE_STATE_WAKING;
    case SYS_STATE_CONNECTING:
    case SYS_STATE_LISTENING:
    case SYS_STATE_SPEAKING: return EYE_STATE_ACTIVE;
    case SYS_STATE_SLEEPING: return EYE_STATE_SLEEPING;
    default:                 return EYE_STATE_IDLE;
    }
}

static action_id_t sys_to_action(sys_state_t s)
{
    switch (s) {
    case SYS_STATE_IDLE:     return ACTION_SIT;
    case SYS_STATE_WAKING:   return ACTION_WAVE;
    case SYS_STATE_CONNECTING:
    case SYS_STATE_LISTENING:
    case SYS_STATE_SPEAKING: return ACTION_STAND;
    case SYS_STATE_SLEEPING: return ACTION_LAY_DOWN;
    default:                 return ACTION_SIT;
    }
}

static void set_state(sys_state_t state)
{
    sys_state_t prev_state = (sys_state_t)s_state;
    s_state = state;
    if (state_is_voice_live(state) && !state_is_voice_live(prev_state)) {
        s_active_since = xTaskGetTickCount();
    }
    roboeyes_set_state(sys_to_eye_state(state));
    action_post(sys_to_action(state));
    action_set_user_control(state_is_voice_live(state));
    publish_state(state);
    ESP_LOGI(TAG, "State -> %s", state_to_str(state));
}


static void enter_error_idle(void)
{
    voice_session_stop();
#if CONFIG_MQTT_ENABLE
    mqtt_stop();
#endif
    wifi_disconnect_sta();

    s_state = SYS_STATE_IDLE;
    roboeyes_set_state(EYE_STATE_IDLE);
    action_post(ACTION_SIT);
    action_set_user_control(false);
    ESP_LOGI(TAG, "State -> %s (error)", state_to_str(s_state));
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
        enter_error_idle();
        return;
    }
    ESP_LOGI(TAG, "WiFi connected");

#if CONFIG_MQTT_ENABLE
    if (mqtt_start() != ESP_OK) {
        ESP_LOGE(TAG, "MQTT start failed");
        enter_error_idle();
        return;
    }

    int wait_ms = 0;
    while (!mqtt_is_connected() && wait_ms < 10000) {
        vTaskDelay(pdMS_TO_TICKS(200));
        wait_ms += 200;
    }
    if (!mqtt_is_connected()) {
        ESP_LOGE(TAG, "MQTT connect timeout");
        enter_error_idle();
        return;
    }
    ESP_LOGI(TAG, "MQTT connected (waited %d ms)");

    (void)mqtt_publish(
        "aurabot/status",
        "{\"src\":\"esp32\",\"state\":\"WAKING\",\"wifi\":\"up\",\"mqtt\":\"up\"}",
        1,
        1
    );
#else
    ESP_LOGI(TAG, "MQTT disabled (voice-session-only test)");
#endif

#if CONFIG_VOICE_SESSION_ENABLE
    esp_err_t start_err = voice_session_start();
    if (start_err != ESP_OK) {
        ESP_LOGE(TAG, "voice_session_start failed %s", esp_err_to_name(start_err));
        enter_error_idle();
        return;
    }
    ESP_LOGI(TAG, "voice_session_start done, waiting for backend ready");
#else
    ESP_LOGI(TAG, "WiFi/MQTT ready, entering LISTENING (no voice session)");
#endif

    set_state(
#if CONFIG_VOICE_SESSION_ENABLE
        SYS_STATE_CONNECTING
#else
        SYS_STATE_LISTENING
#endif
    );
}

static void handle_session_end_for_active_state(void)
{
    TickType_t active_dur = xTaskGetTickCount() - s_active_since;
    if (active_dur < pdMS_TO_TICKS(MIN_ACTIVE_FOR_SLEEP_MS)) {
        enter_error_idle();
    } else {
        enter_sleeping();
    }
}

static void state_task(void *arg)
{
    (void)arg;
    sys_event_t evt;
    const TickType_t status_period_ticks = pdMS_TO_TICKS(STATUS_PUBLISH_PERIOD_MS);

    while (1) {
        if (xQueueReceive(s_evt_queue, &evt, status_period_ticks) != pdTRUE) {
            publish_state((sys_state_t)s_state);
            continue;
        }

        switch (s_state) {
        case SYS_STATE_IDLE:
            if (evt.id == SYS_EVT_WAKE_DETECTED || evt.id == SYS_EVT_FORCE_WAKE) {
                enter_waking();
            }
            break;

        case SYS_STATE_WAKING:
        case SYS_STATE_CONNECTING:
            if (evt.id == SYS_EVT_MQTT_FAIL || evt.id == SYS_EVT_SESSION_END) {
                enter_error_idle();
            } else if (evt.id == SYS_EVT_PI5_READY) {
                ESP_LOGI(TAG, "Voice backend reported ready");
                publish_state((sys_state_t)s_state);
            } else if (evt.id == SYS_EVT_VOICE_LISTENING) {
                set_state(SYS_STATE_LISTENING);
            } else if (evt.id == SYS_EVT_VOICE_SPEAKING) {
                set_state(SYS_STATE_SPEAKING);
            }
            break;

        case SYS_STATE_LISTENING:
            if (evt.id == SYS_EVT_VOICE_SPEAKING) {
                set_state(SYS_STATE_SPEAKING);
            } else if (evt.id == SYS_EVT_SESSION_END) {
                handle_session_end_for_active_state();
            }
            break;

        case SYS_STATE_SPEAKING:
            if (evt.id == SYS_EVT_VOICE_LISTENING) {
                set_state(SYS_STATE_LISTENING);
            } else if (evt.id == SYS_EVT_SESSION_END) {
                handle_session_end_for_active_state();
            }
            break;

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
    action_post(ACTION_SIT);

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
    voice_session_set_event_queue(s_evt_queue);
    wakeword_set_event_queue(s_evt_queue);
#if CONFIG_MQTT_ENABLE
    mqtt_set_event_queue(s_evt_queue);
#endif

    /* Start continuous wake-word detection */
    ret = wakeword_start();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Wake word start failed: %s", esp_err_to_name(ret));
    }

    set_state(SYS_STATE_IDLE);

    xTaskCreate(state_task, "state_task", STATE_TASK_STACK_SIZE, NULL, 6, NULL);
    xTaskCreate(usonic_task, "usonic_task", USONIC_TASK_STACK_SIZE, NULL, 5, NULL);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
