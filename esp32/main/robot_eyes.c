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
 */

#include "display/robot_eyes.h"

#include "lvgl.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <stdbool.h>

static const char *TAG = "roboeyes";

#define SCREEN_WIDTH   320
#define SCREEN_HEIGHT  240

#define EYE_COLOR      lv_color_hex(0x00FFFF)
#define BG_COLOR       lv_color_hex(0x1a1a1a)

/* blink visible duration (ms) */
#define BLINK_HOLD_MS  120

/* ========================================================================== */
/* Internal types                                                             */
/* ========================================================================== */

typedef enum {
    MOOD_DEFAULT = 0,
    MOOD_TIRED   = 1,
    MOOD_ANGRY   = 2,
    MOOD_HAPPY   = 3
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
    lv_obj_t *left_eyelid_tired;
    lv_obj_t *right_eyelid_tired;
    lv_obj_t *left_eyelid_angry;
    lv_obj_t *right_eyelid_angry;
    lv_obj_t *left_eyelid_happy;
    lv_obj_t *right_eyelid_happy;
    lv_obj_t *sweat_drops[3];

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
    int left_eye_height_offset;
    int right_eye_height_offset;

    mood_type_t current_mood;
    bool curious_mode;
    bool cyclops_mode;

    int eyelid_tired_height;
    int eyelid_tired_height_next;
    int eyelid_angry_height;
    int eyelid_angry_height_next;
    int eyelid_happy_offset;
    int eyelid_happy_offset_next;

    bool autoblinker;
    int blink_interval_min;
    int blink_interval_variation;
    uint32_t blink_timer;

    /* blink state-machine (non-blocking) */
    bool blink_in_progress;
    uint32_t blink_end_ms;

    bool idle_mode;
    int idle_interval_min;
    int idle_interval_variation;
    uint32_t idle_timer;

    bool confused;
    uint32_t confused_timer;
    bool confused_toggle;

    bool laugh;
    uint32_t laugh_timer;
    bool laugh_toggle;

    bool h_flicker;
    bool h_flicker_alternate;
    int h_flicker_amplitude;

    bool v_flicker;
    bool v_flicker_alternate;
    int v_flicker_amplitude;

    bool sweat;
    float sweat_y_pos[3];
    float sweat_y_max[3];
    float sweat_x_pos[3];
    float sweat_width[3];
    float sweat_height[3];
    int sweat_x_initial[3];

    lv_timer_t *update_timer;
} robot_eyes_t;

static robot_eyes_t eyes;

/* ---- Eye-state management ------------------------------------------------ */
static volatile roboeyes_state_t s_eye_state      = EYE_STATE_IDLE;
static volatile roboeyes_state_t s_eye_state_prev = (roboeyes_state_t)-1;

/* Forward declarations */
static void create_eyes(void);
static void update_eyes(lv_timer_t *timer);
static void draw_eyes(void);
static int  get_screen_constraint_x(void);
static int  get_screen_constraint_y(void);

static inline uint32_t now_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
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
/* Create LVGL eye objects                                                    */
/* ========================================================================== */

static void create_eyes(void) {
    eyes.container = lv_obj_create(lv_screen_active());
    lv_obj_set_size(eyes.container, SCREEN_WIDTH, SCREEN_HEIGHT);
    lv_obj_center(eyes.container);
    lv_obj_set_style_bg_color(eyes.container, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.container, 0, 0);
    lv_obj_clear_flag(eyes.container, LV_OBJ_FLAG_SCROLLABLE);

    eyes.left_eye = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.left_eye, EYE_COLOR, 0);
    lv_obj_set_style_border_width(eyes.left_eye, 0, 0);
    lv_obj_clear_flag(eyes.left_eye, LV_OBJ_FLAG_SCROLLABLE);

    eyes.right_eye = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.right_eye, EYE_COLOR, 0);
    lv_obj_set_style_border_width(eyes.right_eye, 0, 0);
    lv_obj_clear_flag(eyes.right_eye, LV_OBJ_FLAG_SCROLLABLE);

    eyes.left_eyelid_tired = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.left_eyelid_tired, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.left_eyelid_tired, 0, 0);
    lv_obj_add_flag(eyes.left_eyelid_tired, LV_OBJ_FLAG_HIDDEN);

    eyes.right_eyelid_tired = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.right_eyelid_tired, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.right_eyelid_tired, 0, 0);
    lv_obj_add_flag(eyes.right_eyelid_tired, LV_OBJ_FLAG_HIDDEN);

    eyes.left_eyelid_angry = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.left_eyelid_angry, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.left_eyelid_angry, 0, 0);
    lv_obj_add_flag(eyes.left_eyelid_angry, LV_OBJ_FLAG_HIDDEN);

    eyes.right_eyelid_angry = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.right_eyelid_angry, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.right_eyelid_angry, 0, 0);
    lv_obj_add_flag(eyes.right_eyelid_angry, LV_OBJ_FLAG_HIDDEN);

    eyes.left_eyelid_happy = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.left_eyelid_happy, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.left_eyelid_happy, 0, 0);
    lv_obj_add_flag(eyes.left_eyelid_happy, LV_OBJ_FLAG_HIDDEN);

    eyes.right_eyelid_happy = lv_obj_create(eyes.container);
    lv_obj_set_style_bg_color(eyes.right_eyelid_happy, BG_COLOR, 0);
    lv_obj_set_style_border_width(eyes.right_eyelid_happy, 0, 0);
    lv_obj_add_flag(eyes.right_eyelid_happy, LV_OBJ_FLAG_HIDDEN);

    for (int i = 0; i < 3; i++) {
        eyes.sweat_drops[i] = lv_obj_create(eyes.container);
        lv_obj_set_style_bg_color(eyes.sweat_drops[i], EYE_COLOR, 0);
        lv_obj_set_style_border_width(eyes.sweat_drops[i], 0, 0);
        lv_obj_set_style_radius(eyes.sweat_drops[i], 3, 0);
        lv_obj_add_flag(eyes.sweat_drops[i], LV_OBJ_FLAG_HIDDEN);
    }
}

/* ========================================================================== */
/* Low-level state setters (only modify variables, never call lv_*)           */
/* ========================================================================== */

static void roboeyes_set_mood(mood_type_t mood)        { eyes.current_mood = mood; }

static void roboeyes_set_position(position_t pos) {
    int max_x = get_screen_constraint_x();
    int max_y = get_screen_constraint_y();
    switch (pos) {
        case POS_N:  eyes.left_eye_x_next = max_x / 2; eyes.left_eye_y_next = 0;         break;
        case POS_NE: eyes.left_eye_x_next = max_x;     eyes.left_eye_y_next = 0;         break;
        case POS_E:  eyes.left_eye_x_next = max_x;     eyes.left_eye_y_next = max_y / 2; break;
        case POS_SE: eyes.left_eye_x_next = max_x;     eyes.left_eye_y_next = max_y;     break;
        case POS_S:  eyes.left_eye_x_next = max_x / 2; eyes.left_eye_y_next = max_y;     break;
        case POS_SW: eyes.left_eye_x_next = 0;         eyes.left_eye_y_next = max_y;     break;
        case POS_W:  eyes.left_eye_x_next = 0;         eyes.left_eye_y_next = max_y / 2; break;
        case POS_NW: eyes.left_eye_x_next = 0;         eyes.left_eye_y_next = 0;         break;
        default:     eyes.left_eye_x_next = max_x / 2; eyes.left_eye_y_next = max_y / 2; break;
    }
}

static void roboeyes_set_curious(bool enabled)         { eyes.curious_mode = enabled; }

static void roboeyes_close(void) {
    eyes.eye_height_next = 1;
    eyes.left_eye_open   = false;
    eyes.right_eye_open  = false;
}

static void roboeyes_open(void) {
    eyes.left_eye_open  = true;
    eyes.right_eye_open = true;
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

static void roboeyes_set_sweat(bool enabled)   { eyes.sweat = enabled; }
static void roboeyes_anim_confused(void)       { eyes.confused = true; eyes.confused_toggle = true; }
static void roboeyes_anim_laugh(void)          { eyes.laugh = true; eyes.laugh_toggle = true; }

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
        if (eyes.left_eye_x_next <= 10) {
            eyes.left_eye_height_offset = 8;
        } else if (eyes.left_eye_x_next >= (get_screen_constraint_x() - 10)
                   && eyes.cyclops_mode) {
            eyes.left_eye_height_offset = 8;
        } else {
            eyes.left_eye_height_offset = 0;
        }

        if (eyes.right_eye_x_next >= SCREEN_WIDTH - eyes.eye_width_current - 10) {
            eyes.right_eye_height_offset = 8;
        } else {
            eyes.right_eye_height_offset = 0;
        }
    } else {
        eyes.left_eye_height_offset  = 0;
        eyes.right_eye_height_offset = 0;
    }

    int prev_eye_height = eyes.eye_height_current;

    /* ---- smooth interpolation ---- */
    eyes.eye_width_current     = (eyes.eye_width_current  + eyes.eye_width_next)  / 2;
    eyes.eye_height_current    = (eyes.eye_height_current + eyes.eye_height_next) / 2;
    eyes.space_between_current = (eyes.space_between_current + eyes.space_between_next) / 2;
    eyes.border_radius_current = (eyes.border_radius_current + eyes.border_radius_next) / 2;

    eyes.left_eye_x = (eyes.left_eye_x + eyes.left_eye_x_next) / 2;
    eyes.left_eye_y = (eyes.left_eye_y + eyes.left_eye_y_next) / 2;

    int height_diff = prev_eye_height - eyes.eye_height_current;
    if (height_diff > 0 && eyes.eye_height_current < eyes.eye_height_default) {
        eyes.left_eye_y += height_diff;
    }

    eyes.right_eye_x_next = eyes.left_eye_x_next + eyes.eye_width_current
                            + eyes.space_between_current;
    eyes.right_eye_y_next = eyes.left_eye_y_next;
    eyes.right_eye_x      = (eyes.right_eye_x + eyes.right_eye_x_next) / 2;
    eyes.right_eye_y      = eyes.left_eye_y;

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
            eyes.left_eye_x_next = esp_random() % (get_screen_constraint_x() + 1);
            eyes.left_eye_y_next = esp_random() % (get_screen_constraint_y() + 1);
            uint32_t var_ms = (eyes.idle_interval_variation > 0)
                              ? (uint32_t)((esp_random() % eyes.idle_interval_variation) * 1000)
                              : 0;
            eyes.idle_timer = now + (uint32_t)(eyes.idle_interval_min * 1000) + var_ms;
        }
    }

    /* ---- confused animation ---- */
    if (eyes.confused) {
        uint32_t now = now_ms();
        if (eyes.confused_toggle) {
            roboeyes_set_h_flicker(true, 20);
            eyes.confused_timer  = now;
            eyes.confused_toggle = false;
        } else if (now >= eyes.confused_timer + 500) {
            roboeyes_set_h_flicker(false, 0);
            eyes.confused_toggle = true;
            eyes.confused        = false;
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

    int right_eye_width  = eyes.cyclops_mode ? 0 : eyes.eye_width_current;
    int right_eye_h      = eyes.cyclops_mode ? 0
                           : (eyes.eye_height_current + eyes.right_eye_height_offset);

    /* ---- eye body ---- */
    lv_obj_set_size(eyes.left_eye, eyes.eye_width_current,
                    eyes.eye_height_current + eyes.left_eye_height_offset);
    lv_obj_set_pos(eyes.left_eye, eyes.left_eye_x + flicker_x,
                   eyes.left_eye_y + flicker_y);
    lv_obj_set_style_radius(eyes.left_eye, eyes.border_radius_current, 0);

    if (!eyes.cyclops_mode) {
        lv_obj_set_size(eyes.right_eye, right_eye_width, right_eye_h);
        lv_obj_set_pos(eyes.right_eye, eyes.right_eye_x + flicker_x,
                       eyes.right_eye_y + flicker_y);
        lv_obj_set_style_radius(eyes.right_eye, eyes.border_radius_current, 0);
        lv_obj_clear_flag(eyes.right_eye, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(eyes.right_eye, LV_OBJ_FLAG_HIDDEN);
    }

    /* ---- mood → eyelid targets ---- */
    if (eyes.current_mood == MOOD_TIRED) {
        eyes.eyelid_tired_height_next = eyes.eye_height_current / 2;
        eyes.eyelid_angry_height_next = 0;
    } else if (eyes.current_mood == MOOD_ANGRY) {
        eyes.eyelid_angry_height_next = eyes.eye_height_current / 2;
        eyes.eyelid_tired_height_next = 0;
    } else {
        eyes.eyelid_tired_height_next = 0;
        eyes.eyelid_angry_height_next = 0;
    }

    if (eyes.current_mood == MOOD_HAPPY) {
        eyes.eyelid_happy_offset_next = eyes.eye_height_current / 2;
    } else {
        eyes.eyelid_happy_offset_next = 0;
    }

    eyes.eyelid_tired_height = (eyes.eyelid_tired_height + eyes.eyelid_tired_height_next) / 2;
    eyes.eyelid_angry_height = (eyes.eyelid_angry_height + eyes.eyelid_angry_height_next) / 2;
    eyes.eyelid_happy_offset = (eyes.eyelid_happy_offset + eyes.eyelid_happy_offset_next) / 2;

    /* ---- tired eyelid ---- */
    if (eyes.eyelid_tired_height > 0) {
        lv_obj_set_size(eyes.left_eyelid_tired, eyes.eye_width_current,
                        eyes.eyelid_tired_height);
        lv_obj_set_pos(eyes.left_eyelid_tired, eyes.left_eye_x + flicker_x,
                       eyes.left_eye_y + flicker_y);
        lv_obj_clear_flag(eyes.left_eyelid_tired, LV_OBJ_FLAG_HIDDEN);

        if (!eyes.cyclops_mode) {
            lv_obj_set_size(eyes.right_eyelid_tired, eyes.eye_width_current,
                            eyes.eyelid_tired_height);
            lv_obj_set_pos(eyes.right_eyelid_tired, eyes.right_eye_x + flicker_x,
                           eyes.right_eye_y + flicker_y);
            lv_obj_clear_flag(eyes.right_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        lv_obj_add_flag(eyes.left_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
    }

    /* ---- angry eyelid ---- */
    if (eyes.eyelid_angry_height > 0) {
        lv_obj_set_size(eyes.left_eyelid_angry, eyes.eye_width_current,
                        eyes.eyelid_angry_height);
        lv_obj_set_pos(eyes.left_eyelid_angry, eyes.left_eye_x + flicker_x,
                       eyes.left_eye_y + flicker_y);
        lv_obj_clear_flag(eyes.left_eyelid_angry, LV_OBJ_FLAG_HIDDEN);

        if (!eyes.cyclops_mode) {
            lv_obj_set_size(eyes.right_eyelid_angry, eyes.eye_width_current,
                            eyes.eyelid_angry_height);
            lv_obj_set_pos(eyes.right_eyelid_angry, eyes.right_eye_x + flicker_x,
                           eyes.right_eye_y + flicker_y);
            lv_obj_clear_flag(eyes.right_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        lv_obj_add_flag(eyes.left_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
    }

    /* ---- happy eyelid (bottom cover) ---- */
    if (eyes.eyelid_happy_offset > 0) {
        int happy_y = (eyes.left_eye_y + eyes.eye_height_current)
                      - eyes.eyelid_happy_offset + flicker_y;

        lv_obj_set_size(eyes.left_eyelid_happy, eyes.eye_width_current + 2,
                        eyes.eye_height_default);
        lv_obj_set_pos(eyes.left_eyelid_happy,
                       eyes.left_eye_x - 1 + flicker_x, happy_y + 1);
        lv_obj_set_style_radius(eyes.left_eyelid_happy,
                                eyes.border_radius_current, 0);
        lv_obj_clear_flag(eyes.left_eyelid_happy, LV_OBJ_FLAG_HIDDEN);

        if (!eyes.cyclops_mode) {
            lv_obj_set_size(eyes.right_eyelid_happy, eyes.eye_width_current + 2,
                            eyes.eye_height_default);
            lv_obj_set_pos(eyes.right_eyelid_happy,
                           eyes.right_eye_x - 1 + flicker_x, happy_y + 1);
            lv_obj_set_style_radius(eyes.right_eyelid_happy,
                                    eyes.border_radius_current, 0);
            lv_obj_clear_flag(eyes.right_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        lv_obj_add_flag(eyes.left_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(eyes.right_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
    }

    /* ---- sweat drops ---- */
    if (eyes.sweat) {
        for (int i = 0; i < 3; i++) {
            if (eyes.sweat_y_pos[i] <= eyes.sweat_y_max[i]) {
                eyes.sweat_y_pos[i] += 0.5f;
            } else {
                int x_start = (i == 0) ? 0 : (i == 1) ? 30 : (SCREEN_WIDTH - 30);
                int x_width = (i == 1) ? (SCREEN_WIDTH - 60) : 30;
                eyes.sweat_x_initial[i] = x_start + (esp_random() % x_width);
                eyes.sweat_y_pos[i]     = 2;
                eyes.sweat_y_max[i]     = 10 + (esp_random() % 10);
                eyes.sweat_width[i]     = 1;
                eyes.sweat_height[i]    = 2;
            }

            if (eyes.sweat_y_pos[i] <= eyes.sweat_y_max[i] / 2) {
                eyes.sweat_width[i]  += 0.5f;
                eyes.sweat_height[i] += 0.5f;
            } else {
                eyes.sweat_width[i]  -= 0.1f;
                eyes.sweat_height[i] -= 0.5f;
            }

            eyes.sweat_x_pos[i] = eyes.sweat_x_initial[i]
                                   - (eyes.sweat_width[i] / 2);

            lv_obj_set_size(eyes.sweat_drops[i], (int)eyes.sweat_width[i],
                            (int)eyes.sweat_height[i]);
            lv_obj_set_pos(eyes.sweat_drops[i], (int)eyes.sweat_x_pos[i],
                           (int)eyes.sweat_y_pos[i]);
            lv_obj_clear_flag(eyes.sweat_drops[i], LV_OBJ_FLAG_HIDDEN);
        }
    } else {
        for (int i = 0; i < 3; i++) {
            lv_obj_add_flag(eyes.sweat_drops[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
}

static void update_eyes(lv_timer_t *timer) {
    (void)timer;
    draw_eyes();
}

/* ========================================================================== */
/* Eye-state task – maps system states to eye behaviour                       */
/* ========================================================================== */
/*
 * This task only writes eye state variables.  It never calls lv_* directly.
 *
 *  IDLE     – tired/droopy, centred, very slow blink, no movement
 *  WAKING   – snap open, quick blinks, happy face + laugh bounce
 *  ACTIVE   – alert default look, random gaze, auto-blink, curious
 *  SLEEPING – drift centre, tired mood, eyes close
 */

static void reset_all_effects(void) {
    roboeyes_set_autoblink(false, 0, 0);
    roboeyes_set_idle(false, 0, 0);
    roboeyes_set_curious(false);
    roboeyes_set_sweat(false);
    roboeyes_set_h_flicker(false, 0);
    roboeyes_set_v_flicker(false, 0);
}

static void eye_state_task(void *arg) {
    (void)arg;
    ESP_LOGI(TAG, "Eye-state task started");

    while (1) {
        if (s_eye_state != s_eye_state_prev) {
            roboeyes_state_t entering = s_eye_state;

            ESP_LOGI(TAG, "Eye state -> %d", (int)entering);

            switch (entering) {

            /* -------------------------------------------------------------- */
            /* IDLE – low-power standby look                                  */
            /* -------------------------------------------------------------- */
            case EYE_STATE_IDLE:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_open();
                roboeyes_set_mood(MOOD_TIRED);
                /* Very slow, lazy blink every 6-10 s */
                roboeyes_set_autoblink(true, 6, 4);
                break;

            /* -------------------------------------------------------------- */
            /* WAKING – attention-grabbing entrance                           */
            /* -------------------------------------------------------------- */
            case EYE_STATE_WAKING:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_set_mood(MOOD_DEFAULT);
                roboeyes_open();
                vTaskDelay(pdMS_TO_TICKS(300));

                /* Three quick blinks to catch the user's eye */
                for (int i = 0; i < 3; i++) {
                    roboeyes_blink();
                    vTaskDelay(pdMS_TO_TICKS(300));
                }

                /* Brief "huh?" shake, then happy squint + bounce */
                roboeyes_anim_confused();
                vTaskDelay(pdMS_TO_TICKS(600));
                roboeyes_set_mood(MOOD_HAPPY);
                roboeyes_anim_laugh();
                /* Gentle auto-blink while staying happy */
                roboeyes_set_autoblink(true, 3, 2);
                break;

            /* -------------------------------------------------------------- */
            /* ACTIVE – alert, looking around                                 */
            /* -------------------------------------------------------------- */
            case EYE_STATE_ACTIVE:
                reset_all_effects();
                roboeyes_open();
                roboeyes_set_mood(MOOD_DEFAULT);
                roboeyes_set_curious(true);
                roboeyes_set_autoblink(true, 2, 3);
                roboeyes_set_idle(true, 2, 2);
                break;

            /* -------------------------------------------------------------- */
            /* SLEEPING – winding down, eyes close                            */
            /* -------------------------------------------------------------- */
            case EYE_STATE_SLEEPING:
                reset_all_effects();
                roboeyes_set_position(POS_CENTER);
                roboeyes_set_mood(MOOD_TIRED);
                /* Let the tired eyelids settle for a moment */
                vTaskDelay(pdMS_TO_TICKS(1500));
                /* Then close eyes fully */
                roboeyes_close();
                break;
            }

            s_eye_state_prev = entering;
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

/* ========================================================================== */
/* Public API                                                                 */
/* ========================================================================== */

void roboeyes_set_state(roboeyes_state_t state) {
    s_eye_state = state;
}

void roboeyes_init(lv_display_t *disp) {
    (void)disp;

    ESP_LOGI(TAG, "Initialising RoboEyes (320x240)");

    /* ---- default geometry ---- */
    eyes.eye_width_default  = 60;
    eyes.eye_height_default = 60;
    eyes.eye_width_current  = 60;
    eyes.eye_height_current = 1;   /* boot closed, open() restores */
    eyes.eye_width_next     = 60;
    eyes.eye_height_next    = 60;

    eyes.border_radius_default = 15;
    eyes.border_radius_current = 15;
    eyes.border_radius_next    = 15;

    eyes.space_between_default = 20;
    eyes.space_between_current = 20;
    eyes.space_between_next    = 20;

    eyes.left_eye_x_default = (SCREEN_WIDTH
        - (eyes.eye_width_default * 2 + eyes.space_between_default)) / 2;
    eyes.left_eye_y_default = (SCREEN_HEIGHT - eyes.eye_height_default) / 2;

    eyes.left_eye_x      = eyes.left_eye_x_default;
    eyes.left_eye_y      = eyes.left_eye_y_default;
    eyes.left_eye_x_next = eyes.left_eye_x_default;
    eyes.left_eye_y_next = eyes.left_eye_y_default;

    eyes.right_eye_x      = eyes.left_eye_x + eyes.eye_width_default
                             + eyes.space_between_default;
    eyes.right_eye_y      = eyes.left_eye_y;
    eyes.right_eye_x_next = eyes.right_eye_x;
    eyes.right_eye_y_next = eyes.right_eye_y;

    eyes.left_eye_open          = false;
    eyes.right_eye_open         = false;
    eyes.left_eye_height_offset = 0;
    eyes.right_eye_height_offset = 0;

    eyes.current_mood = MOOD_DEFAULT;
    eyes.curious_mode = false;
    eyes.cyclops_mode = false;

    eyes.eyelid_tired_height      = 0;
    eyes.eyelid_tired_height_next = 0;
    eyes.eyelid_angry_height      = 0;
    eyes.eyelid_angry_height_next = 0;
    eyes.eyelid_happy_offset      = 0;
    eyes.eyelid_happy_offset_next = 0;

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

    eyes.confused        = false;
    eyes.confused_toggle = true;
    eyes.laugh           = false;
    eyes.laugh_toggle    = true;

    eyes.h_flicker           = false;
    eyes.h_flicker_alternate = false;
    eyes.h_flicker_amplitude = 2;

    eyes.v_flicker           = false;
    eyes.v_flicker_alternate = false;
    eyes.v_flicker_amplitude = 10;

    eyes.sweat = false;
    for (int i = 0; i < 3; i++) {
        eyes.sweat_y_pos[i]     = 2;
        eyes.sweat_y_max[i]     = 20;
        eyes.sweat_x_pos[i]     = 2;
        eyes.sweat_width[i]     = 1;
        eyes.sweat_height[i]    = 2;
        eyes.sweat_x_initial[i] = 2;
    }

    create_eyes();

    /* 20 ms LVGL timer drives the rendering */
    eyes.update_timer = lv_timer_create(update_eyes, 20, NULL);

    roboeyes_open();

    /* Start the eye-state task (never calls lv_*) */
    xTaskCreate(eye_state_task, "eye_state", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "RoboEyes initialised – state-driven mode");
}
