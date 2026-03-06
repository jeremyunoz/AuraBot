#include "sensors/usonic.h"

#include "esp_log.h"
#include "ultrasonic.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "motion/action.h"

/*
 * Keep ultrasonic off LCD pins:
 * lcd_lvgl.c uses GPIO21 (LCD DC) and GPIO20 (LCD CS).
 */
#define USONIC_TRIGGER_PIN 32
#define USONIC_ECHO_PIN 33

#define USONIC_MAX_DISTANCE_M 1.00f
#define USONIC_STOP_DISTANCE_M 0.10f
#define USONIC_POLL_MS 200

static const char *TAG = "usonic";

void usonic_task(void *pvParameters)
{
    (void)pvParameters;

    ultrasonic_sensor_t usonic = {
        .trigger_pin = USONIC_TRIGGER_PIN,
        .echo_pin = USONIC_ECHO_PIN,
    };

    esp_err_t init_err = ultrasonic_init(&usonic);
    if (init_err != ESP_OK) {
        ESP_LOGE(TAG, "ultrasonic_init failed: %s", esp_err_to_name(init_err));
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Ultrasonic task started (trig=%d echo=%d)", USONIC_TRIGGER_PIN, USONIC_ECHO_PIN);

    while (1) {
        const bool user_control_on = action_user_control_enabled();
        const action_id_t cmd = action_get_current_command();

        if (user_control_on && cmd == ACTION_WALK) {
            float distance_m = 0.0f;
            esp_err_t err = ultrasonic_measure(&usonic, USONIC_MAX_DISTANCE_M, &distance_m);

            if (err == ESP_OK && distance_m > 0.0f && distance_m < USONIC_STOP_DISTANCE_M) {
                ESP_LOGW(TAG, "Obstacle at %.2f cm, stopping walk", distance_m * 100.0f);
                action_post(ACTION_STAND);
            } else if (err != ESP_OK &&
                       err != ESP_ERR_ULTRASONIC_PING_TIMEOUT &&
                       err != ESP_ERR_ULTRASONIC_ECHO_TIMEOUT) {
                ESP_LOGW(TAG, "ultrasonic_measure failed: %s", esp_err_to_name(err));
            }
        }

        vTaskDelay(pdMS_TO_TICKS(USONIC_POLL_MS));
    }
}
