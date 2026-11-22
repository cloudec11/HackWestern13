"""Build the CLIP image encoder as TFLite for pod-infer (QNX TensorFlow Lite 2.16).

Run once on a laptop (needs Python 3.12 + tensorflow-cpu==2.16.2, tf-keras==2.16.0,
transformers==4.46.3). Writes models/clip_vision.tflite (int8 weights, ~88 MB).

Model: openai/clip-vit-base-patch32 vision tower + projection. Input [1,224,224,3] float32,
already normalised with CLIP's mean/std (RGB, NHWC). Output [1,512], L2-normalised.
Only builtin TFLite ops, so the QNX qnx-ports TFLite build can run it.
"""
import os
import sys

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
import tensorflow as tf  # noqa: E402
from transformers import TFCLIPModel  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "clip_vision.tflite")
model = TFCLIPModel.from_pretrained("openai/clip-vit-base-patch32")


@tf.function(input_signature=[tf.TensorSpec([1, 224, 224, 3], tf.float32, name="image")])
def encode(image):
    e = model.get_image_features(pixel_values=tf.transpose(image, [0, 3, 1, 2]))
    return {"embedding": e / tf.norm(e, axis=-1, keepdims=True)}


conv = tf.lite.TFLiteConverter.from_concrete_functions([encode.get_concrete_function()], model)
conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS]
conv.optimizations = [tf.lite.Optimize.DEFAULT]          # int8 weights, float maths (dynamic range)
data = conv.convert()
with open(out, "wb") as f:
    f.write(data)
print(f"wrote {out} ({len(data) / 1e6:.1f} MB)")
