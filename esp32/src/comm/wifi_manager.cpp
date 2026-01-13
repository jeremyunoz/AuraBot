#include "wifi_manager.hpp"
#include <cstring>

extern "C" {
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
}

static const char* TAG = "WiFiManager";

static EventGroupHandle_t s_event_group = nullptr;
static constexpr int WIFI_CONNECTED_BIT = BIT0;
static constexpr int WIFI_FAILED_BIT    = BIT1;

esp_err_t WiFiManager::init() {
    if (inited_) return ESP_OK;

    if (s_event_group == nullptr) {
        s_event_group = xEventGroupCreate();
        if (s_event_group == nullptr) return ESP_ERR_NO_MEM;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    inited_ = true;
    state_ = State::Idle;
    return ESP_OK;
}

esp_err_t WiFiManager::start(const Config& cfg, Callbacks cbs) {
    if (!inited_) {
        esp_err_t err = init();
        if (err != ESP_OK) return err;
    }

    if (cfg.ssid == nullptr || cfg.ssid[0] == '\0') return ESP_ERR_INVALID_ARG;

    cfg_ = cfg;
    cbs_ = cbs;

    retry_count_ = 0;
    ip_valid_ = false;
    std::memset(&ip_, 0, sizeof(ip_));

    xEventGroupClearBits(s_event_group, WIFI_CONNECTED_BIT | WIFI_FAILED_BIT);

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT,
        ESP_EVENT_ANY_ID,
        &WiFiManager::event_handler,
        this,
        reinterpret_cast<esp_event_handler_instance_t*>(&wifi_any_id_instance_)
    ));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT,
        IP_EVENT_STA_GOT_IP,
        &WiFiManager::event_handler,
        this,
        reinterpret_cast<esp_event_handler_instance_t*>(&got_ip_instance_)
    ));

    wifi_config_t wifi_cfg{};
    std::strncpy(reinterpret_cast<char*>(wifi_cfg.sta.ssid), cfg_.ssid, sizeof(wifi_cfg.sta.ssid));
    if (cfg_.password != nullptr) {
        std::strncpy(reinterpret_cast<char*>(wifi_cfg.sta.password), cfg_.password, sizeof(wifi_cfg.sta.password));
    }

    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));

    state_ = State::Starting;
    ESP_ERROR_CHECK(esp_wifi_start());
    state_ = State::Connecting;

    if (!cfg_.wait_for_ip) return ESP_OK;

    EventBits_t bits = xEventGroupWaitBits(
        s_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(cfg_.wait_timeout_ms)
    );

    if (bits & WIFI_CONNECTED_BIT) return ESP_OK;
    if (bits & WIFI_FAILED_BIT) return ESP_FAIL;

    state_ = State::Failed;
    return ESP_ERR_TIMEOUT;
}

esp_err_t WiFiManager::stop() {
    if (!inited_) return ESP_OK;

    if (wifi_any_id_instance_ != nullptr) {
        esp_event_handler_instance_unregister(
            WIFI_EVENT,
            ESP_EVENT_ANY_ID,
            reinterpret_cast<esp_event_handler_instance_t>(wifi_any_id_instance_)
        );
        wifi_any_id_instance_ = nullptr;
    }

    if (got_ip_instance_ != nullptr) {
        esp_event_handler_instance_unregister(
            IP_EVENT,
            IP_EVENT_STA_GOT_IP,
            reinterpret_cast<esp_event_handler_instance_t>(got_ip_instance_)
        );
        got_ip_instance_ = nullptr;
    }

    esp_err_t err = esp_wifi_stop();
    state_ = State::Stopped;
    return err;
}

WiFiManager::State WiFiManager::state() const {
    return state_;
}

bool WiFiManager::is_connected() const {
    return state_ == State::Connected;
}

bool WiFiManager::get_ip(esp_ip4_addr_t& out_ip) const {
    if (!ip_valid_) return false;
    out_ip = ip_;
    return true;
}

void WiFiManager::event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    auto* self = static_cast<WiFiManager*>(arg);
    if (self == nullptr) return;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        self->handle_wifi_start();
        return;
    }

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        self->handle_wifi_disconnected();
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        auto* ev = static_cast<ip_event_got_ip_t*>(event_data);
        self->handle_got_ip(ev->ip_info.ip);
        return;
    }
}

void WiFiManager::handle_wifi_start() {
    ESP_LOGI(TAG, "WiFi started, connecting");
    esp_wifi_connect();
}

void WiFiManager::handle_wifi_disconnected() {
    ip_valid_ = false;
    state_ = State::Connecting;

    if (cbs_.on_disconnected) cbs_.on_disconnected();

    if (!cfg_.auto_reconnect) {
        state_ = State::Failed;
        xEventGroupSetBits(s_event_group, WIFI_FAILED_BIT);
        if (cbs_.on_failed) cbs_.on_failed();
        return;
    }

    if (retry_count_ < cfg_.max_retry) {
        retry_count_++;
        ESP_LOGW(TAG, "Disconnected, retry %d of %d", retry_count_, cfg_.max_retry);
        esp_wifi_connect();
        return;
    }

    state_ = State::Failed;
    xEventGroupSetBits(s_event_group, WIFI_FAILED_BIT);
    ESP_LOGE(TAG, "Failed to connect after retries");
    if (cbs_.on_failed) cbs_.on_failed();
}

void WiFiManager::handle_got_ip(const esp_ip4_addr_t& ip) {
    ip_ = ip;
    ip_valid_ = true;
    retry_count_ = 0;

    state_ = State::Connected;
    xEventGroupSetBits(s_event_group, WIFI_CONNECTED_BIT);

    ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&ip_));

    if (cbs_.on_connected) cbs_.on_connected();
    if (cbs_.on_got_ip) cbs_.on_got_ip(ip_);
}