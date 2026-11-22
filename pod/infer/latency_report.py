#!/usr/bin/env python3
"""Latency report for pod-infer runs (P3-2, agent-2). Python stdlib only.

Reads one or more CSVs written by `pod-infer --latency-log FILE` and prints, per run:
frames/s, p50/p90/p99/max of model time ("infer") and camera-to-detection time ("e2e"),
the share of frames whose e2e exceeded --deadline, and a text histogram of e2e.

    python3 latency_report.py baseline=base.csv hog=hog.csv hog_fifo=fifo.csv \
        [--deadline 200] [--bin 10] [--md table.md] [--json latency.json]

--md   writes a Markdown comparison table (for slides / README).
--json writes {"runs": [{"label", "frames", "fps", "infer_ms": {...}, "e2e_ms": {...},
       "over_deadline", "histogram": {"bin_ms", "counts"}}], "deadline_ms"} for the dashboard.
"""
import argparse
import csv
import json
import sys


def pct(sorted_vals, p):
    """Nearest-rank percentile on a sorted list (same rule as pod-infer)."""
    if not sorted_vals:
        return 0.0
    return sorted_vals[int(p * (len(sorted_vals) - 1) + 0.5)]


def load(path):
    infer, e2e, t_cap, skipped = [], [], [], 0
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                infer.append(int(row["infer_us"]) / 1000.0)
                e2e.append(int(row["e2e_us"]) / 1000.0)
                t_cap.append(int(row["t_capture_ns"]))
                skipped += int(row["skipped"])
            except (KeyError, ValueError):
                continue        # tolerate a half-written last line
    return infer, e2e, t_cap, skipped


def summarize(label, path, deadline_ms, bin_ms):
    infer, e2e, t_cap, skipped = load(path)
    n = len(e2e)
    span_s = (max(t_cap) - min(t_cap)) / 1e9 if n > 1 else 0.0
    si, se = sorted(infer), sorted(e2e)
    stats = lambda s: {"p50": round(pct(s, 0.50), 2), "p90": round(pct(s, 0.90), 2),
                       "p99": round(pct(s, 0.99), 2), "max": round(s[-1], 2) if s else 0.0}
    top = int(se[-1] // bin_ms) + 1 if se else 0
    counts = [0] * top
    for v in e2e:
        counts[int(v // bin_ms)] += 1
    return {
        "label": label,
        "frames": n,
        "seconds": round(span_s, 1),
        "fps": round((n - 1) / span_s, 2) if span_s > 0 else 0.0,
        "skipped": skipped,
        "infer_ms": stats(si),
        "e2e_ms": stats(se),
        "over_deadline": round(sum(1 for v in e2e if v > deadline_ms) / n, 4) if n else 0.0,
        "histogram": {"bin_ms": bin_ms, "counts": counts},
    }


def text_histogram(run, width=50):
    counts = run["histogram"]["counts"]
    b = run["histogram"]["bin_ms"]
    peak = max(counts) if counts else 1
    lines = []
    for i, c in enumerate(counts):
        if c == 0 and (i == 0 or counts[i - 1] == 0) and (i + 1 >= len(counts) or counts[i + 1] == 0):
            continue    # collapse long empty stretches
        bar = "#" * max(1 if c else 0, round(width * c / peak))
        lines.append(f"  {i * b:5d}-{(i + 1) * b:<5d} ms |{bar} {c}")
    return "\n".join(lines)


def markdown(runs, deadline_ms):
    head = ("| Run | Frames/s | infer p50 | infer p99 | e2e p50 | e2e p99 | e2e max | "
            f"e2e > {deadline_ms:g} ms |\n|---|---|---|---|---|---|---|---|\n")
    rows = "".join(
        f"| {r['label']} | {r['fps']:.1f} | {r['infer_ms']['p50']:.1f} | {r['infer_ms']['p99']:.1f} | "
        f"{r['e2e_ms']['p50']:.1f} | {r['e2e_ms']['p99']:.1f} | {r['e2e_ms']['max']:.1f} | "
        f"{100 * r['over_deadline']:.1f}% |\n" for r in runs)
    return head + rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="label=path.csv")
    ap.add_argument("--deadline", type=float, default=200.0, help="e2e deadline in ms (default 200)")
    ap.add_argument("--bin", type=int, default=10, help="histogram bin width in ms (default 10)")
    ap.add_argument("--md", help="write a Markdown table here")
    ap.add_argument("--json", help="write JSON for the dashboard here")
    a = ap.parse_args(argv)

    runs = []
    for spec in a.runs:
        label, sep, path = spec.partition("=")
        if not sep:
            ap.error(f"expected label=path, got {spec!r}")
        r = summarize(label, path, a.deadline, a.bin)
        if r["frames"] == 0:
            print(f"{label}: no frames in {path}", file=sys.stderr)
            continue
        runs.append(r)

    for r in runs:
        print(f"== {r['label']}: {r['frames']} frames in {r['seconds']} s ({r['fps']:.1f}/s), "
              f"{r['skipped']} older frames skipped")
        print(f"   infer ms  p50 {r['infer_ms']['p50']:7.1f}  p90 {r['infer_ms']['p90']:7.1f}  "
              f"p99 {r['infer_ms']['p99']:7.1f}  max {r['infer_ms']['max']:7.1f}")
        print(f"   e2e ms    p50 {r['e2e_ms']['p50']:7.1f}  p90 {r['e2e_ms']['p90']:7.1f}  "
              f"p99 {r['e2e_ms']['p99']:7.1f}  max {r['e2e_ms']['max']:7.1f}  "
              f"over {a.deadline:g} ms: {100 * r['over_deadline']:.1f}%")
        print(text_histogram(r))
    print()
    print(markdown(runs, a.deadline))
    if a.md:
        with open(a.md, "w") as f:
            f.write(markdown(runs, a.deadline))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"deadline_ms": a.deadline, "runs": runs}, f, indent=1)
    return 0 if runs else 1


if __name__ == "__main__":
    sys.exit(main())
