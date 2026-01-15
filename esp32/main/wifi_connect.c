#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_mac.h"
#include "esp_err.h"

#include "wifi_connect.h"
#include "mqtt.h"

static const char *TAG = "wifi_connect";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static EventGroupHandle_t s_wifi_event_group;
static int s_retry_num;
static int s_max_retry;
static bool mqtt_started = false;

static void event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
        return;
    }

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        // If WiFi drops, stop MQTT so it can cleanly reconnect after IP is back.
        if (mqtt_started) {
            mqtt_stop();
            mqtt_started = false;
        }

        if (s_retry_num < s_max_retry) {
            s_retry_num++;
            ESP_LOGI(TAG, "retry to connect to the AP, %d/%d", s_retry_num, s_max_retry);
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
        ESP_LOGI(TAG, "connect to the AP fail");
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&event->ip_info.ip));

        // Start MQTT only after we have an IP address.
        if (!mqtt_started) {
            esp_err_t err = mqtt_start();
            if (err == ESP_OK) {
                mqtt_started = true;
                ESP_LOGI(TAG, "MQTT started");
            } else {
                ESP_LOGE(TAG, "MQTT start failed: %s", esp_err_to_name(err));
            }
        }

        s_retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        return;
    }
}

esp_err_t wifi_connect_sta(const wifi_sta_cfg_t *cfg)
{
    if (!cfg || !cfg->ssid) return ESP_ERR_INVALID_ARG;

    s_wifi_event_group = xEventGroupCreate();
    if (!s_wifi_event_group) return ESP_ERR_NO_MEM;

    s_retry_num = 0;
    s_max_retry = (cfg->max_retry > 0) ? cfg->max_retry : 10;

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, NULL));

    wifi_config_t wifi_config = { 0 };
    strncpy((char *)wifi_config.sta.ssid, cfg->ssid, sizeof(wifi_config.sta.ssid));
    if (cfg->password) {
        strncpy((char *)wifi_config.sta.password, cfg->password, sizeof(wifi_config.sta.password));
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "wifi_connect_sta started, waiting for IP");

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE,
        pdFALSE,
        portMAX_DELAY
    );

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "connected to ap, SSID: %s", cfg->ssid);
        return ESP_OK;
    }

    ESP_LOGE(TAG, "failed to connect to SSID: %s", cfg->ssid);
    return ESP_FAIL;
}