#!/bin/sh
# Make the Pod start at power-on. Owner: agent-1 (P3-1). Run on the Pi:
#   sh pod/scripts/boot_hook.sh install | remove | status
#
# /system is read-only, but the service manager (SLM) runs /usr/etc/startup/full_startup.sh
# at every boot and /usr is writable by root. install appends ONE marked line that starts
# pod_boot.sh in the background; remove deletes exactly that line. A backup of the original
# is kept at full_startup.sh.pod-backup.
F=/usr/etc/startup/full_startup.sh
BOOT=/data/home/qnxuser/pod-deploy/pod/scripts/pod_boot.sh
MARK='# pod-boot (agent-1)'
LINE="[ -f $BOOT ] && sh $BOOT & $MARK"

case "${1:-status}" in
install)
    if grep -q "$MARK" $F; then echo "boot hook already installed"; exit 0; fi
    [ -f $F.pod-backup ] || sudo -n cp -p $F $F.pod-backup || exit 1
    sudo -n chmod u+w $F && echo "$LINE" | sudo -n tee -a $F > /dev/null && sudo -n chmod 555 $F || exit 1
    echo "boot hook installed:"; tail -1 $F
    ;;
remove)
    if ! grep -q "$MARK" $F; then echo "boot hook not installed"; exit 0; fi
    grep -v "$MARK" $F > /tmp/full_startup.$$ || exit 1
    sudo -n chmod u+w $F && sudo -n cp /tmp/full_startup.$$ $F && sudo -n chmod 555 $F || exit 1
    rm -f /tmp/full_startup.$$
    echo "boot hook removed"
    ;;
status)
    grep -q "$MARK" $F && echo "boot hook installed" || echo "boot hook not installed"
    if [ -e /data/home/qnxuser/pod-noboot ]; then echo "but ~/pod-noboot exists: boot start is skipped"; fi
    ;;
*) echo "usage: $0 install|remove|status"; exit 2 ;;
esac
