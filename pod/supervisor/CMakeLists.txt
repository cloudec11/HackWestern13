# pod-supervisor (P3-1, agent-1). Built by pod/scripts/deploy.sh.
cmake_minimum_required(VERSION 3.16)
project(pod_supervisor CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

set(POD_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
include_directories(${POD_ROOT}/shared ${POD_ROOT}/pod/ipc)

add_executable(pod-supervisor main.cpp)
