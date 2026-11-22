"""pod-servo: slowly pans the webcam on a 180-degree hobby servo (with-motor branch).

  - Sweeps back and forth very slowly.
  - Stops as soon as pod-infer is SEEING an animal (CLIP is voting for it).
  - Starts sweeping again once that animal is CAPTURED (pod-infer confirmed it, so it's
    already on its way to the dashboard), then ignores the camera for a few seconds so it
    doesn't stop on the same animal again.
  - If it stops but nothing gets confirmed within --max-hold seconds (a false alarm), it moves on.

Reads the small JSON that pod-infer writes with --scan-status (see pod/infer/main.cpp):
    {"t_ms": ..., "seeing": "deer"|null, "confirmed": "deer"|null, "captures": 3}

Drives the servo with QNX's rpi_gpio module (hardware PWM, 50 Hz, "MS" mode, which QNX
recommends for servos). Runs at NORMAL priority under pod-supervisor: the camera, saving
data and the watchdog always come first; the motor can stutter, the Pod keeps watching.

    python3 pod/servo/servo.py --status pod-data/scan.json            # on the Pi
    python3 pod/servo/servo.py --dry-run --status /tmp/scan.json      # anywhere: prints, no GPIO
"""

import argparse
import json
import os
import signal
import sys
import time

SWEEP, HOLD = "sweep", "hold"


def read_status(path, max_age_s):
    """The latest status, or None if missing/old (pod-infer stopped): then we just keep sweeping."""
    try:
        with open(path) as f:
            st = json.load(f)
        if time.time() - st.get("t_ms", 0) / 1000 > max_age_s:
            return None
        return st
    except (OSError, ValueError):
        return None


class Servo:
    def __init__(self, args):
        self.args = args
        self.pwm = None
        if args.dry_run:
            return
        import rpi_gpio as GPIO      # QNX's GPIO module (resource manager: rpi_gpio)
        self.GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(args.pin, GPIO.OUT)
        self.pwm = GPIO.PWM(args.pin, 50, GPIO.PWM.MODE_MS)   # 50 Hz = 20 ms period
        self.pwm.start(self.duty(args.min_angle))

    def duty(self, angle):
        """Angle (0..180) -> duty cycle % of the 20 ms period (pulse --min-us .. --max-us)."""
        us = self.args.min_us + (self.args.max_us - self.args.min_us) * angle / 180.0
        return us / 20000.0 * 100.0

    def move(self, angle):
        if self.pwm:
            self.pwm.ChangeDutyCycle(self.duty(angle))

    def close(self, angle):
        if self.pwm:
            self.pwm.stop()
            self.GPIO.cleanup()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--status", default="pod-data/scan.json", help="pod-infer --scan-status file")
    p.add_argument("--pin", type=int, default=18, help="BCM GPIO for the servo signal (18 = physical pin 12, hardware PWM)")
    p.add_argument("--speed", type=float, default=6.0, help="sweep speed, degrees per second (slow = 4-8)")
    p.add_argument("--min-angle", type=float, default=10.0, help="sweep limits; stay off the ends so the servo doesn't buzz")
    p.add_argument("--max-angle", type=float, default=170.0)
    p.add_argument("--min-us", type=float, default=500.0, help="pulse width at 0 degrees (many servos: 500-1000)")
    p.add_argument("--max-us", type=float, default=2500.0, help="pulse width at 180 degrees (many servos: 2000-2500)")
    p.add_argument("--after-capture", type=float, default=2.0, help="seconds to stay on a captured animal before moving on")
    p.add_argument("--ignore", type=float, default=4.0, help="seconds to ignore the camera after moving on")
    p.add_argument("--max-hold", type=float, default=10.0, help="give up on an unconfirmed animal after this many seconds")
    p.add_argument("--tick", type=float, default=0.05, help="control loop period, seconds")
    p.add_argument("--dry-run", action="store_true", help="no GPIO: print what it would do")
    args = p.parse_args()

    servo = Servo(args)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(now=True))

    angle, direction = args.min_angle, 1.0
    state, hold_since, hold_captures, resume_at, ignore_until = SWEEP, 0.0, 0, 0.0, 0.0
    skip_species, skip_until = None, 0.0      # after a false alarm, don't stop on it again for a while
    last_print = 0.0
    print(f"pod-servo: pin GPIO{args.pin}, {args.speed} deg/s, {args.min_angle}-{args.max_angle} deg, "
          f"status {args.status}{' (dry run)' if args.dry_run else ''}", flush=True)

    while not stop["now"]:
        now = time.time()
        st = read_status(args.status, max_age_s=3.0)
        seeing = st.get("seeing") if st else None
        confirmed = st.get("confirmed") if st else None
        captures = st.get("captures", 0) if st else 0
        # Worth stopping for: an animal that isn't already captured and still in view,
        # and isn't one we just gave up on.
        new_animal = (seeing and seeing != confirmed and now >= ignore_until
                      and not (seeing == skip_species and now < skip_until))

        if state == SWEEP:
            if new_animal:
                state, hold_since, hold_captures, resume_at = HOLD, now, captures, 0.0
                print(f"pod-servo: stop at {angle:.0f} deg, seeing {seeing}", flush=True)
            else:
                angle += direction * args.speed * args.tick
                if angle >= args.max_angle:
                    angle, direction = args.max_angle, -1.0
                elif angle <= args.min_angle:
                    angle, direction = args.min_angle, 1.0
                servo.move(angle)
        else:  # HOLD: wait for the capture, then move on
            if captures > hold_captures and not resume_at:
                resume_at = now + args.after_capture
                print(f"pod-servo: captured {st.get('confirmed') if st else '?'}, moving on in {args.after_capture:.0f} s", flush=True)
            give_up = not resume_at and now - hold_since > args.max_hold
            if (resume_at and now >= resume_at) or give_up:
                if give_up:
                    print("pod-servo: nothing confirmed, moving on", flush=True)
                    skip_species, skip_until = seeing, now + 3 * args.ignore
                state, ignore_until = SWEEP, now + args.ignore

        if args.dry_run and now - last_print > 1.0:
            print(f"  {state:5s} angle {angle:5.1f}  seeing={seeing} captures={captures}", flush=True)
            last_print = now
        time.sleep(args.tick)

    servo.close(angle)
    print("pod-servo: stopped", flush=True)


if __name__ == "__main__":
    sys.exit(main())
