#!/bin/sh
# Output-path stall check: each clip through paced pl-inject with a loopback
# TCP client, per-event output stage times (--stages) and 0.2 s samples of
# /proc/meminfo Dirty/Writeback. Invoke under flock /home/ubuntu/stt/board.lock.
# Usage: sh stall_board.sh STTCAP OUTDIR BACKEND CLIP... (extra sttcap
# options in $STTCAP_ARGS; a PL backend needs --program in it).
set -eu
app=$1; out=$2; backend=$3; shift 3
cpu=/home/ubuntu/stt/cpu
client=$(cd "$(dirname "$0")/../../tools" && pwd)/caption_client.py
mkdir -p "$out"; cd "$out"
for clip in "$@"; do
  echo "RUN $clip $backend"
  (while :; do
     printf '%s ' "$(cut -d' ' -f1 /proc/uptime)"
     grep -E '^(Dirty|Writeback):' /proc/meminfo | tr -s ' ' | tr '\n' ' '; echo
     sleep 0.2
   done) > "$clip.$backend.mem" &
  mem=$!
  python3 "$client" 127.0.0.1 --port 8765 --json --wait 30 > "$clip.$backend.tcp.jsonl" &
  tcp=$!
  cut -d' ' -f1 /proc/uptime > "$clip.$backend.start"
  "$app" --source pl-inject "$cpu/clips/$clip.wav" --blob "$cpu/quartznet_int8.bin" \
    --prior "$cpu/running_prior.json" --backend "$backend" --threads 3 --port 8765 \
    --log "$clip.$backend.jsonl" --stages "$clip.$backend.stages.csv" ${STTCAP_ARGS:-} \
    > "$clip.$backend.stdout" 2> "$clip.$backend.stderr" || echo "FAIL $clip $?"
  wait "$tcp" || true
  kill "$mem" 2>/dev/null || true
done
echo COMPLETE
