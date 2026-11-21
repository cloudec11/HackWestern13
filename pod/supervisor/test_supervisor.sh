# pod-supervisor test on the Pi (agent-1, P3-1). Run from ~/pod-deploy after deploy.sh:
#   sh pod/scripts/camera_service.sh pod && sh pod/supervisor/test_supervisor.sh
# Needs the camera. Starts agent-3's ground API on port 8765 in the background.
cd ~/pod-deploy || exit 1
T=/tmp/svtest
rm -rf $T; mkdir -p $T
pidof() { pidin -f an 2>/dev/null | awk -v n="$1" '$2 ~ n {print $1}' | head -1; }
child() { python3 -c "import json;print([c for c in json.load(open('$T/state/status.json'))['children'] if c['name']=='$1'][0]['$2'])"; }

cat > $T/pod.conf <<EOF
capture 30 beat ./build/capture/pod-capture --prio 30 --stats 0
log     20 beat ./build/log/pod-log --pod-id pod-01 --dir $T/events --uplink-state $T/uplink
uplink   0 exit python3 pod/uplink/uplink.py --url http://127.0.0.1:8765 --events-dir $T/events --state-dir $T/uplink
EOF

python3 -m ground.api --port 8765 --db $T/pod.sqlite3 --quiet > $T/api.log 2>&1 &
API=$!
./build/supervisor/pod-supervisor --conf $T/pod.conf --state $T/state > $T/sv.log 2>&1 &
SV=$!
sleep 8

echo '=== 1. all started, frames flowing'
grep -E 'started|temperature' $T/sv.log
./build/capture/capture_check --seconds 3 | tail -1

echo '=== 2. kill -9 pod-capture: restarted, frames flow again'
P=$(child capture pid); kill -9 $P
sleep 9
grep -E 'capture: (killed|started)' $T/sv.log | tail -2
./build/capture/capture_check --seconds 3 | tail -1

echo '=== 3. freeze pod-log (SIGSTOP): watchdog kills and restarts it'
P=$(child log pid); kill -STOP $P
sleep 9
grep -E 'log: ' $T/sv.log | tail -3

echo '=== 4. events flow through log -> uplink -> ground'
./build/log/fake_track --count 15 --interval-ms 20
sleep 3
python3 -c "import json,urllib.request;s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/summary'));print('ground:', s['pods'])"
sleep 8; cat $T/state/status.json

echo '=== 5. stop supervisor: children stop too'
kill $SV; sleep 4
tail -2 $T/sv.log
pidin -f an | grep -E 'pod-capture|pod-log|uplink.py' | grep -v grep || echo 'no Pod processes left'
kill $API
