#!/bin/bash
# Build the stt-caption release tarball (run from the repository root, e.g. in WSL).
#   tools/product_release.sh FIRMWARE_DIR [TAG]
# FIRMWARE_DIR holds the validated stt-full150 overlay (bit.bin, dtbo, shell.json).
# Every binary asset is checked against the hashes validated on the board.
set -euo pipefail
fw=$1
tag=${2:-$(date +%Y%m%d)-$(git rev-parse --short HEAD)}
name=stt-caption-$tag
out=build/release/$name
rm -rf "$out"; mkdir -p "$out/assets" "$out/firmware/stt-full150" "$out/pc"

check() {  # path sha256
  echo "$2  $1" | sha256sum --quiet -c - || { echo "unexpected asset: $1" >&2; exit 1; }
}
check data/export/quartznet_int8.bin 7126b03deb7d3715acd66de69f1c6605461f32c961b53c2fc905137c7142a6ec
check data/export/running_prior.json 66a608be12f041c8cefa175e27df4ffbc6b503efa06289c679260ef355f55a41
check data/export/quartznet_qnc2.bin ee07f475801fddca55fd93c2a1fe61f18557626ffc11cee6991f41d78ae9f583
check "$fw/stt-full150.bit.bin" 4cd65023c6c2933fd7704fb373b7428450c8ec37dfd90b690e12a66245adc1d2
check "$fw/stt-full150.dtbo" eae42493bb781e4c9b8a65d8ca6556f6d36c16ec35a74c7565c8d291a00d3081
check "$fw/shell.json" 4b7eb77be43fea924276958c69fd918d6b1cabf7f225d6e703c9dd17c19ad8c0

cp data/export/quartznet_int8.bin data/export/running_prior.json "$out/assets/"; cp data/export/quartznet_qnc2.bin "$out/assets/program.qnp2"
cp "$fw"/stt-full150.bit.bin "$fw"/stt-full150.dtbo "$fw"/shell.json "$out/firmware/stt-full150/"
# Source: tracked application/driver files (no binaries or measurement data).
git ls-files sw/app sw/qnrt sw/frontend_c sw/fedrv sw/plio sw/engdrv |
  grep -v -E '\.(o|a|so|json|jsonl|png|ppm|tar|csv)$' | grep -v -E '/(measurements[a-z-]*|results)/' > "$out/files.txt"
echo sw/app/policy.json >> "$out/files.txt"; echo model/stream_policy.py >> "$out/files.txt"
tar -cf "$out/source.tar" -T "$out/files.txt"; rm "$out/files.txt"
for f in stt-caption stt-caption.service stt-caption.conf install.sh uninstall.sh; do
  tr -d '\r' < "product/$f" > "$out/$f"
done
chmod 755 "$out/stt-caption" "$out/install.sh" "$out/uninstall.sh"
cp tools/caption_client.py tools/caption_live.py "$out/pc/"; cp docs/product.md "$out/README.md"
dirty=$(git diff --quiet HEAD -- sw product model tools docs || echo -dirty)
echo "$tag (git $(git rev-parse --short HEAD)$dirty)" > "$out/VERSION"
(cd "$out" && find . -type f ! -name SHA256SUMS ! -path './pc/*' ! -name README.md | sort | sed 's|^\./||' | xargs sha256sum > SHA256SUMS)
tar -czf "build/release/$name.tar.gz" -C build/release "$name"
echo "build/release/$name.tar.gz"
