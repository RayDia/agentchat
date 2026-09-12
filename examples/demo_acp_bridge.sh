#!/bin/bash
# ACP CLI Bridge 演示脚本
# 将真实 qwen --acp 接入 AgentChat 频道，并在频道里 @提及触发双向消息。
set -e

BASE_URL="http://localhost:8000"
CHANNEL_ID="${CHANNEL_ID:-4}"
AGENT_ID="${AGENT_ID:-4}"
AGENT_USERNAME="${AGENT_USERNAME:-code-reviewer}"
API_KEY="${API_KEY:-$OPENAI_API_KEY}"

echo "═══════════════════════════════════════════════════════════"
echo "  ACP CLI Bridge 演示（真实 qwen --acp 接入 AgentChat）"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "前置：AgentChat 服务已在 $BASE_URL 运行，且存在"
echo "      channel_id=$CHANNEL_ID、agent 用户 id=$AGENT_ID(is_agent=True)"
echo ""

echo "🚀 步骤1: 启动 Bridge（派生 qwen 子进程并连接频道）"
echo "    python examples/acp_bridge_daemon.py \\"
echo "        --base-url $BASE_URL --channel-id $CHANNEL_ID \\"
echo "        --agent-id $AGENT_ID --agent-username $AGENT_USERNAME \\"
echo "        --api-key ${API_KEY:+<已设置>}${API_KEY:-<未设置: 仅验证连接, 无 LLM 文本>}"
echo ""
echo "    后台运行示例："
echo "      python examples/acp_bridge_daemon.py --channel-id $CHANNEL_ID \\"
echo "          --agent-id $AGENT_ID --api-key \"\$OPENAI_API_KEY\" &"
echo ""

echo "📋 步骤2: 确认 Bridge 已连接"
echo "    日志出现: [bridge] 已连接 AgentChat(channel=$CHANNEL_ID) 与 qwen(session=...)"
echo "              [qwen session/new] sessionId=..."
echo ""

echo "💬 步骤3: 在频道 $CHANNEL_ID 发送消息 @$AGENT_USERNAME 你好"
echo "    → AgentChat 将消息作为 input 帧转发给 Bridge"
echo "    → Bridge 调用 qwen session/prompt，回传 output 帧"
echo "    → 回复广播回频道，所有成员可见"
echo ""

echo "═══════════════════════════════════════════════════════════"
echo "  📚 文档: docs/ACP_CLI_BRIDGE.md"
echo "═══════════════════════════════════════════════════════════"
