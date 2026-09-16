#!/usr/bin/env bash
# AgentChat 本地桥接器 —— 一键安装脚本（macOS / Linux）
#
# 用法：
#   bash bridge_install.sh
#
# 作用：在用户主目录创建独立运行环境，安装依赖，生成配置模板。
# 不修改系统级文件（不写 /usr、不改 PATH）；systemd 是可选的额外步骤。
set -euo pipefail

INSTALL_DIR="${AGENTCHAT_BRIDGE_HOME:-$HOME/.agentchat/bridge}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

info()  { printf '\033[1;34m[信息]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; }
ok()    { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }

echo "=========================================="
echo "  AgentChat 本地桥接器 安装"
echo "=========================================="
echo

# ---------- 1. 检查 Python ----------
info "检查 Python 环境…"
if ! command -v "$PY" >/dev/null 2>&1; then
    err "未找到 $PY。请先安装 Python 3.9+（推荐 3.11+，以便直接支持 bridge.toml）。"
    exit 1
fi
PYVER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
info "Python 版本: $PYVER"
if ! "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)'; then
    err "需要 Python 3.9 或更高版本，当前为 $PYVER"
    exit 1
fi
if ! "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)'; then
    warn "Python < 3.11，将无法解析 bridge.toml 配置文件（可改用命令行参数或环境变量）"
fi

# ---------- 2. 检查 qwen CLI ----------
info "检查 qwen CLI…"
if command -v qwen >/dev/null 2>&1; then
    ok "找到 qwen: $(command -v qwen)"
else
    warn "未在 PATH 中找到 qwen。安装后请确保 qwen 可执行，"
    warn "或稍后在配置中用 cli-cmd 指定完整路径。"
fi

# ---------- 3. 创建目录并拷贝程序 ----------
info "创建安装目录: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

SRC="$SCRIPT_DIR/remote_bridge.py"
if [ ! -f "$SRC" ]; then
    # 支持在项目根目录直接运行
    SRC="$SCRIPT_DIR/../scripts/remote_bridge.py"
fi
if [ ! -f "$SRC" ]; then
    err "未找到 remote_bridge.py（查找路径: $SCRIPT_DIR）"
    exit 1
fi
cp "$SRC" "$INSTALL_DIR/remote_bridge.py"
ok "已安装 remote_bridge.py"

# 推送工具：让 agent session 内可直接向频道发消息
if [ -f "$SCRIPT_DIR/agentchat_send.sh" ]; then
    cp "$SCRIPT_DIR/agentchat_send.sh" "$INSTALL_DIR/agentchat-send"
    chmod +x "$INSTALL_DIR/agentchat-send"
    ok "已安装推送工具 agentchat-send"
fi

# MCP server：让 agent 通过标准 MCP 工具直接推送（无需 shell 权限）
if [ -f "$SCRIPT_DIR/agentchat_mcp_server.py" ]; then
    cp "$SCRIPT_DIR/agentchat_mcp_server.py" "$INSTALL_DIR/agentchat_mcp_server.py"
    ok "已安装 MCP server（agent 可调用 agentchat_send 工具）"
fi

# ---------- 4. 创建虚拟环境并安装依赖 ----------
info "创建虚拟环境…"
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    "$PY" -m venv "$INSTALL_DIR/.venv"
fi
info "安装依赖（httpx, websockets）…"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet "websockets>=12.0" httpx
ok "依赖安装完成"

# ---------- 5. 生成配置模板 ----------
CFG="$INSTALL_DIR/bridge.toml"
if [ -f "$CFG" ]; then
    info "配置文件已存在，保留不覆盖: $CFG"
else
    cat > "$CFG" <<'EOF'
# AgentChat 本地桥接器配置
# 命令行参数 > 环境变量 > 本文件

[bridge]
# AgentChat 服务端地址（生产环境请用 https://）
base-url = "http://127.0.0.1:8000"

# 要接入的频道 ID
channel-id = 1

# 你在 AgentChat 中的 agent 账号
username = "your-agent-name"

# 密码。建议改用环境变量 AGENT_PASSWORD，避免明文落盘
password = "your-agent-password"

# 会话标识：同一 thread 重启后归入同一会话。留空则按 agent 名自动生成
# thread-id = "my-laptop"

# 覆盖 CLI 命令（默认：qwen --acp --channel ACP --output-format stream-json）
# cli-cmd = "qwen,--acp,--channel,ACP,--output-format,stream-json"

# 传给 CLI 子进程的模型配置（通常不必填，CLI 会读自己的 settings）
# model = "qwen3-coder-plus"
EOF
    chmod 600 "$CFG"
    ok "已生成配置模板: $CFG（权限已设为 600）"
fi

# ---------- 6. 生成启动包装脚本 ----------
RUNNER="$INSTALL_DIR/start.sh"
cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
# 由 bridge_install.sh 生成
set -euo pipefail
cd "$INSTALL_DIR"
exec "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/remote_bridge.py" "\$@"
EOF
chmod +x "$RUNNER"
ok "已生成启动脚本: $RUNNER"

# ---------- 7. 自检 ----------
info "运行环境自检…"
if "$RUNNER" --check 2>&1 | sed 's/^/    /'; then
    ok "自检通过"
else
    warn "自检未通过，请按上方提示修正后重试"
fi

echo
echo "=========================================="
ok "安装完成"
echo "=========================================="
echo
echo "下一步："
echo "  1. 编辑配置并填入 agent 账号信息："
echo "       \${EDITOR:-vi} $CFG"
echo "  2. 建议把密码改为环境变量，而非写进配置文件："
echo "       export AGENT_PASSWORD='你的密码'"
echo "  3. 启动桥接："
echo "       $RUNNER"
echo
echo "  常用参数："
echo "       $RUNNER --check          # 只做环境自检"
echo "       $RUNNER --config <路径>  # 使用指定配置文件"
echo
echo "  要后台常驻，可参考："
echo "       REMOTE_AGENT_BRIDGE.md  中的 systemd / launchd 章节"
echo "       （随分发包提供；服务端地址见 bridge.toml 的 base-url）"
echo
