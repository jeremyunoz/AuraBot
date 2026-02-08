/**
 * @file speaker.h
 * @brief Speaker driver API using esp_codec_dev (ES8311 codec)
 *
 * Provides playback support for boards with ES8311 or compatible codec.
 * Requires I2C (control) and I2S (audio data) to be connected to the codec.
 */
#ifndef SPEAKER_H
#define SPEAKER_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "driver/i2s_std.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Default sample rate (Hz) */
#define SPEAKER_DEFAULT_SAMPLE_RATE  16000

/** Default channels (1 = mono, 2 = stereo) */
#define SPEAKER_DEFAULT_CHANNELS     1

/** Bits per sample */
#define SPEAKER_BITS_PER_SAMPLE      16

/** Volume range: 0 (mute) to 100 (max) */
#define SPEAKER_VOL_MIN              0
#define SPEAKER_VOL_MAX              100

/**
 * @brief Initialize the speaker driver (I2C, I2S, codec).
 *
 * Must be called before any other speaker_* function.
 * Uses pin configuration from Kconfig (CONFIG_SPEAKER_*).
 *
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t speaker_init(void);

/**
 * @brief Deinitialize the speaker and free resources.
 *
 * @return ESP_OK on success
 */
esp_err_t speaker_deinit(void);

/**
 * @brief Check if the speaker is initialized and ready.
 *
 * @return true if initialized, false otherwise
 */
bool speaker_is_ready(void);

/**
 * @brief Set output volume (0-100).
 *
 * @param volume Volume level 0-100 (0 = mute, 100 = max)
 * @return ESP_OK on success
 */
esp_err_t speaker_set_volume(int volume);

/**
 * @brief Get current volume setting (0-100).
 *
 * @param[out] volume Pointer to store volume value
 * @return ESP_OK on success
 */
esp_err_t speaker_get_volume(int *volume);

/**
 * @brief Open the speaker for playback with given sample format.
 *
 * @param sample_rate Sample rate in Hz (e.g. 16000, 44100, 48000)
 * @param channels    Number of channels (1 or 2)
 * @return ESP_OK on success
 */
esp_err_t speaker_open(int sample_rate, int channels);

/**
 * @brief Close the speaker (stop playback, release resources).
 *
 * @return ESP_OK on success
 */
esp_err_t speaker_close(void);

/**
 * @brief Write PCM audio data to the speaker.
 *
 * Data format: signed 16-bit little-endian, interleaved if stereo.
 * speaker_open() must have been called first.
 *
 * @param data Pointer to PCM data
 * @param len  Length in bytes (must be multiple of 2 for 16-bit)
 * @return ESP_OK on success, number of bytes written, or error code
 */
esp_err_t speaker_write(const void *data, size_t len);

/**
 * @brief Play a short beep (440 Hz, ~400 ms).
 *
 * Convenience function for notifications. Blocks until beep completes.
 *
 * @return ESP_OK on success
 */
esp_err_t speaker_beep(void);

/**
 * @brief Get the I2S RX channel handle (mic/ADC input).
 *
 * The RX channel shares the same I2S bus as the TX (speaker) channel.
 * Available after speaker_init(). Returns NULL if not initialized or
 * CONFIG_SPEAKER_ENABLE is off.
 *
 * @return I2S RX channel handle, or NULL
 */
i2s_chan_handle_t speaker_get_rx_handle(void);

#ifdef __cplusplus
}
#endif

#endif /* SPEAKER_H */
