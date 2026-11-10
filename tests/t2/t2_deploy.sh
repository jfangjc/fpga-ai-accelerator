#!/bin/bash
# Run as root. Keep board.lock across deployment, build and quick I2.
set -euo pipefail
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || exit 75
for name in sttcap fetool i2 full150_probe; do
  if pgrep -x "$name"; then echo "BUSY: $name"; exit 75; fi
done
base=/home/ubuntu/stt/t2
mkdir -p "$base/deploy"
exec > >(tee "$base/deploy/session.log") 2>&1
date -u --iso-8601=seconds
xmutil unloadapp
xmutil loadapp stt-full150
xmutil listapps
sha256sum /lib/firmware/xilinx/stt-full150/*
cat /sys/kernel/debug/clk/clk_summary > "$base/deploy/clocks.txt"
tar -xf "$base/source.tar" -C "$base/src"
cd "$base/src"
# Force native rebuild, including transferred dependency archives.
make -C sw/engdrv clean
make -C sw/engdrv CFLAGS='-O3 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror' libengdrv.a
make -C sw/app clean
make -C sw/app CFLAGS='-O2 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror -ffp-contract=off' all check
make -C sw/fedrv check-ui check-tail
sha256sum build/sw/app/sttcap /home/ubuntu/stt/i2/program.qnp2
sh tools/setup-buffers.sh
d=/home/ubuntu/stt/full150/assets
probe=/home/ubuntu/stt/full150/src/build/tests/full150/full150_probe
"$probe" "$d/program.qnp2" "$d/cpu.qnw1" "$d/window300.i8" 300 300 5 > "$base/deploy/paired.log"
head -3 "$d/chunks.tsv" | while IFS=$'\t' read -r x t v; do
  "$probe" "$d/program.qnp2" "$d/cpu.qnw1" "$x" "$t" "$v" 0
done > "$base/deploy/i2.log"
echo 'PASS deployment and quick I2; stt-full150 remains loaded'
