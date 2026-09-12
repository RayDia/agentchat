#!/usr/bin/env python3
"""
resume 演示：仅依赖真实 qwen --acp（不需要 AgentChat 服务端）。

流程：
  1) 启动 qwen，initialize，session/new 指定一个稳定 sessionId X（新建）
  2) 退出 qwen 进程（会话 X 已落盘）
  3) 再次启动 qwen，initialize，session/new 指定同一 X
     -> qwen 从磁盘 attach（resume），返回相同 sessionId，无冲突

用法：
  python examples/demo_resume.py
  QWEN_API_KEY=... python examples/demo_resume.py   # 如要真正跑 prompt 可带 key
"""
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def session_new(proc, rid, pending, loop, session_id):
    rid[0] += 1
    i = rid[0]
    fut = loop.create_future()
    pending[i] = fut
    proc.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": i, "method": "session/new",
        "params": {"cwd": os.getcwd(), "mcpServers": [],
                   "_meta": {"qwen-code/sessionId": session_id}},
    }) + "\n").encode())
    await proc.stdin.drain()
    try:
        return await asyncio.wait_for(fut, 60)
    finally:
        pending.pop(i, None)


async def spawn(proc_holder, pending, rid, loop):
    proc = await asyncio.create_subprocess_exec(
        "qwen", "--acp", "--channel", "ACP", "--output-format", "stream-json",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={**os.environ},
    )
    async def reader():
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode().strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if "id" in obj and "method" not in obj:
                f = pending.pop(obj["id"], None)
                if f and not f.done():
                    f.set_result(obj)
    asyncio.create_task(reader())
    await asyncio.sleep(1)
    rid[0] += 1
    i = rid[0]
    fut = loop.create_future()
    pending[i] = fut
    proc.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": i, "method": "initialize",
        "params": {"protocolVersion": 1,
                   "capabilities": {"prompts": {}, "tools": {}, "resources": {}},
                   "clientInfo": {"name": "agentchat-bridge", "version": "1.0.0"}},
    }) + "\n").encode())
    await proc.stdin.drain()
    try:
        await asyncio.wait_for(fut, 30)
    finally:
        pending.pop(i, None)
    return proc


async def main():
    loop = asyncio.get_event_loop()
    pending = {}
    rid = [0]
    x = str(uuid.uuid4())

    p1 = await spawn(None, pending, rid, loop)
    r1 = await session_new(p1, rid, pending, loop, x)
    sid1 = (r1.get("result") or {}).get("sessionId")
    print(f"[1] 新建会话 -> sessionId={sid1} (请求 {x})", flush=True)
    p1.terminate()
    try:
        await asyncio.wait_for(p1.wait(), 5)
    except Exception:
        p1.kill()
    await asyncio.sleep(1)

    p2 = await spawn(None, pending, rid, loop)
    r2 = await session_new(p2, rid, pending, loop, x)
    sid2 = (r2.get("result") or {}).get("sessionId")
    print(f"[2] resume 同一 id -> sessionId={sid2} (请求 {x})", flush=True)
    p2.terminate()

    if r2.get("error"):
        print(f"FAIL: resume 失败 -> {r2['error']}")
    elif sid2 == x:
        print("PASS: 跨进程 resume 成功（qwen 恢复了已落盘的会话）")
    else:
        print(f"WARN: 返回了不同 sessionId ({sid2})，未 resume")


if __name__ == "__main__":
    asyncio.run(main())
