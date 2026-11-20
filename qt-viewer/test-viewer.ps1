$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Build = Join-Path $PSScriptRoot "build-mingw"
$Exe = Join-Path $Build "package\EvanGerberQtPreview.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "Deploy folder missing; run run-mingw-build.ps1 -Action deploy first." }
$env:PATH = "$(Join-Path $Build 'package');$env:PATH"
$manifest = Join-Path (Split-Path $Exe -Parent) "SHA256SUMS.txt"
if (Test-Path -LiteralPath $manifest) {
    foreach ($line in @(Get-Content -LiteralPath $manifest | Where-Object { $_ -notlike '*SHA256SUMS.txt' })) {
        if ($line -match '^([0-9a-f]{64}) \*(.+)$') {
            $path = Join-Path (Split-Path $Exe -Parent) $Matches[2]
            if (-not (Test-Path -LiteralPath $path)) { throw "SHA256 manifest references a missing file: $($Matches[2])" }
            $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actual -ne $Matches[1]) { throw "SHA256 mismatch: $($Matches[2])" }
        }
    }
Write-Host "PASS: deployed package SHA256 manifest"
} else { Write-Warning "No package SHA256 manifest found." }
$Tmp = Join-Path $Repo ".tools\temp\viewer-tests"
New-Item -ItemType Directory -Path $Tmp -Force | Out-Null
$basic = Join-Path $Tmp "simple.gbr"
@("%FSLAX34Y34*%", "%MOMM*%", "%ADD10C,0.2*%", "D10*",
  "X10000Y10000D03*", "X20000Y10000D01*", "M02*") | Set-Content $basic -Encoding ascii
$version = & $Exe --version
if ($LASTEXITCODE -ne 0 -or $version -notmatch "0\.1\.0") { throw "Deployed GUI executable failed --version" }

function Invoke-SelfTest([string[]]$Files) {
    $log = Join-Path $Tmp "selftest-$([guid]::NewGuid().ToString('N')).log"
    $fileList = Join-Path $Tmp "selftest-$([guid]::NewGuid().ToString('N')).lst"
    Set-Content -LiteralPath $fileList -Value $Files -Encoding utf8
    $verify = Join-Path $Tmp "verify-$([guid]::NewGuid().ToString('N')).txt"
    $process = Start-Process -FilePath $Exe -ArgumentList "`"--verify`" `"$verify`"" -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Deployed executable verification failed" }
    if (-not (Test-Path -LiteralPath $verify)) { throw "Deployed executable did not write its verification marker" }
    Remove-Item -LiteralPath $verify -Force
    $env:GERBER_VIEWER_SELFTEST_REPORT = $log
    $env:GERBER_VIEWER_SELFTEST_LIST = $fileList
    if ($env:GERBER_VIEWER_ALLOW_REJECTS) { $env:GERBER_VIEWER_SELFTEST_ALLOW_REJECTS = "1" }
    else { $env:GERBER_VIEWER_SELFTEST_ALLOW_REJECTS = "" }
    $arguments = '"--selftest"'
    $process = Start-Process -FilePath $Exe -ArgumentList $arguments -PassThru -Wait
    $env:GERBER_VIEWER_SELFTEST_REPORT = $null
    $env:GERBER_VIEWER_SELFTEST_LIST = $null
    $env:GERBER_VIEWER_SELFTEST_ALLOW_REJECTS = $null
    if (-not $process.HasExited) { $process.WaitForExit() }
    $output = if (Test-Path -LiteralPath $log) { @(Get-Content -LiteralPath $log -Encoding UTF8) } else { @() }
    if ($process.ExitCode -ne 0 -and -not $output.Count) {
        $report = Join-Path $Tmp "selftest-exception.txt"
        Start-Process -FilePath $Exe -ArgumentList "`"--verify`" `"$report`"" -PassThru -Wait | Out-Null
        if (Test-Path $report) { Remove-Item $report -Force }
    }
    Remove-Item -LiteralPath $log,$fileList -Force -ErrorAction SilentlyContinue
    return @{ Code = $process.ExitCode; Output = $output }
}

Write-Host "=== simple geometry and real large-layer comparison"
$env:GERBER_VIEWER_ALLOW_REJECTS = $null
$valid = Invoke-SelfTest @($basic, (Join-Path $Repo "tools\_corpus\15_big.gbr"))
if ($valid.Output.Count) { $valid.Output | Write-Host }
if ($valid.Code -ne 0) { throw "Valid Gerber selftest failed ($($valid.Code)): $($valid.Error)" }

Write-Host "=== unsupported files must all fail closed"
$env:GERBER_VIEWER_ALLOW_REJECTS = "1"
$unsupported = @(Get-ChildItem (Join-Path $Repo "tools\_corpus") -Filter "*.gbr" -File |
    Where-Object { $_.Name -notin @("15_big.gbr", "_bench300k.gbr") } | Select-Object -ExpandProperty FullName)
$unsupportedResult = Invoke-SelfTest $unsupported
$env:GERBER_VIEWER_ALLOW_REJECTS = $null
if ($unsupportedResult.Output.Count) { $unsupportedResult.Output | Write-Host }
$rejects = @($unsupportedResult.Output | Where-Object { $_ -like "REJECT *" }).Count
if ($unsupportedResult.Code -ne 0) { throw "Unsupported-format suite failed: exit=$($unsupportedResult.Code)" }
if ($rejects -ne $unsupported.Count) { throw "Fail-closed mismatch: rejected $rejects of $($unsupported.Count) files." }
Write-Host "PASS: rejected $rejects unsupported files explicitly"

Write-Host "=== GUI launch smoke"
$marker = Join-Path $Tmp "qt-window-ready.txt"
Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
$argument = '"--smoke=' + $marker + '"'
$process = Start-Process -FilePath $Exe -ArgumentList $argument -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path -LiteralPath $marker) -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 100
}
if (-not (Test-Path -LiteralPath $marker)) {
    if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
    throw "Qt GUI did not create its ready marker (exit=$($process.ExitCode))"
}
if (-not $process.HasExited -and -not $process.WaitForExit(5000)) {
    $process.Kill(); $process.WaitForExit()
    throw "Qt smoke window reported ready but did not close"
}
if ($process.ExitCode -ne 0) { throw "Qt GUI exited with code $($process.ExitCode)" }
Write-Host "PASS: Qt GUI starts and closes cleanly"

Write-Host "=== your Gerber folder (if present)"
$realDir = Join-Path $Repo "test-Gerber"
if (Test-Path -LiteralPath $realDir) {
    $files = @(Get-ChildItem -LiteralPath $realDir -File -Recurse |
        Where-Object { $_.Extension -match '^\.(gbr|ger|art|pho|g[1-9]\d*|gp[1-9]\d*|gm[1-9]\d*|gt[lobsp]|gb[lobsp]|drl|drill|nc|ncd|xln|exc|tap|txt)$' })
    if ($files.Count) {
        $env:GERBER_VIEWER_ALLOW_REJECTS = "1"
        $real = Invoke-SelfTest @($files.FullName)
        $env:GERBER_VIEWER_ALLOW_REJECTS = $null
        if ($real.Output.Count) { $real.Output | Write-Host }
        if ($real.Code -ne 0) { throw "Real-file selftest internal failure: exit=$($real.Code)" }
        Write-Host "Real-file selftest complete (REJECT means format not migrated yet; inspect each result)"
    } else { Write-Host "No candidate Gerber/drill files found in $realDir" }
} else { Write-Host "No real-file folder yet: $realDir" }
