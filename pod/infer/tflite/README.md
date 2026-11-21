# TensorFlow Lite on the Pi (agent-2)

TFLite has no official QNX build. We build the QNX community fork (`qnx-ports/tensorflow`, branch `qnx_v2.16.1`) directly on the Pi, using the Pi's own clang and CMake. The official `qnx-ports/build-files` recipe cross-compiles from Ubuntu with the QNX SDK, which we don't have.

## Build

On the Pi, from the repo root:

```
nohup sh pod/infer/tflite/build_tflite.sh > ~/tflite-status.log 2>&1 &
```

It takes about 30 minutes and writes everything to `~/tflite`. Check progress with `cat ~/tflite-status.log`. The script can be rerun after a failure; finished steps are skipped.

## Files

| File | Purpose |
|------|---------|
| `build_tflite.sh` | Clones the sources, applies fixes, configures and builds. |
| `qnx-native.cmake` | CMake toolchain file: QNX flags and patches from qnx-ports, adapted for a native build. |
| `elementwise_abs.patch` | Fixes a compile error with the Pi's newer C++ library. Upstream TensorFlow made the same change later. |

## Problems this works around

1. **CMake 4** (`4.4.2` on the Pi) rejects old dependency build files. The fix is `CMAKE_POLICY_VERSION_MINIMUM=3.5`, set as an environment variable so the dependency sub-builds see it too.
2. **CPU name.** Native CMake on QNX reports the processor as `aarch64le`. XNNPACK and cpuinfo don't recognise it: XNNPACK leaves out its ARM NEON routines (link errors), and cpuinfo fails at run time. The toolchain file sets `aarch64`, matching the official cross-compile setup.
3. **FP16 download.** If the automatic download fails once (for example during a Wi-Fi drop), CMake caches an empty path. The script downloads FP16 itself.
4. **`std::abs<float>`** can no longer be passed as a function. See `elementwise_abs.patch`.

## Running

Anything that uses TFLite with XNNPACK needs I/O privilege on QNX, because cpuinfo reads a privileged CPU register at startup. As a normal user it exits with `Fatal error in cpuinfo: Failed to get I/O access permission`. For example:

```
sudo ~/tflite/build/tools/benchmark/benchmark_model --graph=models/efficientdet_lite0.tflite --num_threads=3
```

Speed results are in `models/README.md`.

Status: the build was verified on the Pi on 2026-11-21 with these same steps, run by hand. `build_tflite.sh` packages those steps but has not yet been run on a fresh Pi from scratch.
