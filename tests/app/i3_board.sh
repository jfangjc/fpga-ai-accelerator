#!/bin/sh
# I3 board batch: each clip through paced pl-inject with --backend pl (TCP
# client on loopback, PPM + framebuffer capture, final dumps), then the same
# clip with --backend cpu. Invoke under flock /home/ubuntu/stt/board.lock.
# Usage: sh i3_board.sh STTCAP PROGRAM OUTDIR CLIP...
set -eu
app=$1; program=$2; out=$3; shift 3
cpu=/home/ubuntu/stt/cpu
client=$(cd "$(dirname "$0")" && pwd)/caption_client.py
mkdir -p "$out"; cd "$out"
for clip in "$@"; do
  for backend in pl cpu; do
    echo "RUN $clip $backend"
    rm -rf "$clip.$backend.dump"; mkdir "$clip.$backend.dump"
    python3 "$client" 127.0.0.1 --port 8765 --json --wait 30 > "$clip.$backend.tcp.jsonl" &
    tcp=$!
    "$app" --source pl-inject "$cpu/clips/$clip.wav" --blob "$cpu/quartznet_int8.bin" \
      --prior "$cpu/running_prior.json" --backend "$backend" --program "$program" \
      --threads 3 --port 8765 --log "$clip.$backend.jsonl" --dump "$clip.$backend.dump" \
      > "$clip.$backend.stdout" 2> "$clip.$backend.stderr"
    wait "$tcp"
    cp caption.ppm "$clip.$backend.ppm"
    if [ -r /dev/fb0 ]; then
      cat /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/bits_per_pixel \
          /sys/class/graphics/fb0/stride /sys/class/graphics/fb0/pan > "$clip.$backend.fbinfo"
      dd if=/dev/fb0 of="$clip.$backend.fb" bs=4096 2>/dev/null || true
    fi
  done
done
echo COMPLETE
