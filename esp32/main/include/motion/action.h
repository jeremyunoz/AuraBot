/**
 * @file action.h
 * @brief High-level robot actions for AuraBot.
 *
 * Each action is built on top of the servo driver and
 * expressed as a sequence of leg movements.
 *
 * The action subsystem owns a FreeRTOS task + queue.
 * Other modules post commands with action_post() / action_post_user();
 * the task serialises execution and handles cancellation of continuous
 * actions (walk, turn, swing) when a new command arrives.
 */

#ifndef ACTION_H
#define ACTION_H

#include <stdbool.h>

/**
 * @brief Action command identifiers.
 *
 * Used by the action task to select which movement to perform.
 */
typedef enum {
    ACTION_STAND      = 0,
    ACTION_WALK       = 1,
    ACTION_BACK       = 2,
    ACTION_LAY_DOWN   = 3,
    ACTION_TURN_LEFT  = 4,
    ACTION_TURN_RIGHT = 5,
    ACTION_SIT        = 6,
    ACTION_WAVE       = 7,
    ACTION_SWING      = 666,  /**< Easter-egg dance */
} action_id_t;

/* ---- Task lifecycle ------------------------------------------------------ */

/**
 * @brief Initialise the action subsystem.
 *
 * Creates the internal command queue, calls servo_init(), and starts
 * the action FreeRTOS task.  Call once from app_main().
 */
void action_task_start(void);

/**
 * @brief Post an action command (non-blocking, safe from any task / ISR).
 *
 * If a continuous action is running it will be cancelled so the new
 * command can begin.  The most-recent command always wins.
 */
void action_post(action_id_t id);

/**
 * @brief Post a user-initiated action (e.g. from MQTT "move" command).
 *
 * Identical to action_post() but silently ignored when user control
 * is disabled (i.e. the system is not in the ACTIVE state).
 */
void action_post_user(action_id_t id);

/**
 * @brief Enable / disable user-initiated movement.
 *
 * Called by the state machine: enabled when ACTIVE, disabled otherwise.
 */
void action_set_user_control(bool enabled);

/**
 * @brief Return whether user movement commands are currently allowed.
 */
bool action_user_control_enabled(void);

/**
 * @brief Return the latest posted action command.
 */
action_id_t action_get_current_command(void);

/**
 * @brief Parse an action name string to its enum value.
 *
 * Recognised strings: "stand", "walk", "back", "lay_down",
 * "turn_left", "turn_right", "sit", "wave", "swing".
 *
 * @return The matching action_id_t, or ACTION_STAND on unknown input.
 */
action_id_t action_from_string(const char *name);

/* ---- Utility ------------------------------------------------------------- */

/** Blocking delay in milliseconds (FreeRTOS-based). */
void delay_ms(int ms);

/* ---- Low-level actions (used internally by the task) --------------------- */

void stand(void);
void sit(void);
void wave(void);
void lay_down(void);
void walk(void);
void walk_back(void);
void turn_left(void);
void turn_right(void);
void swing(void);

#endif /* ACTION_H */
