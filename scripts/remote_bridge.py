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
import sys
import uuid
from datetime import datetime, timezone

import httpx
import websockets

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


class RemoteBridge:
    """把一个 qwen --acp 子进程桥接到远端 AgentChat 的某个频道。"""

    def __init__(self, base_url, channel_id, username, password,
                 cli_cmd=None, api_key=None, model=None,
                 thread_id=None, resume_session_id=None, prompt_timeout=180):
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
        self._stopping = False

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
            logger.info("[qwen req] %s", obj.get("method"))
            return
        if "method" in obj:
            await self._on_qwen_update(obj.get("params", {}))
            return
        if "id" in obj:
            fut = self._pending.pop(obj["id"], None)
            if fut and not fut.done():
                fut.set_result(obj)

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
    async def _on_input(self, data):
        inp = data.get("message", {})
        content = inp.get("content", "")
        sender = inp.get("sender_username") or inp.get("sender_id")
        logger.info("[agentchat input] from=%s: %s", sender, content[:80])
        try:
            reply = await self.prompt_qwen(content)
        except Exception as e:
            reply = f"[bridge 异常] {e}"
        await self.ws.send(json.dumps({
            "type": "output",
            "data": {"session_id": self.acp_session_id, "output": reply},
        }))

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

        logger.info("启动 qwen 子进程: %s", " ".join(self.cli_cmd))
        self.proc = await asyncio.create_subprocess_exec(
            *self.cli_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,
            env=env,
        )
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

        await self._qwen_initialize()
        await self._qwen_ensure_session()

        await self.ensure_token()
        self.ws = await websockets.connect(self.ws_url)
        await self.ws.send(json.dumps(
            {"type": "connect", "data": {"channel_id": self.channel_id}}))
        logger.info("已连接 AgentChat(%s, channel=%s) 与 qwen(session=%s)",
                    self.base_url, self.channel_id, self.qwen_session_id)
        await self._loop()

    async def stop(self):
        self._stopping = True
        for closer in (
            lambda: self.ws.close() if self.ws else asyncio.sleep(0),
        ):
            try:
                await closer()
            except Exception:
                pass
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


def load_config_file(path=None):
    """加载配置文件的 bridge 段（可选）。

    查找顺序：--config 指定 > ./bridge.toml > ~/.agentchat/bridge.toml
    支持 TOML（Python 3.11+ 内置 tomllib）。
    """
    candidates = [path] if path else ["./bridge.toml",
                                     os.path.expanduser("~/.agentchat/bridge.toml")]
    for c in candidates:
        if c and os.path.isfile(c):
            try:
                import tomllib
            except ImportError:
                logger.warning("需要 Python 3.11+ 才能解析 TOML 配置，已忽略 %s", c)
                return {}
            with open(c, "rb") as f:
                data = tomllib.load(f)
            cfg = data.get("bridge", data)
            logger.info("已加载配置文件 %s", os.path.abspath(c))
            return cfg
    if path:
        logger.warning("指定的配置文件不存在: %s", path)
    return {}


def preflight(base_url, cli_cmd):
    """启动前自检，给出可操作的错误提示而不是底层 traceback。"""
    problems = []

    exe = cli_cmd[0] if cli_cmd else DEFAULT_CLI_CMD[0]
    if shutil.which(exe) is None:
        problems.append(
            f"未找到 CLI 可执行文件 `{exe}`。请先安装 qwen CLI 并确保在 PATH 中"
            f"（当前 PATH 中未匹配）。可用 --cli-cmd 指定完整路径。")

    for mod in ("httpx", "websockets"):
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"缺少 Python 依赖 `{mod}`，请执行：pip install {mod}")

    if not base_url.startswith(("http://", "https://")):
        problems.append(f"--base-url 必须以 http:// 或 https:// 开头，当前：{base_url}")

    if problems:
        logger.error("启动前检查未通过：")
        for i, msg in enumerate(problems, 1):
            logger.error("  %d) %s", i, msg)
        return False
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
    p.add_argument("--check", action="store_true",
                   help="只做环境自检（检查 CLI/依赖/连通性）后退出")
    args = p.parse_args()

    # 优先级：命令行 > 环境变量 > 配置文件
    cfg = load_config_file(args.config)

    def pick(name, env=None, default=None):
        v = getattr(args, name, None)
        if v is not None:
            return v
        if env:
            v = os.environ.get(env)
            if v:
                return v
        v = cfg.get(name.replace("_", "-")) if isinstance(cfg, dict) else None
        if v is None and isinstance(cfg, dict):
            v = cfg.get(name)
        return v if v is not None else default

    base_url = pick("base_url", "AGENTCHAT_BASE_URL")
    channel_id = pick("channel_id", "AGENTCHAT_CHANNEL_ID")
    username = pick("username", "AGENTCHAT_USERNAME")
    password = pick("password", "AGENT_PASSWORD")
    thread_id = pick("thread_id")
    api_key = pick("api_key", "OPENAI_API_KEY")
    model = pick("model")
    cli_cmd_raw = pick("cli_cmd")
    resume = pick("resume_session")

    missing = [n for n, v in (("--base-url", base_url), ("--channel-id", channel_id),
                              ("--username", username), ("--password", password))
               if v in (None, "")]
    if missing:
        p.error("缺少必填项：%s\n可从命令行、环境变量（AGENTCHAT_BASE_URL / "
                "AGENTCHAT_CHANNEL_ID / AGENTCHAT_USERNAME / AGENT_PASSWORD）"
                "或 bridge.toml 提供。" % ", ".join(missing))

    channel_id = int(channel_id)
    cli_cmd = cli_cmd_raw.split(",") if isinstance(cli_cmd_raw, str) else None
    # 未指定 thread_id 时按 agent 名生成，保证同一 agent 重启后仍归到同一线程
    thread_id = thread_id or f"agent-{username}-ch{channel_id}"

    if not preflight(base_url, cli_cmd):
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
