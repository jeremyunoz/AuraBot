#pragma once

/**
 * @brief FreeRTOS task that monitors the ultrasonic sensor and stops walking
 * when an obstacle is too close.
 */
void usonic_task(void *pvParameters);
