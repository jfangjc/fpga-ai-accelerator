#!/bin/bash
# Invoke under flock /home/ubuntu/stt/board.lock after deploying stt-full.
set -euo pipefail
cd /home/ubuntu/stt/engdrv
d=/home/ubuntu/stt/i2
cat /proc/interrupts > "$d/interrupts-before.txt"
for n in /sys/class/uio/uio*/name; do echo "$n: $(cat "$n")"; done > "$d/uio.txt"
for b in /sys/class/u-dma-buf/udmabuf{0,1,2,3}; do
  echo "$b"
  for a in size phys_addr sync_mode sync_direction dma_coherent; do
    test ! -f "$b/$a" || { echo "$a"; cat "$b/$a"; }
  done
done > "$d/buffers.txt"
# The archived Windows source may have CRLF; run an LF copy from build/.
mkdir -p build/tests/i2
tr -d '\r' < tests/i2/i2.sh > build/tests/i2/i2.sh
# Probe: wrapped plio symbols for the transport split; qnrt built with the
# CPU-baseline flags (docs/results/cpu.md) for the paired CPU/PL 3 s timing.
make -C sw/engdrv libengdrv.a
cc -O3 -mcpu=cortex-a53 -std=c11 -Wall -Wextra -Werror tests/i2/i2_probe.c sw/qnrt/qnrt.c build/sw/engdrv/libengdrv.a   -Wl,--wrap=plio_buffer_sync_range -Wl,--wrap=plio_dma_start_sized -Wl,--wrap=plio_irq_wait   -pthread -lm -o build/tests/i2/i2_probe
STT_ROOT=$PWD bash build/tests/i2/i2.sh "$d/program.qnp2" "$d/cpu.qnw1" < "$d/chunks.tsv" > "$d/i2.log" 2>&1
./build/tests/i2/i2_probe "$d/program.qnp2" "$d/window1.i8" "$d/cpu.qnw1" > "$d/probe.log" 2>&1
cat /proc/interrupts > "$d/interrupts-after.txt"
# Preserve the accepted front-end path with one independent clip in each mode.
cd /home/ubuntu/stt/fedrv
for mode in bulk paced; do
  pace=(); test "$mode" = bulk || pace=(-p)
  ../build/sw/fedrv/fetool clip "${pace[@]}" -n 1 -P /home/ubuntu/stt/i1/prior.bin \
    /home/ubuntu/stt/i1/1089-134691-0023.pcm "$d/frontend.$mode.bin" \
    > "$d/frontend.$mode.log" 2>&1
done
cd /home/ubuntu/stt/plio
../build/sw/plio/tests/buffers > "$d/plio-buffers.log" 2>&1
../build/sw/plio/tests/loopback > "$d/plio-loopback.log" 2>&1
echo 'PASS hardware run (front-end golden comparison still required on host)'
