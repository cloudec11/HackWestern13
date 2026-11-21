#!/bin/sh
# Give the Pi's Ethernet port a static address for the direct-cable fallback.
# See pod/scripts/NETWORK.md. Needs root (run with sudo). Not persistent across reboot.
# UNTESTED: written before an Ethernet cable was available.
IFACE="${1:-cgem0}"
ADDR="${2:-192.168.50.2}"
MASK="${3:-255.255.255.0}"

ifconfig "$IFACE" "$ADDR" netmask "$MASK" up || exit 1
ifconfig "$IFACE"
echo "Laptop side must be 192.168.50.1/24 (see NETWORK.md)."
