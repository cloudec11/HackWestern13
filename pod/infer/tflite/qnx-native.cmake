# Native build of the QNX TFLite fork directly on the Pi (agent-2, P0-6). Used by build_tflite.sh.
# Adapted from qnx-ports/build-files ports/tensorflow/qnx.nto.toolchain.cmake,
# minus the SDP cross-compiler settings.
set(QNX TRUE)
set(CMAKE_C_COMPILER clang)
set(CMAKE_CXX_COMPILER clang++)
set(QNX_FLAGS "-D_QNX_SOURCE -funsafe-math-optimizations -DFARMHASH_LITTLE_ENDIAN -D__LITTLE_ENDIAN__")
set(CMAKE_C_FLAGS_INIT "${QNX_FLAGS}")
set(CMAKE_CXX_FLAGS_INIT "${QNX_FLAGS}")
set(EIGEN_PATCH_CMD git reset --hard && git apply "${QNX_PATCH_DIR}/eigen.patch")
set(PTHREADPOOL_PATCH_CMD "patch" "<" "${QNX_PATCH_DIR}/pthreadpool.patch")
set(XNNPACK_PATCH_CMD git reset --hard && git apply "${QNX_PATCH_DIR}/XNNPACK.patch")
# Native CMake on QNX reports CMAKE_SYSTEM_PROCESSOR=aarch64le, which XNNPACK and
# cpuinfo do not recognise (no NEON kernels, cpuinfo_initialize() always fails).
# The SDP cross toolchain uses aarch64, so match it.
set(CMAKE_SYSTEM_NAME QNX)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
