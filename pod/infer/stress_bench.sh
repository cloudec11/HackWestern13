#!/bin/sh
# stress_bench.sh -- pod-infer latency with and without a CPU hog (P3-2, agent-2's half).
#
# On the Pi, from the repo root (or a copy with build/infer and models/):
#   sh pod/infer/stress_bench.sh [SECONDS_PER_RUN] [OUTDIR]
#
# Three runs of SECONDS_PER_RUN (default 30), each with pod-infer as the frame ring's consumer:
#   baseline   no load,               pod-infer at normal priority
#   hog        POD_BENCH_HOGS (4) busy-loop shells at normal priority, pod-infer at normal priority
#   hog_fifo   same hog,              pod-infer --prio POD_BENCH_PRIO (15, SCHED_FIFO)
# then latency_report.py -> OUTDIR/table.md, OUTDIR/latency.json, plus per-run CSVs and logs.
#
# DISRUPTS THE LIVE PIPELINE for about 3 x SECONDS_PER_RUN: any running pod-infer (e.g. the
# supervised one) is paused with SIGSTOP so this one has the ring and the CPU to itself, and
# resumed with SIGCONT afterwards, also on Ctrl-C or error. While paused, the bench writes the
# heartbeat health file itself (POD_BENCH_HEALTH, default pod-data/uplink/heartbeat.json under
# ~/pod-deploy) so the uplink never resends stale numbers; set POD_BENCH_HEALTH="" to skip.
# Needs passwordless sudo (pod-infer needs I/O privilege).
set -u
DUR=${1:-30}
OUT=${2:-$HOME/agent2/bench-$(date +%Y%m%d-%H%M%S)}
PRIO=${POD_BENCH_PRIO:-15}
HOGS=${POD_BENCH_HOGS:-4}
INFER=${POD_INFER:-./build/infer/pod-infer}
HEALTH=${POD_BENCH_HEALTH-$HOME/pod-deploy/pod-data/uplink/heartbeat.json}
STATUS=${POD_BENCH_STATUS:-$HOME/pod-deploy/pod-data/supervisor/status.json}
HERE=$(cd "$(dirname "$0")" && pwd)

[ -x "$INFER" ] || { echo "stress_bench: $INFER not found (run from the dir that has build/infer)"; exit 1; }
mkdir -p "$OUT" || exit 1

# As root: pod-infer runs as root (sudo), and a normal user's pidin does not list root processes.
PAUSED=$(sudo -n pidin -f an | awk '/[p]od-infer/ && !/sudo/ {print $1}')
[ -n "$PAUSED" ] || echo "stress_bench: no other pod-infer running"
HOGP=""

stop_hogs() {
    for p in $HOGP; do kill "$p" 2>/dev/null; done
    HOGP=""
}
cleanup() {
    stop_hogs
    for p in $PAUSED; do sudo -n kill -CONT "$p" 2>/dev/null && echo "stress_bench: resumed pod-infer $p"; done
    PAUSED=""
}
trap cleanup EXIT
trap 'exit 130' INT TERM

for p in $PAUSED; do
    sudo -n kill -STOP "$p" && echo "stress_bench: paused pod-infer $p (resumed at the end)"
done

run() {
    label=$1; shift
    echo "stress_bench: $label for $DUR s ${*:-}"
    hopt=""
    [ -n "$HEALTH" ] && hopt="--health-out $HEALTH --status $STATUS"
    # shellcheck disable=SC2086
    sudo -n "$INFER" --dets-mq "" --quiet --stats 0 --seconds "$DUR" --latency-log "$OUT/$label.csv" $hopt ${1+"$@"} \
        > "$OUT/$label.log" 2>&1 || { echo "stress_bench: $label FAILED, see $OUT/$label.log"; exit 1; }
    grep -E "total|infer p50|e2e" "$OUT/$label.log" | sed 's/^/    /'
}

start_hogs() {
    i=0
    while [ "$i" -lt "$HOGS" ]; do
        sh -c 'while :; do :; done' &
        HOGP="$HOGP $!"
        i=$((i + 1))
    done
    echo "stress_bench: started $HOGS CPU hogs at normal priority:$HOGP"
    sleep 1
}

run baseline
start_hogs
run hog
run hog_fifo --prio "$PRIO"
stop_hogs
cleanup

python3 "$HERE/latency_report.py" \
    baseline="$OUT/baseline.csv" hog="$OUT/hog.csv" "hog_fifo_prio$PRIO=$OUT/hog_fifo.csv" \
    --md "$OUT/table.md" --json "$OUT/latency.json" > "$OUT/report.txt" 2>&1
tail -6 "$OUT/report.txt"
echo "stress_bench: results in $OUT (report.txt, table.md, latency.json, *.csv)"
