/**
 * @file robot_eyes.c
 * @brief RoboEyes – state-driven animated eyes for AuraBot.
 *
 * Key design rules:
 *   1) The eye-state task only mutates state variables – never calls lv_*.
 *   2) The LVGL timer callback (update_eyes / draw_eyes) is the only
 *      code that touches LVGL objects, and it runs inside the LVGL task.
 *   3) Blink uses a non-blocking state-machine (no vTaskDelay in LVGL
 *      callbacks).
 *
 * Moods:
 *   MOOD_DEFAULT, MOOD_HEART, MOOD_GREET, MOOD_SLEEP
 *
 * System states:
 *   EYE_STATE_IDLE     -> MOOD_DEFAULT
 *   EYE_STATE_WAKING   -> MOOD_HEART
 *   EYE_STATE_ACTIVE   -> MOOD_GREET
 *   EYE_STATE_SLEEPING -> MOOD_SLEEP
 */

 #include "display/robot_eyes.h"

 #include "lvgl.h"
 #include "esp_log.h"
 #include "esp_timer.h"
 #include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
 
 #include <stdbool.h>
 
 static const char *TAG = "roboeyes";
 
 #define SCREEN_WIDTH   320
 #define SCREEN_HEIGHT  240
 
 #define EYE_COLOR      lv_color_hex(0x00FFFF)
 #define HEART_COLOR    lv_color_hex(0x4000FF)
 #define BG_COLOR       lv_color_hex(0x1a1a1a)
 
 /* blink visible duration (ms) */
 #define BLINK_HOLD_MS  120
#define EYE_STATE_TASK_PRIORITY 4
 
 /* canvas sizes */
 #define HEART_SIZE     80
 #define GREET_W        45
 #define GREET_H        80
 #define SLEEP_W        70
 #define SLEEP_H        80
 
 /* ========================================================================== */
 /* Internal types                                                             */
 /* ========================================================================== */
 
typedef enum {
    MOOD_DEFAULT = 0,
    MOOD_HEART   = 1,
    MOOD_GREET   = 2,
    MOOD_SLEEP   = 3
} mood_type_t;
 
 typedef enum {
     POS_CENTER = 0,
     POS_N      = 1,
     POS_NE     = 2,
     POS_E      = 3,
     POS_SE     = 4,
     POS_S      = 5,
     POS_SW     = 6,
     POS_W      = 7,
     POS_NW     = 8
 } position_t;
 
 typedef struct {
     lv_obj_t *container;
     lv_obj_t *left_eye;
     lv_obj_t *right_eye;
    /* ❤️ heart canvas objects */
     lv_obj_t *left_heart_canvas;
     lv_obj_t *right_heart_canvas;
 
     /* 👀 "> <" greet canvas objects */
     lv_obj_t *left_greet_canvas;
     lv_obj_t *right_greet_canvas;
 
     /* 💤 sleep line-eye canvas objects */
     lv_obj_t *left_sleep_canvas;
     lv_obj_t *right_sleep_canvas;
 
     /* 💤 zzz labels (3, small → large) */
     lv_obj_t *zzz_labels[3];
     uint32_t  zzz_timer;
     int       zzz_step;
 
     int eye_width_default;
     int eye_height_default;
     int eye_width_current;
     int eye_height_current;
     int eye_width_next;
     int eye_height_next;
 
     int border_radius_default;
     int border_radius_current;
     int border_radius_next;
 
     int space_between_default;
     int space_between_current;
     int space_between_next;
 
     int left_eye_x_default;
     int left_eye_y_default;
     int left_eye_x;
     int left_eye_y;
     int left_eye_x_next;
     int left_eye_y_next;
 
     int right_eye_x;
     int right_eye_y;
     int right_eye_x_next;
     int right_eye_y_next;
 
     bool left_eye_open;
     bool right_eye_open;
     int  left_eye_height_offset;
     int  right_eye_height_offset;
 
     mood_type_t current_mood;
     bool curious_mode;
     bool cyclops_mode;
 
    bool     autoblinker;
     int      blink_interval_min;
     int      blink_interval_variation;
     uint32_t blink_timer;
 
     /* blink state-machine (non-blocking) */
     bool     blink_in_progress;
     uint32_t blink_end_ms;
 
     bool     idle_mode;
     int      idle_interval_min;
     int      idle_interval_variation;
     uint32_t idle_timer;
 
    bool     laugh;
     uint32_t laugh_timer;
     bool     laugh_toggle;
 
     bool h_flicker;
     bool h_flicker_alternate;
     int  h_flicker_amplitude;
 
     bool v_flicker;
     bool v_flicker_alternate;
     int  v_flicker_amplitude;
 
    lv_timer_t *update_timer;
 } robot_eyes_t;
 
 static robot_eyes_t eyes;
 
/* ---- Eye-state management ------------------------------------------------ */
static volatile roboeyes_state_t s_eye_state      = EYE_STATE_IDLE;
static volatile roboeyes_state_t s_eye_state_prev = (roboeyes_state_t)-1;
static TaskHandle_t s_eye_state_task_handle       = NULL;

/* ---- Mutex protecting the eyes struct between eye_state_task and draw_eyes */
static SemaphoreHandle_t s_eyes_mutex = NULL;
 
 /* ---- Canvas pixel buffers (static to avoid heap fragmentation) ----------- */
 static lv_color_t heart_buf_left [HEART_SIZE * HEART_SIZE];
 static lv_color_t heart_buf_right[HEART_SIZE * HEART_SIZE];
 static lv_color_t greet_buf_left [GREET_W * GREET_H];
 static lv_color_t greet_buf_right[GREET_W * GREET_H];
 static lv_color_t sleep_buf_left [SLEEP_W * SLEEP_H];
 static lv_color_t sleep_buf_right[SLEEP_W * SLEEP_H];
 
 /* Forward declarations */
 static void create_eyes(void);
 static void update_eyes(lv_timer_t *timer);
 static void draw_eyes(void);
 static int  get_screen_constraint_x(void);
 static int  get_screen_constraint_y(void);
 
static inline uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
}

/* Smooth half-step toward target; snaps to target when it would stall 1 unit
 * away due to integer truncation.  Works correctly in both directions:
 *   opening  (79→80): (79+80)/2=79 == current → snap to 80
 *   closing  ( 2→ 1): ( 2+ 1)/2=1  != current → returns 1 naturally     */
static inline int lerp_snap(int current, int target) {
    int next = (current + target) / 2;
    return (next == current && current != target) ? target : next;
}
 
 /* ========================================================================== */
 /* Constraint helpers                                                         */
 /* ========================================================================== */
 
 static int get_screen_constraint_x(void) {
     return SCREEN_WIDTH - eyes.eye_width_current - eyes.space_between_current
            - eyes.eye_width_current;
 }
 
 static int get_screen_constraint_y(void) {
     return SCREEN_HEIGHT - eyes.eye_height_default;
 }
 
 /* ========================================================================== */
 /* Canvas drawing helpers                                                     */
 /* ========================================================================== */
 
 /* ❤️ Heart – pixel-perfect using the implicit curve (x²+y²-1)³ – x²y³ ≤ 0  */
 static void draw_heart_on_canvas(lv_obj_t *canvas) {
     lv_canvas_fill_bg(canvas, BG_COLOR, LV_OPA_COVER);
     lv_color_t c = HEART_COLOR;
     for (int py = 0; py < HEART_SIZE; py++) {
         for (int px = 0; px < HEART_SIZE; px++) {
             float nx  = (px - HEART_SIZE / 2)   / (HEART_SIZE / 2.6f);
             float ny  = (HEART_SIZE / 2 - py)    / (HEART_SIZE / 2.8f) - 0.15f;
             float val = nx*nx + ny*ny - 1.0f;
             if (val*val*val - nx*nx * ny*ny*ny <= 0.0f) {
                 lv_canvas_set_px(canvas, px, py, c, LV_OPA_COVER);
             }
         }
     }
 }
 
 /* 👀 "> <" line eyes – dir=1 draws ">", dir=-1 draws "<"                     */
 static void draw_greet_on_canvas(lv_obj_t *canvas, int dir) {
     lv_canvas_fill_bg(canvas, BG_COLOR, LV_OPA_COVER);
     lv_color_t c = EYE_COLOR;
     int mid_y          = GREET_H / 2;
     int line_thickness = 3;
 
     for (int py = 0; py < GREET_H; py++) {
         int x_tip  = (dir == 1) ? GREET_W - 1 : 0;
         int x_open = (dir == 1) ? 0           : GREET_W - 1;
         int x_line;
 
         if (py <= mid_y) {
             float ratio = (mid_y > 0) ? (float)py / mid_y : 1.0f;
             x_line = (int)(x_open + ratio * (x_tip - x_open));
         } else {
             float ratio = (float)(py - mid_y) / (GREET_H - 1 - mid_y);
             x_line = (int)(x_tip + ratio * (x_open - x_tip));
         }
 
         for (int t = -line_thickness / 2; t <= line_thickness / 2; t++) {
             int px = x_line + t;
             if (px >= 0 && px < GREET_W) {
                 lv_canvas_set_px(canvas, px, py, c, LV_OPA_COVER);
             }
         }
     }
 }
 
 /* 💤 Sleep – single thick horizontal line centred in the canvas              */
 static void draw_sleep_on_canvas(lv_obj_t *canvas) {
     lv_canvas_fill_bg(canvas, BG_COLOR, LV_OPA_COVER);
     lv_color_t c   = EYE_COLOR;
     int mid_y      = SLEEP_H / 2;
     int thickness  = 6;
     for (int py = mid_y - thickness / 2; py <= mid_y + thickness / 2; py++) {
         for (int px = 0; px < SLEEP_W; px++) {
             lv_canvas_set_px(canvas, px, py, c, LV_OPA_COVER);
         }
     }
 }
 
 /* ========================================================================== */
 /* Create LVGL eye objects                                                    */
 /* ========================================================================== */
 
 static void create_eyes(void) {
     eyes.container = lv_obj_create(lv_screen_active());
     lv_obj_set_size(eyes.container, SCREEN_WIDTH, SCREEN_HEIGHT);
     lv_obj_center(eyes.container);
     lv_obj_set_style_bg_color(eyes.container, BG_COLOR, 0);
     lv_obj_set_style_border_width(eyes.container, 0, 0);
     lv_obj_clear_flag(eyes.container, LV_OBJ_FLAG_SCROLLABLE);
 
     /* --- regular eyes --- */
     eyes.left_eye = lv_obj_create(eyes.container);
     lv_obj_set_style_bg_color(eyes.left_eye, EYE_COLOR, 0);
     lv_obj_set_style_border_width(eyes.left_eye, 0, 0);
     lv_obj_clear_flag(eyes.left_eye, LV_OBJ_FLAG_SCROLLABLE);
 
     eyes.right_eye = lv_obj_create(eyes.container);
     lv_obj_set_style_bg_color(eyes.right_eye, EYE_COLOR, 0);
     lv_obj_set_style_border_width(eyes.right_eye, 0, 0);
     lv_obj_clear_flag(eyes.right_eye, LV_OBJ_FLAG_SCROLLABLE);
 
    /* --- ❤️ heart canvases --- */
     eyes.left_heart_canvas = lv_canvas_create(eyes.container);
     lv_canvas_set_buffer(eyes.left_heart_canvas, heart_buf_left,
                          HEART_SIZE, HEART_SIZE, LV_COLOR_FORMAT_RGB565);
     draw_heart_on_canvas(eyes.left_heart_canvas);
     lv_obj_add_flag(eyes.left_heart_canvas, LV_OBJ_FLAG_HIDDEN);
 
     eyes.right_heart_canvas = lv_canvas_create(eyes.container);
     lv_canvas_set_buffer(eyes.right_heart_canvas, heart_buf_right,
                          HEART_SIZE, HEART_SIZE, LV_COLOR_FORMAT_RGB565);
     draw_heart_on_canvas(eyes.right_heart_canvas);
     lv_obj_add_flag(eyes.right_heart_canvas, LV_OBJ_FLAG_HIDDEN);
 
     /* --- 👀 greet canvases --- */
     eyes.left_greet_canvas = lv_canvas_create(eyes.container);
     lv_canvas_set_buffer(eyes.left_greet_canvas, greet_buf_left,
                          GREET_W, GREET_H, LV_COLOR_FORMAT_RGB565);
     draw_greet_on_canvas(eyes.left_greet_canvas, 1);   /* ">" */
     lv_obj_add_flag(eyes.left_greet_canvas, LV_OBJ_FLAG_HIDDEN);
 
     eyes.right_greet_canvas = lv_canvas_create(eyes.container);
     lv_canvas_set_buffer(eyes.right_greet_canvas, greet_buf_right,
                          GREET_W, GREET_H, LV_COLOR_FORMAT_RGB565);
     draw_greet_on_canvas(eyes.right_greet_canvas, -1); /* "<" */
     lv_obj_add_flag(eyes.right_greet_canvas, LV_OBJ_FLAG_HIDDEN);
 
     /* --- 💤 sleep canvases --- */
     eyes.left_sleep_canvas = lv_canvas_create(eyes.container);
     lv_canvas_set_buffer(eyes.left_sleep_canvas, sleep_buf_left,
                          SLEEP_W, SLEEP_H, LV_COLOR_FORMAT_RGB565);
     draw_sleep_on_canvas(eyes.left_sleep_canvas);
     lv_obj_add_flag(eyes.left_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
 
     eyes.right_sleep_canvas = lv_canvas_create(eyes.container);
     lv_canvas_set_buffer(eyes.right_sleep_canvas, sleep_buf_right,
                          SLEEP_W, SLEEP_H, LV_COLOR_FORMAT_RGB565);
     draw_sleep_on_canvas(eyes.right_sleep_canvas);
     lv_obj_add_flag(eyes.right_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
 
     /* --- 💤 zzz labels (font size 28 → 36 → 48, staircase to upper-right) --- */
     static const lv_font_t *zzz_fonts[3] = {
         &lv_font_montserrat_28,
         &lv_font_montserrat_36,
         &lv_font_montserrat_48
     };
     static const int zzz_x[3] = { 240, 260, 278 };
     static const int zzz_y[3] = {  40,  22,   4 };
 
     for (int i = 0; i < 3; i++) {
         eyes.zzz_labels[i] = lv_label_create(eyes.container);
         lv_label_set_text(eyes.zzz_labels[i], "z");
         lv_obj_set_style_text_color(eyes.zzz_labels[i], EYE_COLOR, 0);
         lv_obj_set_style_text_font(eyes.zzz_labels[i], zzz_fonts[i], 0);
         lv_obj_set_pos(eyes.zzz_labels[i], zzz_x[i], zzz_y[i]);
         lv_obj_add_flag(eyes.zzz_labels[i], LV_OBJ_FLAG_HIDDEN);
     }
 }
 
 /* ========================================================================== */
 /* Low-level state setters (only modify variables, never call lv_*)           */
 /* ========================================================================== */
 
 static void roboeyes_set_mood(mood_type_t mood)  { eyes.current_mood = mood; }
 
 static void roboeyes_set_position(position_t pos) {
     int total_width = eyes.eye_width_default * 2 + eyes.space_between_default;
     int center_x    = (SCREEN_WIDTH - total_width) / 2;
     int max_x       = get_screen_constraint_x();
     int max_y       = get_screen_constraint_y();
 
     switch (pos) {
         case POS_CENTER: eyes.left_eye_x_next = center_x;  eyes.left_eye_y_next = (SCREEN_HEIGHT - eyes.eye_height_default) / 2; break;
         case POS_N:      eyes.left_eye_x_next = center_x;  eyes.left_eye_y_next = 0;         break;
         case POS_NE:     eyes.left_eye_x_next = max_x;     eyes.left_eye_y_next = 0;         break;
         case POS_E:      eyes.left_eye_x_next = max_x;     eyes.left_eye_y_next = max_y / 2; break;
         case POS_SE:     eyes.left_eye_x_next = max_x;     eyes.left_eye_y_next = max_y;     break;
         case POS_S:      eyes.left_eye_x_next = center_x;  eyes.left_eye_y_next = max_y;     break;
         case POS_SW:     eyes.left_eye_x_next = 0;         eyes.left_eye_y_next = max_y;     break;
         case POS_W:      eyes.left_eye_x_next = 0;         eyes.left_eye_y_next = max_y / 2; break;
         case POS_NW:     eyes.left_eye_x_next = 0;         eyes.left_eye_y_next = 0;         break;
         default:         eyes.left_eye_x_next = center_x;  eyes.left_eye_y_next = (SCREEN_HEIGHT - eyes.eye_height_default) / 2; break;
     }
 }
 
 static void roboeyes_set_curious(bool enabled)   { eyes.curious_mode = enabled; }
 
 static void roboeyes_close(void) {
     eyes.eye_height_next = 1;
     eyes.left_eye_open   = false;
     eyes.right_eye_open  = false;
 }
 
static void roboeyes_open(void) {
    eyes.left_eye_open   = true;
    eyes.right_eye_open  = true;
    eyes.eye_height_next = eyes.eye_height_default;
}
 
 static void roboeyes_blink(void) {
     roboeyes_close();
     eyes.blink_in_progress = true;
     eyes.blink_end_ms      = now_ms() + BLINK_HOLD_MS;
 }
 
 static void roboeyes_set_autoblink(bool enabled, int interval_sec, int var_sec) {
     eyes.autoblinker              = enabled;
     eyes.blink_interval_min       = interval_sec;
     eyes.blink_interval_variation = var_sec;
     if (enabled) {
         eyes.blink_timer = now_ms() + (uint32_t)(interval_sec * 1000);
     }
 }
 
 static void roboeyes_set_idle(bool enabled, int interval_sec, int var_sec) {
     eyes.idle_mode               = enabled;
     eyes.idle_interval_min       = interval_sec;
     eyes.idle_interval_variation = var_sec;
     if (enabled) {
         eyes.idle_timer = now_ms() + (uint32_t)(interval_sec * 1000);
     }
 }
 
 static void roboeyes_set_h_flicker(bool enabled, int amp) {
     eyes.h_flicker           = enabled;
     eyes.h_flicker_amplitude = amp;
 }
 
 static void roboeyes_set_v_flicker(bool enabled, int amp) {
     eyes.v_flicker           = enabled;
     eyes.v_flicker_amplitude = amp;
 }
 
 static void roboeyes_anim_laugh(void)        { eyes.laugh    = true; eyes.laugh_toggle    = true; }
 
 /* ========================================================================== */
 /* draw_eyes – core rendering (runs inside LVGL task via timer)               */
 /* ========================================================================== */
 
 static void draw_eyes(void) {
 
     /* ---- blink state-machine ---- */
     if (eyes.blink_in_progress && now_ms() >= eyes.blink_end_ms) {
         roboeyes_open();
         eyes.blink_in_progress = false;
     }
 
     /* ---- curious height offset ---- */
     if (eyes.curious_mode) {
         eyes.left_eye_height_offset =
             (eyes.left_eye_x_next <= 10) ? 8 : 0;
         eyes.right_eye_height_offset =
             (eyes.right_eye_x_next >= SCREEN_WIDTH - eyes.eye_width_current - 10) ? 8 : 0;
     } else {
         eyes.left_eye_height_offset  = 0;
         eyes.right_eye_height_offset = 0;
     }
 
     int prev_eye_height = eyes.eye_height_current;
 
    /* ---- smooth interpolation ---- */
    eyes.eye_width_current     = lerp_snap(eyes.eye_width_current,     eyes.eye_width_next);
    eyes.eye_height_current    = lerp_snap(eyes.eye_height_current,    eyes.eye_height_next);
    eyes.space_between_current = lerp_snap(eyes.space_between_current, eyes.space_between_next);
    eyes.border_radius_current = lerp_snap(eyes.border_radius_current, eyes.border_radius_next);

    eyes.left_eye_x = lerp_snap(eyes.left_eye_x, eyes.left_eye_x_next);
    eyes.left_eye_y = lerp_snap(eyes.left_eye_y, eyes.left_eye_y_next);
 
     int height_diff = prev_eye_height - eyes.eye_height_current;
     if (height_diff > 0 && eyes.eye_height_current < eyes.eye_height_default)
         eyes.left_eye_y += height_diff;
 
    eyes.right_eye_x_next = eyes.left_eye_x_next + eyes.eye_width_current
                            + eyes.space_between_current;
    eyes.right_eye_y_next = eyes.left_eye_y_next;
   eyes.right_eye_x = lerp_snap(eyes.right_eye_x, eyes.right_eye_x_next);
   eyes.right_eye_y = eyes.left_eye_y;
 
     int left_height  = eyes.eye_height_current + eyes.left_eye_height_offset;
     int right_height = eyes.eye_height_current + eyes.right_eye_height_offset;
 
     if (eyes.left_eye_open  && left_height  <= 1) eyes.eye_height_next = eyes.eye_height_default;
     if (eyes.right_eye_open && right_height <= 1) eyes.eye_height_next = eyes.eye_height_default;
 
     /* ---- autoblink ---- */
     if (eyes.autoblinker) {
         uint32_t now = now_ms();
         if (now >= eyes.blink_timer) {
             roboeyes_blink();
             uint32_t var_ms = (eyes.blink_interval_variation > 0)
                               ? (uint32_t)((esp_random() % eyes.blink_interval_variation) * 1000)
                               : 0;
             eyes.blink_timer = now + (uint32_t)(eyes.blink_interval_min * 1000) + var_ms;
         }
     }
 
     /* ---- idle random movement ---- */
     if (eyes.idle_mode) {
         uint32_t now = now_ms();
         if (now >= eyes.idle_timer) {
             int max_x = SCREEN_WIDTH  - eyes.eye_width_default  * 2 - eyes.space_between_default;
             int max_y = SCREEN_HEIGHT - eyes.eye_height_default;
             if (max_x < 0) max_x = 0;
             if (max_y < 0) max_y = 0;
             eyes.left_eye_x_next = esp_random() % (max_x + 1);
             eyes.left_eye_y_next = esp_random() % (max_y + 1);
             uint32_t var_ms = (eyes.idle_interval_variation > 0)
                               ? (uint32_t)((esp_random() % eyes.idle_interval_variation) * 1000)
                               : 0;
             eyes.idle_timer = now + (uint32_t)(eyes.idle_interval_min * 1000) + var_ms;
         }
     }
 
    /* ---- laugh animation ---- */
     if (eyes.laugh) {
         uint32_t now = now_ms();
         if (eyes.laugh_toggle) {
             roboeyes_set_v_flicker(true, 5);
             eyes.laugh_timer  = now;
             eyes.laugh_toggle = false;
         } else if (now >= eyes.laugh_timer + 500) {
             roboeyes_set_v_flicker(false, 0);
             eyes.laugh_toggle = true;
             eyes.laugh        = false;
         }
     }
 
     /* ---- flicker offsets ---- */
     int flicker_x = 0, flicker_y = 0;
     if (eyes.h_flicker) {
         flicker_x = eyes.h_flicker_alternate ? eyes.h_flicker_amplitude
                                              : -eyes.h_flicker_amplitude;
         eyes.h_flicker_alternate = !eyes.h_flicker_alternate;
     }
     if (eyes.v_flicker) {
         flicker_y = eyes.v_flicker_alternate ? eyes.v_flicker_amplitude
                                              : -eyes.v_flicker_amplitude;
         eyes.v_flicker_alternate = !eyes.v_flicker_alternate;
     }
 
     /* ================================================================== */
     /* MOOD_HEART – show heart canvases, hide everything else             */
     /* ================================================================== */
    if (eyes.current_mood == MOOD_HEART) {
        lv_obj_add_flag(eyes.left_eye,          LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_eye,         LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.left_greet_canvas,  LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_greet_canvas, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.left_sleep_canvas,  LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
         for (int i = 0; i < 3; i++) lv_obj_add_flag(eyes.zzz_labels[i], LV_OBJ_FLAG_HIDDEN);
 
         lv_obj_set_pos(eyes.left_heart_canvas,
                        eyes.left_eye_x  + flicker_x - 17,
                        eyes.left_eye_y  + flicker_y);
         lv_obj_clear_flag(eyes.left_heart_canvas, LV_OBJ_FLAG_HIDDEN);
 
         if (!eyes.cyclops_mode) {
             lv_obj_set_pos(eyes.right_heart_canvas,
                            eyes.right_eye_x + flicker_x - 17,
                            eyes.right_eye_y + flicker_y);
             lv_obj_clear_flag(eyes.right_heart_canvas, LV_OBJ_FLAG_HIDDEN);
         } else {
             lv_obj_add_flag(eyes.right_heart_canvas, LV_OBJ_FLAG_HIDDEN);
         }
         return;
     }
     lv_obj_add_flag(eyes.left_heart_canvas,  LV_OBJ_FLAG_HIDDEN);
     lv_obj_add_flag(eyes.right_heart_canvas, LV_OBJ_FLAG_HIDDEN);
 
     /* ================================================================== */
     /* MOOD_GREET – show "> <" canvases, hide everything else             */
     /* ================================================================== */
    if (eyes.current_mood == MOOD_GREET) {
        lv_obj_add_flag(eyes.left_eye,          LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_eye,         LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.left_sleep_canvas,  LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
         for (int i = 0; i < 3; i++) lv_obj_add_flag(eyes.zzz_labels[i], LV_OBJ_FLAG_HIDDEN);
 
         lv_obj_set_pos(eyes.left_greet_canvas,
                        eyes.left_eye_x  + flicker_x,
                        eyes.left_eye_y  + flicker_y);
         lv_obj_clear_flag(eyes.left_greet_canvas, LV_OBJ_FLAG_HIDDEN);
 
         if (!eyes.cyclops_mode) {
             lv_obj_set_pos(eyes.right_greet_canvas,
                            eyes.right_eye_x + flicker_x,
                            eyes.right_eye_y + flicker_y);
             lv_obj_clear_flag(eyes.right_greet_canvas, LV_OBJ_FLAG_HIDDEN);
         } else {
             lv_obj_add_flag(eyes.right_greet_canvas, LV_OBJ_FLAG_HIDDEN);
         }
         return;
     }
     lv_obj_add_flag(eyes.left_greet_canvas,  LV_OBJ_FLAG_HIDDEN);
     lv_obj_add_flag(eyes.right_greet_canvas, LV_OBJ_FLAG_HIDDEN);
 
     /* ================================================================== */
     /* MOOD_SLEEP – show line-eye canvases + zzz state-machine            */
     /* ================================================================== */
    if (eyes.current_mood == MOOD_SLEEP) {
        lv_obj_add_flag(eyes.left_eye,          LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_eye,         LV_OBJ_FLAG_HIDDEN);
 
         int sleep_offset_x = (SLEEP_W - eyes.eye_width_default) / 2;
         lv_obj_set_pos(eyes.left_sleep_canvas,
                        eyes.left_eye_x  - sleep_offset_x,
                        eyes.left_eye_y  + (eyes.eye_height_default - SLEEP_H) / 2);
         lv_obj_clear_flag(eyes.left_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
 
         if (!eyes.cyclops_mode) {
             lv_obj_set_pos(eyes.right_sleep_canvas,
                            eyes.right_eye_x - sleep_offset_x,
                            eyes.right_eye_y + (eyes.eye_height_default - SLEEP_H) / 2);
             lv_obj_clear_flag(eyes.right_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
         } else {
             lv_obj_add_flag(eyes.right_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
         }
 
         /* zzz state-machine: reveal one 'z' every 800 ms, cycle 0→3 */
         uint32_t now = now_ms();
         if (now >= eyes.zzz_timer) {
             eyes.zzz_step  = (eyes.zzz_step + 1) % 4;
             eyes.zzz_timer = now + 800;
         }
         for (int i = 0; i < 3; i++) {
             if (i < eyes.zzz_step)
                 lv_obj_clear_flag(eyes.zzz_labels[i], LV_OBJ_FLAG_HIDDEN);
             else
                 lv_obj_add_flag(eyes.zzz_labels[i], LV_OBJ_FLAG_HIDDEN);
         }
         return;
     }
     lv_obj_add_flag(eyes.left_sleep_canvas,  LV_OBJ_FLAG_HIDDEN);
     lv_obj_add_flag(eyes.right_sleep_canvas, LV_OBJ_FLAG_HIDDEN);
     for (int i = 0; i < 3; i++) lv_obj_add_flag(eyes.zzz_labels[i], LV_OBJ_FLAG_HIDDEN);
 
    /* ================================================================== */
    /* MOOD_DEFAULT – standard rounded-rectangle eyes                     */
    /* ================================================================== */

    int right_eye_width = eyes.cyclops_mode ? 0 : eyes.eye_width_current;
    int right_eye_h     = eyes.cyclops_mode ? 0
                          : (eyes.eye_height_current + eyes.right_eye_height_offset);

    lv_obj_clear_flag(eyes.left_eye, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_size(eyes.left_eye, eyes.eye_width_current,
                    eyes.eye_height_current + eyes.left_eye_height_offset);
    lv_obj_set_pos(eyes.left_eye,
                   eyes.left_eye_x + flicker_x,
                   eyes.left_eye_y + flicker_y);
    lv_obj_set_style_radius(eyes.left_eye, eyes.border_radius_current, 0);

    if (!eyes.cyclops_mode) {
        lv_obj_set_size(eyes.right_eye, right_eye_width, right_eye_h);
        lv_obj_set_pos(eyes.right_eye,
                       eyes.right_eye_x + flicker_x,
                       eyes.right_eye_y + flicker_y);
        lv_obj_set_style_radius(eyes.right_eye, eyes.border_radius_current, 0);
        lv_obj_clear_flag(eyes.right_eye, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(eyes.right_eye, LV_OBJ_FLAG_HIDDEN);
    }
}
 
static void update_eyes(lv_timer_t *timer) {
    (void)timer;
    if (xSemaphoreTake(s_eyes_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
        draw_eyes();
        xSemaphoreGive(s_eyes_mutex);
    } else {
        static uint32_t s_last_lock_warn_ms = 0;
        uint32_t now = now_ms();
        if (now - s_last_lock_warn_ms >= 1000) {
            ESP_LOGW(TAG, "draw skipped: eyes mutex busy");
            s_last_lock_warn_ms = now;
        }
    }
}
 
 /* ========================================================================== */
 /* Eye-state task – maps system states to eye behaviour                       */
 /* ========================================================================== */
/*
 *  IDLE     – default alert look, auto-blink, random idle gaze
 *  WAKING   – heart eyes, laugh bounce, gentle auto-blink
 *  ACTIVE   – "> <" greet eyes, curious gaze, auto-blink, random movement
 *  SLEEPING – sleep line-eyes + zzz animation, eyes close
 */
 
static void reset_all_effects(void) {
    roboeyes_set_autoblink(false, 0, 0);
    roboeyes_set_idle(false, 0, 0);
    roboeyes_set_curious(false);
    roboeyes_set_h_flicker(false, 0);
    roboeyes_set_v_flicker(false, 0);
    eyes.blink_in_progress = false;
    eyes.laugh             = false;
}
 
 static void eye_state_task(void *arg) {
     (void)arg;
     s_eye_state_task_handle = xTaskGetCurrentTaskHandle();
     ESP_LOGI(TAG, "Eye-state task started");

    while (1) {
        if (s_eye_state != s_eye_state_prev) {
            roboeyes_state_t entering = s_eye_state;
            ESP_LOGI(TAG, "Eye state -> %d", (int)entering);

            xSemaphoreTake(s_eyes_mutex, portMAX_DELAY);

            switch (entering) {

            /* -------------------------------------------------------------- */
            /* IDLE – default alert look, centred, slow blink                 */
            /* -------------------------------------------------------------- */
            case EYE_STATE_IDLE:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_open();
                roboeyes_set_mood(MOOD_DEFAULT);
                roboeyes_set_autoblink(true, 3, 2);
                break;

            /* -------------------------------------------------------------- */
            /* WAKING – heart eyes, warm welcome                              */
            /* -------------------------------------------------------------- */
            case EYE_STATE_WAKING:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_open();
                roboeyes_set_mood(MOOD_HEART);
                /* Gentle bounce to emphasise the hearts */
                roboeyes_anim_laugh();
                /* Slow blink while showing hearts */
                roboeyes_set_autoblink(true, 3, 2);
                break;

            /* -------------------------------------------------------------- */
            /* ACTIVE – greet eyes, curious & alert                          */
            /* -------------------------------------------------------------- */
            case EYE_STATE_ACTIVE:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_open();
                roboeyes_set_mood(MOOD_GREET);
                /* Slight horizontal curiosity wiggle */
                roboeyes_set_h_flicker(true, 2);
                /* Random gaze every 2-4 s */
                // roboeyes_set_idle(true, 2, 2);
                break;

            /* -------------------------------------------------------------- */
            /* SLEEPING – sleep line-eyes + zzz, then fully close             */
            /* -------------------------------------------------------------- */
            case EYE_STATE_SLEEPING:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_set_mood(MOOD_SLEEP);
                xSemaphoreGive(s_eyes_mutex);
                /* Let zzz play for up to 4 s, but bail early if the state
                   changes (e.g. enter_sleeping() transitions back to IDLE). */
                for (int _t = 0; _t < 40; _t++) {
                    if (s_eye_state != EYE_STATE_SLEEPING) break;
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
                xSemaphoreTake(s_eyes_mutex, portMAX_DELAY);
                /* Only close if we are still in SLEEPING */
                if (s_eye_state == EYE_STATE_SLEEPING) {
                    roboeyes_close();
                }
                break;
            }

            xSemaphoreGive(s_eyes_mutex);
            s_eye_state_prev = entering;
        }

        /* Wait for state change notification or 100 ms timeout */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
    }
 }
 
 /* ========================================================================== */
 /* Public API                                                                 */
 /* ========================================================================== */
 
 void roboeyes_set_state(roboeyes_state_t state) {
     s_eye_state = state;
     if (s_eye_state_task_handle != NULL) {
         xTaskNotifyGive(s_eye_state_task_handle);
     }
 }
 
 void roboeyes_init(lv_display_t *disp) {
     (void)disp;
 
     ESP_LOGI(TAG, "Initialising RoboEyes (320x240)");
 
     /* ---- default geometry ---- */
     eyes.eye_width_default  = 45;
     eyes.eye_height_default = 80;
     eyes.eye_width_current  = 45;
     eyes.eye_height_current = 1;   /* boot closed; roboeyes_open() restores */
     eyes.eye_width_next     = 45;
     eyes.eye_height_next    = 80;
 
     eyes.border_radius_default = 22;
     eyes.border_radius_current = 22;
     eyes.border_radius_next    = 22;
 
     eyes.space_between_default = 110;
     eyes.space_between_current = 110;
     eyes.space_between_next    = 110;
 
     eyes.left_eye_x_default =
         (SCREEN_WIDTH - (eyes.eye_width_default * 2 + eyes.space_between_default)) / 2;
     eyes.left_eye_y_default =
         (SCREEN_HEIGHT - eyes.eye_height_default) / 2;
 
     eyes.left_eye_x      = eyes.left_eye_x_default;
     eyes.left_eye_y      = eyes.left_eye_y_default;
     eyes.left_eye_x_next = eyes.left_eye_x_default;
     eyes.left_eye_y_next = eyes.left_eye_y_default;
 
     eyes.right_eye_x      = eyes.left_eye_x + eyes.eye_width_default
                              + eyes.space_between_default;
     eyes.right_eye_y      = eyes.left_eye_y;
     eyes.right_eye_x_next = eyes.right_eye_x;
     eyes.right_eye_y_next = eyes.right_eye_y;
 
     eyes.left_eye_open           = false;
     eyes.right_eye_open          = false;
     eyes.left_eye_height_offset  = 0;
     eyes.right_eye_height_offset = 0;
 
     eyes.current_mood = MOOD_DEFAULT;
     eyes.curious_mode = false;
     eyes.cyclops_mode = false;
 
    eyes.autoblinker              = false;
     eyes.blink_interval_min       = 1;
     eyes.blink_interval_variation = 4;
     eyes.blink_timer              = 0;
     eyes.blink_in_progress        = false;
     eyes.blink_end_ms             = 0;
 
     eyes.idle_mode               = false;
     eyes.idle_interval_min       = 1;
     eyes.idle_interval_variation = 3;
     eyes.idle_timer              = 0;
 
    eyes.laugh           = false;
     eyes.laugh_toggle    = true;
 
     eyes.h_flicker           = false;
     eyes.h_flicker_alternate = false;
     eyes.h_flicker_amplitude = 2;
 
     eyes.v_flicker           = false;
     eyes.v_flicker_alternate = false;
     eyes.v_flicker_amplitude = 10;
 
    eyes.zzz_step  = 0;
     eyes.zzz_timer = 0;
 
    s_eyes_mutex = xSemaphoreCreateMutex();
    configASSERT(s_eyes_mutex);

    create_eyes();

    /* 20 ms LVGL timer drives rendering */
    eyes.update_timer = lv_timer_create(update_eyes, 20, NULL);

    roboeyes_open();

    /* Start eye-state task (never calls lv_*) */
    xTaskCreate(eye_state_task, "eye_state", 4096, NULL, EYE_STATE_TASK_PRIORITY, NULL);
 
     ESP_LOGI(TAG, "RoboEyes initialised – 4-mood state-driven mode");
 }