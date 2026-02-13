#include <stdio.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"

// #include "speaker.h"
// #include "tts.h"
// #include "wakeword.h"
// #include "wifi_connect.h"
// #include "mqtt.h"
// #include "pir.h"
// #include "driver/gpio.h"
// #include "action.h"
// #include "servo.h"

#include "system_events.h"
#include "lcd_lvgl.h"
#include "robot_eyes.h"

static const char *TAG = "main";

typedef enum {
    SYS_STATE_IDLE = 0,
    SYS_STATE_WAKING,
    SYS_STATE_ACTIVE,
    SYS_STATE_SLEEPING
} sys_state_t;

#define STATE_TASK_STACK_SIZE  4096
#define PIR_TASK_STACK_SIZE    4096
#define EVT_QUEUE_LEN          10
#define PIR_EVENT_BIT          BIT0

// static QueueHandle_t s_evt_queue = NULL;
// static EventGroupHandle_t s_pir_evt = NULL;
// static sys_state_t s_state = SYS_STATE_IDLE;
// static bool s_pir_configured = false;



// volatile int state = 0;
// int last_state = -1;

// static const char *state_to_str(sys_state_t state)
// {
//     switch (state) {
//     case SYS_STATE_IDLE:
//         return "IDLE";
//     case SYS_STATE_WAKING:
//         return "WAKING";
//     case SYS_STATE_ACTIVE:
//         return "ACTIVE";
//     case SYS_STATE_SLEEPING:
//         return "SLEEPING";
//     default:
//         return "UNKNOWN";
//     }
// }

// static void publish_state(sys_state_t state)
// {
//     char msg[128];
//     snprintf(msg, sizeof(msg), "{\"src\":\"esp32\",\"state\":\"%s\"}", state_to_str(state));
//     (void)mqtt_publish("aurabot/status", msg, 1, 1);
// }

// static void publish_pir_status(const char *status)
// {
//     char msg[128];
//     snprintf(msg, sizeof(msg), "{\"src\":\"esp32\",\"pir\":\"%s\"}", status);
//     (void)mqtt_publish("aurabot/sensors", msg, 1, 0);
// }

// static void set_state(sys_state_t state)
// {
//     s_state = state;
//     publish_state(state);
//     ESP_LOGI(TAG, "State -> %s", state_to_str(state));
// }

// static void pir_task(void *arg)
// {
//     (void)arg;
//     while (1) {
//         if (!s_pir_evt) {
//             vTaskDelay(pdMS_TO_TICKS(200));
//             continue;
//         }

//         xEventGroupWaitBits(
//             s_pir_evt,
//             PIR_EVENT_BIT,
//             pdTRUE,
//             pdFALSE,
//             portMAX_DELAY
//         );

//         if (s_state == SYS_STATE_ACTIVE && mqtt_is_connected()) {
//             char msg[128];
//             uint32_t count = pir_get_count();
//             snprintf(msg, sizeof(msg), "{\"src\":\"esp32\",\"motion\":1,\"count\":%u}", (unsigned)count);
//             (void)mqtt_publish("aurabot/sensors", msg, 1, 0);
//         }
//     }
// }

// static void enter_sleeping(void)
// {
//     set_state(SYS_STATE_SLEEPING);

//     mqtt_stop();
//     wifi_disconnect_sta();

//     s_state = SYS_STATE_IDLE;
//     ESP_LOGI(TAG, "State -> %s", state_to_str(s_state));
// }

// static void enter_waking(void)
// {
//     set_state(SYS_STATE_WAKING);

//     ESP_LOGI(TAG, "Starting WiFi station");
//     wifi_sta_cfg_t cfg = {
//         .ssid = CONFIG_ESP_WIFI_SSID,
//         .password = CONFIG_ESP_WIFI_PASSWORD,
//         .max_retry = CONFIG_ESP_MAXIMUM_RETRY,
//     };

//     if (wifi_connect_sta(&cfg) != ESP_OK) {
//         ESP_LOGE(TAG, "WiFi connect failed");
//         enter_sleeping();
//         return;
//     }

//     if (mqtt_start() != ESP_OK) {
//         ESP_LOGE(TAG, "MQTT start failed");
//         enter_sleeping();
//         return;
//     }

//     int wait_ms = 0;
//     while (!mqtt_is_connected() && wait_ms < 10000) {
//         vTaskDelay(pdMS_TO_TICKS(200));
//         wait_ms += 200;
//     }
//     if (!mqtt_is_connected()) {
//         ESP_LOGE(TAG, "MQTT connect timeout");
//         enter_sleeping();
//         return;
//     }

//     (void)mqtt_publish(
//         "aurabot/status",
//         "{\"src\":\"esp32\",\"state\":\"WAKING\",\"wifi\":\"up\",\"mqtt\":\"up\",\"pir\":\"warming\"}",
//         1,
//         1
//     );

//     if (!s_pir_evt) {
//         s_pir_evt = xEventGroupCreate();
//     }
//     if (s_pir_evt && !s_pir_configured) {
//         pir_t pir = { .pin = (gpio_num_t)CONFIG_PIR_GPIO };
//         if (pir_int_interrupt(&pir, s_pir_evt, PIR_EVENT_BIT) == ESP_OK) {
//             s_pir_configured = true;
//         }
//     }

//     vTaskDelay(pdMS_TO_TICKS(CONFIG_PIR_WARMUP_MS));
//     pir_reset_count();
//     publish_pir_status("warm");

//     set_state(SYS_STATE_ACTIVE);
// }

// static void state_task(void *arg)
// {
//     (void)arg;
//     sys_event_t evt;

//     while (1) {
//         if (xQueueReceive(s_evt_queue, &evt, portMAX_DELAY) != pdTRUE) {
//             continue;
//         }

//         switch (s_state) {
//         case SYS_STATE_IDLE:
//             if (evt.id == SYS_EVT_WAKE_DETECTED || evt.id == SYS_EVT_FORCE_WAKE) {
//                 enter_waking();
//             }
//             break;

//         case SYS_STATE_ACTIVE:
//             if (evt.id == SYS_EVT_SESSION_END) {
//                 enter_sleeping();
//             }
//             break;

//         case SYS_STATE_WAKING:
//         case SYS_STATE_SLEEPING:
//         default:
//             break;
//         }
//     }
// }

// /**
//  * @brief Reset to standing before a locomotion action.
//  */
// static void ensure_standing(void)
// {
//     stand();
//     delay_ms(250);
// }

// /**
//  * @brief Main action dispatcher task.
//  *
//  * Polls the global @c state variable and runs the corresponding action
//  * whenever it changes.
//  */
// void action_task(void *pvParameters)
// {
//     (void)pvParameters;
//
//     while (1) {
//         int current = state;
//
//         if (current != last_state) {
//             switch ((action_id_t)current) {
//             case ACTION_STAND:
//                 stand();
//                 break;
//
//             case ACTION_WALK:
//                 ensure_standing();
//                 walk();
//                 break;
//
//             case ACTION_BACK:
//                 ensure_standing();
//                 walk_back();
//                 break;
//
//             case ACTION_LAY_DOWN:
//                 lay_down();
//                 break;
//
//             case ACTION_TURN_LEFT:
//                 ensure_standing();
//                 turn_left();
//                 break;
//
//             case ACTION_TURN_RIGHT:
//                 ensure_standing();
//                 turn_right();
//                 break;
//
//             case ACTION_SIT:
//                 sit();
//                 break;
//
//             case ACTION_WAVE:
//                 wave();
//                 break;
//
//             case ACTION_SWING:
//                 ensure_standing();
//                 swing();
//                 break;
//
//             default:
//                 stand();
//                 break;
//             }
//
//             last_state = current;
//         }
//
//         vTaskDelay(pdMS_TO_TICKS(20));
//     }
// }



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

    // servo_init();

    // xTaskCreate(action_task, "action_task", 4096, NULL, 5, NULL);
//     /* Initialize speaker (also sets up I2S RX for mic input) */
// #if CONFIG_SPEAKER_ENABLE
//     ret = speaker_init();
//     if (ret == ESP_OK) {
//         ESP_LOGI(TAG, "Speaker initialized");
//         ret = tts_init();
//         if (ret == ESP_OK) {
//             ESP_LOGI(TAG, "TTS initialized");
//             tts_speak("Hello, I am Aurabot.");
//         } else {
//             ESP_LOGE(TAG, "TTS init failed: %s", esp_err_to_name(ret));
//         }
//     } else {
//         ESP_LOGE(TAG, "Speaker init failed: %s", esp_err_to_name(ret));
//     }
// #endif

//     s_evt_queue = xQueueCreate(EVT_QUEUE_LEN, sizeof(sys_event_t));
//     if (!s_evt_queue) {
//         ESP_LOGE(TAG, "Failed to create event queue");
//         return;
//     }
//     wakeword_set_event_queue(s_evt_queue);
//     mqtt_set_event_queue(s_evt_queue);

//     s_pir_evt = xEventGroupCreate();
//     if (!s_pir_evt) {
//         ESP_LOGW(TAG, "Failed to create PIR event group");
//     } else {
//         xTaskCreate(pir_task, "pir_task", PIR_TASK_STACK_SIZE, NULL, 5, NULL);
//     }

//     /* Start continuous wake-word detection */
//     ret = wakeword_start();
//     if (ret != ESP_OK) {
//         ESP_LOGE(TAG, "Wake word start failed: %s", esp_err_to_name(ret));
//     }

//     set_state(SYS_STATE_IDLE);

//     xTaskCreate(state_task, "state_task", STATE_TASK_STACK_SIZE, NULL, 6, NULL);

//     while (1) {
//         vTaskDelay(pdMS_TO_TICKS(10000));
//     }
}
