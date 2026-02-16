/**
 * @file servo.h
 * @brief Low-level servo driver for AuraBot quadruped legs.
 *
 * Controls four servos (front-left, front-right, rear-left, rear-right)
 * via LEDC PWM with smooth angle ramping.
 */

#ifndef SERVO_H
#define SERVO_H

/**
 * @brief Servo channel identifiers.
 */
typedef enum {
    SERVO_CH_FL = 0,   /**< Front-left leg  */
    SERVO_CH_FR,       /**< Front-right leg */
    SERVO_CH_RL,       /**< Rear-left leg   */
    SERVO_CH_RR,       /**< Rear-right leg  */
    SERVO_CH_COUNT     /**< Total number of servo channels */
} servo_ch_t;

/**
 * @brief Initialise all servo channels and move to neutral (90 deg).
 */
void servo_init(void);

/**
 * @brief Set angle for an individual leg (smooth ramp).
 *
 * Angle mapping:
 *   0   = fully forward
 *   90  = neutral / standing
 *   180 = fully backward
 */
void servo_set_angle(servo_ch_t ch, int angle);

/* Convenience wrappers */
void FL_angle(int angle);
void FR_angle(int angle);
void RL_angle(int angle);
void RR_angle(int angle);

#endif /* SERVO_H */
