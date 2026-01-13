#pragma once

#include <cstdint>

extern "C" {
#include "esp_err.h"
#include "esp_event.h"
#include "esp_netif_ip_addr.h"
}

/**
 * WiFiManager
 *
 * High level C++ wrapper for ESP IDF WiFi station mode.
 * Owns WiFi lifecycle, connection retries, and state tracking.
 *
 * Design goals:
 * - Deterministic behavior
 * - No heap allocation during steady state
 * - Clean interface for application code
 * - All ESP IDF C APIs isolated in implementation
 */
class WiFiManager {
public:
    /**
     * Connection state of the WiFi subsystem.
     * Used for polling, debugging, and system coordination.
     */
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

    /**
     * Optional callbacks invoked on WiFi events.
     * All callbacks are invoked from ESP IDF event context.
     * Keep them lightweight and non blocking.
     */
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

    // Prevent accidental copying of WiFi manager
    WiFiManager(const WiFiManager&) = delete;
    WiFiManager& operator=(const WiFiManager&) = delete;

    /**
     * Initialize ESP IDF WiFi subsystem.
     *
     * Safe to call multiple times.
     * Creates netif, event loop, and WiFi driver.
     *
     * Does NOT start WiFi or connect.
     */
    esp_err_t init();

    /**
     * Start WiFi in station mode using provided configuration.
     *
     * Registers event handlers, configures SSID, and starts WiFi.
     *
     * If wait_for_ip is true, this call blocks until:
     * - IP acquired
     * - Failure
     * - Timeout
     */
    esp_err_t start(const Config& cfg, Callbacks cbs = Callbacks{});

    /**
     * Stop WiFi and unregister event handlers.
     *
     * Safe to call even if WiFi is not running.
     */
    esp_err_t stop();

    /**
     * Get current WiFi connection state.
     */
    State state() const;

    /**
     * Convenience check for connected state.
     */
    bool is_connected() const;

    /**
     * Retrieve last known IP address.
     *
     * Returns false if IP is not valid yet.
     */
    bool get_ip(esp_ip4_addr_t& out_ip) const;

private:
    /**
     * Static ESP IDF event handler entry point.
     *
     * Dispatches events back to the instance.
     */
    static void event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data);

    /**
     * Handle WIFI_EVENT_STA_START.
     * Triggers initial connection attempt.
     */
    void handle_wifi_start();

    /**
     * Handle WIFI_EVENT_STA_DISCONNECTED.
     * Manages retry logic and failure transitions.
     */
    void handle_wifi_disconnected();

    /**
     * Handle IP_EVENT_STA_GOT_IP.
     * Marks WiFi as fully connected and ready.
     */
    void handle_got_ip(const esp_ip4_addr_t& ip);

private:
    Config cfg_;                    // Active WiFi configuration
    Callbacks cbs_;                 // Active callbacks

    State state_ = State::Idle;     // Current connection state
    int retry_count_ = 0;           // Retry counter

    bool ip_valid_ = false;         // IP validity flag
    esp_ip4_addr_t ip_{};           // Last acquired IP

    bool inited_ = false;           // Initialization guard

    // ESP IDF event handler instances
    void* wifi_any_id_instance_ = nullptr;
    void* got_ip_instance_ = nullptr;
};