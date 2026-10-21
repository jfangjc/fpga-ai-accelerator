#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$root/build/native"
${CC:-gcc} -O3 -std=c11 -Wall -Wextra -Werror \
  "$root/software/moonshine/voice_capture.c" -o "$root/build/native/voice_capture"
