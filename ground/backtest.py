"""Backtest the population forecast against real counts (agent-3).

Question: if we'd run ground/simulate.py on a real population in year Y, how
close would its forecast for years Y+1..Y+H have been to what actually happened?

Data: Isle Royale moose, 1980-2026, U.S. National Park Service (public domain),
ground/data/isle_royale_moose_wolves.csv.

Rules, so the test is honest:
  1. The moose settings below are FIXED UP FRONT from general moose biology and
     the same defaults every other species uses. They are not tuned to these
     years. Tuning them to the data would only measure how well we can memorise it.
  2. Every start year that has data after it is tested, not one hand-picked year.
  3. The forecast is compared with a "nothing changes" baseline (N stays at N[Y]).
     A forecast is only useful if it beats that.

The model is used exactly as deployed: run_forecast() with K = N[Y] x K_FACTOR,
a fixed loss rate and log-normal uncertainty on the starting count.

    python -m ground.backtest                      # rolling report, 5-year horizon
    python -m ground.backtest --start 2010         # one start year, year by year
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path

from ground import params
from ground.simulate import Inputs, run_forecast

DATA = Path(__file__).resolve().parent / "data" / "isle_royale_moose_wolves.csv"

# Fixed before looking at any forecast error (see rule 1).
MOOSE = {
    "r_g": (0.15, 0.30),     # moose can grow ~20-30 %/yr with room and few predators
    "sigma": 0.15,           # same noise as the other mid-sized grazers in params.GROWTH
    "loss_rate": 0.04,       # same default threat loss as zebra/giraffe
}
SURVEY_CI = (0.80, 1.25)     # assumed 90 % uncertainty of a winter aerial count, x N[Y]


def load(path: Path = DATA) -> dict[int, int]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    return {int(r["year"]): int(r["moose"]) for r in csv.DictReader(lines) if r["moose"]}


def forecast_from(counts: dict[int, int], year: int, horizon: int,
                  k_factor: tuple[float, float] = params.K_FACTOR) -> dict:
    n0 = counts[year]
    inp = Inputs(n0=n0, n0_ci=(n0 * SURVEY_CI[0], n0 * SURVEY_CI[1]), r_g=MOOSE["r_g"],
                 sigma=MOOSE["sigma"], loss_rate=MOOSE["loss_rate"], k_factor=k_factor,
                 years=horizon, threshold=0)
    return run_forecast(inp, f"backtest/{year}")


def rolling(counts: dict[int, int], horizon: int, k_factor=params.K_FACTOR) -> dict:
    """Forecast from every start year; collect errors by horizon (1..horizon years ahead)."""
    by_h = {h: {"model": [], "naive": [], "inside": [], "bias": []} for h in range(1, horizon + 1)}
    starts = []
    for y in sorted(counts):
        future = [h for h in range(1, horizon + 1) if y + h in counts]
        if not future:
            continue
        starts.append(y)
        fc = forecast_from(counts, y, horizon, k_factor)
        for h in future:
            actual = counts[y + h]
            pred = fc["p50"][h]
            b = by_h[h]
            b["model"].append(abs(pred - actual) / actual)
            b["naive"].append(abs(counts[y] - actual) / actual)
            b["bias"].append((pred - actual) / actual)
            b["inside"].append(fc["p5"][h] <= actual <= fc["p95"][h])
    return {"starts": starts, "by_h": by_h}


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def report_rolling(counts: dict[int, int], horizon: int, k_factor=params.K_FACTOR, title: str = "") -> dict:
    res = rolling(counts, horizon, k_factor)
    print(title or f"Rolling backtest: Isle Royale moose, every start year {res['starts'][0]}-{res['starts'][-1]} "
                   f"({len(res['starts'])} starts), K factor {k_factor}")
    print(f"{'years ahead':>11} {'n':>3} {'model error':>12} {'no-change error':>16} {'model better?':>14} "
          f"{'bias':>7} {'actual in 90% band':>19}")
    summary = {}
    for h, b in res["by_h"].items():
        if not b["model"]:
            continue
        m, n = statistics.median(b["model"]), statistics.median(b["naive"])
        cov = sum(b["inside"]) / len(b["inside"])
        bias = statistics.median(b["bias"])
        wins = sum(a < c for a, c in zip(b["model"], b["naive"])) / len(b["model"])
        summary[h] = {"n": len(b["model"]), "model_mape": m, "naive_mape": n, "coverage": cov,
                      "bias": bias, "win_rate": wins}
        print(f"{h:>11} {len(b['model']):>3} {_pct(m):>12} {_pct(n):>16} {('yes' if m < n else 'no') + f' ({100*wins:.0f}% of starts)':>14} "
              f"{_pct(bias):>7} {_pct(cov):>19}")
    print("(errors are median absolute % error; bias is median signed error, minus = forecast too low)")
    return summary


def report_one(counts: dict[int, int], start: int, horizon: int) -> None:
    fc = forecast_from(counts, start, horizon)
    print(f"Forecast from {start} (moose = {counts[start]}), compared with what happened:")
    print(f"{'year':>6} {'actual':>7} {'forecast p50':>13} {'90% band':>14} {'error':>8} {'in band':>8}")
    errs = []
    for h in range(1, horizon + 1):
        y = start + h
        if y not in counts:
            print(f"{y:>6} {'(none)':>7}")
            continue
        a, p = counts[y], fc["p50"][h]
        errs.append(abs(p - a) / a)
        band = f"{fc['p5'][h]}-{fc['p95'][h]}"
        print(f"{y:>6} {a:>7} {p:>13} {band:>14} {_pct((p - a) / a):>8} {'yes' if fc['p5'][h] <= a <= fc['p95'][h] else 'NO':>8}")
    if errs:
        print(f"mean absolute error {_pct(sum(errs) / len(errs))}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, help="show one start year in detail")
    ap.add_argument("--horizon", type=int, default=5)
    a = ap.parse_args(argv)
    counts = load()
    if a.start:
        report_one(counts, a.start, a.horizon)
    else:
        report_rolling(counts, a.horizon)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
