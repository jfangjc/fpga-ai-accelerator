# Install the appliance on the board: scripts/install.ps1 [-Enable]
param([switch]$Enable)
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot/.."
$app = "build/hw/kv260/stt-kv260"
foreach ($file in "data/export/quartznet_engine.bin", "data/export/running_prior.json", "$app/stt-kv260.bit.bin") {
    if (!(Test-Path $file)) { throw "missing ${file}: run python scripts/setup.py and hw/build.tcl first" }
}
$board = "scripts/board.ps1"
$dir = "/home/ubuntu/stt-caption-install"
& $board run -Sudo "rm -rf $dir" # the previous install built in it as root
& $board run "mkdir -p $dir/sw"
foreach ($file in Get-ChildItem sw -File) { & $board put $file.FullName "$dir/sw/" | Out-Null }
& $board put $app "$dir/" | Out-Null
foreach ($file in "data/export/quartznet_engine.bin", "data/export/running_prior.json", "scripts/stt-caption",
                  "scripts/stt-caption.conf", "scripts/stt-caption.service", "scripts/install.sh", "scripts/uninstall.sh") {
    & $board put $file "$dir/" | Out-Null
}
$options = if ($Enable) { "--enable" } else { "" }
& $board run -Sudo "cd $dir && bash install.sh $options && install -m 755 uninstall.sh /opt/stt-caption/"
