/**
 * @file action.c
 * @brief High-level robot action implementations for AuraBot.
 *
 * Servo angle convention (per leg):
 *   0   = fully forward
 *   90  = neutral / standing
 *   180 = fully backward
 *
 * The module owns a FreeRTOS task that blocks on a length-1 queue.
 * Any task can call action_post() to request a new action; continuous
 * actions (walk, turn, swing) are cancelled automatically when the
 * next command arrives.
 */

#include "motion/action.h"
#include "motion/servo.h"

#include <string.h>

#include "esp_log.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

static const char *TAG = "action";

/* -------------------------------------------------------------------------- */
/*  Internal state                                                             */
/* -------------------------------------------------------------------------- */

/** Length-1 queue so xQueueOverwrite always succeeds. */
static QueueHandle_t  s_action_queue        = NULL;

/** Set true by action_post() to break out of continuous loops. */
static volatile bool  s_cancel              = false;

/** Tracks the last completed action (for transition logic in stand()). */
static action_id_t    s_last_action         = ACTION_STAND;

/** When false, action_post_user() silently drops commands. */
static bool           s_user_control        = false;
/** Tracks the most recent action posted to the queue. */
static volatile action_id_t s_current_cmd   = ACTION_STAND;

#define ACTION_TASK_STACK  4096
#define ACTION_TASK_PRIO   5

/* -------------------------------------------------------------------------- */
/*  Utility                                                                    */
/* -------------------------------------------------------------------------- */

void delay_ms(int ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

/**
 * Return true while no cancellation has been requested.
 * Called inside continuous action loops (walk, turn, swing).
 */
static inline bool action_should_continue(void)
{
    return !s_cancel;
}

/* -------------------------------------------------------------------------- */
/*  Static poses                                                               */
/* -------------------------------------------------------------------------- */

void stand(void)
{
    /* Coming from sit or wave: lift front legs first to avoid scraping */
    if (s_last_action == ACTION_SIT || s_last_action == ACTION_WAVE) {
        FL_angle(0);
        FR_angle(0);
        RL_angle(0);
        RR_angle(0);
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

    for (int i = 0; i < 3 && action_should_continue(); i++) {
        FR_angle(0);
        delay_ms(350);
        if (!action_should_continue()) break;
        FR_angle(60);
        delay_ms(350);
    }

    /* Match teammate flow: force return to standing after wave. */
    action_post(ACTION_STAND);
}

/* -------------------------------------------------------------------------- */
/*  Continuous / looping actions                                               */
/* -------------------------------------------------------------------------- */

void walk(void)
{
    while (action_should_continue()) {
        /* Phase 1: FL + RR step forward */
        FL_angle(45);
        RR_angle(45);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(135);
        RL_angle(135);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        /* Phase 2: FR + RL step forward */
        FR_angle(45);
        RL_angle(45);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(135);
        RR_angle(135);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
    }

    stand();
}

void walk_back(void)
{
    while (action_should_continue()) {
        /* Phase 1: FL + RR step backward */
        FL_angle(125);
        RR_angle(125);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(55);
        RL_angle(55);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        /* Phase 2: FR + RL step backward */
        FR_angle(125);
        RL_angle(125);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(55);
        RR_angle(55);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
    }

    stand();
}

void turn_left(void)
{
    while (action_should_continue()) {
        FR_angle(45);
        RL_angle(135);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(45);
        RR_angle(135);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
    }

    stand();
}

void turn_right(void)
{
    while (action_should_continue()) {
        FL_angle(45);
        RR_angle(135);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(45);
        RL_angle(135);
        delay_ms(250);
        if (!action_should_continue()) break;

        FL_angle(90);
        RR_angle(90);
        delay_ms(250);
        if (!action_should_continue()) break;

        FR_angle(90);
        RL_angle(90);
        delay_ms(250);
    }

    stand();
}

void swing(void)
{
    while (action_should_continue()) {
        FL_angle(135);
        FR_angle(135);
        RL_angle(135);
        RR_angle(135);
        delay_ms(500);
        if (!action_should_continue()) break;

        FL_angle(45);
        FR_angle(45);
        RL_angle(45);
        RR_angle(45);
        delay_ms(500);
    }

    stand();
}

/* -------------------------------------------------------------------------- */
/*  Action task                                                                */
/* -------------------------------------------------------------------------- */

/**
 * @brief Reset to standing before a locomotion action.
 */
static void ensure_standing(void)
{
    stand();
    delay_ms(250);
}

static void action_task(void *arg)
{
    (void)arg;
    action_id_t cmd;

    while (1) {
        if (xQueueReceive(s_action_queue, &cmd, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        s_cancel = false;
        ESP_LOGI(TAG, "Action cmd=%d", (int)cmd);

        switch (cmd) {
        case ACTION_STAND:
            stand();
            break;

        case ACTION_SIT:
            sit();
            break;

        case ACTION_LAY_DOWN:
            lay_down();
            break;

        case ACTION_WAVE:
            wave();
            break;

        case ACTION_WALK:
            ensure_standing();
            walk();
            break;

        case ACTION_BACK:
            ensure_standing();
            walk_back();
            break;

        case ACTION_TURN_LEFT:
            ensure_standing();
            turn_left();
            break;

        case ACTION_TURN_RIGHT:
            ensure_standing();
            turn_right();
            break;

        case ACTION_SWING:
            ensure_standing();
            swing();
            break;

        default:
            stand();
            break;
        }

        s_last_action = cmd;
    }
}

/* -------------------------------------------------------------------------- */
/*  Public API                                                                 */
/* -------------------------------------------------------------------------- */

void action_task_start(void)
{
    servo_init();

    /* Length-1 queue: xQueueOverwrite always succeeds, latest command wins. */
    s_action_queue = xQueueCreate(1, sizeof(action_id_t));
    configASSERT(s_action_queue);

    xTaskCreate(action_task, "action_task", ACTION_TASK_STACK, NULL,
                ACTION_TASK_PRIO, NULL);

    ESP_LOGI(TAG, "Action subsystem started");
}

void action_post(action_id_t id)
{
    if (!s_action_queue) return;
    s_current_cmd = id;
    s_cancel = true;                        /* interrupt running action */
    (void)xQueueOverwrite(s_action_queue, &id);
}

void action_post_user(action_id_t id)
{
    if (!s_user_control) {
        ESP_LOGW(TAG, "User control disabled, ignoring action %d", (int)id);
        return;
    }
    action_post(id);
}

void action_set_user_control(bool enabled)
{
    s_user_control = enabled;
    ESP_LOGI(TAG, "User control %s", enabled ? "ENABLED" : "DISABLED");
}

bool action_user_control_enabled(void)
{
    return s_user_control;
}

action_id_t action_get_current_command(void)
{
    return s_current_cmd;
}

action_id_t action_from_string(const char *name)
{
    if (!name) return ACTION_STAND;

    if (strcmp(name, "stand")      == 0) return ACTION_STAND;
    if (strcmp(name, "walk")       == 0) return ACTION_WALK;
    if (strcmp(name, "back")       == 0) return ACTION_BACK;
    if (strcmp(name, "lay_down")   == 0) return ACTION_LAY_DOWN;
    if (strcmp(name, "turn_left")  == 0) return ACTION_TURN_LEFT;
    if (strcmp(name, "turn_right") == 0) return ACTION_TURN_RIGHT;
    if (strcmp(name, "sit")        == 0) return ACTION_SIT;
    if (strcmp(name, "wave")       == 0) return ACTION_WAVE;
    if (strcmp(name, "swing")      == 0) return ACTION_SWING;

    ESP_LOGW(TAG, "Unknown action name: %s", name);
    return ACTION_STAND;
}
