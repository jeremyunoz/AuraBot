idf_component_register(SRCS "pir.c" "voice_session.c" "voice_ws.c" "voice_conversation.c" "action.c" "servo.c" 
                            "main.c" "wifi_connect.c" "mqtt.c" "speaker.c" "tts.c" "wakeword.c"
                            "lcd_lvgl.c" "robot_eyes.c"
                    PRIV_REQUIRES esp_wifi nvs_flash driver esp_codec_dev picotts esp_psram json esp-sr
                                  esp_lcd esp_timer esp_websocket_client 78__esp-opus
                    INCLUDE_DIRS "include")
