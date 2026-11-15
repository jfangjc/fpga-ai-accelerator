# Board access over SSH (PuTTY, .board.env): scripts/board.ps1 run|put|get SOURCE [DESTINATION] [-Sudo]
param([ValidateSet("run", "put", "get")][string]$Action = "run", [Parameter(Mandatory = $true)][string]$Source,
      [string]$Destination, [switch]$Sudo)
$ErrorActionPreference = "Stop"
$cfg = @{}
Get-Content "$PSScriptRoot/../.board.env" | ForEach-Object {
    if ($_ -match '^\s*(BOARD_\w+)=(.*)$') { $cfg[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'") }
}
$common = @("-batch", "-hostkey", $cfg.BOARD_HOSTKEY, "-pw", $cfg.BOARD_PASS)
$remote = "$($cfg.BOARD_USER)@$($cfg.BOARD_HOST)"
if ($Action -eq "run") {
    # The command goes through a file; with -Sudo the password goes to sudo on stdin
    if ($Sudo) {
        $quoted = "'" + $Source.Replace("'", "'`"'`"'") + "'"
        $Source = "IFS= read -r p; p=`$(printf '%s' `"`$p`" | LC_ALL=C tr -cd '[:print:]'); " +
                  "printf '%s\n' `"`$p`" | sudo -S -p '' sh -c $quoted"
    }
    $file = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($file, $Source.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
        if ($Sudo) { $cfg.BOARD_PASS | & plink @common -ssh $remote -m $file }
        else { & plink @common -ssh $remote -m $file }
    }
    finally { Remove-Item -LiteralPath $file -Force }
}
elseif ($Action -eq "put") { & pscp @common -q -r $Source "${remote}:$Destination" }
else { & pscp @common -q -r "${remote}:$Source" $Destination }
if ($LASTEXITCODE) { throw "board $Action failed ($LASTEXITCODE)" }
