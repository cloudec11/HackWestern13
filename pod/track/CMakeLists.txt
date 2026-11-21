# pod-track (P2-1, agent-2). Build on the Pi:
#   cmake -S pod/track -B build/track && cmake --build build/track
cmake_minimum_required(VERSION 3.16)
project(pod_track CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

set(POD_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
include_directories(${POD_ROOT}/shared ${POD_ROOT}/pod/ipc ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(pod-track main.cpp)
add_executable(track_test track_test.cpp)
add_executable(track_e2e track_e2e.cpp)
