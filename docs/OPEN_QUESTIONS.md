# Open questions (raised by agent-4, 2026-11-21)

Four concerns for the team. Each one has a short answer, a recommendation, and who has to act.
Owner names refer to the lanes in `TASKS.md`. Nothing here changes a task or a contract by itself.
Changes to scope need the humans to agree.

---

## Q1. How do we take the simulation math and display it?

**Owners:** agent-3 (produces the numbers), agent-4 (draws them).

The math lives in agent-3's files (`ground/density.py`, `ground/simulate.py`, `ground/allocate.py`).
The dashboard (`ground/dashboard.py`) should only draw finished numbers. It never re-does the math.
That means agent-3 and agent-4 need a small agreed data shape for each result.

| Result | Math (agent-3) | Dashboard panel (agent-4) |
|--------|----------------|---------------------------|
| Density | Random Encounter Model on visit events → animals per km² with a confidence interval | Number tile with the interval, e.g. "4.2 /km² (2.9–6.1)", plus a per-species bar chart |
| Population forecast | Ricker Monte Carlo: `N[t+1] = N[t] · exp(r · (1 − N[t]/K) + noise)`, run ~1,000 times | **Fan chart**: median line with shaded 50% and 90% bands per year, and a line marking the extinction/threshold level |
| Budget allocation | `allocate.py` over actions/species | Table plus a **budget slider**. Moving the slider re-asks agent-3 for a new allocation |

Proposed shapes (agent-3 to confirm or change). These go through `ground/api.py`, not `shared/`:

```
GET /api/density      -> {"species": "deer", "density_per_km2": 4.2, "ci_low": 2.9, "ci_high": 6.1, "n_events": 37, "hours": 6.5}
GET /api/simulation   -> {"species": "deer", "years": [0,1,...,20],
                          "p5": [...], "p25": [...], "p50": [...], "p75": [...], "p95": [...],
                          "p_below_threshold": 0.12, "n_runs": 1000, "params": {"r": 0.4, "K": 120, "source": "literature"}}
GET /api/allocate?budget=50000 -> {"budget": 50000, "rows": [{"action": "...", "species": "...", "spend": 20000, "expected_gain": 0.08}]}
```

Why percentiles and not 1,000 raw runs: the fan chart only needs five lines per year, so the payload stays tiny and the slider stays responsive.

**Until agent-3's endpoints exist**, agent-4 builds the panels against a local fake file with the same shape (`ground/dashboard/fake_sim.json`).

---

## Q2. How do we stop the models from overfitting?

**Owners:** agent-2 (detector model), agent-3 (simulation).

The "model" here is really two separate things. They have different risks.

### 2a. The detector (TFLite on QNX, agent-2)

- The Pod only runs inference with the TFLite model. It does **not** learn or update weights while running, so it cannot overfit on the device during the demo.
- Overfitting only matters if we fine-tune (optional task P3-6). If we do:
  - Start from a pretrained model and freeze most of it. Train only the last layers.
  - **Split train and test by video clip or location, never by random frame.** Neighbouring frames of one clip are nearly identical, so a random split leaks test data into training and makes the accuracy look fake-good.
  - Use augmentation (flips, brightness, blur, crops), early stopping on the validation loss, and a small learning rate.
  - Test on clips the model has never seen, including the tablet clips we will actually show at the demo.
- The safest choice for a 36-hour build is to not fine-tune at all. Use a stock pretrained model and filter it to the animal classes.

### 2b. The simulation (agent-3)

- The Ricker model is not a learned dot product. It is a two-parameter formula: growth rate `r` and carrying capacity `K`.
  The only learning is estimating those parameters from data.
- The real risk is the opposite of a big model: **far too little data.** A few hours of events from one camera cannot pin down `r` and `K`.
  Fitting them to that would overfit noise.
- Recommended:
  - Take `r` and `K` ranges from the literature for each species (priors).
  - Use our measured density only as the starting population `N[0]`.
  - Run the Monte Carlo over **parameter uncertainty as well as yearly noise**, so the fan chart widens honestly.
  - Keep the model to two parameters. Do not add features just because we can.
  - Show `params.source` ("literature" vs "fitted") on the dashboard, and say plainly in the pitch that the forecast is illustrative.
- If anyone does fit a linear model (weights · features), use ridge (L2) regularization. Validate on a later time window, never on shuffled rows.

---

## Q3. Should the Pod have more hardware sensors?

**Owners:** humans decide scope; agent-1 would own any new driver.

The camera already answers the core question ("what animal, when"). Extra sensors only help if they add context the dashboard can show.
Ranked by value for effort:

| Sensor | What it adds | Effort / risk |
|--------|--------------|---------------|
| PIR motion sensor | Wakes or flags the camera; cross-checks detections ("motion but no animal = false trigger") | Low, one GPIO pin |
| BME280 temp / humidity / pressure (I²C) | Weather context per visit ("deer visit more when it's cool") | Low on Linux; **check QNX I²C support on the Pi 5 first** |
| Light sensor | Day/night label per visit | Low |
| GPS | Already planned for mobile mode (P4-2) | Medium |
| Microphone (bird calls) | A second detector | High: new model and pipeline. Skip |

**Recommendation:** add at most one cheap sensor (PIR or BME280), and only after Gate 2 passes.
If QNX GPIO/I²C on the Pi 5 turns out to be painful, wire the sensor to a microcontroller (see Q4) and send readings over Wi-Fi.
Any new field in events or heartbeats needs a contract change (`AGENTS.md` section 6).

---

## Q4. Can an ESP32 act as the receiver for the NESDR Smartee radio?

**Owners:** humans decide scope; nobody owns this yet.

**Short answer: an ESP32 is the wrong tool for this. Plug the SDR into the laptop instead.**

Why the ESP32 does not work well:

- The NESDR Smartee is an RTL-SDR (RTL2832U chip). It does no decoding. It streams raw radio samples, about 2–5 MB/s, and a computer does all the demodulation in software.
- The classic ESP32 has no USB host port at all. The ESP32-S2/S3 have USB host, but only full-speed (12 Mbit/s). There is also no maintained RTL-SDR driver for it; we would be porting `librtlsdr` during a hackathon.
- The ESP32 has about 0.5 MB of RAM, far too little to decode weather-satellite images.
- Even if it worked, the ESP32 would just forward raw samples over Wi-Fi to a real computer. That is the same as plugging the SDR into that computer directly.

Better options, best first:

1. **Plug the SDR into the laptop (ground station). Recommended.** Area weather is a site-level fact, not a per-camera fact, so it belongs at the ground station anyway.
   It avoids QNX completely. Linux/Windows tools work out of the box, and the dashboard can show the result.
2. **A cheap Linux board** (Pi Zero 2 W or an old Pi) running `rtl_tcp` or a decoder, sending results over Wi-Fi. Use this only if the radio must sit out in the field.
3. **Try it on the QNX Pi.** Agent-1's log says the Pi already detects the "Nooelec RTL-SDR" over USB, so it is recognised. Getting the software running on QNX is unknown work. Not worth it in 36 hours.

What the radio can realistically give us (check before committing time):

| Signal | Tool | Notes |
|--------|------|-------|
| Nearby home weather stations on 433/915 MHz (temperature, wind, rain) | `rtl_433` (outputs JSON) | **Easiest real win.** Works with the stock antenna, gives live local weather in minutes |
| Weatheradio Canada (162.400–162.550 MHz) | `multimon-ng` (SAME alert decoding) | Official weather alerts; voice broadcast plus alert codes |
| Weather-satellite images (Meteor-M LRPT, 137 MHz) | SatDump | Needs a proper 137 MHz antenna (V-dipole or QFH) and a satellite pass (a few short passes per day). The older NOAA APT satellites have reportedly been retired, so verify that any target is still transmitting. **High risk for a demo** |

**Recommendation:** treat this as an optional Phase 4 stretch. The cheapest version is `rtl_433` on the laptop feeding a small "local weather" tile on the dashboard (agent-4's lane).
For guaranteed weather data, Environment Canada's public data from the laptop is simpler still.
