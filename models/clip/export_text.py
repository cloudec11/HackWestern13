"""Export CLIP text embeddings for pod-infer: models/clip/labels.txt + models/clip/text.f32.

Run on the laptop with demo/.venv (uses the ONNX CLIP text model the laptop scanner downloads):
    demo/.venv/bin/python models/clip/export_text.py
labels.txt: one label per row ("_none" rows are "not an animal": person, face, hand, empty room...).
text.f32:   rows x 512 little-endian float32, L2-normalised, same row order.
The animal list comes from demo/species.txt, so the laptop scanner and the Pod know the same animals.
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "demo"))
import laptop_detector as L  # noqa: E402

species = L.load_species(L.SPECIES_FILE)
clip = L.Clip(species)
labels = species + ["_none"] * len(L.NOTHING)
assert clip.text.shape == (len(labels), 512)
(HERE / "labels.txt").write_text("\n".join(labels) + "\n")
clip.text.astype("<f4").tofile(HERE / "text.f32")
print(f"{len(species)} animals + {len(L.NOTHING)} 'not an animal' rows -> {HERE / 'labels.txt'}, {HERE / 'text.f32'}")
