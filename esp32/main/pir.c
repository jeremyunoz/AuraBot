#include "sensors/pir.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <stdbool.h>

static const char *TAG = "pir";


/*
 * 这两个是“静态全局变量”
 * static 的意思是：
 *   - 只在 pir.c 这个文件里可见
 *   - main.c / 其他文件访问不到
 *
 * 用途：
 *   ISR（中断函数）不能带复杂参数
 *   所以我们提前把 event_group 和 bit 存在这里
 */
static EventGroupHandle_t s_evt = NULL;
static EventBits_t s_bit = 0;
static bool s_isr_installed = false;
static volatile uint32_t s_pir_count = 0;  // Counter for PIR triggers (incremented in ISR)

/*
 * 这是 GPIO 中断服务函数（ISR）
 *
 * 重要规则：
 * - ISR 里不能 printf
 * - ISR 里不能 delay
 * - ISR 里不能做耗时逻辑
 *
 * 正确做法：
 *   → “通知别人我被触发了”
 */

static void IRAM_ATTR pir_isr_handler(void *arg)
{
    /*
     * BaseType_t 是 FreeRTOS 定义的类型
     * 用来判断：有没有高优先级 task 被唤醒
     */
    BaseType_t hp_task_woken = pdFALSE;

    /* Increment counter (atomic operation, safe in ISR) */
    s_pir_count++;

    /*
     * 从 ISR 中设置 EventGroup 的 bit
     * 这相当于广播一句话：
     *   “PIR 触发了！”
     */
    if (s_evt) {
        xEventGroupSetBitsFromISR(
            s_evt,  // 要设置的 EventGroup
            s_bit, // 要设置为1的bit
            &hp_task_woken);
    }


    /*
     * 如果 ISR 唤醒了更高优先级的 task
     * 立刻进行一次任务切换
     */
    if (hp_task_woken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

esp_err_t pir_int_interrupt(const pir_t *pir, EventGroupHandle_t event_group, EventBits_t bit_to_set){
    esp_err_t ret;

    /* Validate input parameters */
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


    /* Configure GPIO as input with pull-down to prevent floating */
    gpio_config_t io_conf = {
        .pin_bit_mask = 1ULL << pir->pin,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,  // Enable pull-down to prevent floating 
        /*
         * HC-SR501：
         *   - 没人：低电平 = 0
         *   - 有人：高电平 = 1
         * 所以上升沿 = “检测到人”
         */
        .intr_type = GPIO_INTR_POSEDGE    // 上升沿中断
    };
    ret = gpio_config(&io_conf);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "GPIO config failed for pin %d: %s", pir->pin, esp_err_to_name(ret));
        return ret;
    }

    /* Install GPIO ISR service once (required before adding handlers) */
    if (!s_isr_installed) {
        ret = gpio_install_isr_service(0);
        if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(TAG, "Failed to install ISR service: %s", esp_err_to_name(ret));
            return ret;
        }
        s_isr_installed = true;
    }

    /* Bind this PIR GPIO to the ISR handler */
    ret = gpio_isr_handler_add(pir->pin, pir_isr_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add ISR handler for pin %d: %s", pir->pin, esp_err_to_name(ret));
        return ret;
    }

    /* Reset counter on initialization */
    s_pir_count = 0;

    return ESP_OK;
}

/* Get current PIR trigger count (thread-safe read) */
uint32_t pir_get_count(void)
{
    return s_pir_count;
}

/* Reset PIR trigger count to zero */
void pir_reset_count(void)
{
    s_pir_count = 0;
}

