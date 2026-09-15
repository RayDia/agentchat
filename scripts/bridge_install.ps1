#Requires -Version 5.1
<#
AgentChat 本地桥接器 —— 一键安装脚本（Windows）

用法：
    powershell -ExecutionPolicy Bypass -File bridge_install.ps1

可选参数：
    -InstallDir <路径>   安装目录，默认 %USERPROFILE%\.agentchat\bridge
    -Python <路径>       Python 可执行文件，默认自动探测 python / python3 / py -3

作用：在用户目录创建独立运行环境，安装依赖，生成配置模板与启动脚本。
不写系统目录、不修改 PATH、不需要管理员权限。
#>
[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [string]$Python = ''
)

# 本文件须以 UTF-8 with BOM 保存，否则 Windows PowerShell 5.1 会把中文读成乱码
$ErrorActionPreference = 'Stop'

function Say-Info { param($m) Write-Host "[信息] $m" -ForegroundColor Cyan }
function Say-Ok   { param($m) Write-Host "[完成] $m" -ForegroundColor Green }
function Say-Warn { param($m) Write-Host "[警告] $m" -ForegroundColor Yellow }
function Say-Err  { param($m) Write-Host "[错误] $m" -ForegroundColor Red }

Write-Host "=========================================="
Write-Host "  AgentChat 本地桥接器 安装 (Windows)"
Write-Host "=========================================="
Write-Host ""

# ---------- 0. 解析安装目录 ----------
if (-not $InstallDir) {
    $base = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $InstallDir = Join-Path -Path $base -ChildPath '.agentchat\bridge'
}

# ---------- 1. 定位 Python ----------
Say-Info "检查 Python 环境..."

function Get-PythonVersion {
    param([string]$Exe, [string[]]$PreArgs = @())
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
    try {
        $code = 'import sys;sys.stdout.write("%d.%d" % sys.version_info[:2])'
        $out = & $Exe @PreArgs -c $code 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() }
    } catch {}
    return $null
}

$tries = New-Object System.Collections.ArrayList
if ($Python) { [void]$tries.Add(@{ Exe = $Python; Pre = @() }) }
[void]$tries.Add(@{ Exe = 'python';  Pre = @() })
[void]$tries.Add(@{ Exe = 'python3'; Pre = @() })
[void]$tries.Add(@{ Exe = 'py';      Pre = @('-3') })

$pyExe = $null; $pyPre = @(); $pyVer = $null
foreach ($t in $tries) {
    $v = Get-PythonVersion -Exe $t.Exe -PreArgs $t.Pre
    if ($v) { $pyExe = $t.Exe; $pyPre = $t.Pre; $pyVer = $v; break }
}
if (-not $pyExe) {
    Say-Err "未找到 Python。请先安装 Python 3.9+（推荐 3.11+），"
    Say-Err "安装时务必勾选 Add python.exe to PATH。"
    exit 1
}

$v3 = [version]"$pyVer.0"
if ($v3 -lt [version]"3.9.0") {
    Say-Err "需要 Python 3.9 或更高版本，当前为 $pyVer"
    exit 1
}
Say-Info "Python: $pyExe $pyVer"
if ($v3 -lt [version]"3.11.0") {
    Say-Warn "Python < 3.11，bridge.toml 将由内置简化解析器读取（仅支持 key = value）"
    Say-Warn "建议升级到 Python 3.11+，或执行：$pyExe -m pip install tomli"
}

# ---------- 2. 检查 qwen CLI ----------
Say-Info "检查 qwen CLI..."
$qwen = Get-Command qwen -ErrorAction SilentlyContinue
if ($qwen) {
    Say-Ok "找到 qwen: $($qwen.Source)"
    $ext = [System.IO.Path]::GetExtension($qwen.Source).ToLowerInvariant()
    if ($ext -eq '.ps1') {
        Say-Warn "qwen 是 PowerShell 脚本（.ps1），桥接会自动改用 powershell.exe 启动它。"
    }
} else {
    Say-Warn "未在 PATH 中找到 qwen。安装后请确认 qwen 可执行，"
    Say-Warn "或在 bridge.toml 里用 cli-cmd 指定完整路径（须为 .exe/.cmd/.bat）。"
}

# ---------- 3. 创建目录并安装程序 ----------
Say-Info "创建安装目录: $InstallDir"
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $scriptDir 'remote_bridge.py'
if (-not (Test-Path $src)) {
    $src = Join-Path (Split-Path -Parent $scriptDir) 'scripts\remote_bridge.py'
}
if (-not (Test-Path $src)) {
    Say-Err "未找到 remote_bridge.py（查找路径: $scriptDir）"
    exit 1
}
Copy-Item -Path $src -Destination (Join-Path $InstallDir 'remote_bridge.py') -Force
Say-Ok "已安装 remote_bridge.py"

# ---------- 4. 虚拟环境与依赖 ----------
$venvDir = Join-Path $InstallDir '.venv'
$venvPy  = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Say-Info "创建虚拟环境..."
    & $pyExe @pyPre -m venv $venvDir
    if (-not (Test-Path $venvPy)) {
        Say-Err "虚拟环境创建失败，请检查 Python 安装是否完整（venv 模块）。"
        exit 1
    }
}
Say-Info "安装依赖（httpx, websockets）..."
& $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { Say-Warn "pip 升级失败，继续尝试安装依赖" }
& $venvPy -m pip install --quiet --disable-pip-version-check 'websockets>=12.0' httpx
if ($LASTEXITCODE -ne 0) {
    Say-Err "依赖安装失败。内网环境可能需要指定镜像源，例如："
    Say-Err "  $venvPy -m pip install -i https://mirrors.example.com/pypi/simple websockets httpx"
    exit 1
}
Say-Ok "依赖安装完成"

# ---------- 5. 生成配置模板 ----------
$cfg = Join-Path $InstallDir 'bridge.toml'
if (Test-Path $cfg) {
    Say-Info "配置文件已存在，保留不覆盖: $cfg"
} else {
    $toml = @"
# AgentChat 本地桥接器配置
# 优先级：命令行参数 > 环境变量 > 本文件

[bridge]
# AgentChat 服务端地址（生产环境请用 https://）
base-url = "http://127.0.0.1:8000"

# 要接入的频道 ID（必须是整数，不要加引号）
channel-id = 1

# 你在 AgentChat 中的 agent 账号
username = "your-agent-name"

# 密码。建议改用环境变量 AGENT_PASSWORD，避免明文落盘
password = "your-agent-password"

# 会话标识：同一 thread 重启后归入同一会话。留空则按 agent 名自动生成
# thread-id = "my-laptop"

# 覆盖 CLI 命令（默认：qwen --acp --channel ACP --output-format stream-json）
# Windows 下若 qwen 是批处理脚本，会自动用 cmd.exe 启动，一般无需改动
# cli-cmd = "qwen,--acp,--channel,ACP,--output-format,stream-json"

# 传给 CLI 子进程的模型配置（通常不必填，CLI 会读自己的 settings）
# model = "qwen3-coder-plus"
"@
    # 显式用无 BOM 的 UTF-8 写入
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($cfg, ($toml + "`r`n"), $enc)
    Say-Ok "已生成配置模板: $cfg"
    Say-Info "建议用记事本或 VS Code 编辑；请勿用 Word 等会改变编码的编辑器"
}

# ---------- 6. 生成启动脚本 ----------
$bat = Join-Path $InstallDir 'start.bat'
$batText = @"
@echo off
rem 由 bridge_install.ps1 生成
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0remote_bridge.py" %*
popd
"@
# 统一为 CRLF 行尾，避免个别 Windows 版本处理 LF 时截断最后一行
$batText = (($batText -replace "`r`n", "`n") -replace "`n", "`r`n")
[System.IO.File]::WriteAllText($bat, $batText, (New-Object System.Text.UTF8Encoding($false)))
Say-Ok "已生成启动脚本: $bat"

# ---------- 7. 自检 ----------
Say-Info "运行环境自检..."
& $venvPy (Join-Path $InstallDir 'remote_bridge.py') --check
if ($LASTEXITCODE -eq 0) {
    Say-Ok "自检通过"
} else {
    Say-Warn "自检未通过，请按上方提示修正后重试"
}

Write-Host ""
Write-Host "=========================================="
Say-Ok "安装完成"
Write-Host "=========================================="
Write-Host ""
Write-Host "下一步："
Write-Host "  1. 编辑配置，填入你的 agent 账号与频道 ID："
Write-Host "       notepad `"$cfg`""
Write-Host "  2. 建议改用环境变量传密码，不写进配置文件："
Write-Host "       setx AGENT_PASSWORD 你的密码"
Write-Host "       （注意：setx 在当前窗口不生效，需重新打开一个终端）"
Write-Host "  3. 启动桥接："
Write-Host "       `"$bat`""
Write-Host ""
Write-Host "  常用参数："
Write-Host "       `"$bat`" --check          # 只做环境自检"
Write-Host "       `"$bat`" --config <路径>  # 使用指定配置文件"
Write-Host ""
Write-Host "  开机自启：把 start.bat 的快捷方式放进 shell:startup，"
Write-Host "  或用「任务计划程序」以「用户登录时」触发器运行它。"
Write-Host ""
