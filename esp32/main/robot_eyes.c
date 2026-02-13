/* Roboteye.c
 * ============================================================================
 * RoboEyes - 自动循环演示版本
 * ============================================================================
 *
 * 关键修复：
 * 1) 删除 Hi label，避免 auto_demo_task 跨线程调用 LVGL API 导致 reboot
 * 2) blink 用“状态机”实现：不在 LVGL timer 回调里 vTaskDelay（否则很容易崩）
 * 3) auto_demo_task 只改 eyes 的状态变量，不直接操作 LVGL 对象
 */

 #include "lvgl.h"
 #include "esp_log.h"
 #include "esp_timer.h"
 #include "esp_random.h"
 #include "freertos/FreeRTOS.h"
 #include "freertos/task.h"
 
 #include <math.h>
 #include <stdlib.h>
 #include <string.h>
 #include <stdbool.h>
 
 static const char *TAG = "roboeyes";
 
 #define SCREEN_WIDTH   320
 #define SCREEN_HEIGHT  240
 
 #define EYE_COLOR      lv_color_hex(0x00FFFF)
 #define BG_COLOR       lv_color_hex(0x1a1a1a)
 
 // blink 可见时长（毫秒）
 #define BLINK_HOLD_MS  120
 
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
 
     // blink 状态机（关键：不在 LVGL timer 里 delay）
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
 
 // forward declarations
 static void create_eyes(void);
 static void update_eyes(lv_timer_t *timer);
 static void draw_eyes(void);
 static int get_screen_constraint_x(void);
 static int get_screen_constraint_y(void);
 
 static inline uint32_t now_ms(void) {
     return (uint32_t)(esp_timer_get_time() / 1000ULL);
 }
 
 // ============================================================================
 // 约束
 // ============================================================================
 static int get_screen_constraint_x(void) {
     return SCREEN_WIDTH - eyes.eye_width_current - eyes.space_between_current - eyes.eye_width_current;
 }
 
 static int get_screen_constraint_y(void) {
     return SCREEN_HEIGHT - eyes.eye_height_default;
 }
 
 // ============================================================================
 // 创建眼睛 UI
 // ============================================================================
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
 
 // ============================================================================
 // 公共 API（只改状态，不直接阻塞 LVGL）
 // ============================================================================
 void roboeyes_set_mood(mood_type_t mood) { eyes.current_mood = mood; }
 
 void roboeyes_set_position(position_t pos) {
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
 
 void roboeyes_set_position_xy(int x, int y) { eyes.left_eye_x_next = x; eyes.left_eye_y_next = y; }
 void roboeyes_set_curious(bool enabled) { eyes.curious_mode = enabled; }
 void roboeyes_set_cyclops(bool enabled) { eyes.cyclops_mode = enabled; }
 
 void roboeyes_close(void) {
     eyes.eye_height_next = 1;
     eyes.left_eye_open = false;
     eyes.right_eye_open = false;
 }
 
 void roboeyes_open(void) {
     eyes.left_eye_open = true;
     eyes.right_eye_open = true;
 }
 
 // ✅ blink：只启动一个“闭眼一小段时间”的状态机
 void roboeyes_blink(void) {
     roboeyes_close();
     eyes.blink_in_progress = true;
     eyes.blink_end_ms = now_ms() + BLINK_HOLD_MS;
 }
 
 void roboeyes_set_autoblink(bool enabled, int interval_sec, int variation_sec) {
     eyes.autoblinker = enabled;
     eyes.blink_interval_min = interval_sec;
     eyes.blink_interval_variation = variation_sec;
     if (enabled) {
         eyes.blink_timer = now_ms() + (uint32_t)(interval_sec * 1000);
     }
 }
 
 void roboeyes_set_idle(bool enabled, int interval_sec, int variation_sec) {
     eyes.idle_mode = enabled;
     eyes.idle_interval_min = interval_sec;
     eyes.idle_interval_variation = variation_sec;
     if (enabled) {
         eyes.idle_timer = now_ms() + (uint32_t)(interval_sec * 1000);
     }
 }
 
 void roboeyes_set_h_flicker(bool enabled, int amplitude) { eyes.h_flicker = enabled; eyes.h_flicker_amplitude = amplitude; }
 void roboeyes_set_v_flicker(bool enabled, int amplitude) { eyes.v_flicker = enabled; eyes.v_flicker_amplitude = amplitude; }
 void roboeyes_set_sweat(bool enabled) { eyes.sweat = enabled; }
 void roboeyes_anim_confused(void) { eyes.confused = true; eyes.confused_toggle = true; }
 void roboeyes_anim_laugh(void) { eyes.laugh = true; eyes.laugh_toggle = true; }
 
 // ============================================================================
 // draw_eyes（核心渲染）
 // ============================================================================
 static void draw_eyes(void) {
     // 处理 blink 状态机：到时间就打开
     if (eyes.blink_in_progress && now_ms() >= eyes.blink_end_ms) {
         roboeyes_open();
         eyes.blink_in_progress = false;
     }
 
     // curious 高度偏移
     if (eyes.curious_mode) {
         if (eyes.left_eye_x_next <= 10) {
             eyes.left_eye_height_offset = 8;
         } else if (eyes.left_eye_x_next >= (get_screen_constraint_x() - 10) && eyes.cyclops_mode) {
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
         eyes.left_eye_height_offset = 0;
         eyes.right_eye_height_offset = 0;
     }
 
     int prev_eye_height = eyes.eye_height_current;
 
     // 平滑插值
     eyes.eye_width_current     = (eyes.eye_width_current + eyes.eye_width_next) / 2;
     eyes.eye_height_current    = (eyes.eye_height_current + eyes.eye_height_next) / 2;
     eyes.space_between_current = (eyes.space_between_current + eyes.space_between_next) / 2;
     eyes.border_radius_current = (eyes.border_radius_current + eyes.border_radius_next) / 2;
 
     eyes.left_eye_x = (eyes.left_eye_x + eyes.left_eye_x_next) / 2;
     eyes.left_eye_y = (eyes.left_eye_y + eyes.left_eye_y_next) / 2;
 
     int height_diff = prev_eye_height - eyes.eye_height_current;
     if (height_diff > 0 && eyes.eye_height_current < eyes.eye_height_default) {
         eyes.left_eye_y += height_diff;
     }
 
     eyes.right_eye_x_next = eyes.left_eye_x_next + eyes.eye_width_current + eyes.space_between_current;
     eyes.right_eye_y_next = eyes.left_eye_y_next;
     eyes.right_eye_x = (eyes.right_eye_x + eyes.right_eye_x_next) / 2;
     eyes.right_eye_y = eyes.left_eye_y;
 
     int left_height  = eyes.eye_height_current + eyes.left_eye_height_offset;
     int right_height = eyes.eye_height_current + eyes.right_eye_height_offset;
 
     if (eyes.left_eye_open && left_height <= 1)  eyes.eye_height_next = eyes.eye_height_default;
     if (eyes.right_eye_open && right_height <= 1) eyes.eye_height_next = eyes.eye_height_default;
 
     // autoblink（只触发 blink 状态机，不 delay）
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
 
     // idle 随机移动
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
 
     // confused / laugh（保持你原来的逻辑）
     if (eyes.confused) {
         uint32_t now = now_ms();
         if (eyes.confused_toggle) {
             roboeyes_set_h_flicker(true, 20);
             eyes.confused_timer = now;
             eyes.confused_toggle = false;
         } else if (now >= eyes.confused_timer + 500) {
             roboeyes_set_h_flicker(false, 0);
             eyes.confused_toggle = true;
             eyes.confused = false;
         }
     }
 
     if (eyes.laugh) {
         uint32_t now = now_ms();
         if (eyes.laugh_toggle) {
             roboeyes_set_v_flicker(true, 5);
             eyes.laugh_timer = now;
             eyes.laugh_toggle = false;
         } else if (now >= eyes.laugh_timer + 500) {
             roboeyes_set_v_flicker(false, 0);
             eyes.laugh_toggle = true;
             eyes.laugh = false;
         }
     }
 
     int flicker_x = 0, flicker_y = 0;
     if (eyes.h_flicker) {
         flicker_x = eyes.h_flicker_alternate ? eyes.h_flicker_amplitude : -eyes.h_flicker_amplitude;
         eyes.h_flicker_alternate = !eyes.h_flicker_alternate;
     }
     if (eyes.v_flicker) {
         flicker_y = eyes.v_flicker_alternate ? eyes.v_flicker_amplitude : -eyes.v_flicker_amplitude;
         eyes.v_flicker_alternate = !eyes.v_flicker_alternate;
     }
 
     int right_eye_width  = eyes.cyclops_mode ? 0 : eyes.eye_width_current;
     int right_eye_height = eyes.cyclops_mode ? 0 : (eyes.eye_height_current + eyes.right_eye_height_offset);
 
     // 眼睛主体
     lv_obj_set_size(eyes.left_eye, eyes.eye_width_current, eyes.eye_height_current + eyes.left_eye_height_offset);
     lv_obj_set_pos(eyes.left_eye, eyes.left_eye_x + flicker_x, eyes.left_eye_y + flicker_y);
     lv_obj_set_style_radius(eyes.left_eye, eyes.border_radius_current, 0);
 
     if (!eyes.cyclops_mode) {
         lv_obj_set_size(eyes.right_eye, right_eye_width, right_eye_height);
         lv_obj_set_pos(eyes.right_eye, eyes.right_eye_x + flicker_x, eyes.right_eye_y + flicker_y);
         lv_obj_set_style_radius(eyes.right_eye, eyes.border_radius_current, 0);
         lv_obj_clear_flag(eyes.right_eye, LV_OBJ_FLAG_HIDDEN);
     } else {
         lv_obj_add_flag(eyes.right_eye, LV_OBJ_FLAG_HIDDEN);
     }
 
     // mood -> eyelids
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
 
     // tired eyelid
     if (eyes.eyelid_tired_height > 0) {
         lv_obj_set_size(eyes.left_eyelid_tired, eyes.eye_width_current, eyes.eyelid_tired_height);
         lv_obj_set_pos(eyes.left_eyelid_tired, eyes.left_eye_x + flicker_x, eyes.left_eye_y + flicker_y);
         lv_obj_clear_flag(eyes.left_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
 
         if (!eyes.cyclops_mode) {
             lv_obj_set_size(eyes.right_eyelid_tired, eyes.eye_width_current, eyes.eyelid_tired_height);
             lv_obj_set_pos(eyes.right_eyelid_tired, eyes.right_eye_x + flicker_x, eyes.right_eye_y + flicker_y);
             lv_obj_clear_flag(eyes.right_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
         }
     } else {
         lv_obj_add_flag(eyes.left_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
         lv_obj_add_flag(eyes.right_eyelid_tired, LV_OBJ_FLAG_HIDDEN);
     }
 
     // angry eyelid
     if (eyes.eyelid_angry_height > 0) {
         lv_obj_set_size(eyes.left_eyelid_angry, eyes.eye_width_current, eyes.eyelid_angry_height);
         lv_obj_set_pos(eyes.left_eyelid_angry, eyes.left_eye_x + flicker_x, eyes.left_eye_y + flicker_y);
         lv_obj_clear_flag(eyes.left_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
 
         if (!eyes.cyclops_mode) {
             lv_obj_set_size(eyes.right_eyelid_angry, eyes.eye_width_current, eyes.eyelid_angry_height);
             lv_obj_set_pos(eyes.right_eyelid_angry, eyes.right_eye_x + flicker_x, eyes.right_eye_y + flicker_y);
             lv_obj_clear_flag(eyes.right_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
         }
     } else {
         lv_obj_add_flag(eyes.left_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
         lv_obj_add_flag(eyes.right_eyelid_angry, LV_OBJ_FLAG_HIDDEN);
     }
 
     // happy eyelid (bottom cover)
     if (eyes.eyelid_happy_offset > 0) {
         int happy_y = (eyes.left_eye_y + eyes.eye_height_current) - eyes.eyelid_happy_offset + flicker_y;
 
         lv_obj_set_size(eyes.left_eyelid_happy, eyes.eye_width_current + 2, eyes.eye_height_default);
         lv_obj_set_pos(eyes.left_eyelid_happy, eyes.left_eye_x - 1 + flicker_x, happy_y + 1);
         lv_obj_set_style_radius(eyes.left_eyelid_happy, eyes.border_radius_current, 0);
         lv_obj_clear_flag(eyes.left_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
 
         if (!eyes.cyclops_mode) {
             lv_obj_set_size(eyes.right_eyelid_happy, eyes.eye_width_current + 2, eyes.eye_height_default);
             lv_obj_set_pos(eyes.right_eyelid_happy, eyes.right_eye_x - 1 + flicker_x, happy_y + 1);
             lv_obj_set_style_radius(eyes.right_eyelid_happy, eyes.border_radius_current, 0);
             lv_obj_clear_flag(eyes.right_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
         }
     } else {
         lv_obj_add_flag(eyes.left_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
         lv_obj_add_flag(eyes.right_eyelid_happy, LV_OBJ_FLAG_HIDDEN);
     }
 
     // sweat
     if (eyes.sweat) {
         for (int i = 0; i < 3; i++) {
             if (eyes.sweat_y_pos[i] <= eyes.sweat_y_max[i]) {
                 eyes.sweat_y_pos[i] += 0.5f;
             } else {
                 int x_range_start = (i == 0) ? 0 : (i == 1) ? 30 : (SCREEN_WIDTH - 30);
                 int x_range_width = (i == 1) ? (SCREEN_WIDTH - 60) : 30;
                 eyes.sweat_x_initial[i] = x_range_start + (esp_random() % x_range_width);
                 eyes.sweat_y_pos[i] = 2;
                 eyes.sweat_y_max[i] = 10 + (esp_random() % 10);
                 eyes.sweat_width[i] = 1;
                 eyes.sweat_height[i] = 2;
             }
 
             if (eyes.sweat_y_pos[i] <= eyes.sweat_y_max[i] / 2) {
                 eyes.sweat_width[i] += 0.5f;
                 eyes.sweat_height[i] += 0.5f;
             } else {
                 eyes.sweat_width[i] -= 0.1f;
                 eyes.sweat_height[i] -= 0.5f;
             }
 
             eyes.sweat_x_pos[i] = eyes.sweat_x_initial[i] - (eyes.sweat_width[i] / 2);
 
             lv_obj_set_size(eyes.sweat_drops[i], (int)eyes.sweat_width[i], (int)eyes.sweat_height[i]);
             lv_obj_set_pos(eyes.sweat_drops[i], (int)eyes.sweat_x_pos[i], (int)eyes.sweat_y_pos[i]);
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
 
 // ============================================================================
 // 🎬 自动演示任务 - 循环播放（不调用 lv_*）
 // ============================================================================
 static void auto_demo_task(void *arg) {
     (void)arg;
 
     ESP_LOGI(TAG, "");
     ESP_LOGI(TAG, "========================================");
     ESP_LOGI(TAG, "🎬 自动演示模式启动（无Hi稳定版）");
     ESP_LOGI(TAG, "========================================");
     ESP_LOGI(TAG, "");
 
     vTaskDelay(pdMS_TO_TICKS(2000));
 
     while (1) {
         // 表情1：开机
         ESP_LOGI(TAG, "📍 表情1：开机状态（中心静止）");
         roboeyes_set_autoblink(false, 0, 0);
         roboeyes_set_idle(false, 0, 0);
         roboeyes_set_position(POS_CENTER);
         roboeyes_set_mood(MOOD_DEFAULT);
         vTaskDelay(pdMS_TO_TICKS(4000));
 
         // 表情2：打招呼（仅开心，不显示文字）
         ESP_LOGI(TAG, "📍 表情2：打招呼（开心眯眯眼）");
         roboeyes_set_autoblink(false, 0, 0);
         roboeyes_set_idle(false, 0, 0);
         roboeyes_set_position(POS_CENTER);
         roboeyes_set_mood(MOOD_HAPPY);
         vTaskDelay(pdMS_TO_TICKS(3000));
 
         // 表情3：待机（偶尔眨眼）
         ESP_LOGI(TAG, "📍 表情3：待机（3-6秒眨眼）");
         roboeyes_set_mood(MOOD_DEFAULT);
         roboeyes_set_position(POS_CENTER);
         roboeyes_set_autoblink(true, 3, 3);
         roboeyes_set_idle(false, 0, 0);
         vTaskDelay(pdMS_TO_TICKS(8000));
 
         // 表情4：监督（随机移动 + 偶尔眨眼）
         ESP_LOGI(TAG, "📍 表情4：监督（随机移动）");
         roboeyes_set_mood(MOOD_DEFAULT);
         roboeyes_set_autoblink(true, 2, 3);
         roboeyes_set_idle(true, 2, 2);
         vTaskDelay(pdMS_TO_TICKS(10000));
 
         // 表情5：提醒（停止移动，手动眨眼3次）
         ESP_LOGI(TAG, "📍 表情5：提醒（眨眼3次）");
         roboeyes_set_idle(false, 0, 0);
         roboeyes_set_autoblink(false, 0, 0);
         roboeyes_set_position(POS_CENTER);
         roboeyes_set_mood(MOOD_DEFAULT);
 
         for (int i = 0; i < 3; i++) {
             roboeyes_blink(); // blink 状态机
             vTaskDelay(pdMS_TO_TICKS(350));
         }
         vTaskDelay(pdMS_TO_TICKS(2000));
 
         ESP_LOGI(TAG, "✅ 一轮演示完成！5秒后重新开始...");
         vTaskDelay(pdMS_TO_TICKS(5000));
     }
 }
 
 // ============================================================================
 // 主入口（被你的 LVGL demo main 调用）
 // ============================================================================
 void example_lvgl_demo_ui(lv_display_t *disp) {
     (void)disp;
 
     ESP_LOGI(TAG, "Initializing RoboEyes for ESP32-P4 (320x240)");
 
     // 初始化参数
     eyes.eye_width_default = 60;
     eyes.eye_height_default = 60;
     eyes.eye_width_current = 60;
     eyes.eye_height_current = 1;   // 上电先闭眼一点点，后面 open 会恢复
     eyes.eye_width_next = 60;
     eyes.eye_height_next = 60;
 
     eyes.border_radius_default = 15;
     eyes.border_radius_current = 15;
     eyes.border_radius_next = 15;
 
     eyes.space_between_default = 20;
     eyes.space_between_current = 20;
     eyes.space_between_next = 20;
 
     eyes.left_eye_x_default = (SCREEN_WIDTH - (eyes.eye_width_default * 2 + eyes.space_between_default)) / 2;
     eyes.left_eye_y_default = (SCREEN_HEIGHT - eyes.eye_height_default) / 2;
 
     eyes.left_eye_x = eyes.left_eye_x_default;
     eyes.left_eye_y = eyes.left_eye_y_default;
     eyes.left_eye_x_next = eyes.left_eye_x_default;
     eyes.left_eye_y_next = eyes.left_eye_y_default;
 
     eyes.right_eye_x = eyes.left_eye_x + eyes.eye_width_default + eyes.space_between_default;
     eyes.right_eye_y = eyes.left_eye_y;
     eyes.right_eye_x_next = eyes.right_eye_x;
     eyes.right_eye_y_next = eyes.right_eye_y;
 
     eyes.left_eye_open = false;
     eyes.right_eye_open = false;
     eyes.left_eye_height_offset = 0;
     eyes.right_eye_height_offset = 0;
 
     eyes.current_mood = MOOD_DEFAULT;
     eyes.curious_mode = false;
     eyes.cyclops_mode = false;
 
     eyes.eyelid_tired_height = 0;
     eyes.eyelid_tired_height_next = 0;
     eyes.eyelid_angry_height = 0;
     eyes.eyelid_angry_height_next = 0;
     eyes.eyelid_happy_offset = 0;
     eyes.eyelid_happy_offset_next = 0;
 
     eyes.autoblinker = false;
     eyes.blink_interval_min = 1;
     eyes.blink_interval_variation = 4;
     eyes.blink_timer = 0;
 
     eyes.blink_in_progress = false;
     eyes.blink_end_ms = 0;
 
     eyes.idle_mode = false;
     eyes.idle_interval_min = 1;
     eyes.idle_interval_variation = 3;
     eyes.idle_timer = 0;
 
     eyes.confused = false;
     eyes.confused_toggle = true;
     eyes.laugh = false;
     eyes.laugh_toggle = true;
 
     eyes.h_flicker = false;
     eyes.h_flicker_alternate = false;
     eyes.h_flicker_amplitude = 2;
 
     eyes.v_flicker = false;
     eyes.v_flicker_alternate = false;
     eyes.v_flicker_amplitude = 10;
 
     eyes.sweat = false;
     for (int i = 0; i < 3; i++) {
         eyes.sweat_y_pos[i] = 2;
         eyes.sweat_y_max[i] = 20;
         eyes.sweat_x_pos[i] = 2;
         eyes.sweat_width[i] = 1;
         eyes.sweat_height[i] = 2;
         eyes.sweat_x_initial[i] = 2;
     }
 
     create_eyes();
 
     // LVGL timer 更新（20ms）
     eyes.update_timer = lv_timer_create(update_eyes, 20, NULL);
 
     // 先打开眼睛
     roboeyes_open();
 
     // 启动演示任务（注意：此任务不调用 lv_*）
         xTaskCreate(auto_demo_task, "auto_demo", 4096, NULL, 5, NULL);
 
     ESP_LOGI(TAG, "RoboEyes initialized - Auto demo mode started!");
 }
 