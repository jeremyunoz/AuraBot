"""
PIR sensor integration for AuraBot.

Reads a local PIR GPIO pin and forwards motion state into the existing
MQTTAPI sensor pipeline so session/wellness behavior is reused.
"""

import threading
import time
from typing import Any, Optional


class _BaseMotionAdapter:
    """Interface for motion sensor backends."""

    def read_motion(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        return


class _GpioZeroMotionAdapter(_BaseMotionAdapter):
    """GPIO backend using gpiozero.MotionSensor."""

    def __init__(self, pin: int):
        from gpiozero import MotionSensor  # type: ignore

        self._sensor = MotionSensor(pin=pin)

    def read_motion(self) -> int:
        return 1 if self._sensor.motion_detected else 0

    def close(self) -> None:
        try:
            self._sensor.close()
        except Exception:
            pass


class _RPiGPIOMotionAdapter(_BaseMotionAdapter):
    """GPIO backend using RPi.GPIO."""

    def __init__(self, pin: int):
        import RPi.GPIO as GPIO  # type: ignore

        self._gpio = GPIO
        self._pin = pin
        self._gpio.setmode(self._gpio.BCM)
        self._gpio.setup(self._pin, self._gpio.IN, pull_up_down=self._gpio.PUD_DOWN)

    def read_motion(self) -> int:
        return 1 if self._gpio.input(self._pin) else 0

    def close(self) -> None:
        try:
            self._gpio.cleanup(self._pin)
        except Exception:
            pass


def _create_motion_adapter(pin: int) -> _BaseMotionAdapter:
    """
    Create a GPIO motion adapter using available libraries.

    Tries gpiozero first (recommended on Pi), then RPi.GPIO.
    Raises RuntimeError if no supported GPIO library is available.
    """
    try:
        return _GpioZeroMotionAdapter(pin)
    except Exception:
        pass

    try:
        return _RPiGPIOMotionAdapter(pin)
    except Exception as e:
        raise RuntimeError(
            "No supported GPIO motion backend found. "
            "Install gpiozero or RPi.GPIO on the Raspberry Pi."
        ) from e


class PIRSensorIntegration:
    """Background PIR reader that feeds AuraBot MQTTAPI sensor flow."""

    def __init__(
        self,
        aurabot: Any,
        *,
        gpio_pin: int = 17,
        poll_interval_seconds: float = 0.2,
        heartbeat_seconds: float = 15.0,
    ):
        self.aurabot = aurabot
        self.gpio_pin = gpio_pin
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self.heartbeat_seconds = max(1.0, float(heartbeat_seconds))

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._adapter: Optional[_BaseMotionAdapter] = None
        self._sequence = 0

    def start(self) -> tuple[threading.Event, threading.Event]:
        """Start PIR monitor thread."""
        if self._thread and self._thread.is_alive():
            return self._stop_event, self._ready_event

        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self._stop_event, self._ready_event

    def stop(self) -> None:
        """Stop PIR monitor thread and release GPIO resources."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._close_adapter()

    def _close_adapter(self) -> None:
        if self._adapter:
            try:
                self._adapter.close()
            finally:
                self._adapter = None

    def _run(self) -> None:
        logger = getattr(self.aurabot, "logger", None)
        mqtt_api = getattr(self.aurabot, "mqtt_api", None)
        if not mqtt_api:
            if logger:
                logger.log_sensor("PIR integration skipped: MQTT API not available", "WARNING")
            return

        try:
            self._adapter = _create_motion_adapter(self.gpio_pin)
        except Exception as e:
            if logger:
                logger.log_sensor(f"PIR integration failed to initialize GPIO: {e}", "ERROR")
            return

        if logger:
            logger.log_sensor(
                f"PIR GPIO integration started on BCM pin {self.gpio_pin}",
                "INFO",
                metadata={
                    "pin": self.gpio_pin,
                    "poll_interval_seconds": self.poll_interval_seconds,
                    "heartbeat_seconds": self.heartbeat_seconds,
                },
            )

        self._ready_event.set()
        last_motion: Optional[int] = None
        last_publish = 0.0

        while not self._stop_event.is_set():
            try:
                motion = self._adapter.read_motion() if self._adapter else 0
                now = time.time()
                should_publish = (
                    last_motion is None
                    or motion != last_motion
                    or (now - last_publish) >= self.heartbeat_seconds
                )
                if should_publish:
                    self._sequence += 1
                    mqtt_api.handle_sensor_data(
                        {
                            "motion": motion,
                            "ts_us": int(now * 1_000_000),
                            "count": self._sequence,
                            "src": "pi_pir_gpio",
                            "pin": self.gpio_pin,
                        }
                    )
                    setattr(self.aurabot, "last_pir_activity", now)
                    last_motion = motion
                    last_publish = now
            except Exception as e:
                if logger:
                    logger.log_sensor(f"PIR integration read error: {e}", "ERROR")

            self._stop_event.wait(self.poll_interval_seconds)

        self._close_adapter()
        if logger:
            logger.log_sensor("PIR GPIO integration stopped", "INFO")


def start_pir_integration(
    aurabot: Any,
    *,
    gpio_pin: int = 17,
    poll_interval_seconds: float = 0.2,
    heartbeat_seconds: float = 15.0,
) -> tuple[Optional[PIRSensorIntegration], Optional[threading.Event], Optional[threading.Event]]:
    """
    Start local PIR GPIO integration.

    Returns:
        (integration, stop_event, ready_event)
    """
    integration = PIRSensorIntegration(
        aurabot,
        gpio_pin=gpio_pin,
        poll_interval_seconds=poll_interval_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
    stop_event, ready_event = integration.start()
    return integration, stop_event, ready_event
