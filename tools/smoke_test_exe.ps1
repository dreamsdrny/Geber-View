<#
  冒烟测试: 确认打包出来的 exe 真的能"打开" —— 不是只看编译/打包成功。

  背景: PyInstaller 单文件版启动时要先把内容解压到临时目录;
        若临时目录不可写(安全软件拦截/权限收紧), 会弹出标题为 "Error" 的框,
        进程还活着但界面出不来。只检查"进程是否存在"是抓不到的, 所以这里
        既看进程是否存活, 也看窗口标题里有没有 Error。

  用法:
    pwsh -File tools/smoke_test_exe.ps1 -Exe 'dist\Evan_Gerber_View\Evan_Gerber_View.exe'
    pwsh -File tools/smoke_test_exe.ps1 -Exe 'dist\Evan_Gerber_View.exe','dist\Evan_Gerber_View\Evan_Gerber_View.exe'

  退出码: 0 = 全部通过; 1 = 有 exe 打不开。
#>
param(
    [Parameter(Mandatory = $true)][string[]]$Exe,
    [int]$WaitSeconds = 20,
    [string]$ExpectTitle = 'Gerber'
)

$ErrorActionPreference = 'Stop'
$name = 'Evan_Gerber_View'
$problems = New-Object System.Collections.Generic.List[string]

function Stop-AllInstances {
    Get-Process -Name $name -ErrorAction SilentlyContinue |
        ForEach-Object { try { $_.Kill() } catch { } }
    Start-Sleep -Milliseconds 500
}

foreach ($rel in $Exe) {
    $path = (Resolve-Path -LiteralPath $rel -ErrorAction SilentlyContinue)
    if (-not $path) {
        $problems.Add("$rel : 文件不存在")
        Write-Host "[FAIL] $rel 不存在" -ForegroundColor Red
        continue
    }
    $path = $path.Path
    Write-Host "=== 冒烟: $path"
    Stop-AllInstances

    $out = Join-Path $env:TEMP ('smoke_' + [guid]::NewGuid().ToString('N') + '.out')
    $err = Join-Path $env:TEMP ('smoke_' + [guid]::NewGuid().ToString('N') + '.err')
    $proc = Start-Process -FilePath $path -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    Start-Sleep -Seconds $WaitSeconds

    $alive = -not $proc.HasExited
    $insts = @(Get-Process -Name $name -ErrorAction SilentlyContinue)
    $titles = @($insts | ForEach-Object { $_.MainWindowTitle } | Where-Object { $_ })
    $errText = ''
    if (Test-Path $err) { $errText = (Get-Content -LiteralPath $err -Raw -ErrorAction SilentlyContinue) }
    Stop-AllInstances

    Write-Host ("  存活={0} 实例数={1} 窗口标题={2}" -f $alive, $insts.Count, ($titles -join ' | '))
    if ($errText) { Write-Host ("  stderr: " + $errText.Trim()) }

    $bad = $false
    if (-not ($alive -or $insts.Count -gt 0)) {
        $problems.Add("$rel : 启动后立即退出(打不开)")
        $bad = $true
    }
    $errTitle = @($titles | Where-Object { $_ -match 'Error|错误' })
    if ($errTitle.Count -gt 0) {
        $problems.Add("$rel : 弹出报错框 '$($errTitle -join ',')'")
        $bad = $true
    }
    if ($errText -match 'PYI-\d+:ERROR') {
        $problems.Add("$rel : PyInstaller 启动期报错 -> $($errText.Trim())")
        $bad = $true
    }
    if (-not $bad) {
        if ($titles.Count -eq 0) {
            Write-Host "  [WARN] 没读到窗口标题(可能是无桌面会话), 但进程存活" -ForegroundColor Yellow
        } elseif (-not (@($titles | Where-Object { $_ -like "*$ExpectTitle*" }).Count)) {
            $problems.Add("$rel : 窗口标题里没有 '$ExpectTitle' -> $($titles -join ' | ')")
            $bad = $true
        }
        if (-not $bad) { Write-Host "  [PASS] 能打开" -ForegroundColor Green }
    }
    Remove-Item $out, $err -Force -ErrorAction SilentlyContinue
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "冒烟测试失败:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host ""
Write-Host "冒烟测试全部通过: 每个 exe 都能打开。" -ForegroundColor Green
exit 0
