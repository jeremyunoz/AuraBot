idf_component_register(SRCS "pir.c" "main.c" "wifi_connect.c" "mqtt.c" "speaker.c"
                    PRIV_REQUIRES esp_wifi nvs_flash driver esp_codec_dev
                    INCLUDE_DIRS ".")
