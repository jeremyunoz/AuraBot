echo "Temperature Check Loop"
echo "======================"

while true; do
    TEMP=$(vcgencmd measure_temp)
    echo "Temperature: $TEMP"
    sleep 1
done