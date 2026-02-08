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
static const char *TAG = "wifi_connect";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static EventGroupHandle_t s_wifi_event_group;
static int s_retry_num;
static int s_max_retry;
static bool s_allow_reconnect = true;
static bool s_netif_inited = false;
static bool s_event_loop_inited = false;
static bool s_wifi_inited = false;
static bool s_wifi_started = false;
static esp_netif_t *s_sta_netif = NULL;

static void event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
        return;
    }

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_allow_reconnect && s_retry_num < s_max_retry) {
            s_retry_num++;
            ESP_LOGI(TAG, "retry to connect to the AP, %d/%d", s_retry_num, s_max_retry);
            esp_wifi_connect();
        } else {
            if (s_wifi_event_group) {
                xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
            }
        }
        ESP_LOGI(TAG, "connect to the AP fail");
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&event->ip_info.ip));

        s_retry_num = 0;
        if (s_wifi_event_group) {
            xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        }
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

    if (!s_netif_inited) {
        ESP_ERROR_CHECK(esp_netif_init());
        s_netif_inited = true;
    }
    if (!s_event_loop_inited) {
        esp_err_t loop_err = esp_event_loop_create_default();
        if (loop_err != ESP_OK && loop_err != ESP_ERR_INVALID_STATE) {
            ESP_ERROR_CHECK(loop_err);
        }
        s_event_loop_inited = true;
    }
    if (!s_sta_netif) {
        s_sta_netif = esp_netif_create_default_wifi_sta();
    }

    if (!s_wifi_inited) {
        wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
        ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));

        ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, NULL));
        ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, NULL));
        s_wifi_inited = true;
    }

    wifi_config_t wifi_config = { 0 };
    strncpy((char *)wifi_config.sta.ssid, cfg->ssid, sizeof(wifi_config.sta.ssid));
    if (cfg->password) {
        strncpy((char *)wifi_config.sta.password, cfg->password, sizeof(wifi_config.sta.password));
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    s_allow_reconnect = true;
    if (!s_wifi_started) {
        ESP_ERROR_CHECK(esp_wifi_start());
        s_wifi_started = true;
    }

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
        vEventGroupDelete(s_wifi_event_group);
        s_wifi_event_group = NULL;
        return ESP_OK;
    }

    ESP_LOGE(TAG, "failed to connect to SSID: %s", cfg->ssid);
    vEventGroupDelete(s_wifi_event_group);
    s_wifi_event_group = NULL;
    return ESP_FAIL;
}

void wifi_disconnect_sta(void)
{
    s_allow_reconnect = false;
    if (s_wifi_inited) {
        (void)esp_wifi_disconnect();
        if (s_wifi_started) {
            (void)esp_wifi_stop();
            s_wifi_started = false;
        }
    }
}