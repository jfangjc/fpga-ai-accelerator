#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -gt 1 ]; then
  echo 'Usage: build_moonshine.sh [output-library]' >&2
  exit 2
fi
output=${1:-"$root/build/native/libqnc_moonshine.so"}
case "$output" in /*) ;; *) output="$root/$output" ;; esac
mkdir -p "$(dirname -- "$output")"
${CC:-gcc} -O3 -fPIC -std=c11 -ffp-contract=off -shared \
  "$root/software/ort_fpga_stream_branch512_neon/qnc_transport.c" \
  -o "$output" -lm
echo "Built raw bulk transport: $output"
