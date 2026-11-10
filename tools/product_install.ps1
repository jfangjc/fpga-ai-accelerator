# Build the stt-caption release on this PC and install it on the board.
#   tools/product_install.ps1 [-Tag T] [-NoStart] [-NoEnable]
param([string]$Tag,[switch]$NoStart,[switch]$NoEnable)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot
$board=Join-Path $PSScriptRoot board.ps1
Set-Location $root
$fw='build/release/firmware/stt-full150'
if(-not (Test-Path "$fw/stt-full150.bit.bin")) {
  # The validated overlay lives on the board; keep a local copy for the release.
  New-Item -ItemType Directory -Force $fw | Out-Null
  & $board get 'stt/full150/firmware/*' $fw | Out-Null
}
$rel=@('tools/product_release.sh',$fw); if($Tag){$rel+=$Tag}
$tar=(& wsl -d FedoraLinux-44 -- bash @rel | Select-Object -Last 1).Trim()
if($LASTEXITCODE -or -not (Test-Path $tar)){throw 'release build failed'}
$name=[IO.Path]::GetFileNameWithoutExtension([IO.Path]::GetFileNameWithoutExtension($tar))
& $board run 'mkdir -p ~/stt/release'
& $board put $tar "stt/release/" | Out-Null
$opts=@(); if($NoStart){$opts+='--no-start'}; if($NoEnable){$opts+='--no-enable'}
$remote=@"
cd /home/ubuntu/stt/release && rm -rf $name && tar -xzf $name.tar.gz || exit 1
./$name/install.sh $($opts -join ' ') > $name.install.log 2>&1; rc=`$?
tail -25 $name.install.log; exit `$rc
"@
& $board run -Sudo $remote
Write-Host "Installed $name"
