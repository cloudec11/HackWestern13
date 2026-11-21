"""The backtest runs on the real Isle Royale data (it measures accuracy; it doesn't assert it)."""
import pytest

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


def test_calibration_never_sees_test_years():
    from ground.backtest import TEST_FIRST, TRAIN_LAST, rolling
    c = load()
    train = rolling(c, 5, last_target=TRAIN_LAST)
    assert max(train["starts"]) < TRAIN_LAST and TEST_FIRST > TRAIN_LAST
    test = rolling(c, 5, first=TEST_FIRST)
    assert min(test["starts"]) >= TEST_FIRST


def test_interval_score():
    from ground.backtest import interval_score
    assert interval_score(10, 20, 15) == 10                  # inside: just the width
    assert interval_score(10, 20, 25) == 10 + 20 * 5         # outside: width + 2/alpha * miss


def test_deployed_noise_scale_matches_calibration():
    from ground import params
    from ground.backtest import calibrate
    best = calibrate(load())[0]
    assert best["sigma"] == pytest.approx(MOOSE["sigma"] * params.NOISE_SCALE)
