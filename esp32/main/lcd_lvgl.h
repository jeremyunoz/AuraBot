/**
 * @file lcd_lvgl.h
 * @brief LCD display and LVGL graphics library driver for AuraBot.
 *
 * Initialises an SPI-connected ST7789 (240x320) display, sets up LVGL
 * with double-buffered partial rendering, and starts a background
 * FreeRTOS task that services the LVGL timer.
 *
 * LVGL is *not* thread-safe.  Any code that touches LVGL objects from
 * outside the LVGL task must bracket the call with
 * lcd_lvgl_lock() / lcd_lvgl_unlock().
 */

#ifndef LCD_LVGL_H
#define LCD_LVGL_H

#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialise SPI bus, ST7789 panel, and LVGL.
 *
 * Call once from app_main().  The function returns the LVGL display
 * handle so that UI code (e.g. RoboEyes) can attach to it.
 *
 * @return Pointer to the created lv_display_t, or NULL on failure.
 */
lv_display_t *lcd_lvgl_init(void);

/**
 * @brief Acquire the LVGL API lock (blocking).
 *
 * Must be held while calling any lv_* function from a thread other
 * than the LVGL task.
 */
void lcd_lvgl_lock(void);

/**
 * @brief Release the LVGL API lock.
 */
void lcd_lvgl_unlock(void);

#ifdef __cplusplus
}
#endif

#endif /* LCD_LVGL_H */
