param([string]$Design='loopback')
$ErrorActionPreference='Stop'
if($Design -notmatch '^[a-z0-9-]+$'){throw 'Invalid design'}
# Projects, bitstreams, reports and logs all go to build/hw.
New-Item -ItemType Directory -Force "$PSScriptRoot/../build/hw" | Out-Null
& 'C:/AMDDesignTools/2026.1/Vivado/bin/vivado.bat' -mode batch -log "$PSScriptRoot/../build/hw/$Design.log" -journal "$PSScriptRoot/../build/hw/$Design.jou" -source "$PSScriptRoot/create.tcl" -tclargs $Design
if($LASTEXITCODE){throw 'Vivado failed'}
& "$PSScriptRoot/package.ps1" $Design
