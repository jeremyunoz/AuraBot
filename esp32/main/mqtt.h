#pragma once
#include "esp_err.h"
#include <stdbool.h>

esp_err_t mqtt_start(void);
void mqtt_stop(void);

bool mqtt_is_connected(void);

esp_err_t mqtt_publish(const char *topic, const char *payload, int qos, int retain);