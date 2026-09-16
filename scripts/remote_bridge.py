#!/usr/bin/env python3
"""
AgentChat 远端桥接器（单文件、无项目依赖）

用途：在一台**只装了 qwen CLI** 的远端机器上运行，把该机的 qwen 接入远端的
AgentChat 服务端。通过 /api/auth/login 换取 token，因此**无需共享 SECRET_KEY**。

依赖（仅 2 个第三方包，其余全为标准库）：
    pip install "websockets>=12.0" httpx

用法：
    python remote_bridge.py \
        --base-url https://agentchat.example.com \
        --channel-id 4 \
        --username code-reviewer \
        --password "$AGENT_PASSWORD" \
        --thread-id my-thread-1

常驻运行（systemd）见文件末尾注释；或：
    nohup python remote_bridge.py ... > bridge.log 2>&1 &

架构：
    AgentChat 服务端 <--WSS-- 本进程 --stdio JSON-RPC--> qwen --acp 子进程
本进程与 qwen 子进程必须同机（stdio 限制），但可与 AgentChat 服务端异机。
"""
import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets


def _configure_stdio_encoding():
    """强制 stdout/stderr 按 UTF-8 输出。

    Windows 上把输出重定向到文件/管道时（后台运行、systemd、nohup 均如此），
    流的编码退化为 locale 编码（中文版为 cp936/GBK），一旦日志含 GBK 之外的
    字符（emoji、某些符号）抛 UnicodeEncodeError 导致进程崩溃。
    这里统一按 UTF-8 输出并以 replace 兜底，保证「日志不会因为编码挂掉」。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        # reconfigure 需要 Python 3.7+，且重定向后的 TextIOWrapper 才支持
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:      # 某些替身流（IDE 接管等）不支持，忽略即可
            pass


_configure_stdio_encoding()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("RemoteBridge")

# 只有这些 sessionUpdate 类型属于「助手正式回复」（过滤思考/工具调用噪声）
_MESSAGE_UPDATE_TYPES = {"agent_message_chunk", "agent_message",
                         "message_chunk", "message"}

DEFAULT_CLI_CMD = ["qwen", "--acp", "--channel", "ACP",
                   "--output-format", "stream-json"]


async def login(base_url, username, password, timeout=20.0):
    """走 /api/auth/login 换 token。

    该端点用 OAuth2PasswordRequestForm，**必须 form-data**（不能发 JSON）。
    """
    url = f"{base_url.rstrip('/')}/api/auth/login"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, data={"username": username, "password": password})
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败 HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"登录响应缺少 access_token: {body}")
    return token, (body.get("user") or {})


def _extract_content(raw, content_type=""):
    """从推送请求体里提取内容，兼容 JSON 与纯文本两种写法。

    支持：{"content":"文本"} / {"output":"文本"} / {"text":"文本"} / 纯文本
    """
    text = raw.decode("utf-8", "replace").strip() if raw else ""
    if not text:
        return ""
    looks_json = "json" in (content_type or "").lower() or text[:1] in ("{", "[")
    if looks_json:
        try:
            obj = json.loads(text)
        except Exception:
            return text
        if isinstance(obj, dict):
            return str(obj.get("content") or obj.get("output")
                       or obj.get("text") or "")
        return str(obj)
    return text


def build_mcp_config(push_port):
    """生成供 CLI（qwen --mcp-config）使用的 MCP 配置。

    把 agentchat_send / agentchat_status 两个工具暴露给 agent，使其可以
    不经 shell、直接以标准工具调用的方式向频道推送消息。
    返回临时文件路径；不可用时返回 None。
    """
    try:
        server = Path(__file__).resolve().parent / "agentchat_mcp_server.py"
    except Exception:
        return None
    if not server.is_file():
        return None

    cfg = {"mcpServers": {"agentchat": {
        "command": sys.executable or "python3",
        "args": [str(server)],
        "env": {"AGENTCHAT_PUSH_URL": f"http://127.0.0.1:{push_port}/push"},
    }}}
    try:
        fd, path = tempfile.mkstemp(prefix="agentchat-mcp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("生成 MCP 配置失败: %s", e)
        return None
    return path


def start_push_server(bridge):
    """在本机起一个极简 HTTP 服务，让同机的 agent session 能推送到频道。

    为什么必须有这一跳：服务端的 handle_output 会校验 session 与当前
    websocket 绑定（agent_session.websocket != websocket 一律拒绝），
    所以外部进程无法直接复用桥接器的会话，只能由桥接器代为转发。

    仅监听 127.0.0.1，不对外暴露。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        logger.warning("无法获取事件循环，跳过本地推送服务")
        return

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type",
                             "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def do_GET(self):
            self._reply(200, {
                "ok": True,
                "service": "agentchat-bridge-push",
                "connected": bridge.ws is not None,
                "channel_id": bridge.channel_id,
                "session_ready": bool(bridge.acp_session_id),
            })

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            raw = self.rfile.read(n) if n else b""
            content = _extract_content(raw, self.headers.get("Content-Type", ""))
            if not content:
                self._reply(400, {"ok": False, "error": "缺少推送内容"})
                return
            try:
                fut = asyncio.run_coroutine_threadsafe(bridge.push(content), loop)
                mid = fut.result(timeout=30)
            except Exception as e:
                self._reply(502, {"ok": False, "error": str(e)})
                return
            self._reply(200, {"ok": True, "message_id": mid,
                              "channel_id": bridge.channel_id})

        def log_message(self, fmt, *args):
            logger.debug("[push-server] " + fmt % args)

    # 端口可能被同机的其他服务占用（实测 8765 常被开发项目占用）。
    # 若直接放弃，MCP 工具与 agentchat-send 仍指向该端口 → 调用全部失败，
    # 且只在启动日志里留一行 warning，极难排查。故改为向后顺延重试。
    srv, actual_port = None, None
    want = int(bridge.push_port)
    for candidate in range(want, want + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            actual_port = candidate
            break
        except OSError as e:
            if candidate == want:
                logger.warning("推送端口 %s 已被占用（%s），尝试顺延…", want, e)
            continue

    if srv is None:
        logger.error("推送服务启动失败：%s~%s 全部被占用，"
                     "agent 将无法主动推送（可用 --push-port 指定其他端口）",
                     want, want + 9)
        return None

    if actual_port != want:
        logger.warning("推送服务改用端口 %s（原 %s 被占用）。"
                       "若有外部脚本硬编码了旧端口，请同步更新",
                       actual_port, want)
    bridge.push_port = actual_port

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logger.info("本地推送服务已就绪: http://127.0.0.1:%s/push"
                "（agent session 内用 $AGENTCHAT_PUSH_URL 调用）", actual_port)
    return srv


class RemoteBridge:
    """把一个 qwen --acp 子进程桥接到远端 AgentChat 的某个频道。"""

    def __init__(self, base_url, channel_id, username, password,
                 cli_cmd=None, api_key=None, model=None,
                 thread_id=None, resume_session_id=None, prompt_timeout=180,
                 push_port=None, permission_mode="none",
                 permission_allowlist=None, mcp_enabled=True):
        self.base_url = base_url.rstrip("/")
        self.channel_id = channel_id
        self.username = username
        self.password = password
        self.cli_cmd = cli_cmd or DEFAULT_CLI_CMD
        self.api_key = api_key
        self.model = model
        self.thread_id = thread_id
        self.resume_session_id = resume_session_id
        self.prompt_timeout = prompt_timeout
        # 本地推送通道端口：0 或 None 表示禁用
        self.push_port = push_port
        # 工具审批策略：none(全拒绝) / auto(批准) / allowlist(命令白名单)
        self.permission_mode = (permission_mode or "none").lower()
        self.permission_allowlist = permission_allowlist or []
        # 是否向 CLI 注入 AgentChat MCP 工具（让 agent 能直接调用推送）
        self.mcp_enabled = mcp_enabled
        self.mcp_config_path = None

        self.token = None
        self.user_id = None
        self.ws_base = self.base_url.replace("http://", "ws://", 1) \
                                 .replace("https://", "wss://", 1)

        self.proc = None
        self.ws = None
        self.acp_session_id = None
        self.qwen_session_id = None
        self._rid = 0
        self._pending = {}
        self._accum = ""
        self._accum_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()
        self._push_lock = asyncio.Lock()      # 串行化推送，避免 ack 串台
        self._ack_waiter = None
        self._push_server = None
        self._stopping = False
        # 输入队列：LLM 推理是慢操作，必须与接收循环解耦，否则消息被丢
        self._input_queue = asyncio.Queue()
        self.max_input_queue = 100

    # ---------------- 认证 ----------------
    @property
    def ws_url(self):
        return f"{self.ws_base}/api/acp/ws/socket?token={self.token}"

    async def refresh_token(self):
        async with self._login_lock:
            try:
                token, user = await login(self.base_url, self.username, self.password)
            except Exception as e:
                logger.error("重新登录失败: %s", e)
                return False
            self.token = token
            if user.get("id"):
                self.user_id = user["id"]
            logger.info("已获取 token（user=%s id=%s）", self.username, self.user_id)
            return True

    async def ensure_token(self):
        if not self.token:
            if not await self.refresh_token():
                raise RuntimeError("无法获取 access token")

    # ---------------- qwen 侧（stdio JSON-RPC） ----------------
    def _next_id(self):
        self._rid += 1
        return self._rid

    async def _qwen_write(self, obj):
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _qwen_request(self, method, params, timeout=30):
        rid = self._next_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._qwen_write({"jsonrpc": "2.0", "id": rid,
                                "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def _read_stdout(self):
        assert self.proc and self.proc.stdout
        while True:
            raw = await self.proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                logger.warning("[qwen stdout raw] %s", line[:200])
                continue
            await self._handle_qwen_obj(obj)

    async def _read_stderr(self):
        assert self.proc and self.proc.stderr
        while True:
            raw = await self.proc.stderr.readline()
            if not raw:
                break
            logger.warning("[qwen stderr] %s",
                           raw.decode("utf-8", "replace").strip()[:300])

    async def _handle_qwen_obj(self, obj):
        if "method" in obj and "id" in obj:
            await self._on_qwen_request(obj)
            return
        if "method" in obj:
            await self._on_qwen_update(obj.get("params", {}))
            return
        if "id" in obj:
            fut = self._pending.pop(obj["id"], None)
            if fut and not fut.done():
                fut.set_result(obj)

    async def _on_qwen_request(self, obj):
        """响应 qwen 主动发来的请求（带 method+id）。

        此前这里只打日志就 return，导致 qwen 的 session/request_permission
        永远等不到回复 —— 它要执行工具前必须先获得 client 批准，收不到响应
        就只能卡住或放弃，表现为「LLM 明明有工具却不会用」。
        """
        method = obj.get("method")
        rid = obj.get("id")
        params = obj.get("params") or {}

        if method == "session/request_permission":
            result = self._decide_permission(params)
            logger.info("[qwen req] request_permission -> %s（mode=%s）",
                        json.dumps(result, ensure_ascii=False)[:120],
                        self.permission_mode)
            await self._qwen_write({"jsonrpc": "2.0", "id": rid, "result": result})
            return

        # 其余请求（fs/terminal 等）当前未实现：明确回错，避免 qwen 一直等待
        logger.info("[qwen req] %s（未实现，已回 method not found）", method)
        await self._qwen_write({
            "jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })

    def _decide_permission(self, params):
        """按 --permission-mode 决定批准还是拒绝。"""
        if self.permission_mode == "none":
            return {"outcome": {"outcome": "cancelled"}}

        tool = params.get("toolCall") or {}
        raw = tool.get("rawInput") or {}
        command = str(raw.get("command") or raw.get("cmd") or "")
        title = tool.get("title") or ""

        if self.permission_mode == "allowlist":
            allowed = [p for p in self.permission_allowlist if p]
            if not allowed:
                logger.warning("permission-mode=allowlist 但未配置白名单，拒绝执行")
                return {"outcome": {"outcome": "cancelled"}}
            if not any(p in command for p in allowed):
                logger.warning("命令不在白名单，拒绝: %s", command[:120])
                return {"outcome": {"outcome": "cancelled"}}

        # 在给定选项里挑一个「允许」类选项
        options = params.get("options") or []
        allow = None
        for opt in options:
            kind = str(opt.get("kind") or "").lower()
            name = str(opt.get("name") or "").lower()
            if kind.startswith("allow") or "allow" in name:
                allow = opt
                break
        if allow is None:
            logger.warning("无可用允许选项（title=%s command=%s）",
                           title, command[:80])
            return {"outcome": {"outcome": "cancelled"}}

        logger.info("批准工具执行: %s | %s", title, command[:150])
        return {"outcome": {"outcome": "selected",
                            "optionId": allow.get("optionId")}}

    async def _on_qwen_update(self, params):
        """累积助手正式回复文本（兼容 dict / list / str 三种 content 形态）。"""
        upd = (params or {}).get("update", params or {})
        su = upd.get("sessionUpdate")
        candidates = []

        def _take(node):
            if isinstance(node, str):
                candidates.append(node)
            elif isinstance(node, dict):
                if node.get("text"):
                    candidates.append(node["text"])
                elif node.get("content"):
                    _take(node["content"])
            elif isinstance(node, list):
                for it in node:
                    _take(it)

        if su in _MESSAGE_UPDATE_TYPES or su is None:
            for key in ("content", "text", "delta", "message"):
                if key in upd:
                    _take(upd[key])
        if "response" in upd:
            _take(upd.get("response"))
        if not candidates:
            for k in ("text", "delta", "message"):
                v = upd.get(k)
                if isinstance(v, str) and v:
                    candidates.append(v)

        text = "".join(candidates)
        if text:
            async with self._accum_lock:
                self._accum += text

    async def _qwen_initialize(self):
        resp = await self._qwen_request("initialize", {
            "protocolVersion": 1,
            "capabilities": {"prompts": {}, "tools": {}, "resources": {}},
            "clientInfo": {"name": "agentchat-remote-bridge", "version": "1.0.0"},
        }, timeout=30)
        logger.info("[qwen initialize] %s", json.dumps(resp, ensure_ascii=False)[:200])

    async def _qwen_session_new(self):
        sid = self.resume_session_id or str(uuid.uuid4())
        params = {"cwd": os.getcwd(), "mcpServers": [],
                  "_meta": {"qwen-code/sessionId": sid}}
        resp = await self._qwen_request("session/new", params, timeout=60)
        if "error" in resp:
            err = resp.get("error") or {}
            if err.get("data", {}).get("errorKind") == "session_id_conflict":
                logger.warning("请求的 qwen session %s 仍活跃，回退新建", sid)
                sid = str(uuid.uuid4())
                params["_meta"] = {"qwen-code/sessionId": sid}
                resp = await self._qwen_request("session/new", params, timeout=60)
                if "error" in resp:
                    raise RuntimeError(f"qwen session/new 失败: {resp['error']}")
            else:
                raise RuntimeError(f"qwen session/new 失败: {resp['error']}")
        self.qwen_session_id = (resp.get("result") or {}).get("sessionId")
        logger.info("[qwen session/new] sessionId=%s", self.qwen_session_id)

    async def _qwen_session_resume(self, session_id):
        resp = await self._qwen_request(
            "session/resume",
            {"sessionId": session_id, "cwd": os.getcwd()}, timeout=60)
        if "error" in resp:
            return False, resp["error"]
        self.qwen_session_id = (resp.get("result") or {}).get("sessionId") or session_id
        logger.info("[qwen session/resume] sessionId=%s", self.qwen_session_id)
        return True, None

    async def _qwen_ensure_session(self):
        if self.resume_session_id:
            ok, err = await self._qwen_session_resume(self.resume_session_id)
            if ok:
                return
            logger.warning("session/resume(%s) 失败(%s)，回退新建",
                           self.resume_session_id,
                           (err or {}).get("message", err))
        await self._qwen_session_new()

    async def prompt_qwen(self, content):
        async with self._accum_lock:
            self._accum = ""
        resp = await self._qwen_request("session/prompt", {
            "sessionId": self.qwen_session_id,
            "cwd": os.getcwd(),
            "prompt": [{"type": "text", "text": content}],
        }, timeout=self.prompt_timeout)
        async with self._accum_lock:
            accum = self._accum
        if accum:
            return accum
        if "error" in resp:
            return f"[qwen 错误] {resp['error'].get('message', resp['error'])}"
        result = resp.get("result")
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get("response") or result.get("content") \
                or json.dumps(result, ensure_ascii=False)
        return "[qwen 无内容返回（请检查 API Key 是否已配置）]"

    # ---------------- AgentChat 侧 ----------------
    async def push(self, content, wait_ack=True, timeout=20.0):
        """主动向频道推送一条消息（无需被 @提及）。

        对应服务端 app/acp/endpoints.py 的 handle_output：落库为
        message_type=AGENT_RESPONSE 并广播到频道。

        返回落库后的 message_id（wait_ack=False 或超时则返回 None）。
        """
        if not content:
            raise ValueError("推送内容不能为空")
        if self.ws is None:
            raise RuntimeError("尚未连接到 AgentChat")
        if not self.acp_session_id:
            raise RuntimeError("尚未拿到 ACP session_id（可能刚断线重连中）")

        async with self._push_lock:
            fut = None
            if wait_ack:
                fut = asyncio.get_event_loop().create_future()
                self._ack_waiter = fut
            try:
                await self.ws.send(json.dumps({
                    "type": "output",
                    "data": {"session_id": self.acp_session_id, "output": content},
                }))
                logger.info("[push] 已推送到频道 %s（%d 字）",
                            self.channel_id, len(content))
                if fut is None:
                    return None
                ack = await asyncio.wait_for(fut, timeout)
                return (ack.get("data") or {}).get("message_id")
            finally:
                self._ack_waiter = None

    async def _on_input(self, data):
        """把 input 放入队列，由 _input_worker 串行处理。

        不能在接收循环里直接 await prompt_qwen：LLM 推理耗时数秒到数十秒，
        期间协程不返回，接收循环无法读取 WS 帧 —— 后到的输入会被静默丢弃。
        补发离线消息时多条几乎同时到达，必然踩中该问题。
        """
        inp = data.get("message", {})
        content = inp.get("content", "")
        sender = inp.get("sender_username") or inp.get("sender_id")
        size = self._input_queue.qsize()
        if size >= self.max_input_queue:
            logger.warning("[input] 队列已满(%d)，丢弃来自 %s 的消息: %s",
                           size, sender, content[:60])
            return
        await self._input_queue.put((sender, content))
        logger.info("[input] 入队 from=%s（队列 %d）: %s",
                    sender, self._input_queue.qsize(), content[:80])

    async def _input_worker(self):
        """单协程串行消费输入队列：保证同一会话的 LLM 上下文顺序正确。"""
        while not self._stopping:
            try:
                sender, content = await self._input_queue.get()
            except asyncio.CancelledError:
                raise

            # 等 acp_session_id 就绪。服务端在 handle_connect 里同步补发离线
            # 消息，而该字段要等 _loop 读到 connect 回执才有；不等待就会用
            # None 去 push，导致首条消息回复失败。
            for _ in range(300):      # 最多等 30 秒
                if self.acp_session_id or self._stopping:
                    break
                await asyncio.sleep(0.1)
            if not self.acp_session_id:
                logger.error("[input] 会话未就绪，放弃处理: %s", content[:60])
                self._input_queue.task_done()
                continue

            try:
                reply = await self.prompt_qwen(content)
            except Exception as e:
                reply = f"[bridge 异常] {e}"
            try:
                await self.push(reply)
            except RuntimeError as e:
                logger.error("回复发送失败: %s", e)
            finally:
                self._input_queue.task_done()

    async def _loop(self):
        while not self._stopping:
            try:
                async for raw in self.ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    t = msg.get("type")
                    if t == "connect":
                        self.acp_session_id = (msg.get("data") or {}).get("session_id")
                        logger.info("[agentchat] connect session=%s", self.acp_session_id)
                    elif t == "input":
                        await self._on_input(msg.get("data", {}))
                    elif t == "output_ack":
                        waiter = self._ack_waiter
                        if waiter is not None and not waiter.done():
                            waiter.set_result(msg)
                    elif t == "ping":
                        await self.ws.send(json.dumps({
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }))
                    elif t == "error":
                        logger.warning("[agentchat error] %s",
                                       json.dumps(msg, ensure_ascii=False)[:200])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[loop] 连接中断: %s，准备重连", e)

            if self._stopping:
                return
            if not await self._reconnect():
                logger.error("无法恢复与 AgentChat 的连接，退出")
                return

    async def _reconnect(self, max_attempts=10):
        for attempt in range(1, max_attempts + 1):
            delay = min(30, 2 ** (attempt - 1))
            logger.info("第 %d 次重连，%ds 后重试…", attempt, delay)
            await asyncio.sleep(delay)
            if self._stopping:
                return False
            try:
                self.ws = await websockets.connect(self.ws_url)
                await self.ws.send(json.dumps(
                    {"type": "connect", "data": {"channel_id": self.channel_id}}))
                logger.info("[bridge] 已重连 AgentChat(channel=%s)", self.channel_id)
                return True
            except Exception as e:
                if "4001" in str(e) or "unauthorized" in str(e).lower():
                    logger.warning("连接被拒（token 可能过期），重新登录")
                    if not await self.refresh_token():
                        return False
                else:
                    logger.warning("重连失败: %s", e)
        return False

    # ---------------- 生命周期 ----------------
    async def run(self):
        env = os.environ.copy()
        if self.api_key:
            env["OPENAI_API_KEY"] = self.api_key
        if self.model:
            env["QWEN_MODEL"] = self.model

        # 先启动本地推送服务。必须早于 MCP 配置生成与环境变量注入：
        # 该服务在端口被占用时会顺延到新端口，若先注入就会把错误的
        # 端口写进 MCP 配置和 AGENTCHAT_PUSH_URL，导致推送全部失败。
        if self.push_port:
            self._push_server = start_push_server(self)
            if not self._push_server:
                self.push_port = 0      # 启动失败，后续不再注入相关配置

        # 让 qwen session 内部能直接推送到频道
        if self.push_port:
            env["AGENTCHAT_PUSH_URL"] = f"http://127.0.0.1:{self.push_port}/push"

        # 注入 AgentChat MCP 工具：让 agent 能直接调用 agentchat_send，
        # 不必借助 shell，也就无需放开 --permission-mode
        cli_cmd = list(self.cli_cmd)
        if self.mcp_enabled and self.push_port:
            self.mcp_config_path = build_mcp_config(self.push_port)
            if self.mcp_config_path:
                # -y：自动信任本次注入的 MCP server。
                # qwen 对新出现的 MCP server 会先置为 pending（需 `qwen mcp approve`），
                # 未批准时工具不会激活，agent 只能把它当普通程序调用（走 shell、要审批）。
                # 该配置由桥接器生成且指向本机固定脚本，故直接信任。
                cli_cmd += ["--mcp-config", self.mcp_config_path, "-y"]
                logger.info("已注入 MCP 配置: %s（已自动信任）",
                            self.mcp_config_path)

        logger.info("启动 CLI 子进程: %s", " ".join(cli_cmd))
        self.proc = await asyncio.create_subprocess_exec(
            *cli_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,
            env=env,
            **_subprocess_kwargs(),
        )
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

        await self._qwen_initialize()
        await self._qwen_ensure_session()

        await self.ensure_token()
        self.ws = await websockets.connect(self.ws_url)
        await self.ws.send(json.dumps(
            {"type": "connect", "data": {"channel_id": self.channel_id}}))

        # 先收 connect 回执拿到 ACP session_id —— 主动推送依赖它。
        # 注意服务端会先发一帧 status(waiting_for_connect)，真正的回执在其后，
        # 所以要跳过这些前导帧，否则中间窗口期内推送会因为没有 session_id 而失败。
        self.acp_session_id = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                frame = json.loads(await asyncio.wait_for(
                    self.ws.recv(), timeout=max(1.0, deadline - time.monotonic())))
            except Exception as e:
                logger.warning("等待 connect 回执超时（推送能力暂不可用）: %s", e)
                break
            ftype = frame.get("type")
            if ftype == "connect":
                if frame.get("status") == "success":
                    self.acp_session_id = (frame.get("data") or {}).get("session_id")
                else:
                    logger.warning("connect 被拒绝: %s",
                                   json.dumps(frame, ensure_ascii=False)[:200])
                break
            if ftype == "ping":
                await self.ws.send(json.dumps({
                    "type": "pong",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                continue
            # status / 未知帧：继续等待真正的回执
            logger.debug("跳过前导帧 type=%s", ftype)
        if not self.acp_session_id:
            logger.warning("未取得 ACP session_id，推送能力暂不可用")

        logger.info("已连接 AgentChat(%s, channel=%s) 与 qwen(session=%s)",
                    self.base_url, self.channel_id, self.qwen_session_id)

        # 注意启动顺序：必须先让 _loop 读到 connect 回执（拿到 acp_session_id），
        # 才能开始消费输入队列。服务端的补发是在 handle_connect 里**同步**完成的
        # —— 若先启动 worker，第一条 input 可能在 acp_session_id 就绪前被处理，
        # 回复时会抛"尚未拿到 ACP session_id"，表现为第一条消息无回复。
        loop_task = asyncio.create_task(self._loop())
        await asyncio.sleep(0)
        worker = asyncio.create_task(self._input_worker())
        try:
            await loop_task
        finally:
            worker.cancel()
            try:
                await worker
            except (asyncio.CancelledError, Exception):
                pass

    async def stop(self):
        # 先给队列里已收到的输入一点处理时间，避免退出时把用户的提问丢掉
        pending = self._input_queue.qsize()
        if pending:
            logger.info("退出前等待 %d 条待处理输入完成…", pending)
            try:
                await asyncio.wait_for(self._input_queue.join(), timeout=60)
            except Exception:
                logger.warning("仍有 %d 条输入未处理完，已放弃等待",
                               self._input_queue.qsize())

        self._stopping = True
        if self._push_server:
            try:
                self._push_server.shutdown()
                self._push_server.server_close()
            except Exception:
                pass
            self._push_server = None
        for closer in (
            lambda: self.ws.close() if self.ws else asyncio.sleep(0),
        ):
            try:
                await closer()
            except Exception:
                pass
        proc = self.proc
        if proc and proc.returncode is None:
            # Windows 上先按进程树杀（CLI 常被 cmd.exe 包装，杀外层会留孤儿）
            if os.name == "nt" and proc.pid and _kill_windows_tree(proc.pid):
                return
            try:
                proc.terminate()
            except Exception:
                pass


_TOML_FALLBACK_WARNED = False


def _strip_inline_comment(value):
    """去掉 TOML 行的行尾注释，但不破坏引号内的 #。"""
    quote = None
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == "#":
            return value[:i].strip()
    return value.strip()


def _mini_toml_value(value):
    """解析标量：字符串 / 布尔 / 整数 / 原样字符串。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        return value


def _mini_toml_load(text):
    """极简 TOML 解析，仅支持 [section] 与 key = value。

    用途：Python < 3.11 且未装 tomli 时的兜底。能正确解析安装脚本生成的
    bridge.toml。数组、多行字符串、内联表等高级语法请勿依赖本兜底实现。
    """
    result = {}
    section = result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.rstrip().endswith("]"):
            name = line[1:line.rindex("]")].strip().strip('"\'')
            # 文档约定配置写在 [bridge] 段下；顶层段落也一律收纳
            target = result.setdefault(name, {}) if name else result
            section = target
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip('"\'')
        if not key:
            continue
        section[key] = _mini_toml_value(_strip_inline_comment(value))
    return result


def _load_toml(text):
    """读取 TOML 文本：优先 tomllib(3.11+) → tomli → 简易兜底解析器。"""
    global _TOML_FALLBACK_WARNED
    loader = None
    try:
        import tomllib as loader            # Python 3.11+
    except ImportError:
        try:
            import tomli as loader          # 第三方 backport
        except ImportError:
            loader = None
    if loader is not None:
        return loader.loads(text)
    if not _TOML_FALLBACK_WARNED:
        _TOML_FALLBACK_WARNED = True
        logger.warning(
            "当前 Python 无 tomllib 也未安装 tomli，已使用内置简化解析器读取配置；"
            "仅支持 key = value 写法。建议升级到 Python 3.11+ 或执行 pip install tomli")
    return _mini_toml_load(text)


def config_search_paths(path=None):
    """配置文件的候选路径（按优先级，跨平台）。

    覆盖三处来源：安装脚本的默认落点、脚本同目录、当前工作目录；
    Windows 额外支持 %APPDATA%\\AgentChat\\bridge.toml。
    """
    candidates = []

    def try_add(fn):
        """每步都兜底：找配置文件这件事本身绝不该让进程崩掉。

        pathlib 在不同平台/打包方式下可能抛 NotImplementedError、
        RuntimeError(无 HOME) 等，这里一律视为「该候选不可用」。
        """
        try:
            p = fn()
            if p is None:
                return
            p = Path(p).expanduser()
        except Exception:
            return
        if p not in candidates:
            candidates.append(p)

    if path:
        try_add(lambda: str(path))
        return candidates

    # 1) 脚本自身所在目录（分发包解压目录 / 安装目录）
    try_add(lambda: Path(__file__).resolve().parent / "bridge.toml")
    # 2) 当前工作目录
    try_add(lambda: Path.cwd() / "bridge.toml")
    # 3) 用户主目录（安装脚本默认位置优先，兼容旧位置）
    try_add(lambda: Path.home() / ".agentchat" / "bridge" / "bridge.toml")
    try_add(lambda: Path.home() / ".agentchat" / "bridge.toml")
    # 4) Windows：%APPDATA%
    if os.name == "nt":
        for var in ("APPDATA", "LOCALAPPDATA"):
            val = os.environ.get(var)
            if val:
                try_add(lambda v=val: Path(v) / "AgentChat" / "bridge.toml")
    return candidates


def load_config_file(path=None):
    """加载配置文件的 bridge 段。返回 (配置字典, 实际路径或 None)。

    查找顺序：--config 指定 > 脚本同目录 > 当前目录 > ~/.agentchat/bridge/
    > ~/.agentchat/ > %APPDATA%/AgentChat/（Windows）
    """
    for cand in config_search_paths(path):
        if not cand.is_file():
            continue
        try:
            # utf-8-sig：剥掉 Windows 记事本/PowerShell 写入的 BOM
            data = _load_toml(cand.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception as e:
            logger.warning("配置文件 %s 解析失败（%s），尝试下一个候选", cand, e)
            continue
        cfg = data.get("bridge", data) if isinstance(data, dict) else {}
        if not isinstance(cfg, dict):
            cfg = {}
        logger.info("已加载配置文件 %s", cand)
        return cfg, str(cand)
    if path:
        logger.warning("指定的配置文件不存在: %s", path)
    return {}, None


def resolve_cli_cmd(cli_cmd=None):
    """把 CLI 命令整理成可直接交给 create_subprocess_exec 的形式。

    Windows 上由 npm 安装的 CLI 通常只提供 qwen.cmd / qwen.ps1：
      - CreateProcess 无法直接执行 .cmd/.bat，必须经 `cmd.exe /d /c` 包装，
        否则报 WinError 193（%1 不是有效的 Win32 应用程序）
      - .ps1 根本不是可执行文件，必须由 powershell.exe -File 启动
    不做这层适配，Windows 用户会在启动子进程时直接失败。
    """
    cmd = list(cli_cmd) if cli_cmd else list(DEFAULT_CLI_CMD)
    if not cmd:
        return list(DEFAULT_CLI_CMD)

    exe = cmd[0]

    # POSIX：仅补全 PATH 查找结果，行为与原先一致
    if os.name != "nt":
        return [shutil.which(exe) or exe] + cmd[1:]

    # 用户已给出路径/带扩展名时按其意图处理
    if os.path.isabs(exe) or os.sep in exe or "/" in exe or "\\" in exe:
        ext = os.path.splitext(exe)[1].lower()
        if ext in (".cmd", ".bat"):
            return ["cmd.exe", "/d", "/c"] + cmd
        if ext == ".ps1":
            return ["powershell.exe", "-NoProfile", "-ExecutionPolicy",
                    "Bypass", "-File"] + cmd
        return cmd

    ext = os.path.splitext(exe)[1].lower()
    if ext in (".cmd", ".bat"):
        return ["cmd.exe", "/d", "/c"] + cmd
    if ext == ".ps1":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy",
                "Bypass", "-File"] + cmd

    # 裸命令名：靠 PATH+PATHEXT 定位后再决定是否需要包装
    resolved = shutil.which(exe)
    if resolved is None:
        return cmd
    rext = os.path.splitext(resolved)[1].lower()
    if rext in (".cmd", ".bat"):
        return ["cmd.exe", "/d", "/c", resolved] + cmd[1:]
    return [resolved] + cmd[1:]


def _subprocess_kwargs():
    """创建子进程的平台差异参数。

    Windows 上不加 CREATE_NO_WINDOW 的话，启动 qwen 子进程会弹出控制台
    窗口（后台常驻时尤其干扰），这里统一隐藏。
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _kill_windows_tree(pid):
    """Windows 上连进程树一起杀。

    CLI 经由 cmd.exe 包装时 terminate() 只会杀掉 cmd.exe，真正的 qwen
    会残留成孤儿进程，反复重启后机器上堆满僵尸 CLI。
    """
    try:
        return subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode == 0
    except Exception:
        return False


def preflight(base_url, cli_cmd=None, resolved_cli=None):
    """启动前自检，给出可操作的错误提示而不是底层 traceback。"""
    problems = []

    # 用「用户填写的原始命令名」做存在性检查；resolved 已可能被 cmd.exe 包装，
    # 拿它去 which 只会永远命中 cmd.exe 而漏掉真正的 CLI。
    raw = list(cli_cmd) if cli_cmd else list(DEFAULT_CLI_CMD)
    exe = raw[0] if raw else DEFAULT_CLI_CMD[0]
    if shutil.which(exe) is None and not os.path.isfile(exe):
        extra = ""
        if os.name == "nt":
            extra = ("Windows 下 CLI 常装在 npm 全局目录，可执行 `npm root -g` "
                     "查看位置后用 --cli-cmd 指定完整路径（须为 .exe/.cmd/.bat，"
                     "PowerShell 脚本无法作为可执行文件启动）。")
        problems.append(
            f"未找到 CLI 可执行文件 `{exe}`。请先安装 qwen CLI 并确保在 PATH 中"
            f"（当前 PATH 中未匹配）。可用 --cli-cmd 指定完整路径。{extra}")

    for mod in ("httpx", "websockets"):
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"缺少 Python 依赖 `{mod}`，请执行：pip install {mod}")

    if not base_url.startswith(("http://", "https://")):
        problems.append(f"--base-url 必须以 http:// 或 https:// 开头，当前：{base_url}")

    if not isinstance(base_url, str) or base_url.strip() != base_url:
        problems.append(f"--base-url 首尾不能包含空白字符，当前：{base_url!r}")

    if problems:
        logger.error("启动前检查未通过：")
        for i, msg in enumerate(problems, 1):
            logger.error("  %d) %s", i, msg)
        return False
    if resolved_cli:
        logger.debug("CLI 解析结果: %s", " ".join(resolved_cli))
    return True


def main():
    p = argparse.ArgumentParser(
        description="AgentChat 本地桥接器 —— 把本机 CLI agent 接入 AgentChat",
        epilog="示例：python remote_bridge.py --config bridge.toml")
    p.add_argument("--config", default=None,
                   help="配置文件路径（默认自动查找 ./bridge.toml）")
    p.add_argument("--base-url", default=None,
                   help="AgentChat 服务端地址，如 https://agentchat.example.com")
    p.add_argument("--channel-id", type=int, default=None)
    p.add_argument("--username", default=None, help="Agent 账号用户名")
    p.add_argument("--password", default=None,
                   help="Agent 账号密码（推荐用环境变量 AGENT_PASSWORD 或配置文件）")
    p.add_argument("--thread-id", default=None,
                   help="会话标识；用于跨重启标识同一会话。默认按 username 生成")
    p.add_argument("--resume-session", default=None)
    p.add_argument("--cli-cmd", default=None,
                   help="覆盖 CLI 命令，逗号分隔")
    p.add_argument("--api-key", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--push-port", type=int, default=None,
                   help="本地推送服务端口；agent session 内可 POST 到此端口向频道"
                        "发送消息。0 表示禁用（默认 8765）")
    p.add_argument("--permission-mode", default=None,
                   choices=["none", "auto", "allowlist"],
                   help="工具执行审批策略：none=一律拒绝（默认，agent 不会执行"
                        "命令）；auto=批准；allowlist=仅批准命中 --permission-"
                        "allowlist 的命令。开启即允许 agent 在本机执行命令，"
                        "请评估风险后启用")
    p.add_argument("--permission-allowlist", default=None,
                   help="配合 --permission-mode allowlist 的命令白名单，逗号分隔")
    p.add_argument("--mcp", dest="mcp", action="store_true", default=True,
                   help="向 CLI 注入 AgentChat MCP 工具（默认开启）："
                        "agent 可直接调用 agentchat_send 推送消息，无需 shell")
    p.add_argument("--no-mcp", dest="mcp", action="store_false",
                   help="不注入 MCP 工具")
    p.add_argument("--check", action="store_true",
                   help="只做环境自检（检查 CLI/依赖/连通性）后退出")
    args = p.parse_args()

    # 优先级：命令行 > 环境变量 > 配置文件
    cfg, cfg_path = load_config_file(args.config)

    def pick(name, env=None, default=None):
        v = getattr(args, name, None)
        if v is None and env:
            v = os.environ.get(env)
        if v is None and isinstance(cfg, dict):
            # TOML 段内推荐连字符写法（base-url），同时兼容下划线写法
            v = cfg.get(name.replace("_", "-"))
            if v is None:
                v = cfg.get(name)
        if v is None:
            return default
        # 去首尾空白：Windows 上用 setx 或手工编辑配置容易混入 \r\n、空格，
        # 会让 urllib/websocket 报难以定位的错误，在这里统一兜住
        return v.strip() if isinstance(v, str) else v

    base_url = pick("base_url", "AGENTCHAT_BASE_URL")
    channel_id = pick("channel_id", "AGENTCHAT_CHANNEL_ID")
    username = pick("username", "AGENTCHAT_USERNAME")
    password = pick("password", "AGENT_PASSWORD")
    thread_id = pick("thread_id")
    api_key = pick("api_key", "OPENAI_API_KEY")
    model = pick("model")
    cli_cmd_raw = pick("cli_cmd")
    resume = pick("resume_session")
    push_port_raw = pick("push_port", "AGENTCHAT_PUSH_PORT")
    perm_mode = (pick("permission_mode", "AGENTCHAT_PERMISSION_MODE") or "none").lower()
    if perm_mode not in ("none", "auto", "allowlist"):
        p.error(f"--permission-mode 只能是 none/auto/allowlist，当前为 {perm_mode!r}")
    perm_raw = pick("permission_allowlist", "AGENTCHAT_PERMISSION_ALLOWLIST")
    perm_list = [x.strip() for x in str(perm_raw).split(",") if x.strip()] if perm_raw else []

    # 本地推送通道：默认开启 8765；显式设 0 表示禁用
    try:
        push_port = 8765 if push_port_raw in (None, "") \
            else int(str(push_port_raw).strip())
    except ValueError:
        p.error(f"--push-port 必须是整数，当前为 {push_port_raw!r}")

    missing = [n for n, v in (("--base-url", base_url), ("--channel-id", channel_id),
                              ("--username", username), ("--password", password))
               if v in (None, "")]
    if missing:
        p.error("缺少必填项：%s\n可从命令行、环境变量（AGENTCHAT_BASE_URL / "
                "AGENTCHAT_CHANNEL_ID / AGENTCHAT_USERNAME / AGENT_PASSWORD）"
                "或 bridge.toml 提供。" % ", ".join(missing))

    if cfg_path is None:
        logger.info("未找到配置文件，仅使用命令行参数与环境变量")

    try:
        channel_id = int(str(channel_id).strip())
    except (TypeError, ValueError):
        p.error(f"--channel-id 必须是整数，当前为 {channel_id!r}"
                f"（配置文件中的值不需要加引号）")

    raw_cli = cli_cmd_raw.split(",") if isinstance(cli_cmd_raw, str) else None
    cli_cmd = resolve_cli_cmd(raw_cli)
    # 未指定 thread_id 时按 agent 名生成，保证同一 agent 重启后仍归到同一线程
    thread_id = thread_id or f"agent-{username}-ch{channel_id}"

    if not preflight(base_url, raw_cli, cli_cmd):
        return 2

    if args.check:
        logger.info("环境自检通过：CLI 可用、依赖完整、base-url 合法")
        logger.info("将连接: %s  channel=%s  agent=%s", base_url, channel_id, username)
        logger.info("CLI 命令: %s", " ".join(cli_cmd or DEFAULT_CLI_CMD))
        return 0

    logger.info("接入配置: %s | channel=%s | agent=%s | thread=%s",
                base_url, channel_id, username, thread_id)

    bridge = RemoteBridge(
        base_url=base_url,
        channel_id=channel_id,
        username=username,
        password=password,
        cli_cmd=cli_cmd,
        api_key=api_key,
        model=model,
        thread_id=thread_id,
        resume_session_id=resume,
        push_port=push_port,
        permission_mode=perm_mode,
        permission_allowlist=perm_list,
        mcp_enabled=args.mcp,
    )

    async def _run():
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(
                    sig, lambda: asyncio.create_task(bridge.stop()))
            except NotImplementedError:
                pass
        await bridge.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    logger.info("桥接已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

# ---------------------------------------------------------------------------
# systemd 常驻示例（放到远端机器的 /etc/systemd/system/agentchat-bridge.service）
#
# [Unit]
# Description=AgentChat Remote Bridge
# After=network-online.target
# Wants=network-online.target
#
# [Service]
# Type=simple
# User=YOUR_USER
# WorkingDirectory=/opt/agentchat-bridge
# Environment=AGENT_PASSWORD=your-agent-password
# Environment=OPENAI_API_KEY=sk-xxxx
# ExecStart=/usr/bin/python3 /opt/agentchat-bridge/remote_bridge.py \
#     --base-url https://agentchat.example.com \
#     --channel-id 4 --username code-reviewer --thread-id my-thread-1
# Restart=always
# RestartSec=10
#
# [Install]
# WantedBy=multi-user.target
# ---------------------------------------------------------------------------
