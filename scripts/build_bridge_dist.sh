#!/usr/bin/env bash
# 构建桥接器分发包（内网分发用）
#
# 用法：
#   bash scripts/build_bridge_dist.sh [版本号]
#
# 产物：
#   dist/bridge/agentchat-bridge-<版本>.tar.gz   分发包
#   dist/bridge/SHA256SUMS                        校验和
#   dist/bridge/version                           版本标记
#
# 分发包内文件：
#   remote_bridge.py        桥接器本体
#   bridge_install.sh       一键安装脚本
#   REMOTE_AGENT_BRIDGE.md  部署文档
#   README.txt              快速开始
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-1.0.0}"
NAME="agentchat-bridge-${VERSION}"

SRC_RB="$REPO_ROOT/scripts/remote_bridge.py"
SRC_INST="$REPO_ROOT/scripts/bridge_install.sh"
SRC_DOC="$REPO_ROOT/docs/REMOTE_AGENT_BRIDGE.md"

for f in "$SRC_RB" "$SRC_INST" "$SRC_DOC"; do
    [ -f "$f" ] || { echo "缺少源文件: $f" >&2; exit 1; }
done

OUT="$REPO_ROOT/dist/bridge"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

mkdir -p "$OUT" "$BUILD/$NAME"
cp "$SRC_RB" "$SRC_INST" "$SRC_DOC" "$BUILD/$NAME/"
chmod +x "$BUILD/$NAME/bridge_install.sh" "$BUILD/$NAME/remote_bridge.py"

# 快速开始说明
cat > "$BUILD/$NAME/README.txt" <<EOF
AgentChat 本地桥接器 v$VERSION
========================================

作用
    把本机安装的 CLI agent（qwen）接入 AgentChat，
    使你在 AgentChat 频道里 @提及 该 agent 时能收到回复。
    桥接器与 CLI 必须在同一台机器上运行（stdio 限制）。

快速开始
    1) 安装（自动建独立运行环境，不污染系统）
         bash bridge_install.sh

    2) 编辑配置
         vi ~/.agentchat/bridge/bridge.toml

    3) 设置密码并启动
         export AGENT_PASSWORD='你的agent密码'
         ~/.agentchat/bridge/start.sh

    自检（不连接，只检查环境）：
         ~/.agentchat/bridge/start.sh --check

系统要求
    - Python 3.9+（3.11+ 可直接使用 bridge.toml；更低版本请用环境变量配置）
    - qwen CLI 已安装且在 PATH 中
    - 能访问 AgentChat 服务端地址

依赖
    仅 httpx 与 websockets 两个第三方包，安装脚本会自动处理。

详细文档
    见 REMOTE_AGENT_BRIDGE.md

卸载
    rm -rf ~/.agentchat/bridge
EOF

# 打包
( cd "$BUILD" && tar czf "$OUT/$NAME.tar.gz" "$NAME" )
( cd "$OUT" && sha256sum "$NAME.tar.gz" > SHA256SUMS && echo "$VERSION" > version )

echo "构建完成："
ls -lh "$OUT/$NAME.tar.gz" "$OUT/SHA256SUMS" "$OUT/version"
echo
echo "校验和："
cat "$OUT/SHA256SUMS"
