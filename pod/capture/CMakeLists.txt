# pod-capture (P1-1, agent-1). Build on the Pi:
#   cmake -S pod/capture -B build/capture && cmake --build build/capture
cmake_minimum_required(VERSION 3.16)
project(pod_capture C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 11)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

set(POD_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
include_directories(${POD_ROOT}/shared ${POD_ROOT}/pod/ipc ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(pod-capture main.cpp)
target_link_libraries(pod-capture camapi)

add_executable(capture_check capture_check.cpp)

add_executable(cam_probe cam_probe.c)
target_link_libraries(cam_probe camapi)
