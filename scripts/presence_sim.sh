#!/bin/bash

# MQTT Presence Test Script
# Continuously publishes sensor data to test presence detection logic
# Press Ctrl+C to stop

# Configuration
MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_TOPIC="aurabot/sensors"
INTERVAL="${INTERVAL:-1}"  # seconds between messages

# Sensor values - modify these to test different scenarios
# PRESENT: distance < 50cm, motion >= 1, camera_confirmed = 1
# ABSENT: distance >= 50cm or motion = 0, camera_confirmed = 0

# Default values (simulating user present)
DISTANCE_CM="${DISTANCE_CM:-45.0}"
MOTION="${MOTION:-1}"
CAMERA_CONFIRMED="${CAMERA_CONFIRMED:-1}"

# MQTT auth (optional)
MQTT_USER="${MQTT_USER:-}"
MQTT_PASS="${MQTT_PASS:-}"

# Build mosquitto_pub command
PUB_CMD="mosquitto_pub -h $MQTT_HOST -p $MQTT_PORT -t $MQTT_TOPIC"
if [ -n "$MQTT_USER" ]; then
    PUB_CMD="$PUB_CMD -u $MQTT_USER"
fi
if [ -n "$MQTT_PASS" ]; then
    PUB_CMD="$PUB_CMD -P $MQTT_PASS"
fi

echo "MQTT Presence Test Loop"
echo "======================"
echo "Host: $MQTT_HOST:$MQTT_PORT"
echo "Topic: $MQTT_TOPIC"
echo "Interval: ${INTERVAL}s"
echo ""
echo "Current sensor values:"
echo "  distance_cm: $DISTANCE_CM"
echo "  motion: $MOTION"
echo "  camera_confirmed: $CAMERA_CONFIRMED"
echo ""
echo "Tip: Set environment variables to change values:"
echo "  DISTANCE_CM=60 MOTION=0 CAMERA_CONFIRMED=0 $0"
echo ""
echo "Publishing messages... (Press Ctrl+C to stop)"
echo ""

# Counter for message numbering
COUNT=0

# Trap Ctrl+C for clean exit
trap 'echo ""; echo "Stopped after $COUNT messages"; exit 0' INT

# Main loop
while true; do
    COUNT=$((COUNT + 1))
    TIMESTAMP=$(date +%s%N | cut -b1-13)  # milliseconds
    
    # Build JSON payload
    PAYLOAD="{\"distance_cm\": $DISTANCE_CM, \"motion\": $MOTION, \"camera_confirmed\": $CAMERA_CONFIRMED, \"count\": $COUNT, \"ts_us\": $((TIMESTAMP * 1000))}"
    
    # Publish message
    eval "$PUB_CMD -m '$PAYLOAD'"
    
    if [ $? -eq 0 ]; then
        echo "[$COUNT] Published: distance=$DISTANCE_CM, motion=$MOTION, camera=$CAMERA_CONFIRMED"
    else
        echo "[$COUNT] ERROR: Failed to publish"
    fi
    
    # Wait before next message
    sleep "$INTERVAL"
done
