#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$root/build/native"
${CC:-gcc} -O3 -fPIC -std=c11 -ffp-contract=off -shared \
  "$root/software/ort_fpga_stream_branch512_neon/qnc_transport.c" \
  -o "$root/build/native/libqnc_moonshine.so" -lm
echo 'Built raw bulk transport: build/native/libqnc_moonshine.so'
