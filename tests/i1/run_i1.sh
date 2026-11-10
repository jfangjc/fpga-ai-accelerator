#!/bin/sh
# Run as root after stt-fe is loaded and buffers are allocated.
set -eu
if pgrep -a -f '[q]nrt'; then
    echo 'CPU runtime is active; retry after its benchmark finishes.' >&2
    exit 1
fi
cd /home/ubuntu/stt/fedrv
make clean
make
d=/home/ubuntu/stt/i1
mkdir -p "$d/board"
while IFS= read -r id; do
    for mode in bulk paced; do
        pace=
        [ "$mode" = bulk ] || pace=-p
        ../build/sw/fedrv/fetool clip $pace -n 1 -P "$d/prior.bin" "$d/$id.pcm" "$d/board/$id.$mode.bin" > "$d/board/$id.$mode.log" 2>&1
    done
done < "$d/clips.txt"
cat /proc/interrupts > "$d/board/interrupts.txt"
for n in /sys/class/uio/uio*/name; do printf '%s: ' "$n"; cat "$n"; done > "$d/board/uio.txt"
