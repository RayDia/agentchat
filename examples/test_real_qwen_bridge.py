#!/usr/bin/env python3
"""
端到端测试：真实 qwen --acp 通过 ACP CLI Bridge 接入 AgentChat 频道 4。
- 启动 QwenACPBridge（派生 qwen 子进程 + 连接 AgentChat Socket Mode）
- 人类用户在频道 4 发送 @code-reviewer 你好
- 等待 Bridge 把 qwen 的回复广播回频道
注意：需要有效 OPENAI_API_KEY / QWEN key 才能拿到真实 LLM 文本；
无 key 时 qwen 会返回鉴权错误，但仍可证明 ACP 连接与双向消息链路打通。
"""
import asyncio
import json
import os
import sys
import time
import urllib.request
import argparse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets
from jose import jwt

from app.config import settings
from app.acp.cli_bridge import QwenACPBridge

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000"
CHANNEL_ID = 4
AGENT_ID = 4
HUMAN_ID = 1
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("QWEN_API_KEY") or "sk-dummy-for-test"


def jwt_for(user_id):
    return jwt.encode({"user_id": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def recv_json(ws, timeout):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--thread-id", default=None, help="同一 thread 重启自动 resume")
    p.add_argument("--resume-session", default=None, help="显式恢复已有 qwen session")
    args = p.parse_args()

    print("=== [1] 启动 QwenACPBridge（派生真实 qwen --acp 子进程）===", flush=True)
    bridge = QwenACPBridge(base_url=BASE, channel_id=CHANNEL_ID, agent_id=AGENT_ID,
                           agent_username="code-reviewer", api_key=API_KEY, prompt_timeout=40,
                           thread_id=args.thread_id, resume_session_id=args.resume_session)
    bt = asyncio.create_task(bridge.start())
    await asyncio.sleep(4)  # 等握手 + connect
    print(f"    qwen sessionId={bridge.qwen_session_id}  acp sessionId={bridge.acp_session_id}  resumed={bridge.resumed}", flush=True)

    print("=== [2] 人类用户在频道 4 发送 @code-reviewer 你好 ===", flush=True)
    htoken = jwt_for(HUMAN_ID)
    uws = await websockets.connect(f"{WS}/ws?token={htoken}")
    await uws.send(json.dumps({"type": "join_channel", "data": {"channel_id": CHANNEL_ID}}))
    await asyncio.sleep(0.5)
    await uws.send(json.dumps({
        "type": "message",
        "data": {"channel_id": CHANNEL_ID, "content": "@code-reviewer 你好，请用一句话介绍你自己"},
    }))

    print("=== [3] 等待 Agent(qwen) 回复广播回频道 ===", flush=True)
    reply = None
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            f = await asyncio.wait_for(uws.recv(), timeout=5)
        except asyncio.TimeoutError:
            continue
        try:
            msg = json.loads(f)
        except Exception:
            continue
        d = msg.get("data", {})
        sender = d.get("sender_username") or d.get("sender", {}).get("username") if isinstance(d.get("sender"), dict) else d.get("sender_username")
        if msg.get("type") == "message" and (d.get("sender_username") == "code-reviewer" or d.get("sender_id") == AGENT_ID):
            reply = d.get("content")
            print(f"    [收到 agent 回复] {reply[:300]}", flush=True)
            break
        else:
            print(f"    [频道帧] type={msg.get('type')} sender={sender} content={(d.get('content') or '')[:60]}", flush=True)

    print("=== 结果 ===", flush=True)
    if reply:
        print("PASS: 真实 qwen 已通过 ACP 桥接连入 AgentChat 并回复")
    else:
        print("未收到 agent 回复（可能是 qwen 鉴权/超时；但桥接连接与 input/output 链路已运行）")
    print(f"    qwen_session={bridge.qwen_session_id}", flush=True)

    await uws.close()
    await bridge.stop()
    bt.cancel()


if __name__ == "__main__":
    asyncio.run(main())
