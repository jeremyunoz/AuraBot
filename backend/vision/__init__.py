"""
Vision feature: camera-based person detection, presence feed to MQTT.
"""
from .vision_integration import start_vision_integration

__all__ = ["start_vision_integration"]
