/**
 * @file servo.c
 * @brief Low-level servo driver implementation for AuraBot quadruped legs.
 */

#include "motion/servo.h"

#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "iot_servo.h"
#include "esp_log.h"

/* -------------------------------------------------------------------------- */
/*  Configuration                                                              */
/* -------------------------------------------------------------------------- */

/** GPIO pins for each leg servo */
#define SERVO_PIN_FL  29
#define SERVO_PIN_FR  28
#define SERVO_PIN_RL  3
#define SERVO_PIN_RR  2

/** Angle limits */
#define ANGLE_MIN  0
#define ANGLE_MAX  180

/** Smooth-ramp parameters */
#define RAMP_STEP_DEG       4   /**< Degrees per ramp tick  */
#define RAMP_STEP_DELAY_MS  4   /**< Milliseconds per tick  */

static const char *TAG = "servo";

/* -------------------------------------------------------------------------- */
/*  Internal state                                                             */
/* -------------------------------------------------------------------------- */

static int  s_current_angle[SERVO_CH_COUNT] = {90, 90, 90, 90};
static bool s_initialised = false;

/* -------------------------------------------------------------------------- */
/*  Helpers                                                                    */
/* -------------------------------------------------------------------------- */

static int clamp_angle(int angle)
{
    if (angle < ANGLE_MIN) return ANGLE_MIN;
    if (angle > ANGLE_MAX) return ANGLE_MAX;
    return angle;
}

/**
 * Write a clamped angle to the LEDC channel.
 * Right-side servos (FR, RR) are mirrored so that 0 deg = forward for all legs.
 */
static void write_raw(int channel, int angle)
{
    int clamped = clamp_angle(angle);

    /* Mirror right-side channels so angle direction is consistent */
    if (channel == SERVO_CH_FR || channel == SERVO_CH_RR) {
        clamped = 180 - clamped;
    }

    iot_servo_write_angle(LEDC_LOW_SPEED_MODE, channel, clamped);
}

/**
 * Smoothly ramp from the current angle to @p target on the given channel.
 * Before initialisation completes the angle is written immediately (no ramp).
 */
static void write_smooth(int channel, int target_angle)
{
    int target  = clamp_angle(target_angle);
    int current = s_current_angle[channel];

    /* Before init finishes, jump directly */
    if (!s_initialised) {
        write_raw(channel, target);
        s_current_angle[channel] = target;
        return;
    }

    if (current == target) {
        return;
    }

    int step = (target > current) ? RAMP_STEP_DEG : -RAMP_STEP_DEG;

    while (current != target) {
        current += step;

        /* Clamp overshoot on the last tick */
        if ((step > 0 && current > target) || (step < 0 && current < target)) {
            current = target;
        }

        write_raw(channel, current);
        s_current_angle[channel] = current;
        vTaskDelay(pdMS_TO_TICKS(RAMP_STEP_DELAY_MS));
    }
}

/* -------------------------------------------------------------------------- */
/*  Public API                                                                 */
/* -------------------------------------------------------------------------- */

void servo_init(void)
{
    servo_config_t cfg = {
        .max_angle      = 180,
        .min_width_us   = 500,
        .max_width_us   = 2500,
        .freq           = 50,
        .timer_number   = LEDC_TIMER_0,
        .channels       = {
            .servo_pin = {SERVO_PIN_FL, SERVO_PIN_FR, SERVO_PIN_RL, SERVO_PIN_RR},
            .ch        = {LEDC_CHANNEL_0, LEDC_CHANNEL_1, LEDC_CHANNEL_2, LEDC_CHANNEL_3},
        },
        .channel_number = SERVO_CH_COUNT,
    };

    iot_servo_init(LEDC_LOW_SPEED_MODE, &cfg);

    /* Drive every channel to its default neutral angle */
    for (int i = 0; i < SERVO_CH_COUNT; i++) {
        write_raw(i, s_current_angle[i]);
    }

    s_initialised = true;
    ESP_LOGI(TAG, "Servos initialised (%d channels)", SERVO_CH_COUNT);
}

void servo_set_angle(servo_ch_t ch, int angle)
{
    if (ch >= SERVO_CH_COUNT) {
        ESP_LOGW(TAG, "Invalid servo channel %d", ch);
        return;
    }
    write_smooth(ch, angle);
}

/* ---- Convenience wrappers ------------------------------------------------ */

void FL_angle(int angle) { servo_set_angle(SERVO_CH_FL, angle); }
void FR_angle(int angle) { servo_set_angle(SERVO_CH_FR, angle); }
void RL_angle(int angle) { servo_set_angle(SERVO_CH_RL, angle); }
void RR_angle(int angle) { servo_set_angle(SERVO_CH_RR, angle); }
