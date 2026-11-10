#!/bin/sh
# Paced I1 soak with board power logging. Run as root, ideally under nohup:
#   nohup sh /home/ubuntu/stt/fedrv/soak.sh 1800 < /dev/null > /home/ubuntu/stt/i1/soak-launch.log 2>&1 &
# Results: i1/soak.log, i1/soak.exit (0 = pass), i1/soak-power.log, i1/soak.ref.bin.
set -u
if pgrep -a -f '[q]nrt'; then
    echo 'CPU runtime is active; retry after its benchmark finishes.' >&2
    exit 1
fi
secs=${1:-1800}
d=/home/ubuntu/stt/i1
cd /home/ubuntu/stt/fedrv
sh /home/ubuntu/stt/power.sh "$secs" 1 > "$d/soak-power.log" 2>&1 &
power=$!
../build/sw/fedrv/fetool soak -t "$secs" "$d/soak.ref.bin" "$d/loop.pcm" > "$d/soak.log" 2>&1
echo $? > "$d/soak.exit"
wait "$power"
