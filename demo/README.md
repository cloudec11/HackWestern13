# demo/ (agent-4)

## Laptop animal scanner: test and demo without the Pi

`laptop_detector.py` turns the laptop webcam into an animal scanner. Show it an animal (a phone photo, a plushie, a printed picture). When it is sure, it **beeps, cuts the animal out of the picture and adds it to the habitat simulation**, then waits for the next animal.

This is a **testing and demo stand-in**. The real pipeline is `pod-infer` (C++ on the QNX Pi, agent-2). Laptop timings say nothing about the Pi.

### Why not just agent-2's model?

The COCO models in `models/` only know 10 animals (bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe). A deer comes out as "cat", because "deer" is not a word the model knows. So the scanner works in two steps:

1. **Where:** agent-2's EfficientDet finds animals, teddy bears, and phone/TV/laptop screens or books that may hold a picture.
2. **What:** CLIP (an image-text model, ViT-B/32, ONNX) compares that region with the names in [species.txt](species.txt), described several ways ("a photo of a deer", "a deer plush toy", "a deer on a phone screen", ...). It also has "person / face / hand / empty room" answers that are never accepted.
3. **Sure:** an animal is only added after it wins 6 of the last 10 frames. The same animal is not added again until it has been gone for 2.5 s.

To add an animal, add its name to `species.txt` and restart. No training needed.

Tested on photos: deer, moose, fox, raccoon, owl, bear, cats and a teddy bear all correct; an empty bedroom correctly "nothing". About 50–130 ms per frame on this laptop.

### Setup (once)

From the repo root:

```
sh models/fetch_models.sh
python3 -m venv demo/.venv
demo/.venv/bin/pip install -r demo/requirements.txt
```

The CLIP model (~600 MB) downloads by itself on the first run into `demo/models/` (git-ignored). Windows: use `demo\.venv\Scripts\python`.

### Run

Terminal 1 (the scanner):

```
demo/.venv/bin/python demo/laptop_detector.py                      # webcam 0
demo/.venv/bin/python demo/laptop_detector.py --source 1            # another camera
demo/.venv/bin/python demo/laptop_detector.py --source clip.mp4     # a video
demo/.venv/bin/python demo/laptop_detector.py --source deer.jpg     # one photo
```

Terminal 2 (serves the habitat view):

```
python3 -m http.server 8080 --directory ground/dashboard/sim
```

Then open:

- **Scanner:** http://localhost:8081. Click **Enable sound** once (browsers block sound until you click).
- **Habitat:** http://localhost:8080/?scene=live_scene.json&refresh=2. Added animals walk around using their cut-out photo.

| Option | Default | Meaning |
|--------|---------|---------|
| `--min-prob` | `0.45` | CLIP confidence for a frame to count |
| `--frames-needed` | `6` | of the last 10 frames, how many must agree |
| `--no-photos-in-sim` | off | cartoon placeholders instead of cut-out photos |
| `--biome`, `--weather` | `forest`, `clear` | what the habitat shows until real data exists |
| `--model` | `efficientdet` | or `ssd` for finding regions |

### Privacy

Person is removed from the detector output first, and CLIP's "person/face/hand" answers are never accepted. Only cut-outs of accepted animals are saved, in `ground/dashboard/sim/captures/` (git-ignored). Full frames are never saved; the camera page is streamed from memory and only reachable from this laptop.

### Model files

The scanner works with either EfficientDet file: the post-processed one `models/fetch_models.sh` downloads now, or the older MediaPipe build that outputs raw boxes (it decodes those itself, anchor scale 3).
