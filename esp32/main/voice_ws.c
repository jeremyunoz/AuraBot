/**
 * @file voice_ws.c
 * @brief WebSocket client: init, connect, send hello, maintain connection, deliver data via callback.
 */

#include "voice/voice_ws.h"
#include "sdkconfig.h"

#if CONFIG_VOICE_SESSION_ENABLE

#include <string.h>
#include "esp_log.h"
#include "esp_websocket_client.h"

static const char *TAG = "voice_ws";

#define HELLO_TIMEOUT_MS 10000

static const char HELLO_JSON[] =
    "{\"type\":\"hello\",\"version\":1,\"transport\":\"websocket\","
    "\"audio_params\":{\"format\":\"opus\",\"sample_rate\":16000,\"channels\":1,\"frame_duration\":60}}";

static esp_websocket_client_handle_t s_ws_client;
static voice_ws_data_cb_t s_data_cb;
static void *s_data_cb_arg;
static voice_ws_disconnect_cb_t s_disconnect_cb;
static void *s_disconnect_cb_arg;
static voice_ws_connected_cb_t s_connected_cb;
static void *s_connected_cb_arg;
static bool s_disconnect_handled;

static void ws_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *evt = (esp_websocket_event_data_t *)data;
    (void)arg;
    (void)base;

    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_disconnect_handled = false;
        ESP_LOGI(TAG, "WebSocket connected");
        if (s_connected_cb) {
            s_connected_cb(s_connected_cb_arg);
        }
        if (evt->client) {
            int sent = esp_websocket_client_send_text(s_ws_client, HELLO_JSON,
                sizeof(HELLO_JSON) - 1, pdMS_TO_TICKS(1000));
            if (sent < 0) {
                ESP_LOGE(TAG, "Failed to send hello");
            }
        }
        break;

    case WEBSOCKET_EVENT_DATA:
        if (!evt->data_ptr || evt->data_len <= 0) break;
        if (s_data_cb) {
            bool is_binary = (evt->op_code == 0x02);
            s_data_cb(s_data_cb_arg, (const uint8_t *)evt->data_ptr, (size_t)evt->data_len, is_binary);
        }
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
    case WEBSOCKET_EVENT_ERROR:
        if (!s_disconnect_handled) {
            s_disconnect_handled = true;
            ESP_LOGI(TAG, "WebSocket closed (server down?), returning to idle");
            if (s_disconnect_cb) {
                s_disconnect_cb(s_disconnect_cb_arg);
            }
        }
        break;

    default:
        break;
    }
}

void voice_ws_set_data_callback(voice_ws_data_cb_t cb, void *arg)
{
    s_data_cb = cb;
    s_data_cb_arg = arg;
}

void voice_ws_set_disconnect_callback(voice_ws_disconnect_cb_t cb, void *arg)
{
    s_disconnect_cb = cb;
    s_disconnect_cb_arg = arg;
}

void voice_ws_set_connected_callback(voice_ws_connected_cb_t cb, void *arg)
{
    s_connected_cb = cb;
    s_connected_cb_arg = arg;
}

esp_err_t voice_ws_start(const char *uri)
{
    if (!uri || uri[0] == '\0') {
        ESP_LOGE(TAG, "WebSocket URI not set");
        return ESP_ERR_INVALID_ARG;
    }

    if (s_ws_client) {
        return ESP_OK;
    }

    esp_websocket_client_config_t ws_cfg = {
        .uri = uri,
        .buffer_size = 2048,
        .task_prio = 6,
        .task_stack = 4096,
        .disable_auto_reconnect = true,
        .network_timeout_ms = HELLO_TIMEOUT_MS,
    };

    s_ws_client = esp_websocket_client_init(&ws_cfg);
    if (!s_ws_client) {
        ESP_LOGE(TAG, "WebSocket client init failed");
        return ESP_FAIL;
    }

    esp_websocket_register_events(s_ws_client, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL);

    esp_err_t ret = esp_websocket_client_start(s_ws_client);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WebSocket start failed %s", esp_err_to_name(ret));
        esp_websocket_client_destroy(s_ws_client);
        s_ws_client = NULL;
        return ret;
    }

    return ESP_OK;
}

void voice_ws_stop(void)
{
    if (s_ws_client) {
        s_disconnect_handled = true;
        esp_websocket_client_close(s_ws_client, pdMS_TO_TICKS(1000));
        esp_websocket_client_stop(s_ws_client);
        esp_websocket_client_destroy(s_ws_client);
        s_ws_client = NULL;
    }
    s_data_cb = NULL;
    s_data_cb_arg = NULL;
    s_disconnect_cb = NULL;
    s_disconnect_cb_arg = NULL;
    s_connected_cb = NULL;
    s_connected_cb_arg = NULL;
}

bool voice_ws_is_connected(void)
{
    return s_ws_client && esp_websocket_client_is_connected(s_ws_client);
}

int voice_ws_send_text(const char *data, size_t len, TickType_t timeout_ticks)
{
    if (!s_ws_client) return -1;
    return esp_websocket_client_send_text(s_ws_client, data, len, timeout_ticks);
}

int voice_ws_send_bin(const void *data, size_t len, TickType_t timeout_ticks)
{
    if (!s_ws_client) return -1;
    return esp_websocket_client_send_bin(s_ws_client, (const char *)data, len, timeout_ticks);
}

#else /* !CONFIG_VOICE_SESSION_ENABLE */

void voice_ws_set_data_callback(voice_ws_data_cb_t cb, void *arg) { (void)cb; (void)arg; }
void voice_ws_set_disconnect_callback(voice_ws_disconnect_cb_t cb, void *arg) { (void)cb; (void)arg; }
void voice_ws_set_connected_callback(voice_ws_connected_cb_t cb, void *arg) { (void)cb; (void)arg; }
esp_err_t voice_ws_start(const char *uri) { (void)uri; return ESP_ERR_NOT_SUPPORTED; }
void voice_ws_stop(void) { }
bool voice_ws_is_connected(void) { return false; }
int voice_ws_send_text(const char *data, size_t len, TickType_t timeout_ticks) { (void)data; (void)len; (void)timeout_ticks; return -1; }
int voice_ws_send_bin(const void *data, size_t len, TickType_t timeout_ticks) { (void)data; (void)len; (void)timeout_ticks; return -1; }

#endif /* CONFIG_VOICE_SESSION_ENABLE */
