# Acoustic microphone test: run a board command (as root) and play a WAV
# through this PC's default speaker towards the mic once the command prints
# a line containing READY (or after -Lead seconds when -NoReady is given).
#   tests/mic/mic_play.ps1 -Wav tmp/x.wav -Command '...' [-Lead 1] [-NoReady]
param([Parameter(Mandatory=$true)][string]$Wav,[Parameter(Mandatory=$true)][string]$Command,
      [double]$Lead=1,[switch]$NoReady,[int]$ReadyTimeout=90)
$ErrorActionPreference='Stop'
$board=Join-Path (Split-Path (Split-Path $PSScriptRoot)) tools/board.ps1
$wavPath=(Resolve-Path $Wav).Path
$job=Start-Job -ScriptBlock { param($b,$c) & $b run -Sudo $c 2>&1 } -ArgumentList $board,$Command
try {
  if(-not $NoReady) {
    $deadline=(Get-Date).AddSeconds($ReadyTimeout)
    while(-not ((Receive-Job $job -Keep) -match 'READY')) {
      if($job.State -ne 'Running'){ Receive-Job $job; throw 'Board command ended before READY' }
      if((Get-Date) -gt $deadline){ throw 'Timed out waiting for READY' }
      Start-Sleep -Milliseconds 100
    }
  }
  Start-Sleep -Milliseconds ([int]($Lead*1000))
  if($job.State -ne 'Running'){ Receive-Job $job; throw 'Board command ended before playback' }
  Write-Host "playback start $(Get-Date -Format o)"
  (New-Object System.Media.SoundPlayer $wavPath).PlaySync()
  Write-Host "playback end $(Get-Date -Format o)"
  Wait-Job $job | Out-Null
  Receive-Job $job
  if($job.State -ne 'Completed'){ throw 'Board command failed' }
} finally { Remove-Job $job -Force }
