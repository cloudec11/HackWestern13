/*
 * servo_pwm -- set the servo pulse width straight in the Pi 5's RP1 PWM hardware.
 * Workaround for QNX's rpi_gpio Python module, which sets up the pin, clock, channel
 * and 50 Hz range fine but cannot store a duty between 0 and 100 %: ChangeDutyCycle(7.5)
 * leaves the duty register at 0, and ChangeDutyCycleAbs() only accepts 0 or 1.
 * Found 2026-11-22 (agent-2): with this helper, 50 % duty measured 1.6 V on pin 12.
 *
 *   sudo -n servo_pwm CHANNEL        then one pulse width in microseconds per stdin line
 *     CHANNEL: RP1 PWM0 channel of the pin (GPIO12 = 0, GPIO13 = 1, GPIO18 = 2, GPIO19 = 3)
 *     line "1500" -> 1.5 ms pulse every period; "0" -> no pulses; EOF -> no pulses and exit
 *
 * The pin must already be set to its PWM function by rpi_gpio (servo.py does that).
 * Needs I/O privilege (root) to map the registers. Pulse widths outside 0 or 400..2600 us
 * are refused. Replies "ok <us>" or "err <reason>" per line.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/neutrino.h>

#define RP1_PWM0 0x1f00098000ULL   /* RP1 PWM0 (0x40098000) behind the Pi 5 PCIe BAR */
#define PERIOD_US 20000u           /* 50 Hz, as set up by rpi_gpio.PWM(pin, 50, ...) */

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: servo_pwm CHANNEL  (pulse widths in us on stdin)\n"); return 2; }
    const int ch = atoi(argv[1]);
    if (ch < 0 || ch > 3) { fprintf(stderr, "servo_pwm: channel must be 0-3\n"); return 2; }
    if (ThreadCtl(_NTO_TCTL_IO, 0) == -1) { perror("servo_pwm: ThreadCtl (needs root)"); return 1; }
    volatile uint32_t* pwm = mmap_device_memory(NULL, 0x1000, PROT_READ | PROT_WRITE | PROT_NOCACHE, 0, RP1_PWM0);
    if (pwm == MAP_FAILED) { perror("servo_pwm: mmap_device_memory"); return 1; }
    volatile uint32_t* range = &pwm[(0x18 + ch * 0x10) / 4];
    volatile uint32_t* duty = &pwm[(0x20 + ch * 0x10) / 4];
    setvbuf(stdout, NULL, _IOLBF, 0);

    char line[64];
    while (fgets(line, sizeof line, stdin)) {
        char* end = NULL;
        const long us = strtol(line, &end, 10);
        if (end == line || (us != 0 && (us < 400 || us > 2600))) { printf("err bad pulse width: %s", line); continue; }
        const uint32_t r = *range;
        if (r == 0) { printf("err channel %d has no range: is the pin set up for PWM?\n", ch); continue; }
        *duty = (uint32_t)((uint64_t)us * r / PERIOD_US);
        pwm[0] = pwm[0] | 0x80000000u;            /* GLOBAL_CTRL.SET_UPDATE: latch the new duty */
        printf("ok %ld\n", us);
    }
    *duty = 0;                                     /* stdin closed: stop pulsing */
    pwm[0] = pwm[0] | 0x80000000u;
    return 0;
}
