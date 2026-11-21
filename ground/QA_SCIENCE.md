# Judges' Q&A: the science (agent-3, P5-3)

These are answers for the questions judges are likely to ask about the data, the ecology and the forecast. Each one has a **short answer you can say out loud**, the **detail if they push**, and **where the evidence is**. Every number below comes from a run logged in `logs/` or written up in `ground/README.md`. Re-run before quoting it if the code has changed since.

QNX and real-time questions belong to agent-1's half of P5-3. Facts gathered from the logs for that half are at the bottom.

**The one-line story:** the AI runs on the Pod; everything after it is published statistics. We tested that statistics against real animal counts and changed it based on what we found.

---

## 1. "Where's the AI?"

**Say:** On the Pod. An EfficientDet-Lite0 detector runs in TensorFlow Lite from QNX's open-source qnx-ports, on the Pi's CPU, on every camera frame. It turns video into one record per animal visit, so no video ever leaves the device.

**If pushed:** The population maths afterwards is deliberately *not* machine learning. It's published ecology, so it can be explained and checked. That's a strength, not a gap.

**Evidence:** agent-2's P0-6 and P1-2 log entries.

## 2. "How do you get from counts to a population? The camera can't tell animals apart."

**Say:** We use the Random Encounter Model (Rowcliffe et al., 2008, *Journal of Applied Ecology*), a standard method for camera traps when individuals can't be recognised. Animals that move around cross the camera's view at a rate proportional to how many live there. So from the visit rate, the animals' daily travel distance and the camera's detection zone, you get animals per km².

**If pushed:**
- The formula is `D = (visits / camera-days) × π / (v × r × (2 + θ))`.
- "Camera-days" counts only time we can prove the camera was running, from its regular heartbeats and visits. A gap of more than 30 minutes counts as an outage, not as "saw nothing".
- The 90% interval comes from resampling days.
- On fake data with known answers, the 90% interval contained the truth in **18 of 20** independent tests, with **8%** mean error.

**Evidence:** `ground/density.py`, `ground/tests/test_density.py`.

## 3. "Are those parameters real?"

**Say:** No, and the dashboard says so. Travel distances, camera range, growth rates, costs and effects are all **illustrative**, and every API result carries `"illustrative": true` plus a per-field `source`. Only the visit counts and camera time come from the Pod. A real deployment would take those parameters from ecologists, and would measure the camera's range by walking a target away from it.

**Don't say:** "from the literature". We haven't checked these values against published studies.

## 4. "Does your forecast actually work?"

This is the strongest answer we have, because we tested the forecast against real data and published the bad result too.

**Say:** We back-tested it on real Isle Royale moose counts from 1980 to 2026 (U.S. National Park Service). We forecast from every year and compared against what actually happened. At first it was **no better than assuming nothing changes**, and its 90% band only held the truth about half the time. So we calibrated it on 1980–2004 only and tested on 2005–2026, which it never saw. Now the 90% band holds the real count **94%** of the time, up from **58%**. Its single best-guess line still isn't better than "no change", and we say so.

**If pushed:**
- Held-out results: interval score 2.40 → 1.56 (lower is better). Median error 31.1%, against 29.7% for "no change".
- The settings were fixed before testing, and a test fails if anyone retunes them.
- From 2010 (510 moose), it predicts about 500 a year; the real count reached 1,250 by 2015 as the wolves collapsed from 19 to 3. That's exactly what a single-species model can't see.

**The honest framing:** the forecast tells you *how uncertain* the future is. It doesn't *predict* the path. That's what the fan chart is for.

**Evidence:** `python -m ground.backtest --calibrate`, `ground/README.md` (Backtest section).

## 5. "Why only one test population?"

**Say:** It's the best public, year-by-year series we could get in the time. One population is a real limitation: the held-out test is 80 overlapping comparisons from 19 start years. More series, for example the Global Population Dynamics Database, would be the next step.

## 6. "How does the budget advice work? Can I trust it?"

**Say:** For each zone, we forecast every combination of patrols, habitat restoration and more Pods, with the same random years so only the action differs. Then we spend greedily on the biggest cut in extinction risk per $1,000.

**If pushed:**
- Risk is the expected number of a zone's species that fall below 50 animals within 10 years.
- With $50k on our demo data, it picks patrols in the high-threat zone first, and species at risk fall from **4.28 to 3.60**.
- The slow part is cached, so the slider answers in about **10–30 ms**.
- Trust it as a **comparison of options inside one model**, not a promise of outcomes: the costs and effects are illustrative.

**Evidence:** `ground/allocate.py`, `ground/README.md`.

## 7. "Why do more Pods barely help in your budget table?"

**Say:** That's a real finding, not a bug. After calibrating on real data, most 10-year uncertainty comes from good and bad years, not from how precisely we counted today. Extra cameras narrow the forecast by under 1%. Their real value is better information for future decisions, which a risk score doesn't capture.

## 8. "What about birds?"

**Say:** The detector sees them and they appear in the live feed, but we don't estimate their population. The Random Encounter Model assumes animals moving on the ground. Birds come back as "not applicable", and the dashboard says why.

## 9. "What about people in the frame?"

**Say:** The label table has no person class at all. Person detections are dropped on the device before anything is stored or sent, and the server rejects any event labelled `person`.

**If pushed:** agent-2's still-image check, a photo of a person, produced 0 detections. The contract and the server both refuse `person`.

**Evidence:** `models/labels.csv`, `shared/event_schema.json`, `ground/tests/test_api.py`.

## 10. "The plush toys: is that cheating?"

**Say:** It's a stand-in for the demo. COCO models detect plush bears as "teddy bear", so the label table marks `teddy_bear` as demo-only. It shows up in the live habitat view, but never enters the population estimate or forecast.

## 11. "How much bandwidth does this need? Could it work off-grid?"

**Say:** Very little. A visit event is about **160 bytes** of JSON (measured), so a busy day of about 30 visits is around **5 KB**. Batches of 50 are about 8 KB. That's small enough for LoRa or satellite IoT links (pitch only; we haven't built that).

**If pushed:** store-and-forward means a dropped link only delays data and never loses it. The server only confirms events once it holds every earlier one.

## 12. "What happens if the network or the server goes down?"

**Say:** Nothing is lost. The Pod keeps logging to its SD card and resends everything after the last event the server confirmed. Resends are harmless, because the server ignores duplicates.

**If pushed:** agent-1 tested this for real: they stopped the server while 25 more events were logged on the Pi. After a restart, all 45 were confirmed within about 2 s, with **0 duplicates and 0 lost**.

## 13. "Isn't this all fake data?"

**Say:**
- **Real:** the Pod pipeline runs on real camera frames, and real events have flowed from the Pi to the ground station.
- **Generated:** the multi-week history behind the forecast demo comes from a generator that sends exactly what the Pod sends. A hackathon can't wait a month for real visits.
- **Labelled:** every generated number is marked illustrative.

---

## Don't say

- "Zero delay" or "real-time AI ecology". Say **"bounded, measured latency"** and **"illustrative parameters"**.
- "Our forecast predicts…". Say **"our forecast shows the range of likely outcomes"**.
- "Parameters from the literature".
- "More Pods reduce extinction risk".

---

## Facts for agent-1's QNX half (from the logs; agent-1 to verify and own)

| Claim | Number | Source |
|---|---|---|
| AI module from QNX | TensorFlow Lite 2.16.1 from qnx-ports, XNNPACK, built natively on the Pi | agent-2, P0-6 |
| Model | EfficientDet-Lite0 int8, 3 threads (4 threads was slower: 288 ms) | agent-2, P0-6 / P1-2 |
| Inference time, live camera | p50 67–68 ms, p99 68.5–72.7 ms | agent-2, P1-2 and live end-to-end |
| Capture to detection (end to end) | p50 88–94 ms, p99 130–134 ms | same |
| Detection rate | 14.5–14.7 frames/s (camera ~15–23 fps; newest frame wins, older ones skipped) | same |
| Supervisor restart | `kill -9` on pod-capture → restarted in about 1 s, frames resumed | agent-1, P3-1 |
| Priorities | SCHED_FIFO: supervisor 40, capture 30, log 20 | agent-1, P3-1 |
| Crash-safe log | `kill -9` plus a half-written line → recovered, seq continued | agent-1, P2-2 |
| Network outage | server down, 25 events queued → 45 acked in ~2 s, 0 lost, 0 duplicates | agent-1, P2-3 |
| Starts on power-up | verified with a real reboot | agent-1 |

**Not measured yet (don't claim):**
- The CPU-hog stress test (P3-2). Our "latency barely moves under load" claim has no numbers yet, and it's central to the QNX pitch.
- The ground station's endpoints timed on the Pi.

**Known gotchas:**
- The Pi has no battery-backed clock. It boots in 1970 until NTP syncs, and events from before the sync are rejected.
- Never wipe the Pod's data without also starting a fresh ground database (or a new pod_id). Otherwise new events are ignored as duplicates.
