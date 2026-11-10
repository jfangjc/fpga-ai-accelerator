#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
build=$(mktemp -d /tmp/stt-act-unpack.XXXXXX)
trap 'rm -rf "$build"' EXIT
verilator --binary --timing -Wall --top-module tb_act_unpack --Mdir "$build" -j 8 act_unpack_lint.vlt ../../rtl/engine/stt_engine_pkg.sv ../../rtl/engine/stt_act_unpack.sv tb_act_unpack.sv > "$build/build.log" 2>&1 || { cat "$build/build.log"; exit 1; }
"$build/Vtb_act_unpack"

