"""
MQTT Integration module for AuraBot.
Factory-based MQTT client setup and lifecycle management.
"""

import json
import os
import threading
from typing import Optional, Callable
from dotenv import load_dotenv
import paho.mqtt.client as mqtt


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
            
            print("MQTT connected to broker")
            
            # Subscribe to topics
            for topic in self.topics:
                client.subscribe(topic, qos=1)
                print(f"Subscribed to {topic}")
        else:
            print(f"MQTT connection failed with code {rc}")
    
    def _on_disconnect(self, client: mqtt.Client, userdata, flags, rc, *args, **kwargs):
        """Handle MQTT disconnection event."""
        with self._connection_lock:
            self._is_connected = False
        print("MQTT disconnected from broker")
    
    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        """Handle incoming MQTT messages."""
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            print(f"[MQTT] {msg.topic}: {payload}")
            
            # Parse JSON payload
            try:
                data = json.loads(payload)
                
                # Route by topic
                if msg.topic == "aurabot/sensors":
                    response = self.mqtt_api.handle_sensor_data(data)
                    print(f"[MQTT] Sensors processed: {response.get('status', 'unknown')}")
                elif msg.topic == "aurabot/control":
                    response = self.mqtt_api.handle_control_command(data)
                    print(f"[MQTT] Control processed: {response.get('status', 'unknown')}")
                else:
                    print(f"[MQTT] Unhandled topic: {msg.topic}")
                    
            except json.JSONDecodeError:
                print(f"[MQTT] Non-JSON payload on {msg.topic}: {payload}")
                
        except Exception as e:
            print(f"[MQTT] Error handling message: {e}")
    
    def start(self, client: Optional[mqtt.Client] = None, host: Optional[str] = None, port: Optional[int] = None):
        """
        Start MQTT integration.
        
        Args:
            client: Optional pre-configured MQTT client (if None, creates one)
            host: MQTT broker host (only used if client is None)
            port: MQTT broker port (only used if client is None)
        """
        if self.client and self._is_connected:
            print("MQTT integration already running")
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
            print(f"MQTT integration starting (connecting to {broker_host}:{broker_port})...")
        except Exception as e:
            print(f"Failed to start MQTT integration: {e}")
            self.client = None
    
    def stop(self):
        """Stop MQTT integration."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
                with self._connection_lock:
                    self._is_connected = False
                print("MQTT integration stopped")
            except Exception as e:
                print(f"Error stopping MQTT integration: {e}")
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
            print("MQTT not connected, cannot publish")
            return False
        
        try:
            payload_str = json.dumps(payload)
            result = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            print(f"Error publishing to {topic}: {e}")
            return False

