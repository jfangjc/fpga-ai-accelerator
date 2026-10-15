#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test "$(uname -m)" = aarch64 || { echo 'Build on an ARM64 system' >&2; exit 1; }
mkdir -p "$root/build/native"
cc=${CC:-gcc}
cxx=${CXX:-g++}
"$cc" -O3 -fPIC -std=c11 -c "$root/software/ort_fpga_stream_branch512_neon/qnc_transport.c" -o "$root/build/native/qnc.o"
"$cxx" -O3 -fPIC -std=c++17 -ffp-contract=off -shared "$root/software/ort_fpga_stream_branch512_neon/pointwise.cc" "$root/build/native/qnc.o" -o "$root/build/native/libort_fpga.so" -pthread -lm
for name in boundary_lut residual_tail residual_tail_maskfast se_pool; do
    "$cxx" -O3 -fPIC -std=c++17 -ffp-contract=off -shared "$root/software/latency/$name.cc" -o "$root/build/native/lib$name.so" -pthread -lm
done
echo 'Built libraries under build/native. Shipped libraries remain unchanged.'
