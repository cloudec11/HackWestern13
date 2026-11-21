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
sigma = MOOSE sigma x params.NOISE_SCALE, a fixed loss rate and log-normal
uncertainty on the starting count.

    python -m ground.backtest --calibrate          # tune on <=2004, test on 2005+ (how NOISE_SCALE was set)

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
                  k_factor: tuple[float, float] = params.K_FACTOR, sigma: float | None = None) -> dict:
    n0 = counts[year]
    inp = Inputs(n0=n0, n0_ci=(n0 * SURVEY_CI[0], n0 * SURVEY_CI[1]), r_g=MOOSE["r_g"],
                 sigma=MOOSE["sigma"] * params.NOISE_SCALE if sigma is None else sigma, loss_rate=MOOSE["loss_rate"],
                 k_factor=k_factor, years=horizon, threshold=0)
    return run_forecast(inp, f"backtest/{year}")


def rolling(counts: dict[int, int], horizon: int, k_factor=params.K_FACTOR, sigma: float | None = None,
            first: int | None = None, last_target: int | None = None) -> dict:
    """Forecast from every start year; collect errors by horizon (1..horizon years ahead).

    first: only start years >= first.  last_target: only compare against years <= last_target
    (so calibration never sees the test years)."""
    by_h = {h: {"model": [], "naive": [], "inside": [], "bias": [], "iscore": []} for h in range(1, horizon + 1)}
    starts = []
    for y in sorted(counts):
        if first is not None and y < first:
            continue
        future = [h for h in range(1, horizon + 1) if y + h in counts
                  and (last_target is None or y + h <= last_target)]
        if not future:
            continue
        starts.append(y)
        fc = forecast_from(counts, y, horizon, k_factor, sigma)
        for h in future:
            actual = counts[y + h]
            pred = fc["p50"][h]
            b = by_h[h]
            b["model"].append(abs(pred - actual) / actual)
            b["naive"].append(abs(counts[y] - actual) / actual)
            b["bias"].append((pred - actual) / actual)
            b["inside"].append(fc["p5"][h] <= actual <= fc["p95"][h])
            b["iscore"].append(interval_score(fc["p5"][h], fc["p95"][h], actual) / actual)
    return {"starts": starts, "by_h": by_h}


def interval_score(lo: float, hi: float, y: float, alpha: float = 0.10) -> float:
    """Gneiting & Raftery (2007) interval score for a (1-alpha) band: its width, plus a
    penalty of 2/alpha times the distance by which the real value falls outside it.
    Lower is better. It rewards bands that are narrow AND actually contain the truth."""
    return (hi - lo) + (2 / alpha) * max(0.0, lo - y) + (2 / alpha) * max(0.0, y - hi)


def summarise(res: dict) -> dict:
    allm = [e for b in res["by_h"].values() for e in b["model"]]
    alln = [e for b in res["by_h"].values() for e in b["naive"]]
    alli = [x for b in res["by_h"].values() for x in b["inside"]]
    alls = [x for b in res["by_h"].values() for x in b["iscore"]]
    return {"n": len(allm), "model_mape": statistics.median(allm), "naive_mape": statistics.median(alln),
            "coverage": sum(alli) / len(alli), "iscore": statistics.mean(alls)}


# ---- Calibration: tune on early years, judge on later years only ----

TRAIN_LAST = 2004        # calibration may only look at counts up to this year
TEST_FIRST = 2005        # test forecasts start here, with settings frozen
K_GRID = [(0.9, 1.6), (0.8, 2.0), (0.7, 2.5), (0.6, 3.0), (0.5, 4.0)]
SIGMA_GRID = [0.10, 0.15, 0.20, 0.30, 0.40]


def calibrate(counts: dict[int, int], horizon: int = 5) -> list[dict]:
    """Score every (K range, sigma) pair on the training years; best (lowest interval score) first."""
    rows = []
    for k in K_GRID:
        for s in SIGMA_GRID:
            m = summarise(rolling(counts, horizon, k, s, last_target=TRAIN_LAST))
            rows.append({"k_factor": k, "sigma": s, **m})
    return sorted(rows, key=lambda r: r["iscore"])


def report_calibration(counts: dict[int, int], horizon: int = 5) -> dict:
    rows = calibrate(counts, horizon)
    print(f"CALIBRATION on start years up to {TRAIN_LAST - 1}, compared only with counts up to {TRAIN_LAST}")
    print(f"{'K factor':>11} {'sigma':>6} {'interval score':>15} {'median error':>13} {'in 90% band':>12}")
    for r in rows[:6]:
        print(f"{str(r['k_factor']):>11} {r['sigma']:>6} {r['iscore']:>15.3f} {_pct(r['model_mape']):>13} {_pct(r['coverage']):>12}")
    cur = next(r for r in rows if r["k_factor"] == params.K_FACTOR and r["sigma"] == MOOSE["sigma"])  # before NOISE_SCALE
    print(f"{str(cur['k_factor']):>11} {cur['sigma']:>6} {cur['iscore']:>15.3f} {_pct(cur['model_mape']):>13} "
          f"{_pct(cur['coverage']):>12}   <- original settings (before calibration)")
    best = rows[0]
    print(f"\nTEST on start years {TEST_FIRST}+ (never used for tuning), every horizon 1-{horizon} pooled:")
    print(f"{'settings':>26} {'n':>3} {'interval score':>15} {'median error':>13} {'no-change error':>16} {'in 90% band':>12}")
    out = {}
    for label, k, s in (("original", params.K_FACTOR, MOOSE["sigma"]), ("calibrated", best["k_factor"], best["sigma"])):
        m = summarise(rolling(counts, horizon, k, s, first=TEST_FIRST))
        out[label] = {"k_factor": k, "sigma": s, **m}
        print(f"{label + ' ' + str(k) + ' s=' + str(s):>26} {m['n']:>3} {m['iscore']:>15.3f} {_pct(m['model_mape']):>13} "
              f"{_pct(m['naive_mape']):>16} {_pct(m['coverage']):>12}")
    return out


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
    ap.add_argument("--calibrate", action="store_true",
                    help=f"tune K range and sigma on years <= {TRAIN_LAST}, then test on {TEST_FIRST}+")
    a = ap.parse_args(argv)
    counts = load()
    if a.calibrate:
        report_calibration(counts, a.horizon)
    elif a.start:
        report_one(counts, a.start, a.horizon)
    else:
        report_rolling(counts, a.horizon)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
