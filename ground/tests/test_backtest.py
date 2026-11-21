"""The backtest runs on the real Isle Royale data (it measures accuracy; it doesn't assert it)."""
from ground.backtest import MOOSE, forecast_from, load, rolling


def test_data_loads():
    c = load()
    assert c[1980] == 664 and c[2010] == 510 and c[2015] == 1250 and c[2026] == 524
    assert 2021 not in c and 2025 not in c and len(c) == 45


def test_forecast_and_rolling_run():
    c = load()
    fc = forecast_from(c, 2010, 5)
    assert len(fc["p50"]) == 6 and fc["p5"][0] <= 510 <= fc["p95"][0]
    res = rolling(c, 5)
    assert len(res["starts"]) == 44 and len(res["by_h"][1]["model"]) == 42


def test_settings_are_fixed_up_front():
    # guard against quietly tuning the moose settings to the test data
    assert MOOSE == {"r_g": (0.15, 0.30), "sigma": 0.15, "loss_rate": 0.04}
