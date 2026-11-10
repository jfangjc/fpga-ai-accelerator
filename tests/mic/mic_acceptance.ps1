# Acoustic acceptance test of the appliance: the board runs the installed
# stt-caption control script on the live microphone, this PC plays the clip
# playlist through its speaker and receives the TCP caption stream. Receipt
# and playback times share this PC's clock, so latency covers speaker output,
# acoustics, capture, inference, emission and TCP delivery.
#   tests/mic/mic_acceptance.ps1 -Playlist tmp/mic/all40.wav -Tag t40 [-Control PATH]
param([Parameter(Mandatory=$true)][string]$Playlist,[Parameter(Mandatory=$true)][string]$Tag,
      [string]$Control='/home/ubuntu/stt/mic/stt-caption',[double]$Tail=6)
$ErrorActionPreference='Stop'
if($Tag -notmatch '^[a-zA-Z0-9_-]+$'){throw 'Invalid tag'}
$root=Split-Path (Split-Path $PSScriptRoot); Set-Location $root
$board=Join-Path $root tools/board.ps1
$meta=Get-Content ($Playlist -replace '\.wav$','.json') -Raw | ConvertFrom-Json
$evidence="results/mic/$Tag"
if(Test-Path $evidence){throw "Evidence exists: $evidence"}
New-Item -ItemType Directory -Force $evidence | Out-Null
Copy-Item ($Playlist -replace '\.wav$','.json') "$evidence/playlist.json"
$hostLine=Get-Content .board.env | Where-Object {$_ -match '^BOARD_HOST='}
$boardHost=($hostLine -replace '^BOARD_HOST=','').Trim().Trim('"').Trim("'")
foreach($f in 'mic_session.sh'){ & $board put "tests/mic/$f" "stt/mic/$f" | Out-Null }
& $board put product/stt-caption stt/mic/stt-caption | Out-Null
$seconds=[math]::Ceiling($meta.seconds+$Tail+3)
$cmd="tr -d '\r' < /home/ubuntu/stt/mic/mic_session.sh > /home/ubuntu/stt/mic/mic_session.lf.sh; tr -d '\r' < $Control > $Control.lf; bash /home/ubuntu/stt/mic/mic_session.lf.sh $Control.lf $seconds /home/ubuntu/stt/mic/$Tag 2>&1"
$job=Start-Job -ScriptBlock { param($b,$c) & $b run -Sudo $c 2>&1 | ForEach-Object { "$_" } } -ArgumentList $board,$cmd
$client=$null
try {
  $deadline=(Get-Date).AddSeconds(120)
  while(-not ((Receive-Job $job -Keep) -match 'READY')) {
    if($job.State -ne 'Running'){ Receive-Job $job; throw 'board session ended before READY' }
    if((Get-Date) -gt $deadline){ throw 'timed out waiting for READY' }
    Start-Sleep -Milliseconds 100
  }
  $tcpOut=(Join-Path $root "$evidence/pc-tcp.tsv")
  $client=Start-Job -ScriptBlock {
    param($h,$out)
    $c=New-Object System.Net.Sockets.TcpClient; $c.NoDelay=$true; $c.Connect($h,8765)
    $r=New-Object System.IO.StreamReader($c.GetStream(),[Text.Encoding]::UTF8)
    $w=New-Object System.IO.StreamWriter($out,$false,(New-Object Text.UTF8Encoding($false)))
    while(($line=$r.ReadLine()) -ne $null){ $w.WriteLine([DateTime]::UtcNow.ToString('o')+"`t"+$line); $w.Flush() }
    $w.Close(); $c.Close()
  } -ArgumentList $boardHost,$tcpOut
  Start-Sleep -Seconds 2
  if($client.State -ne 'Running'){ Receive-Job $client; throw 'TCP client failed' }
  $player=New-Object System.Media.SoundPlayer (Resolve-Path $Playlist).Path
  $player.Load()
  $start=[DateTime]::UtcNow
  $player.PlaySync()
  $end=[DateTime]::UtcNow
  @{play_start_utc=$start.ToString('o');play_end_utc=$end.ToString('o');wav_seconds=$meta.seconds} |
    ConvertTo-Json | Set-Content -Encoding ascii "$evidence/playback.json"
  Wait-Job $job | Out-Null
  Receive-Job $job | Tee-Object -FilePath "$evidence/board-session.txt"
  Wait-Job $client -Timeout 30 | Out-Null
} finally {
  if($client){ Stop-Job $client -ErrorAction SilentlyContinue; Remove-Job $client -Force }
  Remove-Job $job -Force
}
& $board get "stt/mic/$Tag/*" $evidence | Out-Null
$python = if(Test-Path '.venv/Scripts/python.exe'){ @('.venv/Scripts/python.exe') } else { @('wsl','-d','FedoraLinux-44','--','.venv/bin/python') }  # Windows venv, else WSL
& $python[0] @($python | Select-Object -Skip 1) tests/mic/mic_analyze.py $evidence
if($LASTEXITCODE){throw 'analysis failed'}
