#pragma once

#include <cstdint>

extern "C" {
#include "esp_err.h"
#include "esp_event.h"
#include "esp_netif_ip_addr.h"
}

class WiFiManager {
public:
    enum class State : uint8_t {
        Idle,
        Starting,
        Connecting,
        Connected,
        Failed,
        Stopped
    };

    struct Config {
        const char* ssid = nullptr;
        const char* password = nullptr;

        int max_retry = 10;

        bool auto_reconnect = true;

        bool wait_for_ip = true;
        uint32_t wait_timeout_ms = 15000;
    };

    struct Callbacks {
        void (*on_connected)();
        void (*on_disconnected)();
        void (*on_got_ip)(const esp_ip4_addr_t& ip);
        void (*on_failed)();
    
        Callbacks()
            : on_connected(nullptr),
              on_disconnected(nullptr),
              on_got_ip(nullptr),
              on_failed(nullptr) {}
    };

    WiFiManager() = default;
    WiFiManager(const WiFiManager&) = delete;
    WiFiManager& operator=(const WiFiManager&) = delete;

    esp_err_t init();
    esp_err_t start(const Config& cfg, Callbacks cbs = Callbacks{});
    esp_err_t stop();

    State state() const;
    bool is_connected() const;

    bool get_ip(esp_ip4_addr_t& out_ip) const;

private:
    static void event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data);

    void handle_wifi_start();
    void handle_wifi_disconnected();
    void handle_got_ip(const esp_ip4_addr_t& ip);

private:
    Config cfg_{};
    Callbacks cbs_{};

    State state_ = State::Idle;

    int retry_count_ = 0;

    bool ip_valid_ = false;
    esp_ip4_addr_t ip_{};

    bool inited_ = false;

    void* wifi_any_id_instance_ = nullptr;
    void* got_ip_instance_ = nullptr;
};