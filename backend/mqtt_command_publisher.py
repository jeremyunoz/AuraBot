import json
import time
import os
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

BROKER_HOST = os.getenv("MQTT_HOST", "127.0.0.1")   # your Mac IP, not localhost
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))

TOPIC_CONTROL = "aurabot/control"
CLIENT_ID = "backend_cmd"

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("Connected to MQTT broker")
    else:
        print(f"Connect failed rc={rc}")

def main():
    client = mqtt.Client(
        client_id=CLIENT_ID,
        clean_session=True,
        protocol=mqtt.MQTTv311
    )

    client.on_connect = on_connect

    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    time.sleep(1)  # wait for connection

    # Example commands
    commands = [
        {"cmd": "start_session"},
        {"cmd": "pause_session"},
        {"cmd": "resume_session"},
        {"cmd": "stop_session"},
    ]

    for cmd in commands:
        payload = json.dumps(cmd)
        print(f"Publishing: {payload}")

        client.publish(
            TOPIC_CONTROL,
            payload,
            qos=1,
            retain=False
        )

        time.sleep(2)

    client.loop_stop()
    client.disconnect()

if __name__ == "__main__":
    main()