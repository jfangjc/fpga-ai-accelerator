#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
sources=(rtl/engine/stt_engine_pkg.sv)
for f in rtl/engine/*.sv; do [[ "$f" == *stt_engine_pkg.sv ]] || sources+=("$f"); done
verilator --lint-only -Wall --top-module stt_engine "${sources[@]}"
