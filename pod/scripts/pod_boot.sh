#!/bin/sh
# Starts the Pod at power-on. Owner: agent-1 (P3-1).
# Runs as root, in the background, from the line that boot_hook.sh adds to
# /usr/etc/startup/full_startup.sh. Safe to run by hand too:  sudo sh pod/scripts/pod_boot.sh
#
#   1. waits for the stock post_startup.sh to finish (it starts USB, drm, sensor ...)
#   2. graphics driver drm-rpi5, if missing: rpi_thermal needs its /dev/vcio for a real reading
#   3. rpi_thermal, if missing: temperature at /dev/thermal, fan left at the fixed boot speed
#   4. camera service on the headless Pod config (camera_service.sh pod)
#   5. pod-supervisor as qnxuser, which starts pod-capture, pod-log, pod-uplink
#
# Skip it for one boot:  touch ~/pod-noboot   (delete the file to re-enable)
# Ground station URL:    echo 'POD_GROUND_URL=http://172.20.10.9:8000' > ~/pod.env
# Logs:                  ~/pod-deploy/pod-data/boot.log and supervisor.log
POD_USER=qnxuser
POD_UID=1000
# uid:gid,supplementary groups. 521 = sensor (the camera nodes are rw only for it), 36 = screen, 523 = mbox.
# "on -u" drops the user's other groups unless they are listed here.
POD_IDS=1000:1000,521,36,523
POD_HOME=/data/home/$POD_USER
DIR=$POD_HOME/pod-deploy
DATA=$DIR/pod-data
export PATH=/usr/local/bin:/usr/bin:/proc/boot:/system/bin:/system/sbin
export LD_LIBRARY_PATH=/usr/local/lib:/usr/lib:/usr/lib/dll:/proc/boot:/system/lib:/system/lib/dll

[ -e $POD_HOME/pod-noboot ] && exit 0
mkdir -p $DATA && chown $POD_UID:$POD_UID $DATA
exec >> $DATA/boot.log 2>&1
echo "=== $(date) pod_boot start"

running() { pidin -f an | grep -q "$1"; }   # -f an: name + args ("-f a" omits the name)

if running '[p]od-supervisor'; then
    echo "pod-supervisor already running, nothing to do"
    exit 0
fi

i=0
while running '[p]ost_startup' && [ $i -lt 180 ]; do sleep 1; i=$((i + 1)); done
echo "post_startup finished (waited ${i} s)"

if [ ! -e /dev/vcio ]; then
    echo "starting drm-rpi5"
    drm-rpi5 -u 36:36
    waitfor /dev/vcio 15 || echo "WARNING: /dev/vcio did not appear"
fi

if [ ! -e /dev/thermal ]; then
    echo "starting rpi_thermal (vcio, no fan control)"
    on -d rpi_thermal -M vcio -f off
    waitfor /dev/thermal 5 || echo "WARNING: /dev/thermal did not appear"
    echo 200 > /dev/fan
fi
[ -e /dev/thermal ] && echo "temperature $(cat /dev/thermal) C"

sh $DIR/pod/scripts/camera_service.sh pod
waitfor /dev/sensor/camera5 20 || echo "WARNING: /dev/sensor/camera5 did not appear (webcam unplugged?)"

POD_GROUND_URL=http://172.20.10.9:8000
[ -f $POD_HOME/pod.env ] && . $POD_HOME/pod.env
echo "starting pod-supervisor, ground $POD_GROUND_URL"
cd $DIR || exit 1
HOME=$POD_HOME POD_GROUND_URL=$POD_GROUND_URL \
    on -d -u $POD_IDS ./build/supervisor/pod-supervisor --conf pod/supervisor/pod.conf \
    --state pod-data/supervisor >> $DATA/supervisor.log 2>&1
sleep 3
running '[p]od-supervisor' && echo "pod-supervisor running" || echo "ERROR: pod-supervisor did not start, see $DATA/supervisor.log"
