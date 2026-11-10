#!/bin/bash
# Board side of tests/mic/mic_play.ps1: run sttcap on the live microphone for a
# fixed time, print READY once it is listening, then keep the evidence.
#   mic_run.sh OUTDIR SECONDS APP_DIR [extra sttcap options...]
set -euo pipefail
out=$1; seconds=$2; app=$3; shift 3
exec 9>/home/ubuntu/stt/board.lock
flock -w 30 9 || { echo 'Board busy'; exit 75; }
rm -rf "$out"; mkdir -p "$out"; cd "$out"
cpu=/home/ubuntu/stt/cpu
cmd=("$app/build/sw/app/sttcap" --source pl-mic --mic 1 --backend pl --program /home/ubuntu/stt/i2/program.qnp2
     --blob "$cpu/quartznet_int8.bin" --prior "$cpu/running_prior.json" --threads 3
     --log session.jsonl --vad-log vad.csv --stages stages.csv --no-ppm "$@")
printf '%q ' "${cmd[@]}" > command.txt
timeout -s INT "$seconds" "${cmd[@]}" > stdout.txt 2> stderr.txt &
pid=$!
for _ in $(seq 600); do grep -q '"Listening"' session.jsonl 2>/dev/null && break; sleep 0.1; done
echo READY
rc=0; wait $pid || rc=$?
echo "sttcap rc=$rc (124 = stopped by timeout)"
tail -2 stderr.txt
chmod -R a+rX "$out"
