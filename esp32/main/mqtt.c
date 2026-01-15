#include "mqtt.h"

#include "esp_log.h"
#include "mqtt_client.h"

static const char *TAG = "mqtt";

static esp_mqtt_client_handle_t client = NULL;
static bool connected = false;

/* ---------- MQTT EVENT HANDLER ---------- */
static void mqtt_event_handler(void *arg,
                               esp_event_base_t event_base,
                               int32_t event_id,
                               void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch (event_id) {

    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        connected = true;

        // Subscribe to your control topic here
        esp_mqtt_client_subscribe(client, "aurabot/control", 1);
        esp_mqtt_client_publish(client, "aurabot/status", "ESP32 online", 0, 1, 1);
        break;

    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        connected = false;
        break;

    case MQTT_EVENT_DATA:
        ESP_LOGI(TAG, "MQTT data received");
        ESP_LOGI(TAG, "TOPIC=%.*s", event->topic_len, event->topic);
        ESP_LOGI(TAG, "DATA=%.*s", event->data_len, event->data);

        // TODO: parse payload and act
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error");
        break;

    default:
        break;
    }
}

/* ---------- START MQTT ---------- */
esp_err_t mqtt_start(void)
{
    if (client) {
        ESP_LOGW(TAG, "MQTT already started");
        return ESP_OK;
    }

    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = "mqtt://192.168.1.239:1883", // CHANGE THIS FOR YOUR LOCAL MACHINE
        .credentials.username = "user",
        .credentials.authentication.password = "pass",
    };

    client = esp_mqtt_client_init(&cfg);
    if (!client) {
        ESP_LOGE(TAG, "Failed to init MQTT client");
        return ESP_FAIL;
    }

    esp_mqtt_client_register_event(
        client,
        ESP_EVENT_ANY_ID,
        mqtt_event_handler,
        NULL
    );

    esp_err_t err = esp_mqtt_client_start(client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start MQTT: %s", esp_err_to_name(err));
        esp_mqtt_client_destroy(client);
        client = NULL;
        return err;
    }

    ESP_LOGI(TAG, "MQTT client started");
    return ESP_OK;
}

/* ---------- STOP MQTT ---------- */
void mqtt_stop(void)
{
    if (!client) return;

    ESP_LOGI(TAG, "Stopping MQTT");

    esp_mqtt_client_stop(client);
    esp_mqtt_client_destroy(client);

    client = NULL;
    connected = false;
}

/* ---------- PUBLISH ---------- */
esp_err_t mqtt_publish(const char *topic,
                       const char *payload,
                       int qos,
                       int retain)
{
    if (!client || !connected) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_mqtt_client_publish(
        client,
        topic,
        payload,
        0,
        qos,
        retain
    );

    return ESP_OK;
}

/* ---------- STATE ---------- */
bool mqtt_is_connected(void)
{
    return connected;
}