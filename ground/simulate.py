"""10-year population forecast: stochastic Ricker model, Monte Carlo (agent-3, P2-5).

Standard library only, so it runs on the QNX Pi as well as the laptop.

Each run steps a population forward one year at a time:

    N[t+1] = N[t] * exp( r_g * (1 - N[t] / K) + e[t] ) - loss_rate * N[t]
    e[t] ~ Normal(0, sigma)                   good and bad years

Each of the FORECAST_RUNS runs also draws its OWN uncertain inputs (agreed with
agent-4 under Q2, overfitting). That way the fan chart shows how unsure we are
about the parameters, not just the weather:

    N[0]       from the Pod's density estimate and its 90% interval (log-normal)
    r_g        uniform over the species' range           (params.GROWTH, illustrative)
    K          today's estimate x uniform(K_FACTOR)       (assumes habitat near capacity)

Nothing is fitted to our few days of data. The Pod only supplies N[0].

Outputs per zone and species: the 5/25/50/75/95th percentiles for each year
0..FORECAST_YEARS (the fan chart), and p_below_threshold, the share of runs
that drop below QUASI_EXTINCTION animals at any point in years 1..FORECAST_YEARS.
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, replace

from ground import params

PCTS = (5, 25, 50, 75, 95)
Z90 = 1.6449   # standard normal quantile for a 90% two-sided interval


@dataclass(frozen=True)
class Inputs:
    """Everything one forecast needs. allocate.py (P3-4) changes these to model actions."""
    n0: float                       # point estimate of today's population
    n0_ci: tuple[float, float] | None
    r_g: tuple[float, float]
    sigma: float
    loss_rate: float                # already multiplied by the zone's threat level
    k_factor: tuple[float, float] = params.K_FACTOR
    years: int = params.FORECAST_YEARS
    runs: int = params.FORECAST_RUNS
    threshold: float = params.QUASI_EXTINCTION


def _draw_n0(rng: random.Random, n0: float, ci: tuple[float, float] | None) -> float:
    """Log-normal matched to the density interval (populations can't go negative)."""
    if not ci or ci[0] <= 0 or ci[1] <= ci[0]:
        return n0
    mu = math.log(n0)
    sd = (math.log(ci[1]) - math.log(ci[0])) / (2 * Z90)
    return math.exp(rng.gauss(mu, sd))


def run_forecast(inp: Inputs, seed: str) -> dict:
    """Monte Carlo forecast. Same inputs and seed -> same numbers, so the dashboard doesn't flicker."""
    rng = random.Random(zlib.crc32(seed.encode()))
    paths = []
    below = 0
    for _ in range(inp.runs):
        n = _draw_n0(rng, inp.n0, inp.n0_ci)
        r = rng.uniform(*inp.r_g)
        k = max(1.0, inp.n0 * rng.uniform(*inp.k_factor))
        path = [n]
        hit = False
        for _ in range(inp.years):
            if n > 0:
                n = n * math.exp(r * (1 - n / k) + rng.gauss(0, inp.sigma)) - inp.loss_rate * n
                if n < 1:
                    n = 0.0          # fewer than one animal left: gone
            path.append(n)
            hit = hit or n < inp.threshold
        below += hit
        paths.append(path)
    bands = {f"p{p}": [] for p in PCTS}
    for t in range(inp.years + 1):
        col = sorted(path[t] for path in paths)
        for p in PCTS:
            bands[f"p{p}"].append(round(_percentile(col, p)))
    return {"years": list(range(inp.years + 1)), **bands,
            "p_below_threshold": round(below / inp.runs, 3), "n_runs": inp.runs}


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolated percentile of an already sorted list."""
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def inputs_for(density_result: dict, threshold: float | None = None) -> Inputs | None:
    """Forecast inputs for one /api/density result, or None if it can't be forecast."""
    sp = params.GROWTH.get(density_result["species"])
    if sp is None or density_result.get("n0") in (None, 0):
        return None
    threat = params.ZONE_THREAT.get(density_result["zone_id"], 1.0)
    ci = density_result.get("n0_ci")
    inp = Inputs(n0=float(density_result["n0"]), n0_ci=tuple(ci) if ci else None,
                 r_g=sp["r_g"], sigma=sp["sigma"], loss_rate=sp["loss_rate"] * threat)
    return replace(inp, threshold=threshold) if threshold is not None else inp


def forecast_result(density_result: dict, threshold: float | None = None) -> dict:
    """One /api/simulation result in the shape agreed with agent-4 (ground/README.md)."""
    zone, species = density_result["zone_id"], density_result["species"]
    out = {"zone_id": zone, "species": species, "illustrative": True}
    inp = inputs_for(density_result, threshold)
    if inp is None:
        reason = (density_result.get("note")
                  or ("no growth parameters for this species" if species not in params.GROWTH
                      else "no population estimate yet"))
        flag = "not_applicable" if (density_result.get("not_applicable")
                                    or species not in params.GROWTH) else "insufficient_data"
        out.update({"years": [], **{f"p{p}": [] for p in PCTS}, "threshold": threshold or params.QUASI_EXTINCTION,
                    "p_below_threshold": None, "n_runs": 0, flag: True, "note": reason})
        return out
    fc = run_forecast(inp, f"{zone}/{species}")
    k = [round(inp.n0 * f) for f in inp.k_factor]
    out.update(fc)
    out.update({
        "threshold": inp.threshold,
        "starts_below_threshold": inp.n0 < inp.threshold,
        "params": {
            "n0": inp.n0, "n0_ci": list(inp.n0_ci) if inp.n0_ci else None,
            "r_g": list(inp.r_g), "K": k, "sigma": inp.sigma,
            "loss_rate": round(inp.loss_rate, 4),
            "H": round(inp.loss_rate * inp.n0, 1),       # expected animals lost in year 1
            "zone_threat": params.ZONE_THREAT.get(zone, 1.0),
            "source": {"n0": "pod", "r_g": params.GROWTH_SOURCE,
                       "K": "illustrative: assumes habitat near capacity today",
                       "sigma": params.GROWTH_SOURCE, "H": params.GROWTH_SOURCE},
        },
    })
    return out


def simulation_results(density_results: list[dict], threshold: float | None = None) -> list[dict]:
    return [forecast_result(d, threshold) for d in density_results]
