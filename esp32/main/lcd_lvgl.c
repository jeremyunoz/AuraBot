/**
 * @file lcd_lvgl.c
 * @brief SPI display (ST7789 240x320) + LVGL initialisation for AuraBot.
 *
 * Workflow:
 *   lcd_lvgl_init()
 *     -> SPI bus init
 *     -> Panel IO (SPI) setup
 *     -> ST7789 driver install & configure
 *     -> LVGL init, display object, double-buffer
 *     -> start periodic tick timer
 *     -> register flush-ready callback
 *     -> create LVGL background task
 *     -> return lv_display_t*
 *
 * SPDX-License-Identifier: CC0-1.0
 */

#include "lcd_lvgl.h"

#include <stdio.h>
#include <unistd.h>
#include <sys/lock.h>
#include <sys/param.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_timer.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_err.h"
#include "esp_log.h"
#include "lvgl.h"

static const char *TAG = "lcd_lvgl";

/* ========================================================================== */
/* Hardware configuration                                                     */
/* ========================================================================== */

#define LCD_HOST                       SPI2_HOST
#define LCD_PIXEL_CLOCK_HZ             (40 * 1000 * 1000)

/* GPIO pins ---------------------------------------------------------------- */
#define PIN_NUM_SCLK                   26
#define PIN_NUM_MOSI                   23
#define PIN_NUM_MISO                   (-1)  /* not used */
#define PIN_NUM_LCD_DC                 21
#define PIN_NUM_LCD_RST                22
#define PIN_NUM_LCD_CS                 20
#define PIN_NUM_BK_LIGHT               (-1)  /* tied to 3V3 */

/* Physical panel resolution (portrait) */
#define PANEL_H_RES                    240
#define PANEL_V_RES                    320

/* LVGL logical resolution (landscape, after swap_xy) */
#define LVGL_H_RES                     320
#define LVGL_V_RES                     240

/* SPI command / param widths */
#define LCD_CMD_BITS                   8
#define LCD_PARAM_BITS                 8

/* ========================================================================== */
/* LVGL configuration                                                         */
/* ========================================================================== */

#define LVGL_DRAW_BUF_LINES           40
#define LVGL_TICK_PERIOD_MS            2
#define LVGL_TASK_MAX_DELAY_MS         500
#define LVGL_TASK_MIN_DELAY_MS         1
#define LVGL_TASK_STACK_SIZE           (6 * 1024)
#define LVGL_TASK_PRIORITY             2

/* ========================================================================== */
/* LVGL API lock                                                              */
/* ========================================================================== */

static _lock_t lvgl_api_lock;

void lcd_lvgl_lock(void)
{
    _lock_acquire(&lvgl_api_lock);
}

void lcd_lvgl_unlock(void)
{
    _lock_release(&lvgl_api_lock);
}

/* ========================================================================== */
/* Internal callbacks                                                         */
/* ========================================================================== */

/**
 * Called by the LCD panel IO layer when DMA transfer of a frame completes.
 * Notifies LVGL that the flush buffer is free for the next render.
 */
static bool notify_lvgl_flush_ready(esp_lcd_panel_io_handle_t panel_io,
                                    esp_lcd_panel_io_event_data_t *edata,
                                    void *user_ctx)
{
    lv_display_t *disp = (lv_display_t *)user_ctx;
    lv_display_flush_ready(disp);
    return false;
}

/**
 * LVGL flush callback – sends a rendered rectangular area to the LCD
 * via the esp_lcd driver.  Performs the RGB565 byte-swap required by
 * SPI displays.
 */
static void lvgl_flush_cb(lv_display_t *disp, const lv_area_t *area,
                           uint8_t *px_map)
{
    esp_lcd_panel_handle_t panel =
        (esp_lcd_panel_handle_t)lv_display_get_user_data(disp);

    int x1 = area->x1;
    int x2 = area->x2;
    int y1 = area->y1;
    int y2 = area->y2;

    /* SPI LCDs expect big-endian RGB565 */
    lv_draw_sw_rgb565_swap(px_map, (x2 + 1 - x1) * (y2 + 1 - y1));

    esp_lcd_panel_draw_bitmap(panel, x1, y1, x2 + 1, y2 + 1, px_map);
}

/**
 * Periodic timer callback – feeds LVGL its time reference (tick).
 */
static void lvgl_tick_cb(void *arg)
{
    (void)arg;
    lv_tick_inc(LVGL_TICK_PERIOD_MS);
}

/* ========================================================================== */
/* LVGL background task                                                       */
/* ========================================================================== */

/**
 * FreeRTOS task that continuously services the LVGL timer handler.
 * Handles rendering, animation, and input processing.
 */
static void lvgl_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "LVGL task started");

    while (1) {
        uint32_t time_till_next_ms;

        _lock_acquire(&lvgl_api_lock);
        time_till_next_ms = lv_timer_handler();
        _lock_release(&lvgl_api_lock);

        time_till_next_ms = MAX(time_till_next_ms, LVGL_TASK_MIN_DELAY_MS);
        time_till_next_ms = MIN(time_till_next_ms, LVGL_TASK_MAX_DELAY_MS);
        usleep(1000 * time_till_next_ms);
    }
}

/* ========================================================================== */
/* Public API                                                                 */
/* ========================================================================== */

lv_display_t *lcd_lvgl_init(void)
{
    ESP_LOGI(TAG, "Initialising SPI LCD + LVGL (ST7789 %dx%d -> %dx%d landscape)",
             PANEL_H_RES, PANEL_V_RES, LVGL_H_RES, LVGL_V_RES);

    /* ---------------------------------------------------------------------- */
    /* Step 1 – SPI bus                                                       */
    /* ---------------------------------------------------------------------- */
    ESP_LOGI(TAG, "Initialise SPI bus");
    spi_bus_config_t buscfg = {
        .sclk_io_num   = PIN_NUM_SCLK,
        .mosi_io_num   = PIN_NUM_MOSI,
        .miso_io_num   = PIN_NUM_MISO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = LVGL_H_RES * LVGL_DRAW_BUF_LINES * sizeof(uint16_t),
    };
    ESP_ERROR_CHECK(spi_bus_initialize(LCD_HOST, &buscfg, SPI_DMA_CH_AUTO));

    /* ---------------------------------------------------------------------- */
    /* Step 2 – Panel IO (SPI transport)                                      */
    /* ---------------------------------------------------------------------- */
    ESP_LOGI(TAG, "Install panel IO");
    esp_lcd_panel_io_handle_t io_handle = NULL;
    esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num     = PIN_NUM_LCD_DC,
        .cs_gpio_num     = PIN_NUM_LCD_CS,
        .pclk_hz         = LCD_PIXEL_CLOCK_HZ,
        .lcd_cmd_bits    = LCD_CMD_BITS,
        .lcd_param_bits  = LCD_PARAM_BITS,
        .spi_mode        = 0,
        .trans_queue_depth = 10,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(
        (esp_lcd_spi_bus_handle_t)LCD_HOST, &io_config, &io_handle));

    /* ---------------------------------------------------------------------- */
    /* Step 3 – ST7789 panel driver                                           */
    /* ---------------------------------------------------------------------- */
    ESP_LOGI(TAG, "Install ST7789 panel driver");
    esp_lcd_panel_handle_t panel_handle = NULL;
    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num  = PIN_NUM_LCD_RST,
        .rgb_ele_order   = LCD_RGB_ELEMENT_ORDER_BGR,
        .bits_per_pixel  = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io_handle, &panel_config,
                                             &panel_handle));

    /* ---------------------------------------------------------------------- */
    /* Step 4 – Panel init & orientation                                      */
    /* ---------------------------------------------------------------------- */
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_handle));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel_handle));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel_handle, true));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel_handle, true, false));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel_handle, true));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel_handle, true));

    /* ---------------------------------------------------------------------- */
    /* Step 5 – LVGL core init                                                */
    /* ---------------------------------------------------------------------- */
    ESP_LOGI(TAG, "Initialise LVGL");
    lv_init();

    /* ---------------------------------------------------------------------- */
    /* Step 6 – LVGL display object                                           */
    /* ---------------------------------------------------------------------- */
    lv_display_t *display = lv_display_create(LVGL_H_RES, LVGL_V_RES);

    /* ---------------------------------------------------------------------- */
    /* Step 7 – Double draw buffers (DMA-capable)                             */
    /* ---------------------------------------------------------------------- */
    size_t draw_buf_sz = LVGL_H_RES * LVGL_DRAW_BUF_LINES * sizeof(lv_color16_t);

    void *buf1 = heap_caps_malloc(draw_buf_sz, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    assert(buf1);
    void *buf2 = heap_caps_malloc(draw_buf_sz, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    assert(buf2);

    ESP_LOGI(TAG, "Draw buffers: %zu bytes x 2", draw_buf_sz);

    /* ---------------------------------------------------------------------- */
    /* Step 8 – Wire buffers, colour format, flush callback                   */
    /* ---------------------------------------------------------------------- */
    lv_display_set_buffers(display, buf1, buf2, draw_buf_sz,
                           LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_user_data(display, panel_handle);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_flush_cb(display, lvgl_flush_cb);

    /* ---------------------------------------------------------------------- */
    /* Step 9 – LVGL tick timer (2 ms)                                        */
    /* ---------------------------------------------------------------------- */
    ESP_LOGI(TAG, "Install LVGL tick timer");
    const esp_timer_create_args_t tick_args = {
        .callback = &lvgl_tick_cb,
        .name     = "lvgl_tick",
    };
    esp_timer_handle_t tick_timer = NULL;
    ESP_ERROR_CHECK(esp_timer_create(&tick_args, &tick_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(tick_timer,
                                             LVGL_TICK_PERIOD_MS * 1000));

    /* ---------------------------------------------------------------------- */
    /* Step 10 – Panel IO flush-ready callback                                */
    /* ---------------------------------------------------------------------- */
    const esp_lcd_panel_io_callbacks_t io_cbs = {
        .on_color_trans_done = notify_lvgl_flush_ready,
    };
    ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(
        io_handle, &io_cbs, display));

    /* ---------------------------------------------------------------------- */
    /* Step 11 – LVGL background task                                         */
    /* ---------------------------------------------------------------------- */
    ESP_LOGI(TAG, "Create LVGL task");
    xTaskCreate(lvgl_task, "LVGL", LVGL_TASK_STACK_SIZE, NULL,
                LVGL_TASK_PRIORITY, NULL);

    ESP_LOGI(TAG, "LCD + LVGL initialised successfully");
    return display;
}
