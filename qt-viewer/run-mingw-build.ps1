param(
    [ValidateSet("build", "deploy", "test")]
    [string]$Action = "deploy",
    [string]$QtRoot = "",
    [string]$BuildDir = "",
    [string]$RustupExe = ""
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Tools = Join-Path $Repo ".tools"
if (-not $QtRoot) { $QtRoot = Join-Path $Tools "Qt" }
if (-not $BuildDir) { $BuildDir = Join-Path $PSScriptRoot "build-mingw" }
if (-not $RustupExe) { $RustupExe = Join-Path $env:USERPROFILE ".cargo\bin\rustup.exe" }
$Qt = Join-Path $QtRoot "6.8.3\mingw_64"
$MingwBin = Join-Path $QtRoot "Tools\mingw1310_64\bin"
$Cmake = Join-Path $Tools "cmake-4.4.4\cmake-4.4.4-windows-x86_64\bin\cmake.exe"
$Ninja = Join-Path $Tools "ninja-1.13.2\ninja.exe"
$Rustup = (Resolve-Path $RustupExe).Path
foreach ($path in @($Qt, (Join-Path $MingwBin "g++.exe"), $Cmake, $Ninja, $Rustup,
        (Join-Path (Split-Path $Rustup -Parent) "cargo.exe"))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required build tool not found: $path" }
}
$env:CMAKE_PREFIX_PATH = $Qt
$env:CARGO_HOME = Split-Path (Split-Path $Rustup -Parent) -Parent
$env:RUSTUP_HOME = Join-Path $env:USERPROFILE ".rustup"
$env:TEMP = Join-Path $Tools "temp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP,(Join-Path $Tools "appdata") -Force | Out-Null
$env:APPDATA = Join-Path $Tools "appdata"
$env:PATH = "$MingwBin;$($Qt)\bin;$($Ninja | Split-Path -Parent);$($Cmake | Split-Path -Parent);$(Split-Path $Rustup -Parent);$env:PATH"
$env:CARGO_TARGET_DIR = Join-Path $Repo "rust\viewer_core\target"
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = Join-Path $MingwBin "gcc.exe"
$env:RUSTFLAGS = "-C target-feature=+crt-static"
$env:RUSTUP_AUTO_INSTALL = "0"
$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-gnu"
$CmakeArgs = @(
    "-S", $PSScriptRoot, "-B", $BuildDir, "-G", "Ninja",
    "-DCMAKE_PREFIX_PATH=$Qt",
    "-DCMAKE_C_COMPILER=$(Join-Path $MingwBin 'gcc.exe')",
    "-DCMAKE_CXX_COMPILER=$(Join-Path $MingwBin 'g++.exe')",
    "-DCMAKE_MAKE_PROGRAM=$Ninja",
    "-DRUSTUP_EXECUTABLE=$Rustup",
    "-DRUST_TOOLCHAIN=stable-x86_64-pc-windows-gnu",
    "-DCMAKE_INSTALL_PREFIX=$BuildDir/package",
    "-DCMAKE_BUILD_TYPE=Release"
)
& $Cmake @CmakeArgs
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed: $LASTEXITCODE" }
if ($Action -eq "build") {
    & $Cmake --build $BuildDir --parallel 6
} else {
    & $Cmake --build $BuildDir --target deploy --parallel 6
}
if ($LASTEXITCODE -ne 0) { throw "Build/deploy failed: $LASTEXITCODE" }
if ($Action -eq "deploy" -or $Action -eq "test") {
    $package = Join-Path $BuildDir "package"
    $files = @(Get-ChildItem -LiteralPath $package -File -Recurse | Sort-Object FullName)
    $manifest = foreach ($file in $files) {
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "{0} *{1}" -f $hash, $file.FullName.Substring($package.Length + 1)
    }
    Set-Content -LiteralPath (Join-Path $package "SHA256SUMS.txt") -Value $manifest -Encoding ascii
}
if ($Action -eq "test") {
    $exe = Join-Path $BuildDir "package\EvanGerberQtPreview.exe"
    $report = Join-Path $env:TEMP "qt-version-check.txt"
    $process = Start-Process -FilePath $exe -ArgumentList "`"--verify`" `"$report`"" -PassThru -Wait
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw "Deployed Qt executable verification failed" }
    Remove-Item -LiteralPath $report -Force
}
if ($Action -eq "test") {
    $exe = Join-Path $BuildDir "package\EvanGerberQtPreview.exe"
    $env:PATH = "$(Join-Path $BuildDir 'package');$env:PATH"
    $env:GERBER_VIEWER_SELFTEST_LOG = Join-Path $env:TEMP "qt-native-test.log"
    & $exe --selftest $Tools\temp\qt_simple.gbr (Join-Path $Repo "tools\_corpus\15_big.gbr")
    $code = $LASTEXITCODE
    $env:GERBER_VIEWER_SELFTEST_LOG = $null
    if ($code -ne 0) { throw "Native parser regression failed: $code" }
    $report = Get-Content -LiteralPath (Join-Path $env:TEMP "qt-native-test.log") -Raw -Encoding UTF8
    if ($report -notmatch 'strokes=15500') { throw "Large Gerber selftest output mismatch: $report" }
    & "$BuildDir\package\EvanGerberQtPreview.exe" --version
    if ($LASTEXITCODE -ne 0) { throw "Deployed GUI --version verification failed" }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test-viewer.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Qt/Rust deployment tests failed: $LASTEXITCODE" }
}
Write-Host "SUCCESS: $Action; package = $(Join-Path $BuildDir 'package')"
