param([string]$Design='loopback')
$ErrorActionPreference='Stop'
if($Design -notmatch '^[a-z0-9-]+$'){throw 'Invalid design'}
$out="$PSScriptRoot/../build/hw/$Design"
"all: { stt-$Design.bit }" | Set-Content "$out/image.bif"
Push-Location $out
try { & 'C:/AMDDesignTools/2026.1/Vitis/bin/bootgen.bat' -arch zynqmp -image image.bif -process_bitstream bin -w on; if($LASTEXITCODE){throw 'bootgen failed'} } finally { Pop-Location }
Copy-Item "$PSScriptRoot/$Design.dtso" "$out/stt-$Design.dtso"
$linuxOut=(& wsl -d FedoraLinux-44 -- wslpath -u $out.Replace('\','/')).Trim()
$linuxSource=(& wsl -d FedoraLinux-44 -- wslpath -u "$PSScriptRoot/$Design.dtso".Replace('\','/')).Trim()
# One UIO node per DMA interrupt shares the AXI DMA register block, so silence
# dtc's unique_unit_address warning (stderr would abort under ErrorAction Stop).
& wsl -d FedoraLinux-44 -- dtc -@ -W no-unique_unit_address -I dts -O dtb -o "$linuxOut/stt-$Design.dtbo" $linuxSource
if($LASTEXITCODE){throw 'dtc failed'}
Copy-Item "$PSScriptRoot/shell.json" $out

