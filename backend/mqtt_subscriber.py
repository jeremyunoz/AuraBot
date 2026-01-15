import json
import time
import os
from dotenv import load_dotenv
from typing import Optional

import paho.mqtt.client as mqtt

load_dotenv()

BROKER_HOST = os.getenv("MQTT_HOST", "127.0.0.1")   # change to your broker IP (your Mac running mosquitto)
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC = "aurabot/#"            # subscribe to all aurabot topics
USERNAME: Optional[str] = os.getenv("MQTT_USERNAME") or None  # set to None if no auth
PASSWORD: Optional[str] = os.getenv("MQTT_PASSWORD") or None   # set to None if no auth

CLIENT_ID = "backend_server"

def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to broker")
        client.subscribe(TOPIC, qos=1)
        print(f"Subscribed to {TOPIC}")
    else:
        print(f"Connect failed, rc={rc}")

def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    payload = msg.payload.decode("utf-8", errors="replace")
    print(f"[{msg.topic}] {payload}")

    # If you publish JSON from ESP32, this makes it easy to handle
    try:
        data = json.loads(payload)
        # Example: route by topic or fields
        if msg.topic == "aurabot/sensors":
            handle_sensors(data)
        elif msg.topic == "aurabot/control":
            handle_control(data)
        else:
            handle_generic(msg.topic, data)
    except json.JSONDecodeError:
        # Non JSON payloads are fine too
        handle_text(msg.topic, payload)

def handle_sensors(data: dict):
    # TODO connect to your AuraBot logic, database, timer manager, etc.
    print("SENSORS:", data)

def handle_control(data: dict):
    print("CONTROL:", data)

def handle_generic(topic: str, data: dict):
    print("GENERIC:", topic, data)

def handle_text(topic: str, payload: str):
    print("TEXT:", topic, payload)

def main():
    client = mqtt.Client(
        client_id=CLIENT_ID,
        protocol=mqtt.MQTTv311,
        clean_session=True,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2 # type: ignore
    )

    if USERNAME is not None:
        client.username_pw_set(USERNAME, PASSWORD)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()