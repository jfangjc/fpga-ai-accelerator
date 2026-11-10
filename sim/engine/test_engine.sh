#!/usr/bin/env bash
# Build the full-engine Verilator model and run the per-layer vectors.
#   test_engine.sh [BUILD_DIR]   (default: build/sim/engine)
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$src/../.." && pwd)
build=${1:-$root/build/sim/engine}
mkdir -p "$build" && cd "$build"
"$root/.venv/bin/python" "$src/layer_vectors.py"
sources=("$root/rtl/engine/stt_engine_pkg.sv")
for f in "$root"/rtl/engine/*.sv; do [[ "$f" == *stt_engine_pkg.sv ]] || sources+=("$f"); done
verilator --cc --exe --build -j 8 -CFLAGS -O3 -Wall --top-module stt_engine --Mdir obj_engine "${sources[@]}" "$src/tb_engine.cpp"
./obj_engine/Vstt_engine
