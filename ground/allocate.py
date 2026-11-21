"""Budget allocation: where does money cut extinction risk the most? (agent-3, P3-4)

Standard library only, so it runs on the QNX Pi as well as the laptop.

1. Actions (params.INTERVENTIONS) each change one input of the forecast:
   patrols lower the loss rate, habitat restoration raises carrying capacity,
   and more Pods tighten the starting-population estimate.
2. For every zone, forecast every combination of actions (3 actions -> 8
   scenarios). "Risk" for a zone is the expected number of its species that
   fall below the threshold within 10 years: the sum of their p_below_threshold.
   All scenarios for a species share one random seed (common random numbers),
   so differences come from the action, not from luck.
3. Greedy: repeatedly buy the affordable action with the most risk reduction
   per $1,000, given what that zone already has, until nothing affordable
   still helps by at least params.MIN_GAIN (smaller gains are Monte Carlo noise). Simple, explainable, and easy to defend in Q&A.

Step 2 is the slow part, and it depends only on the data, not the budget. So
the API caches it, and moving the budget slider only reruns step 3 (instant).

The costs and effects are illustrative, and the forecast itself is only
calibrated for honest uncertainty, not accurate central values (see the
backtest in ground/README.md). So read the output as a comparison of options
inside one model, not as a promise of outcomes.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import combinations

from ground import params
from ground.simulate import Inputs, inputs_for, run_forecast

ACTIONS = tuple(params.INTERVENTIONS)


def apply_actions(inp: Inputs, actions: frozenset[str]) -> Inputs:
    out = inp
    for a in sorted(actions):
        eff = params.INTERVENTIONS[a]
        if "loss_mult" in eff:
            out = replace(out, loss_rate=out.loss_rate * eff["loss_mult"])
        if "k_mult" in eff:
            out = replace(out, k_factor=(out.k_factor[0] * eff["k_mult"], out.k_factor[1] * eff["k_mult"]))
        if "ci_shrink" in eff and out.n0_ci:
            s, n0 = eff["ci_shrink"], out.n0
            lo, hi = out.n0_ci
            out = replace(out, n0_ci=(n0 * (lo / n0) ** s, n0 * (hi / n0) ** s))
    return out


def scenario_table(density_results: list[dict], threshold: float | None = None) -> dict:
    """{zone_id: {"species": [...], "risk": {frozenset(actions): {species: (p_below, band_width_y10)}}}}.
    Only species that can be forecast are included."""
    table: dict[str, dict] = {}
    for d in density_results:
        inp = inputs_for(d, threshold)
        if inp is None:
            continue
        z = table.setdefault(d["zone_id"], {"species": [], "risk": {}})
        z["species"].append(d["species"])
        for k in range(len(ACTIONS) + 1):
            for combo in combinations(ACTIONS, k):
                acts = frozenset(combo)
                fc = run_forecast(apply_actions(inp, acts), f"{d['zone_id']}/{d['species']}")
                z["risk"].setdefault(acts, {})[d["species"]] = (fc["p_below_threshold"], fc["p95"][-1] - fc["p5"][-1])
    return table


def _zone_risk(z: dict, acts: frozenset[str]) -> float:
    return sum(p for p, _ in z["risk"][acts].values())


def allocate(table: dict, budget: float) -> dict:
    """Greedy allocation over a precomputed scenario table. Fast: no forecasting here."""
    applied = {zone: frozenset() for zone in table}
    remaining = float(budget)
    rows = []
    while True:
        best = None
        for zone, z in table.items():
            have = applied[zone]
            before = _zone_risk(z, have)
            for a in ACTIONS:
                if a in have:
                    continue
                cost = params.INTERVENTIONS[a]["cost"]
                if cost > remaining:
                    continue
                after = _zone_risk(z, have | {a})
                gain = before - after
                if gain < params.MIN_GAIN:       # below Monte Carlo noise: not worth buying
                    continue
                score = gain / cost * 1000
                if best is None or score > best[0] + 1e-12:
                    best = (score, zone, a, before, after, cost)
        if best is None:
            break
        score, zone, a, before, after, cost = best
        z = table[zone]
        have = applied[zone]
        rows.append({
            "step": len(rows) + 1, "zone_id": zone, "intervention": a,
            "label": params.INTERVENTIONS[a]["label"], "cost": cost,
            "risk_before": round(before, 3), "risk_after": round(after, 3),
            "risk_reduction_per_1k": round(score, 5),
            "by_species": {sp: {"p_below_before": z["risk"][have][sp][0],
                                "p_below_after": z["risk"][have | {a}][sp][0]}
                           for sp in z["species"]},
        })
        applied[zone] = have | {a}
        remaining -= cost
    spent = float(budget) - remaining
    total_before = sum(_zone_risk(z, frozenset()) for z in table.values())
    total_after = sum(_zone_risk(z, applied[zone]) for zone, z in table.items())
    return {
        "budget": budget, "spent": spent, "unallocated": remaining,
        "species_at_risk_before": round(total_before, 3),
        "species_at_risk_after": round(total_after, 3),
        "rows": rows,
        "illustrative": True,
        "note": ("risk = expected number of a zone's species that fall below the threshold within "
                 f"{params.FORECAST_YEARS} years (sum of their probabilities). Costs and effects are illustrative; "
                 "compare options, don't read outcomes as promises."),
        "actions": {k: {"label": v["label"], "cost": v["cost"], "source": params.INTERVENTION_SOURCE}
                    for k, v in params.INTERVENTIONS.items()},
    }


def options(table: dict) -> list[dict]:
    """Every single action on its own, from no action, ranked by value for money (for a 'why' view)."""
    out = []
    for zone, z in table.items():
        base = _zone_risk(z, frozenset())
        for a in ACTIONS:
            after = _zone_risk(z, frozenset({a}))
            cost = params.INTERVENTIONS[a]["cost"]
            w0 = sum(w for _, w in z["risk"][frozenset()].values())
            w1 = sum(w for _, w in z["risk"][frozenset({a})].values())
            out.append({"zone_id": zone, "intervention": a, "cost": cost,
                        "risk_before": round(base, 3), "risk_after": round(after, 3),
                        "risk_reduction_per_1k": round((base - after) / cost * 1000, 5),
                        "band_width_change": round((w1 - w0) / w0, 3) if w0 else None})
    return sorted(out, key=lambda r: -r["risk_reduction_per_1k"])
