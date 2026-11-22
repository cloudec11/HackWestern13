# Agent 4 log

Append-only. Written by agent-4 only.

## 2026-11-21 15:00:45 | agent-4 | - | REVIEW
**What:** Raised 4 open questions: displaying simulation math, overfitting, extra sensors, NESDR SDR via ESP32
**Files:** `docs/OPEN_QUESTIONS.md`, `TASKS.md`
**Notes:** Proposed /api/density, /api/simulation (p5/p25/p50/p75/p95 per year), /api/allocate shapes for agent-3 to confirm. Recommends literature priors for Ricker r/K, clip-level splits for any fine-tuning, at most one extra sensor after Gate 2, SDR on laptop with rtl_433 rather than ESP32. Blockers rows added.

## 2026-11-21 15:40:08 | agent-4 | - | DOING
**What:** Habitat view: immersive 360-style three.js scene (biome, live weather, wandering plushie sprites) on fake data
**Files:** `ground/dashboard/sim/index.html`, `ground/dashboard/sim/sim.js`, `ground/dashboard/sim/fake_scene.json`, `ground/dashboard/sim/README.md`
**Notes:** Not a TASKS.md task yet (human decision). Run: python3 -m http.server 8080 --directory ground/dashboard/sim. Proposed scene JSON shape in the README; real source would be an agent-3 endpoint (/api/scene). Biome detection is not in the current plan (needs agent-2 or a per-Pod setting). Not yet tested in a browser.

## 2026-11-21 15:52:31 | agent-4 | - | DOING
**What:** Habitat view rewritten as plain HTML + JS (2D canvas, no libraries, works offline, no GPU) so it can run from/on the QNX Pi
**Files:** `ground/dashboard/sim/sim.js`, `ground/dashboard/sim/index.html`, `ground/dashboard/sim/README.md`
**Notes:** Dropped three.js/CDN. Classic script, ES2017-level syntax, auto-lowers resolution if slow, ?scale= to force. Smoke-tested in QuickJS with stubbed DOM across all biomes/weathers; NOT yet viewed in a real browser. Pi can serve it with python3 -m http.server; unknown whether the QNX image has a browser to display it locally.

## 2026-11-21 15:59:49 | agent-4 | - | DOING
**What:** Laptop detector: runs agent-2's TFLite model on webcam/video/photo and feeds live animals into the habitat view
**Files:** `demo/laptop_detector.py`, `demo/README.md`, `demo/requirements.txt`, `demo/.gitignore`, `ground/dashboard/sim/sim.js`, `ground/dashboard/sim/.gitignore`, `TASKS.md`
**Notes:** Testing stand-in, not pod-infer. EfficientDet-Lite0 needs anchor decode + NMS (anchor scale 3); verified vs SSD on COCO photos (2 cats, bear, empty room). ~7 ms/frame on laptop. Person dropped from scores before NMS; frames never saved. Habitat view gained ?refresh=N seconds. Handoff row for agent-2 in TASKS.md.

## 2026-11-21 16:20:57 | agent-4 | - | DOING
**What:** Laptop scanner: CLIP species recognition (53 animals, editable list), beep + cut-out + 'added to simulation' page, cut-outs walk in the habitat view
**Files:** `demo/laptop_detector.py`, `demo/scanner.html`, `demo/species.txt`, `demo/requirements.txt`, `demo/README.md`, `demo/.gitignore`, `ground/dashboard/sim/sim.js`, `ground/dashboard/sim/.gitignore`
**Notes:** COCO models know no deer/fox/raccoon etc., so EfficientDet now only finds regions (animals, teddy bears, phone/TV screens, books) and CLIP ViT-B/32 (ONNX, auto-downloaded to demo/models/) names the animal. Accept after 6/10 frames agree. Tested: deer, moose, fox, raccoon, owl, bear, cats, teddy all correct; empty room = nothing; video run adds deer, fox, deer(x2) with events. Laptop only, not for the Pi as-is.

## 2026-11-21 17:26:58 | agent-4 | - | DOING
**What:** Scanner accepts both EfficientDet files (raw MediaPipe build and agent-2's new post-processed one); dropped obsolete decode handoff row
**Files:** `demo/laptop_detector.py`, `demo/README.md`, `TASKS.md`
**Notes:** Detector picks raw vs post-processed outputs from tensor shapes. Retested with the current models/fetch_models.sh model: deer, fox, bear added, empty room rejected, video adds deer, fox, deer x2.

## 2026-11-21 18:15:44 | agent-4 | - | DOING
**What:** Habitat view charts panel: appearances over time (per species), time-of-day heat strip, most-seen bars, headline stats; scanner keeps live_history.json
**Files:** `ground/dashboard/sim/charts.js`, `ground/dashboard/sim/index.html`, `ground/dashboard/sim/sim.js`, `ground/dashboard/sim/README.md`, `ground/dashboard/sim/.gitignore`, `demo/laptop_detector.py`
**Notes:** History source: ?history=, else ?api= (/api/events, also /api/scene for the 3D view), else live_history.json from the scanner, else labelled fake. Plain JS+SVG, palette validated on the dark panel. Rendered via QuickJS+cairosvg and checked by eye; not yet opened in a real browser.

## 2026-11-22 00:06:06 | agent-4 | - | REVIEW
**What:** Prototype 1: CLIP animal identification inside pod-infer (QNX TFLite), from the laptop scanner; 53 animals, voting, full-res crops
**Files:** `pod/infer/clip.h`, `pod/infer/clip_test.cpp`, `pod/infer/main.cpp`, `pod/infer/CMakeLists.txt`, `pod/infer/README.md`, `models/labels.csv`, `models/clip/`, `models/clip_vision.tflite`, `pod/supervisor/pod.conf`, `pod/scripts/deploy.sh`, `demo/laptop_detector.py`
**Notes:** Human-approved cross-lane (agent-2 pod/infer + models, agent-1 pod.conf + deploy.sh). CLIP ViT-B/32 image tower converted with TF 2.16 to TFLite (builtin ops only), int8 weights 88.5 MB. Laptop checks: compiles vs TFLite 2.16 headers, clip_test passes, C++ YUYV crop + TFLite CLIP 9/9 photos right. NOT run on the Pi yet: needs deploy, build, and a CLIP speed check. Branch prototype-1, not main.

## 2026-11-22 02:59:02 | agent-4 | - | REVIEW
**What:** with-motor: pod-servo pans the webcam on a 180-degree servo, stops while an animal is seen, resumes after capture
**Files:** `pod/servo/servo.py`, `pod/servo/README.md`, `pod/infer/main.cpp`, `pod/supervisor/pod.conf`
**Notes:** pod-infer --scan-status writes {seeing, confirmed, captures} 5x/s; pod-servo (Python, QNX rpi_gpio hardware PWM GPIO18/pin 12, 50 Hz MS mode) runs at normal priority under the supervisor. Laptop dry-run tested; not run on the Pi with the servo yet. Branch with-motor (on top of the CLIP merge).

