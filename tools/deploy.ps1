param([string]$Design='loopback')
$ErrorActionPreference='Stop'
if($Design -notmatch '^[a-z0-9-]+$'){throw 'Invalid design'}
$b=Join-Path $PSScriptRoot board.ps1
$out=Join-Path $PSScriptRoot "../build/hw/$Design"
foreach($f in "stt-$Design.bit.bin","stt-$Design.dtbo",'shell.json'){if(!(Test-Path "$out/$f")){throw "Missing $f; build first"}}
& $b run "mkdir -p ~/stt/firmware/stt-$Design"
foreach($f in "stt-$Design.bit.bin","stt-$Design.dtbo",'shell.json'){& $b put "$out/$f" "stt/firmware/stt-$Design/"}
& $b run -Sudo "mkdir -p /lib/firmware/xilinx/stt-$Design; cp /home/ubuntu/stt/firmware/stt-$Design/* /lib/firmware/xilinx/stt-$Design/; xmutil unloadapp; xmutil loadapp stt-$Design"
