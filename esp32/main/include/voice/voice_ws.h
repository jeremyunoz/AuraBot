/**
 * @file voice_ws.h
 * @brief WebSocket client for voice: connect to Pi5, send hello, maintain connection.
 *
 * Handles initialization, connection lifecycle, and sending text/binary.
 * Incoming data is delivered to the conversation layer via a registered callback.
 */
#ifndef VOICE_WS_H
#define VOICE_WS_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Callback for incoming WebSocket data (text or binary) */
typedef void (*voice_ws_data_cb_t)(void *arg, const uint8_t *data, size_t len, bool is_binary);

/** Callback when WebSocket disconnects or errors */
typedef void (*voice_ws_disconnect_cb_t)(void *arg);

/** Callback when WebSocket is connected (once per connection) */
typedef void (*voice_ws_connected_cb_t)(void *arg);

/** Set callback for incoming data; called from WS task context */
void voice_ws_set_data_callback(voice_ws_data_cb_t cb, void *arg);

/** Set callback for disconnect/error; called from WS task context */
void voice_ws_set_disconnect_callback(voice_ws_disconnect_cb_t cb, void *arg);

/** Set callback for connected; called from WS task context when WEBSOCKET_EVENT_CONNECTED */
void voice_ws_set_connected_callback(voice_ws_connected_cb_t cb, void *arg);

/** Start WebSocket client (connect to URI, send hello on connect) */
esp_err_t voice_ws_start(const char *uri);

/** Stop and destroy WebSocket client */
void voice_ws_stop(void);

/** True when client is connected */
bool voice_ws_is_connected(void);

/** Send text frame; returns bytes sent or negative on error */
int voice_ws_send_text(const char *data, size_t len, TickType_t timeout_ticks);

/** Send binary frame; returns bytes sent or negative on error */
int voice_ws_send_bin(const void *data, size_t len, TickType_t timeout_ticks);

#ifdef __cplusplus
}
#endif

#endif /* VOICE_WS_H */
