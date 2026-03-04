/*
 * DEPRECATED — not compiled into the firmware build (omitted from CMakeLists.txt SRCS).
 *
 * PIR sensor driver for the HC-SR501 (or compatible) passive infrared sensor.
 * Kept as a reference for future re-integration.  See sensors/pir.h for the
 * API and a complete usage example.
 *
 * To re-enable: add "pir.c" to the SRCS list in main/CMakeLists.txt.
 */

#include "sensors/pir.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <stdbool.h>

static const char *TAG = "pir";

static EventGroupHandle_t s_evt       = NULL;
static EventBits_t        s_bit       = 0;
static bool               s_isr_installed = false;
static volatile uint32_t  s_pir_count = 0;

/* GPIO ISR — must be minimal: no printf, no delay, no heap. */
static void IRAM_ATTR pir_isr_handler(void *arg)
{
    BaseType_t hp_task_woken = pdFALSE;

    s_pir_count++;

    if (s_evt) {
        xEventGroupSetBitsFromISR(s_evt, s_bit, &hp_task_woken);
    }

    if (hp_task_woken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

/*
 * Configure the PIR GPIO as an interrupt input and bind it to the provided
 * FreeRTOS event group.  The HC-SR501 output is active-high, so a rising-edge
 * interrupt is used.  A pull-down is enabled to prevent floating.
 */
esp_err_t pir_int_interrupt(const pir_t *pir, EventGroupHandle_t event_group, EventBits_t bit_to_set)
{
    if (pir == NULL) {
        ESP_LOGE(TAG, "PIR pointer is NULL");
        return ESP_ERR_INVALID_ARG;
    }
    if (event_group == NULL) {
        ESP_LOGE(TAG, "Event group is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    s_evt = event_group;
    s_bit = bit_to_set;

    gpio_config_t io_conf = {
        .pin_bit_mask  = 1ULL << pir->pin,
        .mode          = GPIO_MODE_INPUT,
        .pull_up_en    = GPIO_PULLUP_DISABLE,
        .pull_down_en  = GPIO_PULLDOWN_ENABLE,
        .intr_type     = GPIO_INTR_POSEDGE,
    };
    esp_err_t ret = gpio_config(&io_conf);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "GPIO config failed for pin %d: %s", pir->pin, esp_err_to_name(ret));
        return ret;
    }

    if (!s_isr_installed) {
        ret = gpio_install_isr_service(0);
        if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(TAG, "Failed to install ISR service: %s", esp_err_to_name(ret));
            return ret;
        }
        s_isr_installed = true;
    }

    ret = gpio_isr_handler_add(pir->pin, pir_isr_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add ISR handler for pin %d: %s", pir->pin, esp_err_to_name(ret));
        return ret;
    }

    s_pir_count = 0;
    return ESP_OK;
}

uint32_t pir_get_count(void)
{
    return s_pir_count;
}

void pir_reset_count(void)
{
    s_pir_count = 0;
}
