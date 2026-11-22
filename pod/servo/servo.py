"""pod-servo: slowly pans the webcam on a 180-degree hobby servo (with-motor branch).

  - Sweeps back and forth very slowly.
  - Stops as soon as pod-infer is SEEING an animal (CLIP is voting for it).
  - Starts sweeping again once that animal is CAPTURED (pod-infer confirmed it, so it's
    already on its way to the dashboard), then ignores the camera for a few seconds so it
    doesn't stop on the same animal again.
  - If it stops but nothing gets confirmed within --max-hold seconds (a false alarm), it moves on.

Reads the small JSON that pod-infer writes with --scan-status (see pod/infer/main.cpp):
    {"t_ms": ..., "seeing": "deer"|null, "confirmed": "deer"|null, "captures": 3}

Low power (written for an MG996R, which can pull 0.5 A moving and ~2.5 A when it stalls):
  - it moves in small steps and only sends pulses while a step settles; the rest of the time,
    and whenever it's stopped on an animal, it sends NO pulses, so the motor isn't pushing;
  - it starts from the middle and stays away from the ends (20-160 degrees), where it strains most.
Software can't make an MG996R safe on the Pi's 5V pin: give it its own 5V supply (see README).

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


def write_report(path, **fields):
    """Debug status for pod-view's page (/servo.json): what the servo is doing right now."""
    if not path:
        return
    fields["t_ms"] = int(time.time() * 1000)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fields, f)
        os.replace(tmp, path)
    except OSError:
        pass


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
        self.driving = False
        if args.dry_run:
            return
        import rpi_gpio as GPIO      # QNX's GPIO module (resource manager: rpi_gpio)
        self.GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(args.pin, GPIO.OUT)
        self.pwm = GPIO.PWM(args.pin, 50, GPIO.PWM.MODE_MS)   # 50 Hz = 20 ms period
        self.pwm.start(0)                                     # no pulses until the first move

    def duty(self, angle):
        """Angle (0..180) -> duty cycle % of the 20 ms period (pulse --min-us .. --max-us)."""
        us = self.args.min_us + (self.args.max_us - self.args.min_us) * angle / 180.0
        return us / 20000.0 * 100.0

    def move(self, angle):
        self.driving = True
        if self.pwm:
            self.pwm.ChangeDutyCycle(self.duty(angle))

    def relax(self):
        """Stop sending pulses: the motor stops pushing and draws almost nothing (gears hold the camera)."""
        if self.driving and self.pwm:
            self.pwm.ChangeDutyCycle(0)
        self.driving = False

    def close(self, angle):
        if self.pwm:
            self.pwm.stop()
            self.GPIO.cleanup()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--status", default="pod-data/scan.json", help="pod-infer --scan-status file")
    p.add_argument("--pin", type=int, default=18, help="BCM GPIO for the servo signal (18 = physical pin 12, hardware PWM)")
    p.add_argument("--step", type=float, default=2.0, help="degrees per step (small steps = small current spikes)")
    p.add_argument("--settle", type=float, default=0.3, help="seconds of pulses per step (time to reach the new angle)")
    p.add_argument("--rest", type=float, default=0.4, help="seconds with NO pulses between steps (saves power)")
    p.add_argument("--min-angle", type=float, default=20.0, help="sweep limits; the ends are where it strains most")
    p.add_argument("--max-angle", type=float, default=160.0)
    p.add_argument("--min-us", type=float, default=500.0, help="pulse width at 0 degrees (many servos: 500-1000)")
    p.add_argument("--max-us", type=float, default=2500.0, help="pulse width at 180 degrees (many servos: 2000-2500)")
    p.add_argument("--after-capture", type=float, default=2.0, help="seconds to stay on a captured animal before moving on")
    p.add_argument("--ignore", type=float, default=4.0, help="seconds to ignore the camera after moving on")
    p.add_argument("--max-hold", type=float, default=10.0, help="give up on an unconfirmed animal after this many seconds")
    p.add_argument("--tick", type=float, default=0.05, help="control loop period, seconds")
    p.add_argument("--dry-run", action="store_true", help="no GPIO: print what it would do")
    p.add_argument("--report", default="pod-data/servo.json", help="debug status file, shown by pod-view ('' = off)")
    args = p.parse_args()

    try:
        servo = Servo(args)
    except Exception as e:                          # e.g. no rpi_gpio module, /dev/gpio permission
        msg = f"{type(e).__name__}: {e}"
        print(f"pod-servo: cannot start the servo: {msg}", flush=True)
        write_report(args.report, state="error", error=msg, pin=args.pin, pulses=False, angle=None)
        return 1
    started_ms, moves = int(time.time() * 1000), 0
    last_report = 0.0
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *a: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *a: stop.update(now=True))

    angle, direction = 90.0, 1.0               # start from the middle: smallest first jump
    servo.move(angle)
    next_step_at, relax_at = time.time() + 1.0, time.time() + 1.0
    driven_s, started = 0.0, time.time()
    state, hold_since, hold_captures, resume_at, ignore_until = SWEEP, 0.0, 0, 0.0, 0.0
    skip_species, skip_until = None, 0.0      # after a false alarm, don't stop on it again for a while
    last_print = 0.0
    speed = args.step / (args.settle + args.rest)
    print(f"pod-servo: pin GPIO{args.pin}, {speed:.1f} deg/s in {args.step:.0f}-degree steps "
          f"(pulses {100 * args.settle / (args.settle + args.rest):.0f}% of the time), {args.min_angle:.0f}-{args.max_angle:.0f} deg, "
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
                servo.relax()                      # stopped on an animal: no pulses, no pushing
                print(f"pod-servo: stop at {angle:.0f} deg, seeing {seeing}", flush=True)
            elif now >= next_step_at:              # one small step, then rest
                angle += direction * args.step
                if angle >= args.max_angle:
                    angle, direction = args.max_angle, -1.0
                elif angle <= args.min_angle:
                    angle, direction = args.min_angle, 1.0
                servo.move(angle)
                moves += 1
                relax_at, next_step_at = now + args.settle, now + args.settle + args.rest
            elif now >= relax_at:
                servo.relax()
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
                next_step_at = now

        if servo.driving:
            driven_s += args.tick
        if now - last_report >= 0.25:
            if state == SWEEP:
                label = "turning" if now >= ignore_until else "turning (ignoring camera for a moment)"
            elif resume_at:
                label = f"captured {confirmed or seeing or 'animal'}, moving on soon"
            else:
                label = f"stopped: looking at {seeing or 'an animal'}"
            write_report(args.report, state=state, label=label, angle=round(angle, 1), pulses=servo.driving,
                         duty_pct=round(servo.duty(angle), 2) if servo.driving else 0.0, pin=args.pin,
                         moves=moves, seeing=seeing, confirmed=confirmed, captures=captures,
                         status_file_ok=st is not None, dry_run=args.dry_run, started_ms=started_ms, error=None)
            last_report = now
        if args.dry_run and now - last_print > 1.0:
            print(f"  {state:5s} angle {angle:5.1f} {'PULSES' if servo.driving else 'relaxed'}  seeing={seeing} captures={captures}", flush=True)
            last_print = now
        time.sleep(args.tick)

    servo.relax()
    write_report(args.report, state="stopped", label="servo program stopped", angle=round(angle, 1), pulses=False,
                 pin=args.pin, moves=moves, started_ms=started_ms, error=None)
    print(f"pod-servo: motor powered {100 * driven_s / max(1e-6, time.time() - started):.0f}% of the time", flush=True)
    servo.close(angle)
    print("pod-servo: stopped", flush=True)


if __name__ == "__main__":
    sys.exit(main())
