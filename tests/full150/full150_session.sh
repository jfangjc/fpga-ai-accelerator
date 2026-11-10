#!/bin/bash
# Entire deploy/test/restore session holds one advisory lock. Run as root.
set -euo pipefail
base=/home/ubuntu/stt/full150
exec 9>/home/ubuntu/stt/board.lock
flock -n 9 || { echo 'BUSY board.lock'; exit 75; }
users=$(pgrep -a -f '[s]ttcap|[i]2|[p]robe|[b]ench|[f]etool' | awk '$2 !~ /^\[/' || true)
test -z "$users" || { printf 'BUSY processes:\n%s\n' "$users"; exit 75; }
mkdir -p "$base/results"
exec > >(tee "$base/results/session.log") 2>&1
test -f /lib/firmware/xilinx/stt-full/stt-full.bit.bin
test -f /lib/firmware/xilinx/stt-full/stt-full.dtbo
restore() {
 local rc=$?
 trap - EXIT
 echo "Restoring stt-full; test exit=$rc"
 xmutil unloadapp || true
 if ! xmutil loadapp stt-full; then echo 'RESTORE FAILED'; exit 1; fi
 xmutil listapps
 echo 'RESTORED stt-full'
 exit "$rc"
}
# Install restoration before changing the loaded app.
trap restore EXIT
mkdir -p /lib/firmware/xilinx/stt-full150
cp "$base/firmware/"* /lib/firmware/xilinx/stt-full150/
xmutil unloadapp
xmutil loadapp stt-full150
xmutil listapps
cat /sys/kernel/debug/clk/clk_summary > "$base/results/clocks.txt"
awk '$1 == "pl0_ref" { n++; if ($5 < 99999000 || $5 > 100001000) exit 1 }
     $1 == "pl1_ref" { n++; if ($5 < 149999000 || $5 > 150001000) exit 1 }
     END { if (n != 2) exit 1 }' "$base/results/clocks.txt"
cat /proc/interrupts > "$base/results/interrupts-before.txt"
for n in /sys/class/uio/uio*/name; do echo "$n: $(cat "$n")"; done
for b in /sys/class/u-dma-buf/udmabuf{0,1,2,3}; do echo "$b $(cat "$b/size")"; done
cd "$base/src"
make -C sw/engdrv i2
mkdir -p build/tests/full150
cc -O3 -std=c11 -Wall -Wextra -Werror tests/full150/full150_probe.c build/sw/engdrv/libengdrv.a sw/qnrt/qnrt.c -pthread -lm -o build/tests/full150/full150_probe
d=$base/assets
while IFS=$'\t' read -r x t v; do
 build/sw/engdrv/i2 "$d/program.qnp2" "$d/cpu.qnw1" "$x" "$t" "$v"
 ./build/tests/full150/full150_probe "$d/program.qnp2" "$d/cpu.qnw1" "$x" "$t" "$v" 0
done < "$d/chunks.tsv" | tee "$base/results/i2.log"
./build/tests/full150/full150_probe "$d/program.qnp2" "$d/cpu.qnw1" "$d/window300.i8" 300 300 20 | tee "$base/results/paired.log"
cd /home/ubuntu/stt/fedrv
for mode in bulk paced; do
 pace=(); test "$mode" = bulk || pace=(-p)
 ../build/sw/fedrv/fetool clip "${pace[@]}" -n 1 -P /home/ubuntu/stt/i1/prior.bin /home/ubuntu/stt/i1/1089-134691-0023.pcm "$base/results/frontend.$mode.bin" > "$base/results/frontend.$mode.log" 2>&1
done
cat /proc/interrupts > "$base/results/interrupts-after.txt"
echo 'PASS full150 acceptance and paired timing'
