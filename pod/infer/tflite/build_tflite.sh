#!/bin/sh
# Build TensorFlow Lite natively on the Pi (QNX 8.0, aarch64). agent-2, P0-6.
#
# Usage (on the Pi):  sh pod/infer/tflite/build_tflite.sh [WORKDIR]
#   WORKDIR defaults to ~/tflite. Takes ~30 min (mostly downloads + compile).
#   Run under nohup so a Wi-Fi drop does not kill it:
#     nohup sh pod/infer/tflite/build_tflite.sh > ~/tflite-status.log 2>&1 &
#
# Output: WORKDIR/build/libtensorflow-lite.a, plus benchmark_model and label_image.
# Every step is skipped if already done, so it is safe to rerun after a failure.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
T=${1:-$HOME/tflite}
mkdir -p "$T/deps"
cd "$T"
echo "start $(date)"

# 1. Sources: QNX fork of TensorFlow and the qnx-ports build files (for its patches).
[ -d build-files ] || git clone -q --depth 1 https://github.com/qnx-ports/build-files.git
[ -d tensorflow ] || git clone -q --depth 1 -b qnx_v2.16.1 https://github.com/qnx-ports/tensorflow.git

# 2. Fix for newer libc++: std::abs<float> can no longer be passed as a function.
if git -C tensorflow apply --check "$HERE/elementwise_abs.patch" 2>/dev/null; then
    git -C tensorflow apply "$HERE/elementwise_abs.patch"
fi

# 3. FP16 by hand: its auto-download caches an empty path if it ever fails once.
if [ ! -f deps/FP16/CMakeLists.txt ]; then
    curl -fsSL -o deps/fp16.zip https://github.com/Maratyszcza/FP16/archive/0a92994d729ff76a58f692d3028ca1b64b145d91.zip
    (cd deps && unzip -q -o fp16.zip && rm -rf FP16 && mv FP16-0a92994d729ff76a58f692d3028ca1b64b145d91 FP16 && rm fp16.zip)
fi

# 4. Configure. CMake 4 rejects deps with cmake_minimum_required < 3.5; the env var
#    (unlike -D) also reaches the dependency sub-builds.
export CMAKE_POLICY_VERSION_MINIMUM=3.5
mkdir -p build && cd build
if [ ! -f build.ninja ]; then
    cmake -G Ninja "$T/tensorflow/tensorflow/lite" \
     -DCMAKE_TOOLCHAIN_FILE="$HERE/qnx-native.cmake" \
     -DQNX_PATCH_DIR="$T/build-files/ports/tensorflow/patches" \
     -DCMAKE_BUILD_TYPE=Release \
     -DTFLITE_ENABLE_XNNPACK=ON -DXNNPACK_ENABLE_ASSEMBLY=OFF \
     -DBUILD_SHARED_LIBS=OFF -DTFLITE_KERNEL_TEST=OFF \
     -DFP16_SOURCE_DIR="$T/deps/FP16" \
     -DOVERRIDABLE_FETCH_CONTENT_cpuinfo_GIT_REPOSITORY=https://github.com/qnx-ports/cpuinfo \
     -DOVERRIDABLE_FETCH_CONTENT_cpuinfo_GIT_TAG=qnx \
     -DOVERRIDABLE_FETCH_CONTENT_ruy_GIT_REPOSITORY=https://github.com/qnx-ports/ruy \
     -DOVERRIDABLE_FETCH_CONTENT_ruy_GIT_TAG=master \
     -DOVERRIDABLE_FETCH_CONTENT_abseil-cpp_GIT_REPOSITORY=https://github.com/qnx-ports/abseil-cpp.git \
     -DOVERRIDABLE_FETCH_CONTENT_abseil-cpp_GIT_TAG=qnx_20230802.1 \
     -DOVERRIDABLE_FETCH_CONTENT_farmhash_GIT_REPOSITORY=https://github.com/qnx-ports/farmhash.git \
     -DOVERRIDABLE_FETCH_CONTENT_farmhash_GIT_TAG=qnx \
     -DOVERRIDABLE_FETCH_CONTENT_eigen_GIT_REPOSITORY=https://github.com/qnx-ports/eigen.git \
     -DOVERRIDABLE_FETCH_CONTENT_eigen_GIT_TAG=qnx_v3.4.1 \
     > ../configure.log 2>&1 || { echo "CONFIGURE FAILED, see $T/configure.log"; exit 1; }
fi
echo "configured $(date)"

# 5. Build. -j4 fits in the Pi's 8 GB.
ninja -j4 tensorflow-lite benchmark_model label_image > ../build.log 2>&1 \
    || { echo "BUILD FAILED, see $T/build.log"; exit 1; }
echo "ALL DONE $(date)"
