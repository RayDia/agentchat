"""
Bridge Distribution API — 内网分发本地桥接器

用途：内网环境下无法访问 GitHub，需要由 AgentChat 自身对外提供桥接器下载。
这些端点提供：
  - GET /api/bridge/version          当前分发包版本与校验和
  - GET /api/bridge/download         下载分发包（tar.gz）
  - GET /api/bridge/checksum         下载 SHA256SUMS
  - GET /api/bridge/install.sh       一键安装脚本 Linux/macOS（注入服务端地址）
  - GET /api/bridge/install.ps1      一键安装脚本 Windows（注入服务端地址）
  - GET /api/bridge/readme           下载安装说明（纯文本，含 Windows 章节）

无需认证：分发包本身不含任何凭据，用户仍需 agent 账号密码才能接入。
"""
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter(prefix="/bridge", tags=["Bridge Distribution"])

# 分发包目录：<repo>/dist/bridge
_DIST_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "dist", "bridge")
)

_PKG_PREFIX = "agentchat-bridge-"


def _read_version():
    try:
        with open(os.path.join(_DIST_DIR, "version")) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_checksum(filename=None):
    path = os.path.join(_DIST_DIR, "SHA256SUMS")
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return None
    if filename:
        for line in content.splitlines():
            if line.endswith(filename):
                return line
        return None
    return content


def _find_package():
    """定位当前分发包，返回 (文件名, 绝对路径)；不存在返回 (None, None)。"""
    version = _read_version()
    candidates = []
    if version:
        candidates.append(f"{_PKG_PREFIX}{version}.tar.gz")
    try:
        candidates += sorted(
            (f for f in os.listdir(_DIST_DIR)
             if f.startswith(_PKG_PREFIX) and f.endswith(".tar.gz")),
            reverse=True,
        )
    except OSError:
        return None, None
    for name in candidates:
        p = os.path.join(_DIST_DIR, name)
        if os.path.isfile(p):
            return name, p
    return None, None


@router.get("/version")
async def bridge_version():
    """返回当前可下载的桥接器版本与校验和。"""
    version = _read_version()
    name, path = _find_package()
    if not name:
        raise HTTPException(
            status_code=404,
            detail="分发包尚未构建。请在服务端执行 "
                   "`bash scripts/build_bridge_dist.sh` 后重试。",
        )
    return {
        "version": version,
        "filename": name,
        "size": os.path.getsize(path),
        "sha256": (_read_checksum(name) or "").split()[0] or None,
        "download_url": "/api/bridge/download",
        "checksum_url": "/api/bridge/checksum",
        "install_url": "/api/bridge/install.sh",
        "install_ps1_url": "/api/bridge/install.ps1",
    }


@router.get("/download")
async def bridge_download():
    """下载桥接器分发包。"""
    name, path = _find_package()
    if not name:
        raise HTTPException(
            status_code=404,
            detail="分发包尚未构建。请在服务端执行 "
                   "`bash scripts/build_bridge_dist.sh` 后重试。",
        )
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=name,
    )


@router.get("/checksum")
async def bridge_checksum():
    """下载 SHA256SUMS 校验和文件。"""
    name, _ = _find_package()
    content = _read_checksum(name) if name else None
    if not content:
        raise HTTPException(status_code=404, detail="校验和文件不存在")
    return PlainTextResponse(content, media_type="text/plain")


@router.get("/install.sh", response_class=PlainTextResponse)
async def bridge_install_sh(request: Request):
    """下载一键安装脚本（自动检测并注入本服务端地址）。

    用户直接 `curl -fsSL <服务端>/api/bridge/install.sh -o install.sh` 即可拿到
    一个已配置好 base-url 的安装脚本，减少手工填错地址的概率。
    """
    name, _ = _find_package()
    if not name:
        raise HTTPException(status_code=404, detail="分发包尚未构建")

    # 从请求中推断服务端可达地址，优先使用 Host 头（用户就是通过它访问上来的）
    host = request.headers.get("host") or "127.0.0.1:8000"
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    base_url = f"{scheme}://{host}"

    script = f"""#!/usr/bin/env bash
# AgentChat 本地桥接器安装脚本（由服务端 {base_url} 自动生成）
#
# 用法：bash install.sh
set -euo pipefail

BASE_URL="${{AGENTCHAT_BASE_URL:-{base_url}}}"
INSTALL_DIR="${{AGENTCHAT_BRIDGE_HOME:-$HOME/.agentchat/bridge}}"
PKG_URL="$BASE_URL/api/bridge/download"

info() {{ printf '\\033[1;34m[信息]\\033[0m %s\\n' "$*"; }}
err()  {{ printf '\\033[1;31m[错误]\\033[0m %s\\n' "$*" >&2; }}
ok()   {{ printf '\\033[1;32m[完成]\\033[0m %s\\n' "$*"; }}

PY="${{PYTHON:-python3}}"
if ! command -v "$PY" >/dev/null 2>&1; then
    err "未找到 $PY，请先安装 Python 3.9+"
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

info "从 $PKG_URL 下载分发包…"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$PKG_URL" -o "$TMP/pkg.tar.gz"
elif command -v wget >/dev/null 2>&1; then
    wget -q "$PKG_URL" -O "$TMP/pkg.tar.gz"
else
    err "需要 curl 或 wget"
    exit 1
fi
ok "下载完成 ($(du -h "$TMP/pkg.tar.gz" | cut -f1))"

info "解压…"
tar xzf "$TMP/pkg.tar.gz" -C "$TMP"
EXTRACTED="$(find "$TMP" -maxdepth 1 -type d -name 'agentchat-bridge-*' | head -1)"
[ -n "$EXTRACTED" ] || {{ err "分发包结构异常"; exit 1; }}

mkdir -p "$INSTALL_DIR"
# bridge_install.sh 会把 remote_bridge.py 装到 INSTALL_DIR
cp "$EXTRACTED/remote_bridge.py" "$TMP/remote_bridge.py"
cp "$EXTRACTED/bridge_install.sh" "$TMP/bridge_install.sh"
cp "$EXTRACTED/REMOTE_AGENT_BRIDGE.md" "$TMP/REMOTE_AGENT_BRIDGE.md" 2>/dev/null || true

bash "$TMP/bridge_install.sh"

# 注入服务端地址到配置（若用户没改过）
CFG="$INSTALL_DIR/bridge.toml"
if [ -f "$CFG" ]; then
    if grep -q '^base-url = "http://127.0.0.1:8000"' "$CFG"; then
        sed -i.bak "s|^base-url = .*|base-url = \\"$BASE_URL\\"|" "$CFG" && rm -f "$CFG.bak"
        ok "已在配置中写入服务端地址: $BASE_URL"
    else
        info "配置文件已自定义，保留原 base-url 不变"
    fi
fi

echo
ok "安装完成"
echo
echo "下一步："
echo "  1. 编辑配置填入你的 agent 账号："
echo "       \\${{EDITOR:-vi}} $CFG"
echo "  2. 设置密码（建议用环境变量，不写进配置文件）："
echo "       export AGENT_PASSWORD='你的agent密码'"
echo "  3. 启动："
echo "       $INSTALL_DIR/start.sh"
echo
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/install.ps1", response_class=PlainTextResponse)
async def bridge_install_ps1(request: Request):
    """下载 Windows 一键安装脚本（自动检测并注入本服务端地址）。

    用法（PowerShell）：
        Invoke-WebRequest -Uri <服务端>/api/bridge/install.ps1 -OutFile install.ps1
        powershell -ExecutionPolicy Bypass -File install.ps1
    """
    name, _ = _find_package()
    if not name:
        raise HTTPException(status_code=404, detail="分发包尚未构建")

    host = request.headers.get("host") or "127.0.0.1:8000"
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    base_url = f"{scheme}://{host}"

    # 以 __BASE_URL__ 占位，避免 PowerShell 的 $ 变量与 f-string 冲突
    script = r"""# AgentChat 本地桥接器安装脚本（Windows，由服务端自动生成）
[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'

function Say-Ok   { param($m) Write-Host "[完成] $m" -ForegroundColor Green }
function Say-Info { param($m) Write-Host "[信息] $m" -ForegroundColor Cyan }

# PS 5.1 需要 -UseBasicParsing（否则依赖 IE 引擎）；PS 7 已移除该参数
function Get-BridgeFile {
    param([string]$Uri, [string]$OutFile)
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
    } catch {
        Invoke-WebRequest -Uri $Uri -OutFile $OutFile
    }
}

# 兼容老旧系统的 TLS 协商失败
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$BaseUrl = $env:AGENTCHAT_BASE_URL
if (-not $BaseUrl) { $BaseUrl = '__BASE_URL__' }

if (-not $InstallDir) {
    $homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $InstallDir = Join-Path $homeDir '.agentchat\bridge'
}

$tmp = Join-Path $env:TEMP ('agentchat-bridge-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    Say-Info "从 $BaseUrl 下载分发包..."
    $pkg = Join-Path $tmp 'agentchat-bridge.tar.gz'
    $sum = Join-Path $tmp 'SHA256SUMS'
    Get-BridgeFile -Uri "$BaseUrl/api/bridge/download" -OutFile $pkg
    Get-BridgeFile -Uri "$BaseUrl/api/bridge/checksum" -OutFile $sum
    if (-not (Test-Path $pkg)) { throw "分发包下载失败：$BaseUrl/api/bridge/download" }

    $expected = ((Get-Content $sum | Select-Object -First 1) -split '\s+' |
                 Select-Object -First 1)
    $actual = (Get-FileHash $pkg -Algorithm SHA256).Hash
    if ($expected -and ($actual.ToLower() -ne $expected.ToLower())) {
        throw "分发包校验和不匹配，已中止安装（预期 $expected，实际 $actual）"
    }
    Say-Info "下载完成并校验通过"

    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        $noTar = "未找到 tar 命令（需 Windows 10 17063+ 内置的 tar.exe）。"
        $noTar = $noTar + " 请改用「下载 + 手动安装」方式。"
        throw $noTar
    }
    & tar -xzf $pkg -C $tmp
    if ($LASTEXITCODE -ne 0) { throw "解压失败" }

    $dir = Get-ChildItem -Path $tmp -Directory -Filter 'agentchat-bridge-*' |
           Select-Object -First 1
    if (-not $dir) { throw "分发包结构异常：未找到 agentchat-bridge-* 目录" }

    $installer = Join-Path $dir.FullName 'bridge_install.ps1'
    if (-not (Test-Path $installer)) { throw "分发包缺少 bridge_install.ps1" }

    $passArgs = @()
    if ($MyInvocation.BoundParameters.ContainsKey('InstallDir')) {
        $passArgs += @('-InstallDir', $InstallDir)
    }
    if ($Python) { $passArgs += @('-Python', $Python) }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer @passArgs
    if ($LASTEXITCODE -ne 0) { throw "安装脚本执行失败" }

    # 注入服务端地址（仅在用户尚未改动默认占位值时）
    $cfg = Join-Path $InstallDir 'bridge.toml'
    if (Test-Path $cfg) {
        $text = [System.IO.File]::ReadAllText($cfg)
        if ($text -match '(?m)^base-url\s*=\s*"http://127\.0\.0\.1:8000"') {
            $text = [regex]::Replace(
                $text, '(?m)^base-url\s*=.*$', ('base-url = "' + $BaseUrl + '"'))
            [System.IO.File]::WriteAllText(
                $cfg, $text, (New-Object System.Text.UTF8Encoding($false)))
            Say-Ok "已在配置中写入服务端地址: $BaseUrl"
        } else {
            Say-Info "配置文件已自定义，保留原 base-url 不变"
        }
    }

    Write-Host ""
    Say-Ok "安装完成"
    Write-Host ""
    Write-Host "下一步：编辑配置填入 agent 账号，然后启动："
    Write-Host "       notepad `"$cfg`""
    Write-Host "       `"$(Join-Path $InstallDir 'start.bat')`" --check"
    Write-Host ""
}
catch {
    Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
"""
    script = script.replace("__BASE_URL__", base_url)
    # 加 UTF-8 BOM：Windows PowerShell 5.1 读无 BOM 脚本会把中文按 ANSI 解成乱码
    return PlainTextResponse("\ufeff" + script,
                             media_type="text/plain; charset=utf-8")


@router.get("/readme", response_class=PlainTextResponse)
async def bridge_readme(request: Request):
    """返回纯文本安装说明（方便 curl 查看，无需解压）。"""
    host = request.headers.get("host") or "127.0.0.1:8000"
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    base_url = f"{scheme}://{host}"
    version = _read_version() or "(未构建)"
    name, path = _find_package()

    lines = [
        "AgentChat 本地桥接器 —— 安装说明",
        "=" * 40,
        "",
        "作用：把本机安装的 CLI agent（qwen）接入 AgentChat。",
        "     安装后，在 AgentChat 频道里 @提及 该 agent 即可收到回复。",
        "     桥接器与 CLI 必须同机运行（stdio 限制）。",
        "",
        f"服务端地址   : {base_url}",
        f"当前版本     : {version}",
    ]
    if name:
        lines.append(f"分发包       : {name} ({os.path.getsize(path)} 字节)")
        lines.append(f"SHA256       : {(_read_checksum(name) or '').split()[0]}")
    lines += [
        "",
        "── Linux / macOS ──────────────────────────",
        "安装方式 A：一键脚本（推荐）",
        f"  curl -fsSL {base_url}/api/bridge/install.sh -o install.sh",
        "  bash install.sh",
        "",
        "安装方式 B：下载 + 校验 + 手动安装",
        f"  curl -LO {base_url}/api/bridge/download",
        f"  curl -LO {base_url}/api/bridge/checksum",
        "  sha256sum -c SHA256SUMS          # 校验完整性",
        "  tar xzf agentchat-bridge-*.tar.gz",
        "  cd agentchat-bridge-*/ && bash bridge_install.sh",
        "",
        "  export AGENT_PASSWORD='你的agent密码'",
        "  ~/.agentchat/bridge/start.sh --check   # 先自检",
        "  ~/.agentchat/bridge/start.sh           # 正式启动",
        "",
        "── Windows ───────────────────────────────",
        "安装方式 A：一键脚本（PowerShell 执行，不需要 Git Bash）",
        f"  Invoke-WebRequest -Uri {base_url}/api/bridge/install.ps1 "
        "-OutFile install.ps1",
        "  powershell -ExecutionPolicy Bypass -File install.ps1",
        "",
        "安装方式 B：下载 + 手动安装（需 Windows 10 17063+ 内置的 tar）",
        f"  Invoke-WebRequest -Uri {base_url}/api/bridge/download "
        "-OutFile pkg.tar.gz",
        "  tar -xzf pkg.tar.gz",
        "  cd agentchat-bridge-*",
        "  powershell -ExecutionPolicy Bypass -File bridge_install.ps1",
        "",
        "  # 设置密码（setx 需重开终端生效）",
        "  setx AGENT_PASSWORD 你的agent密码",
        "",
        "  # 在 CMD 中启动",
        "  %USERPROFILE%\\.agentchat\\bridge\\start.bat --check   # 先自检",
        "  %USERPROFILE%\\.agentchat\\bridge\\start.bat           # 正式启动",
        "",
        "注意：不要用 Word 编辑 bridge.toml，它会改变文件编码导致读取失败。",
        "      Windows 下 npm 安装的 qwen 是 qwen.cmd，桥接会自动用 cmd.exe 启动。",
        "",
        "── 系统要求 ──────────────────────────────",
        "  - Python 3.9+（3.11+ 可完整解析 bridge.toml；更低版本用简化解析器）",
        "  - qwen CLI 已安装且在 PATH 中",
        "  - 能访问上面的服务端地址",
        "",
        "── 安装后配置文件位置 ───────────────────",
        "  Linux/macOS: ~/.agentchat/bridge/bridge.toml",
        "  Windows    : %USERPROFILE%\\.agentchat\\bridge\\bridge.toml",
        "",
        "  [bridge]",
        f'  base-url = "{base_url}"',
        '  channel-id = 1                 # 要接入的频道 ID（整数，不加引号）',
        '  username = "your-agent-name"   # 你的 agent 账号',
        "",
        "详细文档见分发包内的 REMOTE_AGENT_BRIDGE.md",
        "",
    ]
    return PlainTextResponse("\n".join(lines),
                             media_type="text/plain; charset=utf-8")
