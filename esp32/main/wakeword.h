/**
 * @file wakeword.h
 * @brief Continuous wake-word detection using ESP-SR AFE + WakeNet
 *
 * Requires:
 *  - speaker_init() called first (provides shared I2S RX handle)
 *  - A WakeNet model selected via menuconfig (ESP Speech Recognition)
 *  - "model" partition present in the partition table
 */
#ifndef WAKEWORD_H
#define WAKEWORD_H

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Start continuous wake-word detection.
 *
 * Initialises the AFE pipeline with WakeNet, spawns a feed task (reads
 * mic audio via I2S) and a fetch task (checks for detections).
 * On each detection the speaker beeps, then WakeNet is re-armed so
 * detection runs indefinitely.
 *
 * @return ESP_OK on success, or an error code if initialisation fails.
 */
esp_err_t wakeword_start(void);

/**
 * @brief Provide a queue to receive wake-word events.
 *
 * When a wake word is detected, SYS_EVT_WAKE_DETECTED is posted to the queue.
 */
void wakeword_set_event_queue(QueueHandle_t queue);

#ifdef __cplusplus
}
#endif

#endif /* WAKEWORD_H */
