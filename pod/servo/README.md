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

- **Power:** a small servo (SG90, MG90S) is fine on the Pi's 5V pin. A big servo (MG996R and similar) can pull enough current to reset the Pi: power it from a separate 5V supply and connect that supply's ground to Pi pin 6 too.
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
| `--speed` | 6 | degrees per second (180° takes about 30 s) |
| `--min-angle`, `--max-angle` | 10, 170 | sweep range; staying off the ends stops buzzing |
| `--min-us`, `--max-us` | 500, 2500 | pulse width at 0° / 180°. If it only turns about 90°, keep these; if it strains at the ends, try 1000 / 2000 |
| `--after-capture` | 2 | seconds to stay on a captured animal before moving on |
| `--ignore` | 4 | seconds to ignore the camera after moving on |
| `--max-hold` | 10 | give up on an unconfirmed animal after this long |
| `--pin` | 18 | BCM GPIO of the signal wire |

Uses QNX's `rpi_gpio` Python module (hardware PWM, 50 Hz, MS mode). If it fails with a permission error, run it through `sudo -n` like pod-infer. The QNX docs list GPIO 12, 13, 18 and 19 as hardware PWM pins but don't say whether that's confirmed for the Pi 5: if the servo doesn't move on pin 12, try `--pin 13` with the signal wire on **physical pin 33**.

## Tested

On the laptop only (`--dry-run` against a scripted status file): stops on a new animal, moves on 2 s after it's captured, doesn't stop again on the same captured animal, gives up on an unconfirmed one once. pod-infer's `--scan-status` compiles against the TFLite 2.16 headers. **Not yet run with the real servo on the Pi.**
