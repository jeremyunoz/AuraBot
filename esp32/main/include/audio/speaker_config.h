/**
 * @file speaker_config.h
 * @brief Speaker (ES8311) pin and hardware config
 *
 * Values are taken from Kconfig (CONFIG_SPEAKER_*). Include this only when
 * CONFIG_SPEAKER_ENABLE is set (e.g. inside speaker.c implementation).
 */
#ifndef SPEAKER_CONFIG_H
#define SPEAKER_CONFIG_H

#include "sdkconfig.h"

#if CONFIG_SPEAKER_ENABLE

/* I2C (codec control) */
#define SPEAKER_I2C_NUM      I2C_NUM_0
#define SPEAKER_I2C_SCL_GPIO CONFIG_SPEAKER_I2C_SCL_GPIO
#define SPEAKER_I2C_SDA_GPIO CONFIG_SPEAKER_I2C_SDA_GPIO

/* I2S (audio data) */
#define SPEAKER_I2S_BCK_GPIO  CONFIG_SPEAKER_I2S_BCK_GPIO
#define SPEAKER_I2S_WS_GPIO   CONFIG_SPEAKER_I2S_WS_GPIO
#define SPEAKER_I2S_DOUT_GPIO CONFIG_SPEAKER_I2S_DOUT_GPIO
#define SPEAKER_I2S_DIN_GPIO  CONFIG_SPEAKER_I2S_DIN_GPIO
#define SPEAKER_I2S_MCK_GPIO  CONFIG_SPEAKER_I2S_MCK_GPIO

/* Power amplifier enable (e.g. NS4150B); -1 if unused */
#define SPEAKER_PA_GPIO       CONFIG_SPEAKER_PA_GPIO

/* MCLK multiple (match i2s_es8311 example; 256 ok for 16-bit) */
#define SPEAKER_I2S_MCLK_MULTIPLE  384

#endif /* CONFIG_SPEAKER_ENABLE */

#endif /* SPEAKER_CONFIG_H */
