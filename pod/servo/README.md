# pod-servo (branch `with-motor`)

Pans the C270 webcam slowly on a 180° hobby servo. It **stops when the AI is seeing an animal**, waits until that animal is **captured** (confirmed by pod-infer, so it's sent to the dashboard), then **sweeps again**. Unconfirmed sightings (false alarms) are given up after 10 s.

## Wiring

A servo plug is `ground | 5V | signal` with 5V in the middle. No three side-by-side Pi pins have that order, so use **3 male-to-female jumper wires**:

| Servo wire | Colour (usual) | Pi physical pin | What it is |
|---|---|---|---|
| Ground | brown or black | **pin 6** | GND |
| Power | red | **pin 4** | 5V |
| Signal | orange, yellow or white | **pin 12** | GPIO 18 (hardware PWM) |

Pin 1 is the corner pin nearest the SD card end, on the inner row; pins 2, 4, 6, 8, 10, 12 run along the **outer edge** of the board. So pins 4 and 6 are the 2nd and 3rd outer pins, and pin 12 is the 6th outer pin.

- **Power (MG996R):** it can pull about 0.5 A while moving and up to about 2.5 A in a spike or a stall, which can reset the Pi. **Give it its own 5-6 V supply (2 A or more)**: supply + to the servo's red wire, supply - to the servo's brown wire **and** to Pi pin 6 (shared ground). The signal wire still goes to Pi pin 12, and the Pi's 5V pin 4 is then not used. A 470-1000 µF capacitor across the servo's power wires smooths the spikes.
- **Software already keeps it gentle:** 2-degree steps with rests, no pulses at all while it's stopped on an animal or resting (about 40% "on" while sweeping), starts from the middle, and stays between 20 and 160 degrees, away from the ends where it strains most.
- **Plug it in with the Pi off.**

## Run

It starts with the Pod (line `servo` in `pod/supervisor/pod.conf`). By hand on the Pi:

```
python3 pod/servo/servo.py --status pod-data/scan.json
python3 pod/servo/servo.py --dry-run --status pod-data/scan.json     # no motor, just prints
```

pod-infer must run with `--scan-status pod-data/scan.json` (it does in pod.conf).

| Option | Default | Meaning |
|---|---|---|
| `--step`, `--settle`, `--rest` | 2, 0.3, 0.4 | 2° steps; pulses for 0.3 s, then none for 0.4 s (about 3°/s) |
| `--min-angle`, `--max-angle` | 20, 160 | sweep range; the ends are where it strains most |
| `--min-us`, `--max-us` | 500, 2500 | pulse width at 0° / 180°. If it only turns about 90°, keep these; if it strains at the ends, try 1000 / 2000 |
| `--after-capture` | 2 | seconds to stay on a captured animal before moving on |
| `--ignore` | 4 | seconds to ignore the camera after moving on |
| `--max-hold` | 10 | give up on an unconfirmed animal after this long |
| `--pin` | 18 | BCM GPIO of the signal wire (12, 13, 18 or 19: the Pi 5's hardware PWM pins) |
| `--pwm-helper` | `./build/servo/servo_pwm` | the helper that writes the pulse width (see below) |

## Why there's a C helper (servo_pwm)

**QNX's `rpi_gpio` Python module can't drive a servo on its own.** It sets the pin, the PWM clock (50 MHz), the channel and the 50 Hz period correctly, but it never stores a duty between 0 and 100 %:
- `ChangeDutyCycle(7.5)` returns without an error, but the channel's duty register stays at **0**, so the pin never pulses.
- `ChangeDutyCycleAbs()` only accepts 0 or 1 (0 % or 100 %).

So `servo.py` uses the module for setup, and sends each pulse width (µs) to **`servo_pwm`** (`servo_pwm.c`). That helper runs as root through `sudo -n` and writes the RP1 PWM0 duty register directly. The pulse is timed by the hardware: precise, with no CPU cost.

Build it on the Pi with `cmake -S pod/servo -B build/servo && cmake --build build/servo`, or deploy with `POD_COMPONENTS="... servo" sh pod/scripts/deploy.sh`. Without the helper, `servo.py` stops with a red `error` on the debug page.

**How it was found (2026-11-22, agent-2), with a multimeter (black probe on pin 6, red on the signal pin, DC):**

| Test | Reading |
|---|---|
| GPIO18 held high as a plain output | 3.3 V (pin and wiring fine) |
| rpi_gpio PWM 50 % on GPIO18, MS mode and PWM mode | 0 V |
| rpi_gpio PWM 50 % on GPIO13 (physical pin 33) | 0 V |
| rpi_gpio setup + 500000 written straight into the duty register | **1.6 V** |

Reading the registers (RP1 PWM0 at `0x1f00098000`, clocks at `0x1f00018000`) showed `CLK_PWM0` enabled, channel range 1,000,000, and duty 0 after every rpi_gpio call. `gpio-rp1 get 18` can't see PWM: its `level` stays 0 even at 50 % duty, so use a meter or the duty register.

Also needed: the Pod's processes must be in group **522 (gpio)** to open `/dev/gpio`. `pod/scripts/pod_boot.sh` adds it.

## Tested

- **On the Pi with the real MG996R (2026-11-22):** the camera pans. The duty register follows the angle, e.g. 1,900 µs at 126°, 2,033 µs at 138° and 2,122 µs at 146°, which is 500 + 2000 × angle / 180. It reads 0 between steps (the designed rest).
- **Laptop** (`--dry-run` against a scripted status file): stops on a new animal, moves on 2 s after it's captured, doesn't stop again on the same captured animal, gives up on an unconfirmed one once.
- **Not yet tested:** stopping on a real animal end to end, and long runs. The Pi rebooted once around 09:01 UTC during testing, cause not confirmed: check the servo has its own supply.

## Debugging: what is the servo doing?

- **Live page:** `http://qnxpi.local:8080` has a **Servo** box under "Detected now", updated twice a second:
  - `turning` (angle, whether it's sending pulses right now, GPIO pin, number of steps)
  - `stopped: looking at deer` / `captured deer, moving on soon`
  - **red `error`** with the reason (e.g. `/dev/gpio` permission, missing `rpi_gpio` module)
  - **orange `not updating`** = pod-servo stopped or crashed (check `pod-data/supervisor.log`)
  - `no servo status` = pod-servo has never run with this pod-view
- **Raw data (for scripts / another Claude):** `curl http://qnxpi.local:8080/servo.json`
  ```
  {"now_ms": ..., "servo": {"state": "sweep", "label": "turning", "angle": 96.0, "pulses": true,
   "duty_pct": 7.83, "pin": 18, "moves": 41, "seeing": null, "confirmed": null, "captures": 2,
   "status_file_ok": true, "dry_run": false, "started_ms": ..., "error": null, "t_ms": ...}}
  ```
  `now_ms - servo.t_ms` = age of the status (Pi clock). `moves` going up + `pulses` toggling = the program is commanding the servo; if that's true and the motor doesn't move, the problem is wiring/power (shared ground!) or PWM on that pin.
- The file itself is `pod-data/servo.json` (`--report`; pod-view reads it with `--servo`, same default path).
