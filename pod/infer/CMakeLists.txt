# pod-infer (P1-2, agent-2). Build on the Pi:
#   cmake -S pod/infer -B build/infer && cmake --build build/infer
# Needs TFLite built by pod/infer/tflite/build_tflite.sh (default ~/tflite, override with
# -DTFLITE_ROOT=...). Without it, only the TFLite-free unit test (infer_test) is built.
cmake_minimum_required(VERSION 3.16)
project(pod_infer CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

set(POD_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
include_directories(${POD_ROOT}/shared ${POD_ROOT}/pod/ipc ${POD_ROOT}/pod/track ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(infer_test infer_test.cpp)
add_executable(clip_test clip_test.cpp)   # CLIP crop + voting tests (prototype 1), no TFLite

set(TFLITE_ROOT "$ENV{HOME}/tflite" CACHE PATH "TFLite work dir from build_tflite.sh")
set(TFLITE_BUILD ${TFLITE_ROOT}/build)

if(NOT EXISTS ${TFLITE_BUILD}/libtensorflow-lite.a)
  message(WARNING "TFLite not found at ${TFLITE_BUILD}: building infer_test only. "
                  "Run pod/infer/tflite/build_tflite.sh first.")
  return()
endif()

# Every static library the TFLite build produced (TFLite, XNNPACK, cpuinfo, abseil, ruy, ...).
# Linked as one group so their order does not matter.
file(GLOB_RECURSE TFLITE_LIBS ${TFLITE_BUILD}/*.a)

add_executable(pod-infer main.cpp)
target_compile_definitions(pod-infer PRIVATE _QNX_SOURCE)
target_include_directories(pod-infer SYSTEM PRIVATE
  ${TFLITE_ROOT}/tensorflow
  ${TFLITE_BUILD}/flatbuffers/include
  ${TFLITE_BUILD}/abseil-cpp
  ${TFLITE_BUILD}/eigen
  ${TFLITE_BUILD}/gemmlowp
  ${TFLITE_BUILD}/ruy
  ${TFLITE_BUILD}/xnnpack/include
  ${TFLITE_BUILD}/pthreadpool-source/include
  ${TFLITE_BUILD}/cpuinfo/include
  ${TFLITE_BUILD}/ml_dtypes
  ${TFLITE_ROOT}/deps/FP16/include)
target_link_libraries(pod-infer -Wl,--start-group ${TFLITE_LIBS} -Wl,--end-group rt m pthread)
