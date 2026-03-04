#pragma once

/*
 * DEPRECATED — not compiled into the firmware build (omitted from CMakeLists.txt SRCS).
 *
 * Kept as a reference in case PIR-based motion detection is re-introduced.
 * On AuraBot the default firmware no longer uses a PIR sensor; motion handling
 * has moved to the Pi 5 side.  If you want to re-enable this driver:
 *
 *   1. Add "pir.c" back to the SRCS list in main/CMakeLists.txt.
 *   2. Wire pir_int_interrupt() into app_main() (see example below).
 *   3. Restore pir_task() in main.c and register it with xTaskCreate().
 *
 * Example integration (main.c sketch):
 *
 *     #include "sensors/pir.h"
 *
 *     #define PIR_EVENT_BIT   BIT0
 *     #define PIR_GPIO        GPIO_NUM_4
 *
 *     static EventGroupHandle_t s_pir_evt;
 *
 *     static void pir_task(void *arg)
 *     {
 *         (void)arg;
 *         while (1) {
 *             xEventGroupWaitBits(s_pir_evt, PIR_EVENT_BIT,
 *                                 pdTRUE, pdFALSE, portMAX_DELAY);
 *             uint32_t count = pir_get_count();
 *             if (s_state == SYS_STATE_ACTIVE && mqtt_is_connected()) {
 *                 char msg[128];
 *                 snprintf(msg, sizeof(msg),
 *                          "{\"src\":\"esp32\",\"motion\":1,\"count\":%u}",
 *                          (unsigned)count);
 *                 mqtt_publish("aurabot/sensors", msg, 1, 0);
 *             }
 *         }
 *     }
 *
 *     // Inside app_main():
 *     s_pir_evt = xEventGroupCreate();
 *     pir_t pir = { .pin = PIR_GPIO };
 *     pir_int_interrupt(&pir, s_pir_evt, PIR_EVENT_BIT);
 *     xTaskCreate(pir_task, "pir_task", 2048, NULL, 5, NULL);
 */

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include <stdint.h>

typedef struct {
    gpio_num_t pin;
} pir_t;

esp_err_t pir_int_interrupt(const pir_t *pir, EventGroupHandle_t event_group, EventBits_t bit_to_set);

uint32_t pir_get_count(void);

void pir_reset_count(void);
