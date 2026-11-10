# Collect only reports from the launch recorded in s2a2_vivado.log.
# Optional process wait lets this collector survive an interactive session.
param([int]$VivadoProcessId = 0)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$buildDir = Join-Path $repoRoot 'build/sim/engine'
$logPath = Join-Path $buildDir 's2a2_vivado.log'
$sessionLine = Select-String -Path $logPath -Pattern '# Start of session at: (.+)'
$dateText = ($sessionLine.Matches[0].Groups[1].Value -replace '\s+', ' ').Trim()
$sessionStart = [datetime]::ParseExact($dateText, 'ddd MMM d HH:mm:ss yyyy', [cultureinfo]::InvariantCulture)
if ($VivadoProcessId) { Wait-Process -Id $VivadoProcessId -ErrorAction SilentlyContinue }
$rows = @()
foreach ($mhz in @(100,150)) {
    $outDir = Join-Path $buildDir "ooc_engine_$mhz"
    $timingPath = Join-Path $outDir 'timing.rpt'
    $utilPath = Join-Path $outDir 'utilization.rpt'
    $values = @('pending','pending','pending','pending','pending','pending','pending')
    if ((Test-Path $timingPath) -and (Test-Path $utilPath) -and
        (Get-Item $timingPath).LastWriteTime -ge $sessionStart -and
        (Get-Item $utilPath).LastWriteTime -ge $sessionStart) {
        $timing = Get-Content $timingPath
        for ($index=0; $index -lt $timing.Count; $index++) {
            if ($timing[$index] -match '^\s+WNS\(ns\)') {
                $numbers = $timing[$index+2].Trim() -split '\s+'
                $values[0]=$numbers[0]; $values[1]=$numbers[4]; break
            }
        }
        $util = Get-Content $utilPath -Raw
        $labels = @('CLB LUTs\*','CLB Registers','DSPs','Block RAM Tile','URAM')
        for ($index=0; $index -lt $labels.Count; $index++) {
            $found = [regex]::Match($util, '\|\s*'+$labels[$index]+'\s*\|\s*(\d+)')
            if (!$found.Success) { throw "Missing utilization row $($labels[$index])" }
            $values[$index+2]=$found.Groups[1].Value
        }
    }
    $rows += "| $mhz MHz | " + ($values -join ' | ') + ' |'
}
$resultPath = Join-Path $repoRoot 'docs/results/engine.md'
$resultText = Get-Content $resultPath -Raw
foreach ($row in $rows) {
    $mhz = ($row -split '\|')[1].Trim()
    $resultText = [regex]::Replace($resultText, '(?m)^\| '+[regex]::Escape($mhz)+' \| pending[^\r\n]*', $row)
}
[IO.File]::WriteAllText($resultPath,$resultText)
$rows | Set-Content (Join-Path $buildDir 's2a2_ooc_summary.log')
$rows
