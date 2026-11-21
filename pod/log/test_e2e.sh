cd ~/pod-deploy || exit 1
T=/tmp/podtest
rm -rf $T; mkdir -p $T
B=./build/log
ARGS="--pod-id pod-01 --dir $T/events --uplink-state $T/uplink --max-file-kb 4 --max-total-kb 12"
UP="python3 pod/uplink/uplink.py --url http://127.0.0.1:8765 --events-dir $T/events --state-dir $T/uplink --once"

cat > $T/check.py <<'EOF'
import glob, json, sys, urllib.request
seqs = []
for p in sorted(glob.glob(sys.argv[1] + "/events-*.jsonl")):
    for line in open(p):
        seqs.append(json.loads(line)["seq"])
print(f"files={len(glob.glob(sys.argv[1] + '/events-*.jsonl'))} lines={len(seqs)} unique={len(set(seqs))} "
      f"min={min(seqs)} max={max(seqs)} contiguous={sorted(seqs) == list(range(min(seqs), max(seqs) + 1))}")
if len(sys.argv) > 2:
    s = json.load(urllib.request.urlopen(sys.argv[2] + "/api/summary"))
    pods = s["pods"] if isinstance(s["pods"], list) else [dict(v, pod_id=k) for k, v in s["pods"].items()]
    p = [x for x in pods if x["pod_id"] == "pod-01"][0]
    print(f"server: acked_seq={p['acked_seq']} n_events={p['n_events']}")
EOF

echo '=== 1. ground API on the Pi (agent-3)'
python3 -m ground.api --port 8765 --db $T/pod.sqlite3 --quiet > $T/api.log 2>&1 &
API=$!
sleep 2

echo '=== 2. pod-log + fake tracker: 60 valid + 3 invalid, 4 KB files'
$B/pod-log $ARGS > $T/log1.txt 2>&1 &
LOG=$!
sleep 1
$B/fake_track --count 60 --interval-ms 5 --invalid 3
sleep 1
grep -c 'skipped invalid' $T/log1.txt | sed 's/^/invalid skipped: /'

echo '=== 3. hard crash (kill -9) + half-written line, then restart'
kill -9 $LOG; sleep 1
LAST=$(ls $T/events | grep jsonl | tail -1)
printf '{"pod_id":"pod-01","seq":99' >> "$T/events/$LAST"
$B/pod-log $ARGS > $T/log2.txt 2>&1 &
LOG=$!
sleep 1
head -2 $T/log2.txt
$B/fake_track --count 40 --interval-ms 5
sleep 1
python3 $T/check.py $T/events

echo '=== 4. uplink sends everything (nothing acked yet, so nothing deleted so far)'
$UP | tail -1
python3 $T/check.py $T/events http://127.0.0.1:8765

echo '=== 5. 20 more events: retention may now delete only acked files'
$B/fake_track --count 20 --interval-ms 5
sleep 1
grep 'retention deleted' $T/log2.txt
$UP | tail -1
python3 $T/check.py $T/events http://127.0.0.1:8765
echo "last_seq file: $(cat $T/events/last_seq)"

kill $LOG; sleep 1; tail -1 $T/log2.txt
kill $API
