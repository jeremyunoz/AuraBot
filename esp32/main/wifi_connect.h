#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *ssid;
    const char *password;
    int max_retry;
} wifi_sta_cfg_t;

/* Connects to WiFi and blocks until connected or failed.
   Returns ESP_OK on success, ESP_FAIL on failure. */
esp_err_t wifi_connect_sta(const wifi_sta_cfg_t *cfg);

#ifdef __cplusplus
}
#endif