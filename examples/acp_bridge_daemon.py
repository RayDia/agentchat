#!/usr/bin/env python3
"""
ACP CLI Bridge 守护进程（acp-bridge）

将真实 CLI（qwen --acp 等，stdio JSON-RPC）接入 AgentChat 的指定频道：
  - 作为 ACP Socket Mode client 连接 AgentChat 的 /api/acp/ws/socket
  - 派生 qwen 子进程并走 stdio JSON-RPC 握手（initialize / session/new / session/prompt）
  - 双向翻译：频道 @提及(input 帧) -> qwen；qwen session/update -> 频道(output 帧)

用法：
  python examples/acp_bridge_daemon.py \
      --base-url http://127.0.0.1:8000 \
      --channel-id 4 --agent-id 4 --agent-username code-reviewer \
      --api-key $OPENAI_API_KEY

  # 同一 thread_id 重启后自动 resume 到已有 qwen 会话：
  --thread-id thread-1

  # 显式恢复某个已有 qwen session：
  --resume-session <qwen-session-id>
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.acp.cli_bridge import QwenACPBridge, bridge_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    p = argparse.ArgumentParser(description="ACP CLI Bridge Daemon")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--channel-id", type=int, required=True,
                   help="AgentChat 频道 ID（用户在此频道 @提及 Agent）")
    p.add_argument("--agent-id", type=int, default=None,
                   help="AgentChat 中 Agent 用户的 id（is_agent=True）。"
                        "用 --username/--password 登录时可不填，会自动获取")
    p.add_argument("--agent-username", default="code-reviewer")
    p.add_argument("--username", default=None,
                   help="Agent 账号用户名（走 /api/auth/login，远端部署推荐）")
    p.add_argument("--password", default=os.environ.get("AGENT_PASSWORD"),
                   help="Agent 账号密码（也可用环境变量 AGENT_PASSWORD）")
    p.add_argument("--token", default=None,
                   help="直接使用已有的 access token（优先级最高）")
    p.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"),
                   help="CLI 所需的 API Key（如 OPENAI_API_KEY / QWEN key）")
    p.add_argument("--model", default=None)
    p.add_argument("--cli-cmd", default=None,
                   help="覆盖默认 CLI 命令，逗号分隔，例如 'qwen,--acp,--channel,ACP'")
    p.add_argument("--thread-id", default=None,
                   help="同一 thread_id 在桥接重启时自动 resume 到已有 qwen 会话")
    p.add_argument("--resume-session", default=None,
                   help="显式恢复某个已存在的 qwen session id")
    args = p.parse_args()

    if not (args.token or args.agent_id or (args.username and args.password)):
        p.error("必须提供 --token / --agent-id / (--username 与 --password) 之一")

    async def run():
        cli_cmd = args.cli_cmd.split(",") if args.cli_cmd else None
        bridge = QwenACPBridge(
            base_url=args.base_url,
            channel_id=args.channel_id,
            agent_id=args.agent_id,
            agent_username=args.agent_username,
            token=args.token,
            username=args.username,
            password=args.password,
            api_key=args.api_key,
            model=args.model,
            cli_cmd=cli_cmd,
            thread_id=args.thread_id,
            resume_session_id=args.resume_session,
        )
        bridge_manager.add(f"ch{args.channel_id}", bridge)
        logging.getLogger("ACPBridge").info(
            "启动桥接 channel=%s agent=%s(login=%s) api_key=%s thread_id=%s resume=%s",
            args.channel_id, args.username or args.agent_username,
            "yes" if args.password else "no",
            "yes" if args.api_key else "no", args.thread_id, args.resume_session,
        )
        try:
            await bridge.start()
        finally:
            await bridge_manager.stop_all()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("ACPBridge").info("收到中断，退出")


if __name__ == "__main__":
    main()
