# Pod network plan (P0-7, agent-1)

Goal: the Pi (Pod) and the laptop (ground station) can always reach each other, even if venue Wi-Fi fails.

## Observed 2026-11-21

| Item | Value |
|------|-------|
| Network | phone hotspot, `172.20.10.0/28` (14 usable hosts), gateway `172.20.10.1` |
| Pi (`qnxpi`) | `172.20.10.11` on Wi-Fi `bcm0`, DHCP via dhcpcd |
| Laptop | `172.20.10.9` on Wi-Fi |
| Pi Ethernet | `cgem0`, no cable, no IP |
| Pi hostname | `qnxpi`, reachable as `qnxpi.local` (SSH key login works) |
| Pi account | `qnxuser` (not root; in `sudo` group; DHCP config in `/usr/etc` is read-only) |

Hotspot addresses are leased and can change. Do not hard-code them.

## Primary: shared Wi-Fi / hotspot

1. Put the laptop and Pi on the same Wi-Fi (the hotspot).
2. Reach the Pi with `ssh qnxuser@qnxpi.local`. Fall back to the IP from `ifconfig bcm0` on the Pi.
3. The Pod finds the ground station through one setting, `POD_GROUND_URL` (default `http://<laptop-ip>:8000`). Never compile an IP into the Pod binaries.
4. Find the laptop IP with `ipconfig` and set `POD_GROUND_URL` when starting `pod-uplink`.
5. Allow inbound TCP 8000 on the laptop firewall (Windows asks the first time the API starts; choose Private network).

## Fallback: direct Ethernet cable with static IPs

Use if the Wi-Fi is down or crowded. No router needed.

| Side | Interface | Address |
|------|-----------|---------|
| Pi | `cgem0` | `192.168.50.2/24` |
| Laptop | Ethernet adapter | `192.168.50.1/24` |

Then `POD_GROUND_URL=http://192.168.50.1:8000`.

Laptop (PowerShell as Administrator; replace `Ethernet` with the adapter name from `ipconfig`):

```
netsh interface ip set address name="Ethernet" static 192.168.50.1 255.255.255.0
```

To undo: `netsh interface ip set address name="Ethernet" dhcp`.

Pi: run `pod/scripts/eth_static.sh` (needs `sudo`). It does not persist across reboot, which is fine for a demo; rerun it after boot.

If the Pi's Wi-Fi is also up, SSH to the Pi at its Ethernet address `192.168.50.2` so traffic stays on the cable.

## Checks

```
ping 192.168.50.2                       (from the laptop)
ssh qnxuser@192.168.50.2 uname -a       (from the laptop)
curl http://192.168.50.1:8000/api/summary   (from the Pi, once agent-3's API is up)
```

## Status

- Wi-Fi path: verified (SSH, git clone over the hotspot).
- Ethernet fallback: written, **not yet tested** (no cable connected, and it needs `sudo` on the Pi).
