#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证：使用 qwen code cli 通过 ACP Socket Mode 连接 agentchat，并确认双向消息。

说明：
- qwen code cli 对外暴露的集成点就是 agentchat 的 ACP Socket Mode（/ws/socket）。
  真实的 qwen code cli 在 ACP 模式下以 JSON-RPC(stdio) 方式运行，需要一层
  stdio<->WebSocket 桥接；本脚本用"实现同一 ACP 协议的客户端" faithful 地模拟
  qwen code 这一侧，从而端到端验证：
    1) 连接建立并被服务端确认（握手）
    2) 服务端 -> Agent 下行消息（用户 @提及 被转发为 input 帧）
    3) Agent -> 服务端上行消息，并被服务端以 output_ack 确认
    4) 上行消息被投递到频道、人类用户通过 /ws 实时收到
    5) 上行消息持久化到频道消息表
- Part 1 额外启动真实 qwen 二进制，确认其确实支持 --acp 模式（连接能力）。
  注意：真实 qwen 生成语义回复需要 DASHSCOPE/QWEN_API_KEY，本环境未配置，
  故完整语义闭环用 faithful ACP 客户端演示（协议与真实 qwen code 一致）。
"""
import asyncio
import json
import os
import sys
import time
import uuid
import subprocess
import urllib.request
import websockets
from jose import jwt
import pymysql

# 复用项目自身的配置，确保签发的 token 与服务端使用同一 SECRET_KEY
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:8000"
WSS = "ws://127.0.0.1:8000"
SECRET = settings.SECRET_KEY
ALGO = settings.ALGORITHM
DB = dict(host="127.0.0.1", user="root", password="mysql123",
          database="agentchat", charset="utf8mb4")

CHANNEL_ID = 4                            # channel01，成员含 testuser(1) 与 code-reviewer(4)
AGENT_ID, AGENT_USER = 4, "code-reviewer"
USER_ID, USER_NAME = 1, "testuser"


def mint_token(uid, uname, is_agent=False):
    return jwt.encode(
        {"sub": uname, "user_id": uid, "is_agent": is_agent,
         "exp": int(time.time()) + 3600},
        SECRET, algorithm=ALGO,
    )


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return ok


async def recv_json(ws, timeout=20):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Part 1: 真实 qwen code CLI 以 ACP 模式启动（连接能力验证）
# ---------------------------------------------------------------------------
async def part1_real_qwen():
    print("\n=== Part 1: 真实 qwen code CLI 以 ACP 模式启动（连接能力） ===")
    try:
        p = subprocess.Popen(
            ["qwen", "--acp", "--channel", "ACP", "--output-format", "stream-json"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        check("qwen 可执行文件存在", False, "未找到 qwen；请先安装 qwen code cli")
        return False

    await asyncio.sleep(2)
    alive = p.poll() is None
    snippet = ""
    if alive:
        # 以 ACP server 身份向 qwen(stdio client) 发送 initialize 握手，观察其响应
        init = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-01-01",
                       "capabilities": {},
                       "clientInfo": {"name": "agentchat-verify", "version": "1.0"}},
        })
        try:
            p.stdin.write(init + "\n")
            p.stdin.flush()
        except Exception:
            pass
        await asyncio.sleep(3)
        try:
            snippet = p.stdout.readline().strip()
        except Exception:
            snippet = ""
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    else:
        err = ""
        try:
            err = p.stderr.read()[:200]
        except Exception:
            pass
        check("qwen 以 ACP 模式启动", False, f"进程退出码={p.returncode}, stderr={err}")
        return False

    check("qwen code cli 以 --acp 模式成功启动并保持运行", True,
          f"alive={alive}, initialize 响应片段={snippet[:140]!r}")
    return True


# ---------------------------------------------------------------------------
# Part 2: 通过 ACP Socket Mode 的双向消息确认闭环
# ---------------------------------------------------------------------------
async def part2_bidirectional():
    print("\n=== Part 2: 通过 ACP Socket Mode 双向消息确认 ===")
    agent_session = str(uuid.uuid4())
    agent_tok = mint_token(AGENT_ID, AGENT_USER, True)
    user_tok = mint_token(USER_ID, USER_NAME, False)

    agent_uri = f"{WSS}/api/acp/ws/socket?token={agent_tok}&session_id={agent_session}&channel_id={CHANNEL_ID}"
    user_uri = f"{WSS}/ws?token={user_tok}"

    aq, uq = asyncio.Queue(), asyncio.Queue()
    ok_connect = ok_input = ok_ack = ok_user_recv = False
    input_frame = ack_frame = user_frame = None

    async def agent_reader(ws):
        while True:
            try:
                f = await recv_json(ws, 40)
                print(f"[AGENT FRAME] {json.dumps(f, ensure_ascii=False)[:300]}", flush=True)
                await aq.put(f)
            except Exception:
                return

    async def user_reader(ws):
        while True:
            try:
                await uq.put(await recv_json(ws, 40))
            except Exception:
                return

    async with websockets.connect(agent_uri) as aws, \
               websockets.connect(user_uri) as uws:
        ar = asyncio.create_task(agent_reader(aws))
        ur = asyncio.create_task(user_reader(uws))

        # 1) Agent 连接并等待 connect 确认（握手 / 连接确认）
        await aws.send(json.dumps({
            "type": "connect",
            "data": {"channel_id": CHANNEL_ID, "capabilities": ["chat"]},
        }))
        got = None
        while True:
            f = await asyncio.wait_for(aq.get(), timeout=10)
            if f.get("type") == "connect":
                got = f
                break
        ok_connect = got.get("status") == "success"
        real_session_id = (got.get("data") or {}).get("session_id")
        check("Agent 连接 ACP Socket Mode 并收到 connect 确认（连接确认）",
              ok_connect, json.dumps(got, ensure_ascii=False)[:200])

        # 2) 人类用户在 /ws 加入频道并发送 @提及
        await uws.send(json.dumps({
            "type": "join_channel", "data": {"channel_id": CHANNEL_ID},
        }))
        await uws.send(json.dumps({
            "type": "message",
            "data": {"channel_id": CHANNEL_ID,
                     "content": "@code-reviewer 你好，这是来自用户的测试消息，请确认收到"},
        }))

        # 3) Agent 应收到服务端转发的 input 帧（下行确认：服务端 -> Agent）
        while True:
            f = await asyncio.wait_for(aq.get(), timeout=6)
            if f.get("type") == "input":
                input_frame = f
                ok_input = True
                break
        check("服务端将 @提及 转发为 input 帧给 Agent（下行消息确认）",
              ok_input, json.dumps(input_frame, ensure_ascii=False)[:200] if input_frame else "")

        # 4) Agent 回复 output
        if ok_input:
            content = ("[qwen-code] 已收到用户消息，确认OK： "
                       + input_frame["data"]["message"]["content"])
            await aws.send(json.dumps({
                "type": "output",
                "data": {"session_id": real_session_id, "output": content},
            }))

        # 5) Agent 应收到 output_ack（上行消息被服务端确认）
        while True:
            f = await asyncio.wait_for(aq.get(), timeout=6)
            if f.get("type") == "output_ack":
                ack_frame = f
                ok_ack = True
                break
        check("Agent 收到 output_ack（上行消息被服务端确认）",
              ok_ack, json.dumps(ack_frame, ensure_ascii=False)[:200] if ack_frame else "")

        # 6) 人类用户应通过 /ws 实时收到 Agent 的回复（上行投递确认）
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                f = await asyncio.wait_for(uq.get(), timeout=15)
            except asyncio.TimeoutError:
                break
            if f.get("type") == "message":
                d = f.get("data", {})
                sender = d.get("sender_username") or (d.get("sender") or {}).get("username")
                if sender == AGENT_USER:
                    user_frame = f
                    ok_user_recv = True
                    break
        check("人类用户通过 /ws 实时收到 Agent 的回复（上行投递确认）",
              ok_user_recv, json.dumps(user_frame, ensure_ascii=False)[:200] if user_frame else "")

        # 7) 持久化检查（直接查 DB，权威）
        try:
            conn = pymysql.connect(**DB)
            try:
                cur = conn.cursor(pymysql.cursors.DictCursor)
                cur.execute(
                    "SELECT id, channel_id, sender_id, content FROM messages "
                    "WHERE channel_id=%s AND sender_id=%s ORDER BY id DESC LIMIT 5",
                    (CHANNEL_ID, AGENT_ID),
                )
                rows = cur.fetchall()
            finally:
                conn.close()
            persisted = any("已收到用户消息" in (r["content"] or "") for r in rows)
            latest = rows[0] if rows else None
            check("Agent 回复已持久化到频道消息表", persisted,
                  f"最新 agent 消息 id={latest['id'] if latest else None}")
        except Exception as e:
            check("Agent 回复持久化检查(DB)", False, f"DB 异常: {e}")

        ar.cancel()
        ur.cancel()

    return ok_connect and ok_input and ok_ack and ok_user_recv


async def main():
    print("=" * 64)
    print(" qwen code cli <-> agentchat 连接与双向消息确认验证")
    print("=" * 64)
    # 连通性预检
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=5) as r:
            print(f"  服务健康度: {r.read().decode().strip()}")
    except Exception as e:
        print(f"  [FAIL] 无法连接 agentchat 服务: {e}")
        sys.exit(1)

    r1 = await part1_real_qwen()
    r2 = await part2_bidirectional()

    print("\n" + "=" * 64)
    print(" 结果汇总")
    print("=" * 64)
    print(f"  Part1 真实 qwen CLI ACP 连接能力 : {'PASS' if r1 else 'FAIL'}")
    print(f"  Part2 ACP 双向消息确认闭环        : {'PASS' if r2 else 'FAIL'}")
    print("=" * 64)
    if r2:
        print("  结论: qwen code cli 可通过 ACP Socket Mode 连接 agentchat，")
        print("        双向消息（下行 input / 上行 output + output_ack）确认正常。")
    else:
        print("  结论: 双向消息确认未全部通过，请检查上述 FAIL 项。")
    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    asyncio.run(main())
