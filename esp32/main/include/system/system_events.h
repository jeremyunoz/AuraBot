#pragma once

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SYS_EVT_WAKE_DETECTED = 0,
    SYS_EVT_FORCE_WAKE,
    SYS_EVT_PI5_READY,
    SYS_EVT_SESSION_END,
    SYS_EVT_MQTT_UP,
    SYS_EVT_MQTT_FAIL
} sys_event_id_t;

typedef struct {
    sys_event_id_t id;
} sys_event_t;

#ifdef __cplusplus
}
#endif
