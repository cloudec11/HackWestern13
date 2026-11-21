# pod-log (P2-2, agent-1). Build on the Pi:
#   cmake -S pod/log -B build/log && cmake --build build/log
cmake_minimum_required(VERSION 3.16)
project(pod_log CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

set(POD_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
include_directories(${POD_ROOT}/shared ${POD_ROOT}/pod/ipc)

add_executable(pod-log main.cpp)
add_executable(fake_track fake_track.cpp)
