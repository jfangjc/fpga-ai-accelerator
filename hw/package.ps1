param([string]$Design='loopback')
$ErrorActionPreference='Stop'
if($Design -notmatch '^[a-z0-9-]+$'){throw 'Invalid design'}
$out="$PSScriptRoot/../build/hw/$Design"
"all: { stt-$Design.bit }" | Set-Content "$out/image.bif"
Push-Location $out
try { & 'C:/AMDDesignTools/2026.1/Vitis/bin/bootgen.bat' -arch zynqmp -image image.bif -process_bitstream bin -w on; if($LASTEXITCODE){throw 'bootgen failed'} } finally { Pop-Location }
Copy-Item "$PSScriptRoot/$Design.dtso" "$out/stt-$Design.dtso"
# Vivado ships dtc; its overlay is byte-identical to the validated one. One UIO
# node per DMA interrupt shares the AXI DMA register block, so silence dtc's
# unique_unit_address warning.
& 'C:/AMDDesignTools/2026.1/Vivado/bin/dtc.exe' -@ -W no-unique_unit_address -I dts -O dtb -o "$out/stt-$Design.dtbo" "$PSScriptRoot/$Design.dtso"
if($LASTEXITCODE){throw 'dtc failed'}
Copy-Item "$PSScriptRoot/shell.json" $out

