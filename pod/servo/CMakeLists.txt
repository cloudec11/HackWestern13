# servo_pwm (with-motor): sets the servo pulse width in the RP1 PWM hardware, because QNX's
# rpi_gpio Python module cannot store a duty between 0 and 100 %. Build on the Pi:
#   cmake -S pod/servo -B build/servo && cmake --build build/servo
cmake_minimum_required(VERSION 3.16)
project(pod_servo C)
set(CMAKE_C_STANDARD 11)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)
add_executable(servo_pwm servo_pwm.c)
