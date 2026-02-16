/**
 * @file robot_eyes.h
 * @brief RoboEyes animated display for AuraBot.
 *
 * Provides an expressive pair of eyes on an LVGL-driven LCD.
 * The eye behaviour is controlled by a high-level "eye state" that
 * maps to moods, blink patterns, and gaze movement so the rest of
 * the system only needs to say *what* the robot is doing, not *how*
 * the eyes should look.
 *
 * Thread safety:
 *   roboeyes_init() must be called while holding the LVGL lock.
 *   roboeyes_set_state() is safe to call from any thread (it only
 *   writes a volatile variable; the eye-state task picks it up).
 */

#ifndef ROBOT_EYES_H
#define ROBOT_EYES_H

#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief High-level eye states that drive the display expression.
 *
 * Each state configures mood, blink rate, gaze movement, etc.
 */
typedef enum {
    /** Standby – tired/droopy, centred, very slow blink, no movement. */
    EYE_STATE_IDLE = 0,

    /** Boot-up – snappy blinks + happy face to signal "I'm alive!". */
    EYE_STATE_WAKING,

    /** Running – alert default gaze, random look-around, auto-blink. */
    EYE_STATE_ACTIVE,

    /** Shutting down – drift to centre, close eyes, go dark. */
    EYE_STATE_SLEEPING,
} roboeyes_state_t;

/**
 * @brief Initialise the RoboEyes UI and start the background task.
 *
 * Must be called while the LVGL API lock is held (see lcd_lvgl.h).
 * After this call the eye-state task runs continuously; control it
 * with roboeyes_set_state().
 *
 * @param disp  LVGL display returned by lcd_lvgl_init().
 */
void roboeyes_init(lv_display_t *disp);

/**
 * @brief Change the eye expression to match a system state.
 *
 * Safe to call from any thread at any time.  The eye-state task
 * will detect the change and play the appropriate transition
 * animation before settling into steady-state behaviour.
 *
 * @param state  Desired eye state.
 */
void roboeyes_set_state(roboeyes_state_t state);

#ifdef __cplusplus
}
#endif

#endif /* ROBOT_EYES_H */
