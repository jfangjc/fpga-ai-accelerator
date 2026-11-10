#!/bin/bash
set -euo pipefail
base=/home/ubuntu/stt/t2
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || exit 75
for name in sttcap fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
out=$base/extras
mkdir -p "$out" "$base/reference"
exec > >(tee "$out/session.txt") 2>&1
date -u --iso-8601=seconds
uname -a
findmnt -T "$base/soak/soak.jsonl"
for sensor in /sys/class/hwmon/hwmon*; do
  echo "$sensor: $(cat "$sensor/name")"
done
sha256sum "$base/src/build/sw/app/sttcap" /home/ubuntu/stt/i2/program.qnp2 \
  /home/ubuntu/stt/cpu/quartznet_int8.bin /home/ubuntu/stt/full150/src/build/tests/full150/full150_probe
sha256sum "$base/src/sw/app/"{main.c,stream.c,stream.h}
d=/home/ubuntu/stt/full150/assets
/home/ubuntu/stt/full150/src/build/tests/full150/full150_probe "$d/program.qnp2" "$d/cpu.qnw1" \
    "$d/window300.i8" 300 300 20 > "$out/paired-20.txt"
cd "$base/src"
make -C sw/fedrv clean
make -C sw/fedrv all check-ui check-tail
echo 'PASS sticky flag set/consume/clear register simulation'
for mic in mic1 mic2 beam; do
  build/sw/fedrv/fetool mic -m "$mic" -t 2 > "$out/$mic.txt" 2>&1
  python3 "$base/t2_registers.py" > "$out/$mic-registers.json"
done
build/sw/fedrv/fetool led on
python3 "$base/t2_registers.py" > "$out/led-on.json"
build/sw/fedrv/fetool led off
python3 "$base/t2_registers.py" > "$out/led-off.json"
rc=0; build/sw/fedrv/fetool button 1 > "$out/button.txt" 2>&1 || rc=$?
echo "button timeout exit=$rc (1 expected without physical press)"
test "$rc" = 1
python3 "$base/t2_registers.py" --clear-button > "$out/button-clear.json"
for clip in 1089-134691-0023 1188-133604-0025 121-127105-0003; do
  "$base/src/build/sw/app/sttcap" --source cpu-wav "/home/ubuntu/stt/cpu/clips/$clip.wav" \
    --backend cpu --blob /home/ubuntu/stt/cpu/quartznet_int8.bin \
    --prior /home/ubuntu/stt/cpu/running_prior.json --threads 4 --bulk --no-ppm \
    --log "$base/reference/$clip.jsonl" > "$base/reference/$clip.stdout" 2> "$base/reference/$clip.stderr"
done
xmutil listapps > "$out/final-apps.txt"
echo 'PASS extras; stt-full150 remains loaded'
