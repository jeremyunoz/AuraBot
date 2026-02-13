/**
 * @file action.h
 * @brief High-level robot actions for AuraBot.
 *
 * Each action is built on top of the servo driver and
 * expressed as a sequence of leg movements.
 */

#ifndef ACTION_H
#define ACTION_H

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

/* ---- Utility ------------------------------------------------------------- */

/** Blocking delay in milliseconds (FreeRTOS-based). */
void delay_ms(int ms);

/* ---- Actions ------------------------------------------------------------- */

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
