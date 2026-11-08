# HF Mirror Downloader 一键构建脚本
# 用法：
#   .\build.ps1                 # 创建 venv + 安装依赖 + 测试 + 打包单文件 exe
#   .\build.ps1 -SkipTests      # 跳过测试
#   .\build.ps1 -OneDir         # 打包为目录模式（启动更快）
#Requires -Version 5.1
param(
    [switch]$SkipTests,
    [switch]$OneDir
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "=== HF Mirror Downloader 构建 ===" -ForegroundColor Cyan

# 1. 选择 Python（>= 3.11）
$py = $null
if (Test-Path ".venv\Scripts\python.exe") {
    $py = @(".venv\Scripts\python.exe")
    Write-Host "使用现有虚拟环境 .venv"
} else {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3"))
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidates += , @("python")
    }
    foreach ($cand in $candidates) {
        try {
            $ver = (& $cand[0] $cand[1..($cand.Count - 1)] -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null)
            if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
            $parts = ([string]$ver).Trim().Split('.')
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                $py = $cand
                Write-Host ("找到 Python {0}.{1}" -f $parts[0], $parts[1])
                break
            }
        } catch { continue }
    }
    if (-not $py) { throw "未找到 Python >= 3.11，请安装后重试，或先手动创建 .venv。" }
    Write-Host "创建虚拟环境 .venv …"
    & $py[0] $py[1..($py.Count - 1)] -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败" }
    $py = @(".venv\Scripts\python.exe")
}
$vp = $py[0]

# 2. 安装依赖
Write-Host "安装依赖 …"
& $vp -m pip install --upgrade pip -q
& $vp -m pip install -r requirements.txt -r requirements-dev.txt -q
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

# 3. 测试
if (-not $SkipTests) {
    Write-Host "运行测试 …"
    & $vp -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "测试未通过，已中止构建" }
} else {
    Write-Host "跳过测试"
}

# 4. 图标
if (-not (Test-Path "assets\app.ico")) {
    Write-Host "生成应用图标 …"
    & $vp scripts\make_icon.py
}

# 5. PyInstaller 打包
$mode = if ($OneDir) { "--onedir" } else { "--onefile" }
$name = "HF-Mirror-Downloader"
Write-Host "PyInstaller 打包（$mode）…"
& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed $mode `
    --name $name `
    --icon "assets\app.ico" `
    --collect-data huggingface_hub `
    --collect-all qtawesome `
    main.py
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

if ($OneDir) {
    $out = "dist\$name\$name.exe"
} else {
    $out = "dist\$name.exe"
}
Write-Host ""
Write-Host "=== 构建完成 ===" -ForegroundColor Green
if (Test-Path $out) {
    $size = (Get-Item $out).Length / 1MB
    Write-Host ("输出：{0}（{1:N1} MB）" -f $out, $size)
    $hash = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
    Write-Host "SHA256：$hash"
    "$hash  $name" | Out-File -Encoding ascii "dist\SHA256SUMS.txt"
    Write-Host "已写入 dist\SHA256SUMS.txt（发布时随 exe 提供，供用户校验防篡改与误报排查）"
} else {
    Write-Host "输出：$out"
}
