#include <stdio.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "wifi_connect.h"
#include "mqtt.h"
#include "esp_timer.h"

static const char *TAG = "main";

static void publisher_task(void *arg)
{
    (void)arg;

    char payload[160];
    int counter = 0;

    while (1) {
        // Replace these placeholder values with real sensor reads.
        int motion = 1.5;
        float distance_cm = 42.5f;

        long long ts_us = (long long)esp_timer_get_time();

        // JSON payload that your Python subscriber can decode.
        snprintf(payload, sizeof(payload),
                 "{\"motion\":%d,\"distance_cm\":%.2f,\"count\":%d,\"ts_us\":%lld}",
                 motion, distance_cm, counter++, ts_us);

        esp_err_t err = mqtt_publish("aurabot/sensors", payload, 1, 0);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "mqtt_publish failed: %s", esp_err_to_name(err));
        }

        vTaskDelay(pdMS_TO_TICKS(1000));
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

    ESP_LOGI(TAG, "WiFi connected, main loop running");

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        ESP_LOGI(TAG, "Main loop alive");
    }
}