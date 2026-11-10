param([ValidateSet('run','put','get')][string]$Action='run',[Parameter(Mandatory=$true)][string]$Source,[string]$Destination,[switch]$Sudo)
$ErrorActionPreference='Stop'
$cfg=@{}
Get-Content (Join-Path $PSScriptRoot '../.board.env') | ForEach-Object { if ($_ -match '^\s*(BOARD_\w+)=(.*)$') { $cfg[$matches[1]]=$matches[2].Trim().Trim('"').Trim("'") } }
foreach($k in 'BOARD_HOST','BOARD_USER','BOARD_PASS','BOARD_HOSTKEY') { if(!$cfg[$k]) {throw "Missing $k in .board.env"} }
$common=@('-batch','-hostkey',$cfg.BOARD_HOSTKEY,'-pw',$cfg.BOARD_PASS)
$remote="$($cfg.BOARD_USER)@$($cfg.BOARD_HOST)"
if ($Sudo) {
 function Quote-Sh([string]$s) { return "'" + $s.Replace("'", "'" + '"' + "'" + '"' + "'") + "'" }
 # Windows PowerShell 5.1 may add a UTF-8 BOM and CRLF when piping to native commands; strip
 # non-printables on the board before handing the password to sudo.
 $Source = "IFS= read -r p; p=`$(printf '%s' `"`$p`" | LC_ALL=C tr -cd '[:print:]'); printf '%s\n' `"`$p`" | sudo -S -p '' sh -c $(Quote-Sh $Source)"
}
if($Action -eq 'run') {
 # -m avoids Windows native argument quoting corrupting remote shell quotes.
 # Only the command goes in this temporary file; the sudo secret uses stdin.
 $commandFile=[IO.Path]::GetTempFileName()
 try {
  [IO.File]::WriteAllText($commandFile,$Source.Replace("`r`n","`n"),[Text.UTF8Encoding]::new($false))
  if($Sudo) { $cfg.BOARD_PASS | & plink @common -ssh $remote -m $commandFile }
  else { & plink @common -ssh $remote -m $commandFile }
  $result=$LASTEXITCODE
 } finally { Remove-Item -LiteralPath $commandFile -Force }
 if($result){throw "Board run failed ($result)"}
}
elseif($Action -eq 'put') { & pscp @common -r $Source "${remote}:$Destination" }
else { & pscp @common -r "${remote}:$Source" $Destination }
if($LASTEXITCODE -ne 0) {throw "Board $Action failed ($LASTEXITCODE)"}
