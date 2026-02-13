/**
 * @file action.c
 * @brief High-level robot action implementations for AuraBot.
 *
 * Servo angle convention (per leg):
 *   0   = fully forward
 *   90  = neutral / standing
 *   180 = fully backward
 */

#include "action.h"
#include "servo.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* Global state shared with the action task in main.c */
extern volatile int state;
extern int last_state;

/* -------------------------------------------------------------------------- */
/*  Utility                                                                    */
/* -------------------------------------------------------------------------- */

void delay_ms(int ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

/** Return true while the global state still matches @p expected. */
static inline bool action_active(int expected)
{
    return state == expected;
}

/* -------------------------------------------------------------------------- */
/*  Static poses                                                               */
/* -------------------------------------------------------------------------- */

void stand(void)
{
    /* Coming from sit or wave: lift front legs first to avoid scraping */
    if (last_state == ACTION_SIT || last_state == ACTION_WAVE) {
        FL_angle(45);
        FR_angle(45);
        RL_angle(45);
        RR_angle(45);
        delay_ms(500);
    }

    FL_angle(90);
    FR_angle(90);
    RL_angle(90);
    RR_angle(90);
}

void sit(void)
{
    FL_angle(75);
    FR_angle(75);
    RL_angle(30);
    RR_angle(30);
}

void lay_down(void)
{
    FL_angle(0);
    FR_angle(0);
    RL_angle(180);
    RR_angle(180);
}

/* -------------------------------------------------------------------------- */
/*  One-shot actions                                                           */
/* -------------------------------------------------------------------------- */

void wave(void)
{
    /* Sit first, then wave the front-right leg */
    sit();
    delay_ms(500);

    for (int i = 0; i < 3; i++) {
        FR_angle(0);
        delay_ms(350);
        FR_angle(60);
        delay_ms(350);
    }

    state = ACTION_STAND;  /* auto-return to standing */
}

/* -------------------------------------------------------------------------- */
/*  Continuous / looping actions                                               */
/* -------------------------------------------------------------------------- */

void walk(void)
{
    const int saved = state;

    while (action_active(saved)) {
        /* Phase 1: FL + RR step forward */
        FL_angle(45);
        RR_angle(45);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(135);
        RL_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        /* Phase 2: FR + RL step forward */
        FR_angle(45);
        RL_angle(45);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(135);
        RR_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
    }

    stand();
}

void walk_back(void)
{
    const int saved = state;

    while (action_active(saved)) {
        /* Phase 1: FL + RR step backward */
        FL_angle(135);
        RR_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(45);
        RL_angle(45);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        /* Phase 2: FR + RL step backward */
        FR_angle(135);
        RL_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(45);
        RR_angle(45);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
    }

    stand();
}

void turn_left(void)
{
    const int saved = state;

    while (action_active(saved)) {
        FR_angle(45);
        RL_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(45);
        RR_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
    }

    stand();
}

void turn_right(void)
{
    const int saved = state;

    while (action_active(saved)) {
        FL_angle(45);
        RR_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(45);
        RL_angle(135);
        delay_ms(250);
        if (!action_active(saved)) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
        if (!action_active(saved)) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
    }

    stand();
}

void swing(void)
{
    const int saved = state;

    while (action_active(saved)) {
        FL_angle(135);
        FR_angle(135);
        RL_angle(135);
        RR_angle(135);
        delay_ms(500);
        if (!action_active(saved)) break;

        FL_angle(45);
        FR_angle(45);
        RL_angle(45);
        RR_angle(45);
        delay_ms(500);
    }

    stand();
}
