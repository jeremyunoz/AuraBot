#pragma once

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include <stdint.h>

/*
 * pir_t：描述一个 PIR 传感器
 * 目前只需要一个 GPIO 引脚
 */
typedef struct {
    gpio_num_t pin; // PIR传感器连接的GPIO引脚
} pir_t;

/*
 * pir_int_interrupt：
 * - 把 PIR GPIO 配置成“中断输入”
 * - 当 PIR 触发时：
 *     → 向 event_group 里设置一个 bit
 *
 * 参数解释：
 * pir            : PIR 的配置（主要是 GPIO）
 * event_group    : 要通知的 EventGroup（系统公共状态）
 * bit_to_set     : PIR 触发时要置 1 的那个 bit
 */

esp_err_t pir_int_interrupt(const pir_t *pir, EventGroupHandle_t event_group, EventBits_t bit_to_set);

/* Get current PIR trigger count */
uint32_t pir_get_count(void);

/* Reset PIR trigger count to zero */
void pir_reset_count(void);

