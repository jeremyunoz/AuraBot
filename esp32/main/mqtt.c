#include "network/mqtt.h"
#include "sdkconfig.h"

#if CONFIG_MQTT_ENABLE

#include "esp_log.h"
#include "mqtt_client.h"
#include <string.h>

#include "cJSON.h"

#include "system/system_events.h"
#include "motion/action.h"
#include "display/robot_eyes.h"

#include "freertos/FreeRTOS.h"
#include "freertos/timers.h"

#if CONFIG_SPEAKER_ENABLE
#include "audio/tts.h"
#endif

static const char *TAG = "mqtt";
static const TickType_t WAVE_HEART_EXPR_MS = pdMS_TO_TICKS(3200);

static esp_mqtt_client_handle_t client = NULL;
static bool connected = false;
static QueueHandle_t s_evt_queue = NULL;
static TimerHandle_t s_wave_expr_timer = NULL;

void mqtt_set_event_queue(QueueHandle_t queue)
{
    s_evt_queue = queue;
}

static void mqtt_post_event(sys_event_id_t id)
{
    if (!s_evt_queue) return;
    sys_event_t evt = { .id = id };
    (void)xQueueSend(s_evt_queue, &evt, 0);
}

static bool topic_equals(const esp_mqtt_event_handle_t event, const char *topic)
{
    if (!event || !topic) return false;
    size_t topic_len = strlen(topic);
    return (event->topic_len == (int)topic_len) && (strncmp(event->topic, topic, topic_len) == 0);
}

static void wave_expr_timer_cb(TimerHandle_t timer)
{
    (void)timer;
    roboeyes_set_state(EYE_STATE_ACTIVE);
}

static void set_expression_for_user_move(action_id_t id)
{
    if (id == ACTION_WAVE) {
        roboeyes_set_state(EYE_STATE_WAKING);
        if (s_wave_expr_timer != NULL) {
            (void)xTimerStop(s_wave_expr_timer, 0);
            (void)xTimerChangePeriod(s_wave_expr_timer, WAVE_HEART_EXPR_MS, 0);
            (void)xTimerStart(s_wave_expr_timer, 0);
        }
        return;
    }

    if (s_wave_expr_timer != NULL) {
        (void)xTimerStop(s_wave_expr_timer, 0);
    }
    roboeyes_set_state(EYE_STATE_ACTIVE);
}

/* Deprecated: TTS-over-MQTT path; voice output now uses Voice WebSocket (Pi sends Opus). */
static void handle_tts_speak(const esp_mqtt_event_handle_t event)
{
    if (!event || !event->data || event->data_len <= 0) return;

    cJSON *root = cJSON_ParseWithLength(event->data, (size_t)event->data_len);
    if (!root) {
        ESP_LOGW(TAG, "TTS payload is not valid JSON");
        return;
    }

    const cJSON *text = cJSON_GetObjectItemCaseSensitive(root, "text");
    if (!cJSON_IsString(text) || text->valuestring == NULL) {
        ESP_LOGW(TAG, "TTS payload missing 'text' string");
        cJSON_Delete(root);
        return;
    }

    const char *msg = text->valuestring;
    size_t msg_len = strlen(msg);
    if (msg_len == 0) {
        ESP_LOGW(TAG, "TTS payload has empty 'text'");
        cJSON_Delete(root);
        return;
    }

    ESP_LOGI(TAG, "TTS speak request len=%u", (unsigned)msg_len);

    esp_err_t speak_err = ESP_ERR_NOT_SUPPORTED;
#if CONFIG_SPEAKER_ENABLE
    speak_err = tts_speak(msg);
    if (speak_err != ESP_OK) {
        ESP_LOGW(TAG, "tts_speak failed: %s", esp_err_to_name(speak_err));
    }
#else
    ESP_LOGW(TAG, "TTS request received but speaker/TTS disabled in config");
#endif

    char ack[128];
    snprintf(
        ack,
        sizeof(ack),
        "{\"device\":\"esp32\",\"type\":\"tts\",\"status\":\"%s\",\"len\":%u}",
        (speak_err == ESP_OK) ? "queued" : "error",
        (unsigned)msg_len
    );
    (void)mqtt_publish("aurabot/tts/ack", ack, 1, 0);

    cJSON_Delete(root);
}

static void handle_control_command(const esp_mqtt_event_handle_t event)
{
    if (!event || !event->data || event->data_len <= 0) return;

    cJSON *root = cJSON_ParseWithLength(event->data, (size_t)event->data_len);
    if (!root) {
        ESP_LOGW(TAG, "Control payload is not valid JSON");
        return;
    }

    const cJSON *cmd = cJSON_GetObjectItemCaseSensitive(root, "cmd");
    if (!cJSON_IsString(cmd) || cmd->valuestring == NULL) {
        ESP_LOGW(TAG, "Control payload missing 'cmd' string");
        cJSON_Delete(root);
        return;
    }

    if (strcmp(cmd->valuestring, "wake_ready") == 0) {
        mqtt_post_event(SYS_EVT_PI5_READY);
    } else if (strcmp(cmd->valuestring, "sleep") == 0) {
        mqtt_post_event(SYS_EVT_SESSION_END);
    } else if (strcmp(cmd->valuestring, "reconnect") == 0) {
        mqtt_post_event(SYS_EVT_FORCE_WAKE);
    } else if (strcmp(cmd->valuestring, "move") == 0) {
        /* User movement: {"cmd":"move","action":"walk"} */
        const cJSON *action = cJSON_GetObjectItemCaseSensitive(root, "action");
        if (cJSON_IsString(action) && action->valuestring) {
            if (!action_user_control_enabled()) {
                ESP_LOGW(TAG, "Ignoring move while user control is disabled");
                cJSON_Delete(root);
                return;
            }
            action_id_t id = action_from_string(action->valuestring);
            action_post_user(id);
            set_expression_for_user_move(id);
        } else {
            ESP_LOGW(TAG, "move cmd missing 'action' string");
        }
    } else {
        ESP_LOGW(TAG, "Unknown control cmd: %s", cmd->valuestring);
    }

    cJSON_Delete(root);
}

/* ---------- MQTT EVENT HANDLER ---------- */
static void mqtt_event_handler(void *arg,
                               esp_event_base_t event_base,
                               int32_t event_id,
                               void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch (event_id) {

    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        connected = true;

        // Subscribe to your control topic here
        esp_mqtt_client_subscribe(client, "aurabot/control", 1);
        esp_mqtt_client_subscribe(client, "aurabot/tts/speak", 1); /* deprecated: TTS now via Voice WS */
        mqtt_post_event(SYS_EVT_MQTT_UP);
        break;

    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        connected = false;
        mqtt_post_event(SYS_EVT_MQTT_FAIL);
        break;

    case MQTT_EVENT_DATA:
        ESP_LOGI(TAG, "MQTT data received");
        ESP_LOGI(TAG, "TOPIC=%.*s", event->topic_len, event->topic);
        ESP_LOGI(TAG, "DATA=%.*s", event->data_len, event->data);

        if (topic_equals(event, "aurabot/tts/speak")) {
            handle_tts_speak(event);
        } else if (topic_equals(event, "aurabot/control")) {
            handle_control_command(event);
        }
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error");
        break;

    default:
        break;
    }
}

/* ---------- START MQTT ---------- */
esp_err_t mqtt_start(void)
{
    if (client) {
        ESP_LOGW(TAG, "MQTT already started");
        return ESP_OK;
    }

    if (s_wave_expr_timer == NULL) {
        s_wave_expr_timer = xTimerCreate(
            "wave_expr",
            WAVE_HEART_EXPR_MS,
            pdFALSE,
            NULL,
            wave_expr_timer_cb
        );
        if (s_wave_expr_timer == NULL) {
            ESP_LOGE(TAG, "Failed to create wave expression timer");
            return ESP_ERR_NO_MEM;
        }
    }

    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = "mqtt://aurabot.local:1883", // CHANGE THIS FOR YOUR LOCAL MACHINE
        .credentials.username = "user",
        .credentials.authentication.password = "231617",
    };

    client = esp_mqtt_client_init(&cfg);
    if (!client) {
        ESP_LOGE(TAG, "Failed to init MQTT client");
        return ESP_FAIL;
    }

    esp_mqtt_client_register_event(
        client,
        ESP_EVENT_ANY_ID,
        mqtt_event_handler,
        NULL
    );

    esp_err_t err = esp_mqtt_client_start(client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start MQTT: %s", esp_err_to_name(err));
        esp_mqtt_client_destroy(client);
        client = NULL;
        return err;
    }

    ESP_LOGI(TAG, "MQTT client started");
    return ESP_OK;
}

/* ---------- STOP MQTT ---------- */
void mqtt_stop(void)
{
    if (!client) return;

    ESP_LOGI(TAG, "Stopping MQTT");

    esp_mqtt_client_stop(client);
    esp_mqtt_client_destroy(client);

    client = NULL;
    connected = false;
}

/* ---------- PUBLISH ---------- */
esp_err_t mqtt_publish(const char *topic,
                       const char *payload,
                       int qos,
                       int retain)
{
    if (!client || !connected) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!topic || topic[0] == '\0' || !payload) {
        return ESP_ERR_INVALID_ARG;
    }
    if (qos < 0 || qos > 2) {
        return ESP_ERR_INVALID_ARG;
    }
    if (retain != 0 && retain != 1) {
        return ESP_ERR_INVALID_ARG;
    }

    int msg_id = esp_mqtt_client_publish(client, topic, payload, 0, qos, retain);
    if (msg_id < 0) {
        ESP_LOGE(TAG, "Publish failed topic=%s", topic);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Published msg_id=%d topic=%s payload=%s", msg_id, topic, payload);
    return ESP_OK;
}

/* ---------- STATE ---------- */
bool mqtt_is_connected(void)
{
    return connected;
}

#else /* !CONFIG_MQTT_ENABLE */

/* Stubs for voice-session-only build (no broker required) */
#include "esp_log.h"
#include "esp_err.h"
#include "freertos/queue.h"

esp_err_t mqtt_start(void)
{
    (void)0;
    return ESP_OK;
}

void mqtt_stop(void)
{
}

esp_err_t mqtt_publish(const char *topic, const char *payload, int qos, int retain)
{
    (void)topic;
    (void)payload;
    (void)qos;
    (void)retain;
    return ESP_OK;
}

bool mqtt_is_connected(void)
{
    return false;
}

void mqtt_set_event_queue(QueueHandle_t queue)
{
    (void)queue;
}

#endif /* CONFIG_MQTT_ENABLE */
