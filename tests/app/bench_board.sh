#!/bin/sh
# Invoke under flock ~/stt/board.lock; caller owns the mutex for the entire batch.
set -eu
cd /home/ubuntu/stt/app
mkdir -p measurements
while IFS= read -r clip; do
    clip=$(printf '%s' "$clip" | tr -d '\r')
    [ -n "$clip" ] || continue
    echo "RUN $clip"
    ../build/sw/app/sttcap --source pl-inject "../cpu/clips/$clip.wav" \
        --blob ../cpu/quartznet_int8.bin --prior ../cpu/running_prior.json \
        --threads 3 --mel-threshold "${1:-0.01}" --vad-log "measurements/$clip.vad.csv" \
        --log "measurements/$clip.jsonl" > "measurements/$clip.stdout"
done < testset_40.txt
echo COMPLETE
