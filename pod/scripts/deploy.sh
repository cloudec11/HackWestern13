#!/bin/sh
# Copy the Pod code to the Pi and build it. Owner: agent-1.
# Run from anywhere inside the repo, in Git Bash (Windows), macOS or Linux:
#
#   sh pod/scripts/deploy.sh
#   POD_HOST=qnxuser@192.168.50.2 POD_DIR=pod-deploy sh pod/scripts/deploy.sh
#
# Copies pod/, shared/ and models/labels.csv to ~/$POD_DIR on the Pi using tar over SSH
# (plain scp was unreliable on the hotspot), then builds agent-1's components there.
# Needs SSH key login to the Pi (see the repo notes). Does not start or stop anything.
set -eu

HOST="${POD_HOST:-qnxuser@qnxpi.local}"
DIR="${POD_DIR:-pod-deploy}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

cd "$(dirname "$0")/../.."
[ -f shared/pod_msgs.h ] || { echo "deploy: run this from inside the repo" >&2; exit 1; }

FILES="pod shared"
[ -f models/labels.csv ] && FILES="$FILES models/labels.csv"
# CLIP (prototype 1): animal names + text table, and the image model if it was built/downloaded
[ -f models/clip/labels.txt ] && FILES="$FILES models/clip/labels.txt models/clip/text.f32"
[ -f models/clip_vision.tflite ] && FILES="$FILES models/clip_vision.tflite"

echo "deploy: copying $FILES -> $HOST:~/$DIR"
tar -cf - --exclude=build --exclude=__pycache__ $FILES \
    | $SSH "$HOST" "mkdir -p ~/$DIR && cd ~/$DIR && tar -xf -"

# agent-1's components by default. agent-2 builds pod/infer and pod/track themselves (they need
# the TFLite build); include them with e.g. POD_COMPONENTS="capture log supervisor track".
COMPONENTS="${POD_COMPONENTS:-capture log supervisor}"
echo "deploy: building on the Pi: $COMPONENTS"
for c in $COMPONENTS; do
    $SSH "$HOST" "cd ~/$DIR && mkdir -p build && export CMAKE_POLICY_VERSION_MINIMUM=3.5 \
        && cmake -S pod/$c -B build/$c > build/$c.cmake.log 2>&1 \
        && cmake --build build/$c > build/$c.make.log 2>&1 \
        && { grep -i warning build/$c.make.log || true; echo 'deploy: $c ok'; } \
        || { echo 'deploy: $c BUILD FAILED'; tail -20 build/$c.cmake.log build/$c.make.log; exit 1; }"
done

echo "deploy: done. On the Pi:  cd ~/$DIR && sh pod/scripts/camera_service.sh pod && ./build/capture/pod-capture --prio 30"
