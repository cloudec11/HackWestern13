#!/bin/sh
# Run the ground station ON the Pi, next to the Pod, so no laptop is needed (with-motor, agent-2,
# human request). Opt-in: pod_boot.sh calls this only when ~/pod.env has POD_RUN_GROUND=1
# (and then POD_GROUND_URL=http://127.0.0.1:8001).
#
#   sh pod/scripts/ground_on_pi.sh start|stop|status        (as qnxuser; any directory)
#
#   ground API  :8001  python3 -m ground.api, database pod-data/ground.sqlite3, log pod-data/ground.log
#   dashboard   :8081  python3 -m http.server on ground/dashboard/sim, log pod-data/dashboard.log
#                      (8080 is pod-view). Open http://<pi>:8081/?mode=live&api=http://<pi>:8001&pod=http://<pi>:8080
# Port 8001, not the usual 8000: QNX's qconn (Momentics target agent) already listens on 8000
# on the Pi, and pod-uplink then crashes on its banner (http.client.BadStatusLine: QCONN).
#
# Not under pod-supervisor on purpose: its health table holds 8 processes (pod/ipc/health.h,
# kHealthSlots) and pod.conf already uses all 8, so nothing restarts these if they crash.
# "start" is idempotent (PID files in pod-data/). Needs ground/ copied to ~/pod-deploy.
cd "$(dirname "$0")/../.." || exit 1
DATA=pod-data
API_PORT=${POD_API_PORT:-8001}
DASH_PORT=${POD_DASH_PORT:-8081}
mkdir -p "$DATA"

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

start_one() {   # name pidfile log command...
    name=$1; pidf=$2; log=$3; shift 3
    if alive "$pidf"; then
        echo "ground_on_pi: $name already running (pid $(cat "$pidf"))"
        return
    fi
    nohup "$@" >> "$log" 2>&1 < /dev/null &
    echo $! > "$pidf"
    echo "ground_on_pi: $name started (pid $!)"
}

stop_one() {    # name pidfile
    if alive "$2"; then kill "$(cat "$2")" && echo "ground_on_pi: $1 stopped"; else echo "ground_on_pi: $1 not running"; fi
    rm -f "$2"
}

case "${1:-start}" in
start)
    [ -d ground ] || { echo "ground_on_pi: no ground/ in $(pwd): copy it to the Pi first"; exit 1; }
    start_one "ground API on :$API_PORT" "$DATA/ground.pid" "$DATA/ground.log" \
        python3 -m ground.api --host 0.0.0.0 --port "$API_PORT" --db "$DATA/ground.sqlite3" --quiet
    start_one "dashboard on :$DASH_PORT" "$DATA/dashboard.pid" "$DATA/dashboard.log" \
        python3 -m http.server "$DASH_PORT" --directory ground/dashboard/sim
    ;;
stop)
    stop_one "ground API" "$DATA/ground.pid"
    stop_one "dashboard" "$DATA/dashboard.pid"
    ;;
status)
    for n in ground dashboard; do
        if alive "$DATA/$n.pid"; then echo "$n: running (pid $(cat "$DATA/$n.pid"))"; else echo "$n: stopped"; fi
    done
    ;;
*)
    echo "usage: ground_on_pi.sh start|stop|status"; exit 2 ;;
esac
