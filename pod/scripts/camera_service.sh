#!/bin/sh
# Switch the QNX camera service (sensor) between the Pod config and the stock config.
# Owner: agent-1. Needs passwordless sudo (qnxuser has it on qnxpi).
#
#   sh camera_service.sh pod     # headless webcam config (pod/scripts/sensor_pod.conf)
#   sh camera_service.sh stock   # restore /etc/config/sensor/sensor_rpi5.conf
#   sh camera_service.sh status  # show which config the service is running
#
# Restarting the service interrupts anyone else using a camera on this Pi.

MODE="${1:-status}"
STOCK=/etc/config/sensor/sensor_rpi5.conf
POD=/data/share/sensor/sensor_pod.conf
SRC="$(dirname "$0")/sensor_pod.conf"
ON="$(command -v on 2>/dev/null || echo /proc/boot/on)"

running() {
    # Anchored match: a loose "grep sensor" would also match this grep itself.
    sudo -n pidin ar 2>/dev/null | grep -E '^ *[0-9]+ sensor '
}

restart_with() {
    sudo -n slay sensor >/dev/null 2>&1   # QNX slay exits 1 even when it worked; check with pidin
    i=0
    while running >/dev/null && [ "$i" -lt 10 ]; do sleep 1; i=$((i + 1)); done
    if running >/dev/null; then echo "ERROR: old sensor service did not stop"; exit 1; fi
    # Redirect output so the detached service does not hold the SSH session open.
    sudo -n "$ON" -d sensor -U 521:521 -b external -r /data/share/sensor -c "$1" >/dev/null 2>&1
    i=0
    while ! running >/dev/null && [ "$i" -lt 10 ]; do sleep 1; i=$((i + 1)); done
    if ! running >/dev/null; then echo "ERROR: sensor service did not start with $1"; exit 1; fi
    running
}

case "$MODE" in
    pod)
        [ -f "$SRC" ] || { echo "ERROR: $SRC not found"; exit 1; }
        sudo -n cp "$SRC" "$POD" && sudo -n chmod 644 "$POD" || { echo "ERROR: could not install $POD"; exit 1; }
        restart_with "$POD"
        ;;
    stock)
        restart_with "$STOCK"
        ;;
    status)
        running || echo "sensor service not running"
        ;;
    *)
        echo "usage: $0 pod|stock|status"; exit 2
        ;;
esac
