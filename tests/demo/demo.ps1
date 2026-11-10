param(
  [ValidateSet('pl','cpu')][string]$Backend='pl',
  [switch]$Fallback,
  [string]$Tag=(Get-Date -Format 'yyyyMMdd-HHmmss'),
  [string[]]$Clips=@('1089-134691-0023','1188-133604-0025','121-127105-0003')
)
$ErrorActionPreference='Stop'
if($Fallback){$Backend='cpu'}
if($Tag -notmatch '^[a-zA-Z0-9_-]+$'){throw 'Invalid evidence tag'}
foreach($clip in $Clips){if($clip -notmatch '^\d+-\d+-\d+$'){throw 'Invalid clip ID'}}
$root=Split-Path (Split-Path $PSScriptRoot)
$board=Join-Path $root 'tools/board.ps1'
$evidence=Join-Path $root "results/t2/demo/$Tag"
if(Test-Path $evidence){throw "Evidence tag already exists: $Tag"}
New-Item -ItemType Directory -Force $evidence | Out-Null
# Reuse the validated native app/assets staged by tests/t2/t2_deploy.sh.
foreach($name in 'demo_board.sh','demo_board.py'){
  & $board put (Join-Path $PSScriptRoot $name) "stt/t2/$name"
}
$hostLine=Get-Content (Join-Path $root '.board.env') | Where-Object {$_ -match '^BOARD_HOST='}
$boardHost=($hostLine -replace '^BOARD_HOST=','').Trim().Trim('"').Trim("'")
$command="{ tr -d '\r' < /home/ubuntu/stt/t2/demo_board.sh > /home/ubuntu/stt/t2/demo_board.lf.sh; bash /home/ubuntu/stt/t2/demo_board.lf.sh $Backend $Tag $($Clips -join ' '); } 2>&1"
$job=Start-Job -ScriptBlock {
  param($helper,$cmd,$transcript)
  $ErrorActionPreference='Stop'
  & $helper run -Sudo $cmd 2>&1 | Tee-Object -FilePath $transcript
} -ArgumentList $board,$command,(Join-Path $evidence 'board-launch.txt')
$client=Start-Job -ScriptBlock {
  param($directory,$address,$output,$rawOutput)
  $ErrorActionPreference='Stop'
  Set-Location $directory
  $python = if(Test-Path '.venv/Scripts/python.exe'){ @('.venv/Scripts/python.exe') } else { @('wsl','-d','FedoraLinux-44','--','.venv/bin/python') }  # Windows venv, else WSL
  & $python[0] @($python | Select-Object -Skip 1) tools/caption_client.py $address --wait 120 --timestamps --output $output --json-output $rawOutput
  if($LASTEXITCODE){throw 'PC TCP client failed'}
} -ArgumentList $root,$boardHost,"results/t2/demo/$Tag/pc-tcp.txt","results/t2/demo/$Tag/pc-tcp.jsonl"
try {
  # Drain both jobs so board startup failures surface immediately.
  while($job.State -eq 'Running' -or $client.State -eq 'Running') {
    Receive-Job $job -ErrorAction Stop
    Receive-Job $client -ErrorAction Stop
    if($job.State -eq 'Failed' -or $client.State -eq 'Failed'){throw 'Demo worker failed'}
    Start-Sleep -Milliseconds 100
  }
  Receive-Job $job -ErrorAction Stop
  Receive-Job $client -ErrorAction Stop
  if($job.State -ne 'Completed' -or $client.State -ne 'Completed'){throw 'Demo worker failed'}
  & $board get "stt/t2/demo/$Tag/evidence/*" $evidence
  $python = if(Test-Path '.venv/Scripts/python.exe'){ @('.venv/Scripts/python.exe') } else { @('wsl','-d','FedoraLinux-44','--','.venv/bin/python') }  # Windows venv, else WSL
  & $python[0] @($python | Select-Object -Skip 1) tests/demo/check_demo.py "results/t2/demo/$Tag"
  if($LASTEXITCODE){throw 'Demo evidence validation failed'}
} finally {
  # Never terminate an active board process on a client error; it finishes replay.
  if($job.State -eq 'Completed' -or $job.State -eq 'Failed'){Remove-Job $job}
  if($client.State -eq 'Running'){Stop-Job $client}
  Remove-Job $client
}
Write-Host "Demo evidence: $evidence"
