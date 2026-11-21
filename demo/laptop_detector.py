"""Laptop animal scanner for demos and testing (agent-4, demo/).

Show it an animal (a phone photo, a plushie, a printed picture). When it is sure what
the animal is, it beeps, cuts the animal out of the picture, and adds it to the
habitat simulation, where it walks around. Then show it the next animal.

How it decides:
  1. agent-2's TFLite detector (models/) finds WHERE things are: animals, teddy bears,
     and phone/TV/laptop screens or books that may hold a picture of one.
  2. CLIP (an image-text model) decides WHAT the animal is, by matching the picture
     against the names in demo/species.txt. Add an animal by adding a line there.
  3. It only accepts an animal after seeing the same answer in most of the last
     ~10 frames, so a single wrong frame never gets added.

This is a TESTING stand-in on the laptop, not part of the Pod (pod-infer, agent-2, runs on QNX).

Usage (from the repo root, after `sh models/fetch_models.sh`):
    demo/.venv/bin/python demo/laptop_detector.py                    # webcam 0
    demo/.venv/bin/python demo/laptop_detector.py --source clip.mp4  # video file
    demo/.venv/bin/python demo/laptop_detector.py --source deer.jpg  # one photo
Open http://localhost:8081 for the scanner view (beep + "added" message), and the
habitat view at http://localhost:8080/?scene=live_scene.json&refresh=2

Privacy: person is removed from the detector output before anything else, CLIP has
explicit "person / face / hand" answers that are never accepted, and only cut-outs of
accepted animals are saved (ground/dashboard/sim/captures/, git-ignored). Full camera
frames are never saved; the preview is streamed from memory to this laptop only.
"""

import argparse
import collections
import csv
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from ai_edge_litert.interpreter import Interpreter
from tokenizers import Tokenizer

REPO = Path(__file__).resolve().parents[1]
MODELS = {
    "efficientdet": REPO / "models" / "efficientdet_lite0.tflite",
    "ssd": REPO / "models" / "ssd_mobilenet_v1_quant.tflite",
}
LABELS = REPO / "models" / "labels.csv"
SPECIES_FILE = REPO / "demo" / "species.txt"
CLIP_DIR = REPO / "demo" / "models" / "clip-vit-b32"
CLIP_URL = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
CLIP_FILES = {"vision_model.onnx": "onnx/vision_model.onnx", "text_model.onnx": "onnx/text_model.onnx",
              "tokenizer.json": "tokenizer.json"}
SIM_DIR = REPO / "ground" / "dashboard" / "sim"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# COCO indices (as output by both detectors) for things that may contain a picture of an animal.
PICTURE_HOLDERS = {71: "tv", 72: "laptop", 76: "cell phone", 83: "book", 87: "teddy bear"}

# CLIP prompts. Each animal is described several ways so phone photos, plushies and drawings all match.
TEMPLATES = ["a photo of a {}.", "a picture of a {} on a phone screen.", "a {} plush toy.",
             "a cartoon drawing of a {}.", "a close-up photo of a {}."]
NOTHING = ["a photo of a person.", "a photo of a human face.", "a photo of a hand.", "a photo of an empty room.",
           "a photo of a wall.", "a photo of furniture.", "a blank phone screen.", "a screen with text on it.",
           "a blurry photo of nothing in particular."]


# ---------------------------------------------------------------- detector (where)
def load_labels(path):
    """model_index -> species, from agent-2's label table. Unlisted classes (person) are dropped."""
    rows = [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    return {int(r["model_index"]): r["species"].strip() for r in csv.DictReader(rows) if r["model_index"].strip()}


def efficientdet_anchors(size=320, levels=range(3, 8), anchor_scale=3.0):
    """Anchor boxes (yc, xc, h, w), normalised, in the order EfficientDet-Lite0 outputs them.
    Checked against SSD MobileNet's built-in post-processing on COCO sample images."""
    out = []
    for lvl in levels:
        stride = 2 ** lvl
        fs = int(np.ceil(size / stride))
        for y in range(fs):
            for x in range(fs):
                for octave in range(3):
                    base = anchor_scale * stride * 2 ** (octave / 3) / size
                    for ax, ay in ((1.0, 1.0), (1.4, 0.7), (0.7, 1.4)):
                        out.append(((y + 0.5) / fs, (x + 0.5) / fs, base * ay, base * ax))
    return np.array(out, np.float32)


def nms(boxes, scores, iou_thr=0.5):
    """Plain non-max suppression. boxes: N x 4 (ymin, xmin, ymax, xmax)."""
    def area(b):
        return (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
    order, keep = np.argsort(-scores), []
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(i)
        yy1 = np.maximum(boxes[i, 0], boxes[rest, 0]); xx1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        yy2 = np.minimum(boxes[i, 2], boxes[rest, 2]); xx2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(yy2 - yy1, 0, None) * np.clip(xx2 - xx1, 0, None)
        order = rest[inter / (area(boxes[i]) + area(boxes[rest]) - inter + 1e-9) < iou_thr]
    return keep


class Detector:
    """Finds regions worth looking at. Returns (name, score, (ymin, xmin, ymax, xmax)) normalised."""

    def __init__(self, kind, labels, threshold):
        path = MODELS[kind]
        if not path.exists():
            sys.exit(f"{path} not found. Run: sh models/fetch_models.sh")
        self.kind, self.threshold = kind, threshold
        self.names = dict(labels)
        self.names.update(PICTURE_HOLDERS)                       # person (index 0) is never in here
        self.it = Interpreter(model_path=str(path), num_threads=os.cpu_count() or 4)
        self.it.allocate_tensors()
        inp = self.it.get_input_details()[0]
        self.input_index, self.h, self.w = inp["index"], int(inp["shape"][1]), int(inp["shape"][2])
        outs = self.it.get_output_details()
        # Two kinds of model file: raw boxes + class scores (needs decoding here), or
        # post-processed boxes/classes/scores/count (SSD MobileNet, TF task-library EfficientDet).
        raw = [o for o in outs if len(o["shape"]) == 3 and o["shape"][-1] > 4]
        if raw:
            self.format = "raw"
            self.scores_index = raw[0]["index"]
            self.boxes_index = next(o["index"] for o in outs if len(o["shape"]) == 3 and o["shape"][-1] == 4)
            self.anchors = efficientdet_anchors(self.w)
            n = raw[0]["shape"][-1]
            self.allowed = np.zeros(n, np.float32)
            self.allowed[[i for i in self.names if i < n]] = 1.0
        else:
            self.format = "post"
            self.boxes_index = next(o["index"] for o in outs if len(o["shape"]) == 3)
            self.pair = [o["index"] for o in outs if len(o["shape"]) == 2]   # classes and scores, order varies

    def __call__(self, frame_bgr):
        rgb = cv2.cvtColor(cv2.resize(frame_bgr, (self.w, self.h)), cv2.COLOR_BGR2RGB)
        self.it.set_tensor(self.input_index, rgb[None].astype(np.uint8))
        self.it.invoke()
        return self._raw() if self.format == "raw" else self._post()

    def _raw(self):
        scores = self.it.get_tensor(self.scores_index)[0] * self.allowed  # person -> 0
        raw = self.it.get_tensor(self.boxes_index)[0]
        cls = scores.argmax(1)
        conf = scores[np.arange(len(cls)), cls]
        keep = conf >= self.threshold
        if not keep.any():
            return []
        a, t, cls, conf = self.anchors[keep], raw[keep], cls[keep], conf[keep]
        yc = t[:, 0] * a[:, 2] + a[:, 0]; xc = t[:, 1] * a[:, 3] + a[:, 1]
        h = np.exp(t[:, 2]) * a[:, 2];    w = np.exp(t[:, 3]) * a[:, 3]
        boxes = np.clip(np.stack([yc - h / 2, xc - w / 2, yc + h / 2, xc + w / 2], 1), 0, 1)
        keep_all = nms(boxes, conf)                              # class-agnostic: one box per thing
        return sorted(((self.names[int(cls[j])], float(conf[j]), tuple(boxes[j])) for j in keep_all), key=lambda d: -d[1])

    def _post(self):
        boxes = self.it.get_tensor(self.boxes_index)[0]
        a, b = (self.it.get_tensor(i)[0] for i in self.pair)
        cls, conf = (a, b) if np.all(a == np.round(a)) and not np.all(b == np.round(b)) else (b, a)
        return [(self.names[int(c)], float(s), tuple(np.clip(b, 0, 1)))
                for b, c, s in zip(boxes, cls, conf) if s >= self.threshold and int(c) in self.names]


# ---------------------------------------------------------------- CLIP (what)
def ensure_clip():
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    for name, remote in CLIP_FILES.items():
        dest = CLIP_DIR / name
        if dest.exists():
            continue
        print(f"downloading CLIP {name} (once, ~{'350' if 'vision' in name else '250' if 'text' in name else '2'} MB)...")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(CLIP_URL + remote, tmp)
        os.replace(tmp, dest)


class Clip:
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
    STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

    def __init__(self, species):
        ensure_clip()
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.vision = ort.InferenceSession(str(CLIP_DIR / "vision_model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.species = species
        self.labels = species + [None] * len(NOTHING)          # None = "not an animal"
        self.text = self._text_embeddings(species, opts)

    def _text_embeddings(self, species, opts):
        key = hashlib.sha1(json.dumps([species, TEMPLATES, NOTHING]).encode()).hexdigest()[:12]
        cache = CLIP_DIR / f"text_{key}.npy"
        if cache.exists():
            return np.load(cache)
        print(f"preparing CLIP text for {len(species)} animals (once)...")
        tok = Tokenizer.from_file(str(CLIP_DIR / "tokenizer.json"))
        sess = ort.InferenceSession(str(CLIP_DIR / "text_model.onnx"), opts, providers=["CPUExecutionProvider"])

        def embed(sentences):
            enc = tok.encode_batch(sentences)
            n = max(len(e.ids) for e in enc)
            ids = np.array([e.ids + [49407] * (n - len(e.ids)) for e in enc], np.int64)
            e = sess.run(None, {"input_ids": ids})[0]
            return e / np.linalg.norm(e, axis=1, keepdims=True)

        rows = []
        for sp in species:
            e = embed([t.format(sp.replace("_", " ")) for t in TEMPLATES]).mean(0)
            rows.append(e / np.linalg.norm(e))
        out = np.vstack([np.array(rows), embed(NOTHING)]).astype(np.float32)
        np.save(cache, out)
        return out

    def __call__(self, crop_bgr):
        """Probabilities over self.labels for one picture."""
        h, w = crop_bgr.shape[:2]
        s = max(h, w)                                            # pad to square, keep the whole animal
        sq = cv2.copyMakeBorder(crop_bgr, (s - h) // 2, s - h - (s - h) // 2, (s - w) // 2, s - w - (s - w) // 2,
                                cv2.BORDER_REPLICATE)
        im = cv2.resize(sq, (224, 224), interpolation=cv2.INTER_CUBIC)[:, :, ::-1].astype(np.float32) / 255
        x = ((im - self.MEAN) / self.STD).transpose(2, 0, 1)[None]
        e = self.vision.run(None, {"pixel_values": x})[0][0]
        logits = 100 * self.text @ (e / np.linalg.norm(e))
        p = np.exp(logits - logits.max())
        return p / p.sum()


def load_species(path):
    names = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip().lower().replace(" ", "_")
        if line and line != "person" and line not in names:
            names.append(line)
    return names


# ---------------------------------------------------------------- scanning logic
def square_region(frame, box, pad=0.12):
    """Crop around a normalised box, a little larger, clamped to the frame. Returns (crop, pixel box)."""
    H, W = frame.shape[:2]
    y0, x0, y1, x1 = box
    py, px = (y1 - y0) * pad, (x1 - x0) * pad
    Y0, X0 = int(max(0, (y0 - py) * H)), int(max(0, (x0 - px) * W))
    Y1, X1 = int(min(H, (y1 + py) * H)), int(min(W, (x1 + px) * W))
    if Y1 - Y0 < 16 or X1 - X0 < 16:
        return None, None
    return frame[Y0:Y1, X0:X1], (X0, Y0, X1, Y1)


class Scanner:
    """Per frame: find candidate regions, ask CLIP, return the best animal guess."""

    def __init__(self, detector, clip, min_prob):
        self.detector, self.clip, self.min_prob = detector, clip, min_prob

    def __call__(self, frame):
        t0 = time.perf_counter()
        dets = self.detector(frame)
        regions = [(name, box) for name, _, box in dets[:2]]
        if not regions:                                          # nothing found: look at the middle of the view
            regions = [("centre", (0.1, 0.15, 0.9, 0.85))]
        best = None
        for name, box in regions:
            crop, px = square_region(frame, box)
            if crop is None:
                continue
            p = self.clip(crop)
            order = np.argsort(-p)
            top3 = [(self.clip.labels[i] or "nothing", float(p[i])) for i in order[:3]]
            i = order[0]
            species = self.clip.labels[i]
            guess = {"species": species, "prob": float(p[i]), "box": px, "crop": crop, "top3": top3, "region": name}
            if best is None or (species and (best["species"] is None or guess["prob"] > best["prob"])):
                best = guess
        if best is None and regions[0][0] != "centre":            # boxes too small: fall back to the centre
            crop, px = square_region(frame, (0.1, 0.15, 0.9, 0.85))
            p = self.clip(crop)
            order = np.argsort(-p)
            best = {"species": self.clip.labels[order[0]], "prob": float(p[order[0]]), "box": px, "crop": crop,
                    "top3": [(self.clip.labels[i] or "nothing", float(p[i])) for i in order[:3]], "region": "centre"}
        ms = (time.perf_counter() - t0) * 1000
        if best and (best["species"] is None or best["prob"] < self.min_prob):
            best["accepted"] = False
        elif best:
            best["accepted"] = True
        return best, ms


class Confirmer:
    """Accepts an animal only when it wins most of the recent frames, then waits for the next one."""

    def __init__(self, window=10, need=6, release_s=2.5, cooldown_s=2.0):
        self.votes = collections.deque(maxlen=window)
        self.need, self.release_s, self.cooldown_s = need, release_s, cooldown_s
        self.locked, self.last_seen_locked, self.last_capture = None, 0.0, 0.0

    def update(self, guess, now):
        """Returns (species or None to capture now, progress dict for the UI)."""
        sp = guess["species"] if guess and guess["accepted"] else None
        self.votes.append(sp)
        if self.locked and sp == self.locked:
            self.last_seen_locked = now
        if self.locked and now - self.last_seen_locked > self.release_s:
            self.locked = None                                   # the previous animal has gone
        leader, n = (collections.Counter(v for v in self.votes if v).most_common(1) or [(None, 0)])[0]
        progress = {"species": leader, "votes": n, "need": self.need, "locked": self.locked}
        if leader and n >= self.need and leader != self.locked and now - self.last_capture > self.cooldown_s:
            self.locked, self.last_seen_locked, self.last_capture = leader, now, now
            self.votes.clear()
            return leader, progress
        return None, progress


def cutout(crop):
    """Cut the animal out of its background (OpenCV GrabCut). Falls back to a rounded card."""
    scale = 320 / max(crop.shape[:2])
    img = cv2.resize(crop, None, fx=scale, fy=scale) if scale < 1 else crop.copy()
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    try:
        cv2.grabCut(img, mask, (int(w * 0.06), int(h * 0.06), int(w * 0.88), int(h * 0.88)),
                    np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 4, cv2.GC_INIT_WITH_RECT)
        m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
        if n > 1:
            m = np.where(lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA]), 255, 0).astype(np.uint8)
        frac = (m > 0).mean()
    except cv2.error:
        frac = 0
    if not 0.12 < frac < 0.95:                                   # cut-out failed: use a rounded card instead
        m = np.zeros((h, w), np.uint8)
        r = int(min(h, w) * 0.12)
        cv2.rectangle(m, (r, 0), (w - r, h), 255, -1); cv2.rectangle(m, (0, r), (w, h - r), 255, -1)
        for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
            cv2.circle(m, (cx, cy), r, 255, -1)
    m = cv2.GaussianBlur(m, (5, 5), 0)
    ys, xs = np.where(m > 10)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = m
    return bgra[y0:y1, x0:x1]


# ---------------------------------------------------------------- simulation scene file
class Scene:
    def __init__(self, args):
        self.args, self.counts, self.images = args, {}, {}
        self.captures = args.scene_out.parent / "captures"
        self.captures.mkdir(exist_ok=True)
        # Every accepted animal with its time, kept across runs, for the habitat view's charts.
        self.history_path = args.scene_out.parent / "live_history.json"
        self.history = []
        if self.history_path.exists() and not args.clear_history:
            try:
                self.history = json.loads(self.history_path.read_text())
            except ValueError:
                self.history = []
        self._write_json(self.history_path, self.history)

    def add(self, species, crop):
        self.counts[species] = self.counts.get(species, 0) + 1
        name = f"{species}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        cv2.imwrite(str(self.captures / name), cutout(crop))
        if not self.args.no_photos_in_sim:
            self.images[species] = f"captures/{name}"
        self.history.append({"species": species, "t_ms": int(time.time() * 1000), "image": f"captures/{name}"})
        self._write_json(self.history_path, self.history)
        self.write()
        return f"captures/{name}"

    @staticmethod
    def _write_json(path, obj):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1))
        os.replace(tmp, path)                                    # atomic: the page never reads half a file

    def reset(self):
        self.counts.clear(); self.images.clear(); self.write()

    def write(self):
        animals = []
        for sp, c in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            entry = {"species": sp, "count": c}
            if sp in self.images:
                entry["image"] = self.images[sp]
            animals.append(entry)
        scene = {
            "pod_id": self.args.pod_id, "biome": self.args.biome, "biome_source": "laptop test (--biome)",
            "weather": {"condition": self.args.weather, "source": "laptop test (--weather)"},
            "animals": animals, "updated_ms": int(time.time() * 1000),
        }
        self._write_json(self.args.scene_out, scene)


# ---------------------------------------------------------------- drawing
def draw(frame, guess, progress, ms):
    H, W = frame.shape[:2]
    if guess and guess["box"]:
        X0, Y0, X1, Y1 = guess["box"]
        ok = guess["accepted"]
        color = (60, 220, 120) if ok else (160, 160, 160)
        cv2.rectangle(frame, (X0, Y0), (X1, Y1), color, 2)
        text = f"{guess['top3'][0][0].replace('_', ' ')} {guess['top3'][0][1]:.0%}" if ok else "looking..."
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.rectangle(frame, (X0, max(0, Y0 - th - 10)), (X0 + tw + 8, max(th + 10, Y0)), color, -1)
        cv2.putText(frame, text, (X0 + 4, max(th + 4, Y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    cv2.rectangle(frame, (0, H - 28), (W, H), (0, 0, 0), -1)
    cv2.putText(frame, f"{ms:.0f} ms per frame", (10, H - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


# ---------------------------------------------------------------- browser scanner page
PAGE = (Path(__file__).with_name("scanner.html")).read_bytes


class Preview:
    """Local web page: live camera, progress, beep + 'added to simulation' message."""

    def __init__(self, port, captures_dir):
        self.jpeg, self.status, self.events = None, {}, []
        self.cond, self.reset_requested, self.captures_dir = threading.Condition(), False, captures_dir
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                try:
                    if self.path == "/stream.mjpg":
                        self.send_response(200)
                        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                        self.end_headers()
                        while True:
                            with preview.cond:
                                preview.cond.wait(timeout=2)
                                jpg = preview.jpeg
                            if jpg:
                                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    elif self.path == "/events":                 # server-sent events: status + captures
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        sent, last_status = len(preview.events), None
                        while True:
                            with preview.cond:
                                preview.cond.wait(timeout=1)
                                new, status = preview.events[sent:], preview.status
                            for ev in new:
                                self.wfile.write(b"event: capture\ndata: " + json.dumps(ev).encode() + b"\n\n")
                            sent += len(new)
                            if status != last_status:
                                self.wfile.write(b"event: status\ndata: " + json.dumps(status).encode() + b"\n\n")
                                last_status = status
                            self.wfile.flush()
                    elif self.path.startswith("/captures/"):
                        f = preview.captures_dir / Path(self.path).name
                        if f.suffix == ".png" and f.exists():
                            self._send(200, "image/png", f.read_bytes())
                        else:
                            self._send(404, "text/plain", b"not found")
                    else:
                        self._send(200, "text/html; charset=utf-8", PAGE())
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_POST(self):
                if self.path == "/reset":
                    preview.reset_requested = True
                self._send(204, "text/plain", b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, frame, status):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with self.cond:
            if ok:
                self.jpeg = buf.tobytes()
            self.status = status
            self.cond.notify_all()

    def event(self, ev):
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()


# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="0", help="webcam index (0, 1, ...), video file or image file")
    p.add_argument("--model", choices=MODELS, default="efficientdet", help="detector used to find regions")
    p.add_argument("--region-threshold", type=float, default=0.3, help="detector score to look closer at a region")
    p.add_argument("--min-prob", type=float, default=0.45, help="CLIP confidence needed for a frame to count")
    p.add_argument("--frames-needed", type=int, default=6, help="of the last 10 frames, how many must agree")
    p.add_argument("--biome", default="forest", help="habitat to show until real biome data exists")
    p.add_argument("--weather", default="clear", help="weather to show until real weather data exists")
    p.add_argument("--pod-id", default="laptop")
    p.add_argument("--scene-out", type=Path, default=SIM_DIR / "live_scene.json")
    p.add_argument("--no-photos-in-sim", action="store_true", help="use cartoon placeholders instead of the cut-outs")
    p.add_argument("--clear-history", action="store_true", help="start the appearance history (charts) from empty")
    p.add_argument("--preview-port", type=int, default=8081)
    p.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = run forever)")
    args = p.parse_args()

    species = load_species(SPECIES_FILE)
    scanner = Scanner(Detector(args.model, load_labels(LABELS), args.region_threshold), Clip(species), args.min_prob)
    scene = Scene(args)
    scene.write()
    print(f"knows {len(species)} animals (edit demo/species.txt to add more)")

    src = Path(args.source)
    if src.suffix.lower() in IMAGE_EXT:                          # one photo: no voting, just answer
        frame = cv2.imread(str(src))
        if frame is None:
            sys.exit(f"cannot read {src}")
        guess, ms = scanner(frame)
        top = ", ".join(f"{s} {q:.2f}" for s, q in guess["top3"]) if guess else "-"
        if guess and guess["accepted"]:
            path = scene.add(guess["species"], guess["crop"])
            print(f"{src.name}: ADDED {guess['species']} ({ms:.0f} ms; {top}) -> {path}")
        else:
            print(f"{src.name}: not sure ({ms:.0f} ms; {top})")
        return

    cap = cv2.VideoCapture(int(args.source) if args.source.isdigit() else args.source)
    if not cap.isOpened():
        sys.exit(f"cannot open source {args.source}")
    preview = Preview(args.preview_port, scene.captures) if args.preview_port else None
    confirm = Confirmer(need=args.frames_needed)
    if preview:
        print(f"scanner page:  http://localhost:{args.preview_port}   (beep + 'added to simulation' message)")
    print("habitat view:  http://localhost:8080/?scene=live_scene.json&refresh=2   (needs the web server)")
    print("stop with Ctrl+C")
    n = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.time()
            guess, ms = scanner(frame)
            new, progress = confirm.update(guess, now)
            if new:
                path = scene.add(new, guess["crop"])
                print(f"\a{time.strftime('%H:%M:%S')}  ADDED {new.replace('_', ' ')} to the simulation "
                      f"({guess['prob']:.0%}, total {scene.counts[new]})")
                if preview:
                    preview.event({"species": new, "prob": guess["prob"], "image": "/" + path,
                                   "count": scene.counts[new], "counts": scene.counts})
            if preview:
                status = dict(progress)
                if guess:
                    status["top3"] = guess["top3"]
                preview.publish(draw(frame, guess, progress, ms), status)
                if preview.reset_requested:
                    preview.reset_requested = False
                    scene.reset()
                    preview.event({"reset": True})
            n += 1
            if args.max_frames and n >= args.max_frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        print("added:", scene.counts or "nothing")


if __name__ == "__main__":
    main()
