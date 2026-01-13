extern "C" {
    #include "nvs_flash.h"
    #include "esp_log.h"
    }
    
    #include "comm/wifi_manager.hpp"
    #include "secrets.hpp"
    
    static const char* TAG = "Main";
    
    static void on_ip(const esp_ip4_addr_t& ip) {
        ESP_LOGI(TAG, "Ready, IP: " IPSTR, IP2STR(&ip));
    }
    
    extern "C" void app_main(void) {
        ESP_ERROR_CHECK(nvs_flash_init());
    
        WiFiManager wifi;
    
        WiFiManager::Config cfg;
        cfg.ssid = WIFI_SSID;
        cfg.password = WIFI_PASSWORD;
        cfg.max_retry = 10;
        cfg.auto_reconnect = true;
        cfg.wait_for_ip = true;
        cfg.wait_timeout_ms = 20000;
    
        WiFiManager::Callbacks cbs;
        cbs.on_got_ip = on_ip;
    
        esp_err_t err = wifi.start(cfg, cbs);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "WiFi start failed: %s", esp_err_to_name(err));
        }
    
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }