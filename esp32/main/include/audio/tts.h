#pragma once

#include "esp_err.h"
#include <stdbool.h>

esp_err_t tts_init(void); // initialize the TTS
esp_err_t tts_deinit(void); // deinitialize the TTS
esp_err_t tts_speak(const char *text); // speak the text

bool tts_is_busy(void); // check if the TTS is busy
