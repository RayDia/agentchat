#!/usr/bin/env bash
# AgentChat 推送工具 —— 让 agent session 主动向频道发消息，无需被 @提及。
#
# 前提：桥接器已启动且其本地推送服务可用（默认 http://127.0.0.1:8765/push）。
#       桥接器会把 AGENTCHAT_PUSH_URL 注入 CLI 子进程，因此同 session 内直接用即可。
#
# 用法：
#   agentchat-send.sh "消息内容"
#   echo "多行内容" | agentchat-send.sh
#   agentchat-send.sh --json '{"content":"消息", "output":"..."}'
#
# 退出码：0 成功；非 0 见下方错误信息。
set -euo pipefail

URL="${AGENTCHAT_PUSH_URL:-http://127.0.0.1:8765/push}"

die() { printf '%s\n' "$*" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || die "需要 curl，请先安装"

MODE="text"
case "${1:-}" in
    --json) MODE="json"; shift ;;
esac

if [ "$MODE" = "json" ]; then
    BODY="${1:-}"
    [ -n "$BODY" ] || die "用法: agentchat-send.sh --json '{\"content\":\"消息\"}'"
    CT="application/json"
else
    if [ "$#" -gt 0 ]; then
        BODY="$*"
    else
        # 无参数时从 stdin 读取，便于管道/多行内容
        BODY="$(cat)"
    fi
    [ -n "${BODY//[[:space:]]/}" ] || die "用法: agentchat-send.sh \"消息内容\"（或经管道输入）"
    CT="text/plain; charset=utf-8"
fi

RESP="$(curl -sS --max-time 35 -X POST "$URL" \
              -H "Content-Type: $CT" \
              --data-binary "$BODY" 2>&1)" || die "推送请求失败：无法访问 $URL
请确认桥接器正在运行，且本地推送端口未被禁用（见 --push-port）。"

# 成功时服务返回 {"ok":true,"message_id":N,...}
case "$RESP" in
    *'"ok":true'*|*'"ok": true'*)
        printf '已推送到频道。服务端响应: %s\n' "$RESP"
        ;;
    *)
        printf '推送失败，服务端响应: %s\n' "$RESP" >&2
        exit 1
        ;;
esac
