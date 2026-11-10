#!/usr/bin/env bash
set -euo pipefail
cd "${STT_ROOT:-$(dirname "$0")/../..}"  # source tree root
if (( $# != 2 )); then echo 'usage: i2.sh PROGRAM.qnp2 CPU.qnw1 < chunks.tsv' >&2; exit 2; fi
make -C sw/engdrv i2
# TSV: saved channel-major INT8 feature path, allocated frames, valid frames.
while IFS=$'\t' read -r features frames valid; do
  [[ -z "$features" || "$features" == \#* ]] && continue
  build/sw/engdrv/i2 "$1" "$2" "$features" "$frames" "$valid"
done
