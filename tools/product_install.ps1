# Build the stt-caption release on this PC and install it on the board.
#   tools/product_install.ps1 [-Tag T] [-NoStart] [-NoEnable]
param([string]$Tag,[switch]$NoStart,[switch]$NoEnable)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot
$board=Join-Path $PSScriptRoot board.ps1
Set-Location $root
$fw='build/release/firmware/stt-full150'
if(-not (Test-Path "$fw/stt-full150.bit.bin")) {
  # Take the overlay from a local ./hw/build.ps1 full150 if there is one, otherwise
  # from the board's validated copy. product_release.py checks its hashes either way.
  New-Item -ItemType Directory -Force $fw | Out-Null
  $built='build/hw/full150'
  if(Test-Path "$built/stt-full150.bit.bin") {
    Copy-Item "$built/stt-full150.bit.bin","$built/stt-full150.dtbo","$built/shell.json" $fw
  } else {
    & $board get 'stt/full150/firmware/*' $fw | Out-Null
  }
}
# The release builder needs only the Python standard library.
if(Test-Path "$root/.venv/Scripts/python.exe"){ $python=@("$root/.venv/Scripts/python.exe") }
elseif(Get-Command py -ErrorAction SilentlyContinue){ $python=@('py','-3') }
else { $python=@('python') }
$rel=@($python | Select-Object -Skip 1) + @('tools/product_release.py',$fw); if($Tag){$rel+=$Tag}
$tar=(& $python[0] @rel | Select-Object -Last 1).Trim()
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
