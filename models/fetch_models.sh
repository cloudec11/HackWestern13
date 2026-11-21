#!/bin/sh
# Download the detection models into models/ (agent-2).
# Model files are not committed; run this on the laptop or the Pi.
# Usage: sh models/fetch_models.sh
set -e
cd "$(dirname "$0")"

# EfficientDet-Lite0, int8, 320x320 input (MediaPipe build, COCO classes).
if [ ! -f efficientdet_lite0.tflite ]; then
    curl -fsSL -o efficientdet_lite0.tflite \
        https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/1/efficientdet_lite0.tflite
fi

# SSD MobileNet v1, uint8 quantised, 300x300 input (TFLite sample model, COCO classes).
if [ ! -f ssd_mobilenet_v1_quant.tflite ]; then
    curl -fsSL -o ssd.zip \
        https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
    unzip -o -q ssd.zip detect.tflite
    mv detect.tflite ssd_mobilenet_v1_quant.tflite
    rm -f ssd.zip
fi

ls -l *.tflite
