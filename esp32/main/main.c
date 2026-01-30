#include <stdio.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "wifi_connect.h"
#include "mqtt.h"
#include "esp_timer.h"
#include "pir.h"
#include "driver/gpio.h"

#define PIR_GPIO       GPIO_NUM_24
#define PIR_PRESENCE_BIT  (1 << 0)

static pir_t pir = {
    .pin = PIR_GPIO,
};

static const char *TAG = "main";

static void publisher_task(void *arg)
{
    (void)arg;

    char payload[200];
    EventGroupHandle_t event_group = xEventGroupCreate();
    if (event_group == NULL) {
        ESP_LOGE(TAG, "Failed to create PIR event group");
        vTaskDelete(NULL);
        return;
    }

    /* Initialize PIR sensor */
    esp_err_t ret = pir_int_interrupt(&pir, event_group, PIR_PRESENCE_BIT);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "PIR initialization failed: %s", esp_err_to_name(ret));
        vTaskDelete(NULL);
        return;
    }

    /* HC-SR501 needs warm-up time after power-on (20 seconds like Arduino code) */
    vTaskDelay(pdMS_TO_TICKS(20000));

    while (1) {
        /* Wait for PIR motion event (blocking wait, publishes immediately on detection) */
        EventBits_t bits = xEventGroupWaitBits(
            event_group,
            PIR_PRESENCE_BIT,
            pdTRUE,   // Clear bits on exit
            pdFALSE,  // Wait for any bit
            portMAX_DELAY  // Wait indefinitely
        );

        if (bits & PIR_PRESENCE_BIT) {
            /* Event detected - check GPIO level to determine if it's motion (HIGH) */
            int detected_level = gpio_get_level(pir.pin);
            
            /* Only publish on HIGH level (motion detected), ignore LOW (motion ended) */
            if (detected_level == 1) {
                /* Read counter safely (outside ISR, so no need for noInterrupts) */
                uint32_t count = pir_get_count();

                /* Build payload matching Arduino format */
                int motion = 1;
                int camera_confirmed = 0;  // Update later from camera / Pi
                float distance_cm = 0.0;    // Placeholder
                long long ts_us = (long long)esp_timer_get_time();

                snprintf(payload, sizeof(payload),
                         "{\"motion\":%d,\"camera_confirmed\":%d,\"distance_cm\":%.2f,\"ts_us\":%lld,\"count\":%lu}",
                         motion, camera_confirmed, distance_cm, ts_us, (unsigned long)count);

                esp_err_t err = mqtt_publish("aurabot/sensors", payload, 1, 0);
                if (err != ESP_OK) {
                    ESP_LOGW(TAG, "mqtt_publish failed: %s", esp_err_to_name(err));
                }
            }
        }
    }
}

void app_main(void)
{
    /* Initialize NVS (required for WiFi) */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    ESP_LOGI(TAG, "Starting WiFi station");

    wifi_sta_cfg_t cfg = {
        .ssid = CONFIG_ESP_WIFI_SSID,
        .password = CONFIG_ESP_WIFI_PASSWORD,
        .max_retry = CONFIG_ESP_MAXIMUM_RETRY,
    };

    ESP_ERROR_CHECK(wifi_connect_sta(&cfg));

    // Start periodic publishing of sensor data.
    xTaskCreate(publisher_task, "publisher_task", 4096, NULL, 5, NULL);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}