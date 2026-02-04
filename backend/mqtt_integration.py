"""
MQTT Integration module for AuraBot.
Factory-based MQTT client setup and lifecycle management.
"""

import json
import os
import threading
from typing import Optional
from dotenv import load_dotenv
import paho.mqtt.client as mqtt


class TTSWithMQTT:
    """
    TTS wrapper that publishes text to MQTT (aurabot/tts/speak) for the device
    (e.g. ESP32) to speak. Pi does not play TTS locally when speaker is on ESP32.
    """

    def __init__(self, tts_engine, mqtt_integration: "MQTTIntegration"):
        self._tts = tts_engine
        self._mqtt = mqtt_integration

    def speak(self, message: str) -> None:
        # Only publish to MQTT; ESP32 (or other device) does the actual TTS playback
        if self._mqtt.is_connected():
            self._mqtt.publish_tts(message)
        # Optional: uncomment next line to also speak on Pi (e.g. for debugging)
        # self._tts.speak(message)

    def style(self) -> None:
        if hasattr(self._tts, "style"):
            self._tts.style()

    def shutdown_tts(self) -> None:
        if hasattr(self._tts, "shutdown_tts"):
            self._tts.shutdown_tts()


class MQTTClientFactory:
    """Factory for creating and configuring MQTT clients."""
    
    @staticmethod
    def create_client(
        client_id: str = "aurabot_client",
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> mqtt.Client:
        """
        Create and configure an MQTT client.
        
        Args:
            client_id: Unique client identifier
            host: MQTT broker host (defaults to MQTT_HOST env var or 127.0.0.1)
            port: MQTT broker port (defaults to MQTT_PORT env var or 1883)
            username: Optional username for authentication
            password: Optional password for authentication
        
        Returns:
            Configured MQTT client instance
        """
        load_dotenv()
        
        host = host or os.getenv("MQTT_HOST", "127.0.0.1")
        port = port or int(os.getenv("MQTT_PORT", "1883"))
        username = username or os.getenv("MQTT_USERNAME")
        password = password or os.getenv("MQTT_PASSWORD")
        
        client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        
        if username:
            client.username_pw_set(username, password)
        
        return client


class MQTTIntegration:
    """
    MQTT integration service for AuraBot.
    Handles MQTT client lifecycle and message routing.
    """
    
    def __init__(self, mqtt_api, topics: Optional[list] = None):
        """
        Initialize MQTT integration.
        
        Args:
            mqtt_api: MQTTAPI instance for handling messages
            topics: List of topics to subscribe to (defaults to ["aurabot/#"])
        """
        self.mqtt_api = mqtt_api
        self.topics = topics or ["aurabot/#"]
        self.client: Optional[mqtt.Client] = None
        self._is_connected = False
        self._connection_lock = threading.Lock()
    
    def _on_connect(self, client: mqtt.Client, userdata, flags, rc, properties=None):
        """Handle MQTT connection event."""
        if rc == 0:
            with self._connection_lock:
                self._is_connected = True
            
            self.mqtt_api.logger.log_mqtt("MQTT connected to broker", "INFO")
            
            # Subscribe to topics
            for topic in self.topics:
                client.subscribe(topic, qos=1)
                self.mqtt_api.logger.log_mqtt(f"Subscribed to {topic}", "INFO")
        else:
            self.mqtt_api.logger.log_mqtt(f"MQTT connection failed with code {rc}", "ERROR")
    
    def _on_disconnect(self, client: mqtt.Client, userdata, flags, rc, *args, **kwargs):
        """Handle MQTT disconnection event."""
        with self._connection_lock:
            self._is_connected = False
        self.mqtt_api.logger.log_mqtt("MQTT disconnected from broker", "INFO")
    
    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        """Handle incoming MQTT messages."""
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            self.mqtt_api.logger.log_mqtt(f"Received message on {msg.topic}: {payload}", "INFO")
            
            # Parse JSON payload
            try:
                data = json.loads(payload)
                
                # Route by topic
                if msg.topic == "aurabot/sensors":
                    response = self.mqtt_api.handle_sensor_data(data)
                    self.mqtt_api.logger.log_mqtt(
                        f"Sensors processed: {response.get('status', 'unknown')}",
                        "INFO",
                        metadata={"topic": msg.topic, "status": response.get('status')}
                    )
                elif msg.topic == "aurabot/control":
                    response = self.mqtt_api.handle_control_command(data)
                    self.mqtt_api.logger.log_mqtt(
                        f"Control processed: {response.get('status', 'unknown')}",
                        "INFO",
                        metadata={"topic": msg.topic, "status": response.get('status')}
                    )
                elif msg.topic == "aurabot/status" or msg.topic.startswith("aurabot/status/"):
                    if "esp32" in msg.topic or (isinstance(data, dict) and "esp32" in data) or "esp32" in payload:
                        self.mqtt_api.record_esp32_message_received()
                elif msg.topic == "aurabot/tts/speak":
                    # Backend publishes here for ESP32; ignore when we receive our own or echoes (no action)
                    pass
                elif msg.topic == "aurabot/tts/ack":
                    response = self.mqtt_api.handle_tts_ack(data)
                    level = "INFO" if response.get("status") == "success" else "WARNING"
                    self.mqtt_api.logger.log_mqtt(
                        f"TTS ack processed: {response.get('status', 'unknown')}",
                        level,
                        metadata={"topic": msg.topic, "status": response.get("status")}
                    )
                    if response.get("status") == "success":
                        ack = response.get("ack", {})
                        if ack.get("status") == "error":
                            self.mqtt_api.logger.log_mqtt(
                                "ESP32 reported TTS error",
                                "ERROR",
                                metadata={"len": ack.get("len")}
                            )
                else:
                    self.mqtt_api.logger.log_mqtt(f"Unhandled topic: {msg.topic}", "WARNING")
                    
            except json.JSONDecodeError:
                self.mqtt_api.logger.log_mqtt(
                    f"Non-JSON payload on {msg.topic}: {payload}",
                    "WARNING",
                    metadata={"topic": msg.topic}
                )
                
        except Exception as e:
            self.mqtt_api.logger.log_mqtt(f"Error handling message: {e}", "ERROR")
    
    def start(self, client: Optional[mqtt.Client] = None, host: Optional[str] = None, port: Optional[int] = None):
        """
        Start MQTT integration.
        
        Args:
            client: Optional pre-configured MQTT client (if None, creates one)
            host: MQTT broker host (only used if client is None)
            port: MQTT broker port (only used if client is None)
        """
        if self.client and self._is_connected:
            self.mqtt_api.logger.log_mqtt("MQTT integration already running", "WARNING")
            return
        
        # Create client if not provided
        if client is None:
            client = MQTTClientFactory.create_client(
                client_id="aurabot_integrated",
                host=host,
                port=port
            )
        
        self.client = client
        
        # Register callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Connect and start loop
        load_dotenv()
        broker_host = host or os.getenv("MQTT_HOST", "127.0.0.1")
        broker_port = port or int(os.getenv("MQTT_PORT", "1883"))
        
        try:
            self.client.connect(broker_host, broker_port, keepalive=60)
            self.client.loop_start()
            self.mqtt_api.logger.log_mqtt(
                f"MQTT integration starting (connecting to {broker_host}:{broker_port})",
                "INFO",
                metadata={"host": broker_host, "port": broker_port}
            )
        except Exception as e:
            self.mqtt_api.logger.log_mqtt(f"Failed to start MQTT integration: {e}", "ERROR")
            self.client = None
    
    def stop(self):
        """Stop MQTT integration."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
                with self._connection_lock:
                    self._is_connected = False
                self.mqtt_api.logger.log_mqtt("MQTT integration stopped", "INFO")
            except Exception as e:
                self.mqtt_api.logger.log_mqtt(f"Error stopping MQTT integration: {e}", "ERROR")
            finally:
                self.client = None
    
    def is_connected(self) -> bool:
        """Check if MQTT is connected."""
        with self._connection_lock:
            return self._is_connected
    
    def publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> bool:
        """
        Publish a message to MQTT broker.
        
        Args:
            topic: MQTT topic
            payload: Message payload (dict, will be JSON-encoded)
            qos: Quality of service level
            retain: Whether to retain the message
        
        Returns:
            True if published successfully, False otherwise
        """
        if not self.client or not self._is_connected:
            self.mqtt_api.logger.log_mqtt("MQTT not connected, cannot publish", "WARNING")
            return False
        
        try:
            payload_str = json.dumps(payload)
            result = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            success = result.rc == mqtt.MQTT_ERR_SUCCESS
            if success:
                self.mqtt_api.logger.log_mqtt(
                    f"Published to {topic}",
                    "INFO",
                    metadata={"topic": topic, "qos": qos}
                )
            return success
        except Exception as e:
            self.mqtt_api.logger.log_mqtt(f"Error publishing to {topic}: {e}", "ERROR")
            return False

    def publish_tts(self, text: str, qos: int = 1) -> bool:
        """
        Publish TTS text to aurabot/tts/speak for the device (e.g. ESP32) to speak.
        
        Args:
            text: Text to speak on the device
            qos: MQTT QoS (default 1)
        
        Returns:
            True if published successfully, False otherwise
        """
        if not text:
            return False
        success = self.publish("aurabot/tts/speak", {"text": text}, qos=qos, retain=False)
        if success:
            preview = text[:80] + "..." if len(text) > 80 else text
            self.mqtt_api.logger.log_mqtt(
                "Published TTS to device (aurabot/tts/speak)",
                "INFO",
                metadata={"text_preview": preview}
            )
        return success

