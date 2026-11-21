#!/bin/sh
# Copy the Pod code to the Pi and build it. Owner: agent-1.
# Run from anywhere inside the repo, in Git Bash (Windows), macOS or Linux:
#
#   sh pod/scripts/deploy.sh
#   POD_HOST=qnxuser@192.168.50.2 POD_DIR=pod-deploy sh pod/scripts/deploy.sh
#
# Copies pod/, shared/ and models/labels.csv to ~/$POD_DIR on the Pi using tar over SSH
# (plain scp was unreliable on the hotspot), then builds pod/capture with CMake there.
# Needs SSH key login to the Pi (see the repo notes). Does not start or stop anything.
set -eu

HOST="${POD_HOST:-qnxuser@qnxpi.local}"
DIR="${POD_DIR:-pod-deploy}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

cd "$(dirname "$0")/../.."
[ -f shared/pod_msgs.h ] || { echo "deploy: run this from inside the repo" >&2; exit 1; }

FILES="pod shared"
[ -f models/labels.csv ] && FILES="$FILES models/labels.csv"

echo "deploy: copying $FILES -> $HOST:~/$DIR"
tar -cf - --exclude=build --exclude=__pycache__ $FILES \
    | $SSH "$HOST" "mkdir -p ~/$DIR && cd ~/$DIR && tar -xf -"

echo "deploy: building pod/capture on the Pi"
$SSH "$HOST" "cd ~/$DIR && mkdir -p build && export CMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && cmake -S pod/capture -B build/capture > build/cmake.log 2>&1 \
    && cmake --build build/capture > build/make.log 2>&1 \
    && { grep -i warning build/make.log || true; echo 'deploy: build ok'; } \
    || { echo 'deploy: BUILD FAILED'; tail -20 build/cmake.log build/make.log; exit 1; }"

echo "deploy: done. On the Pi:  cd ~/$DIR && sh pod/scripts/camera_service.sh pod && ./build/capture/pod-capture --prio 30"
