"""
ACP CLI Bridge（桥接器核心实现）

将 AgentChat（ACP Socket Mode WebSocket）与真实 CLI（如 qwen --acp，stdio JSON-RPC）桥接：
  - 作为 JSON-RPC client 连接 qwen 子进程的 stdio
  - 作为 ACP Socket Mode client 连接 AgentChat 的 /api/acp/ws/socket
  - 双向翻译：
        AgentChat 的 input 帧  -->  qwen session/prompt
        qwen 的 session/update  -->  AgentChat output 帧（写入频道）

qwen --acp 协议（实测）：
  bridge -> initialize{protocolVersion,capabilities,clientInfo}
  qwen   -> initialize result{protocolVersion,agentInfo,agentCapabilities,authMethods}
  bridge -> session/new{cwd,mcpServers:[],_meta:{"qwen-code/sessionId":<id>}}
  qwen   -> session/new result{sessionId,models,...} + session/update 通知
            （若 <id> 已落盘则 attach/resume，否则新建；同进程内仍活跃则报 session_id_conflict）
  bridge -> session/prompt{sessionId,cwd,prompt:[{type:"text",text:"..."}]}
  qwen   -> session/update 通知（流式内容） + session/prompt 结果

resume：同一 thread_id 经 DB 复用 qwen session id；或显式传入 resume_session_id，
均通过 session/new 的 _meta 指定稳定 id 实现跨桥接重启恢复已有会话。
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import websockets
from jose import jwt

from ..config import settings

logger = logging.getLogger("ACPBridge")

# 只有这些 sessionUpdate 类型属于「助手正式回复」；
# agent_thought_chunk（思考过程）、工具调用、usage_update 等不该作为回复发给用户
_MESSAGE_UPDATE_TYPES = {"agent_message_chunk", "agent_message",
                         "message_chunk", "message"}

# 持久化（thread -> session）：表不存在时降级为内存，不阻塞桥接
try:
    from .models import ACPBridgeSession, ACPBridgeMessage
    from ..database import SessionLocal
    _HAS_STORE = True
except Exception as e:  # pragma: no cover
    _HAS_STORE = False
    logger.warning("ACPMysqlStore 不可用（acp_sessions/acp_messages 表可能未创建）: %s", e)


def make_agent_token(user_id: int, expires_hours: int = 24) -> str:
    """为 Agent 用户生成 JWT（与 AgentChat 服务端 authenticate_socket_token 一致）"""
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expires_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


class ACPMysqlStore:
    """thread -> session 持久化（可选）"""

    def save_session(self, session_id, cli_type, thread_id=None, channel_id=None,
                      process_state="running", extra=None):
        """保存/更新会话行。

        优先按 thread_id upsert（同一 thread 始终映射到同一个 qwen session，
        从而实现跨桥接重启 resume）；无 thread_id 时按 session_id upsert。
        """
        if not _HAS_STORE:
            return
        try:
            db = SessionLocal()
            row = None
            if thread_id:
                row = db.query(ACPBridgeSession).filter(
                    ACPBridgeSession.thread_id == thread_id
                ).order_by(ACPBridgeSession.id.desc()).first()
            if row is None:
                row = db.query(ACPBridgeSession).filter(
                    ACPBridgeSession.session_id == session_id
                ).first()
            if row is None:
                row = ACPBridgeSession(session_id=session_id)
                db.add(row)
            row.session_id = session_id
            row.cli_type = cli_type
            row.thread_id = thread_id
            row.channel_id = channel_id
            row.process_state = process_state
            row.extra_data = json.dumps(extra or {})
            db.commit()
        except Exception as e:
            logger.warning("save_session 失败: %s", e)
        finally:
            db.close()

    def get_session_by_thread(self, thread_id):
        """返回某 thread 最近一次持久化的 qwen session id（用于 resume）。"""
        if not _HAS_STORE or not thread_id:
            return None
        try:
            db = SessionLocal()
            row = db.query(ACPBridgeSession).filter(
                ACPBridgeSession.thread_id == thread_id
            ).order_by(ACPBridgeSession.id.desc()).first()
            if not row:
                return None
            return (json.loads(row.extra_data or "{}")).get("qwen_session_id")
        except Exception as e:
            logger.warning("get_session_by_thread 失败: %s", e)
            return None
        finally:
            db.close()

    def mark_stopped(self, session_id):
        if not _HAS_STORE or not session_id:
            return
        try:
            db = SessionLocal()
            db.query(ACPBridgeSession).filter(
                ACPBridgeSession.session_id == session_id
            ).update({"process_state": "stopped"})
            db.commit()
        except Exception as e:
            logger.warning("mark_stopped 失败: %s", e)
        finally:
            db.close()

    def log_message(self, session_id, direction, content, extra=None):
        if not _HAS_STORE:
            return
        try:
            db = SessionLocal()
            db.add(ACPBridgeMessage(
                session_id=session_id, direction=direction,
                content=content, extra_data=json.dumps(extra or {}),
            ))
            db.commit()
        except Exception as e:
            logger.warning("log_message 失败: %s", e)
        finally:
            db.close()

    def list_sessions(self):
        if not _HAS_STORE:
            return []
        db = SessionLocal()
        try:
            return [s.to_dict() for s in db.query(ACPBridgeSession).all()]
        finally:
            db.close()

    def get_messages(self, session_id):
        if not _HAS_STORE:
            return []
        db = SessionLocal()
        try:
            return [m.to_dict() for m in db.query(ACPBridgeMessage).filter_by(session_id=session_id).all()]
        finally:
            db.close()


class QwenACPBridge:
    """单个 CLI（qwen）会话的桥接器"""

    def __init__(self, base_url, channel_id, agent_id, agent_username,
                 token=None, api_key=None, model=None, cli_cmd=None,
                 prompt_timeout=180, thread_id=None, resume_session_id=None):
        self.base_url = base_url.rstrip("/")
        self.channel_id = channel_id
        self.agent_id = agent_id
        self.agent_username = agent_username
        self.token = token or make_agent_token(agent_id)
        self.api_key = api_key
        self.model = model
        self.cli_cmd = cli_cmd or ["qwen", "--acp", "--channel", "ACP", "--output-format", "stream-json"]
        self.prompt_timeout = prompt_timeout

        # resume 支持：
        #  - thread_id: 同一 thread 稳定映射到同一个 qwen session（跨桥接重启自动 resume）
        #  - resume_session_id: 显式恢复某个已存在的 qwen session
        self.thread_id = thread_id
        self.resume_session_id = resume_session_id
        self.requested_qwen_session_id = None
        self.resumed = False

        self.ws_base = self.base_url.replace("http", "ws", 1)
        self.ws_url = f"{self.ws_base}/api/acp/ws/socket?token={self.token}"

        self.proc = None
        self.ws = None
        self.acp_session_id = None
        self.qwen_session_id = None
        self.bridge_session_id = str(uuid.uuid4())

        self._rid = 0
        self._pending = {}
        self._accum = ""
        self._accum_lock = asyncio.Lock()
        self._reader = None
        self.store = ACPMysqlStore()

    # ---------------- qwen 侧（stdio JSON-RPC） ----------------
    def _next_id(self):
        self._rid += 1
        return self._rid

    async def _qwen_write(self, obj):
        if not self.proc or self.proc.stdin is None:
            raise RuntimeError("qwen 进程未启动")
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _qwen_request(self, method, params, timeout=30):
        rid = self._next_id()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[rid] = fut
        await self._qwen_write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def _read_qwen_stdout(self):
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

    async def _read_qwen_stderr(self):
        assert self.proc and self.proc.stderr
        while True:
            raw = await self.proc.stderr.readline()
            if not raw:
                break
            logger.warning("[qwen stderr] %s", raw.decode("utf-8", "replace").strip()[:300])

    async def _handle_qwen_obj(self, obj):
        if "method" in obj and "id" in obj:
            # qwen 主动发来的请求（本桥暂不处理，仅记录）
            logger.info("[qwen req] %s", obj.get("method"))
            return
        if "method" in obj:
            # notification：session/update 等
            await self._on_qwen_update(obj.get("params", {}))
            return
        if "id" in obj:
            rid = obj["id"]
            fut = self._pending.pop(rid, None)
            if fut and not fut.done():
                fut.set_result(obj)

    async def _on_qwen_update(self, params):
        """累积 qwen 流式回复文本。

        qwen 的 update.content 既可能是 {"type":"text","text":...} 字典，
        也可能是 [{"type":"text","text":...}] 列表或纯字符串，三种都要支持
        （实测 qwen 0.23.3 走的是字典形式，旧实现只处理 str/list 导致漏抓、
        回复退化成原始 JSON-RPC 结果）。
        另外只累积助手正式回复，过滤思考过程与工具调用等噪声。
        """
        upd = (params or {}).get("update", params or {})
        su = upd.get("sessionUpdate")
        # 未携带 sessionUpdate 时保持旧行为（宽松累积）
        if su is not None and su not in _MESSAGE_UPDATE_TYPES:
            return

        candidates = []

        def _take(node):
            if isinstance(node, str):
                if node:
                    candidates.append(node)
            elif isinstance(node, dict):
                for k in ("text", "content"):
                    v = node.get(k)
                    if isinstance(v, str) and v:
                        candidates.append(v)
                        return
            elif isinstance(node, list):
                for part in node:
                    _take(part)

        _take(upd.get("content"))
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
            "clientInfo": {"name": "agentchat-bridge", "version": "1.0.0"},
        }, timeout=30)
        logger.info("[qwen initialize] %s", json.dumps(resp, ensure_ascii=False)[:200])

    def _resolve_requested_session_id(self):
        """决定本次要请求的 qwen session id（用于新建或 resume）。

        - 显式 resume_session_id 优先；
        - 否则若该 thread 之前已持久化过 qwen session，复用其 id（自动 resume）；
        - 否则随机生成一个新 id（全新会话）。
        """
        if self.resume_session_id:
            return self.resume_session_id
        if self.thread_id and self.store:
            existing = self.store.get_session_by_thread(self.thread_id)
            if existing:
                return existing
        return str(uuid.uuid4())

    async def _qwen_session_new(self):
        self.requested_qwen_session_id = self._resolve_requested_session_id()
        params = {"cwd": os.getcwd(), "mcpServers": []}
        # 通过 _meta 指定 sessionId（新建会话用；已存在的 id 会被 reserveCreate 拒绝，
        # 故「附加已有会话」统一走 session/resume，见 _qwen_session_resume）
        if self.requested_qwen_session_id:
            params["_meta"] = {"qwen-code/sessionId": self.requested_qwen_session_id}

        resp = await self._qwen_request("session/new", params, timeout=60)
        if "error" in resp:
            err = resp.get("error") or {}
            # 同一进程内该 id 仍活跃才会冲突；回退为全新会话以保证桥接可用
            if err.get("data", {}).get("errorKind") == "session_id_conflict":
                logger.warning("请求的 qwen session %s 仍活跃(冲突)，回退为新建会话",
                               self.requested_qwen_session_id)
                self.requested_qwen_session_id = str(uuid.uuid4())
                params["_meta"] = {"qwen-code/sessionId": self.requested_qwen_session_id}
                resp = await self._qwen_request("session/new", params, timeout=60)
                if "error" in resp:
                    raise RuntimeError(f"qwen session/new 失败: {resp['error']}")
            else:
                raise RuntimeError(f"qwen session/new 失败: {resp['error']}")

        self.qwen_session_id = (resp.get("result") or {}).get("sessionId")
        prior = self.store.get_session_by_thread(self.thread_id) if self.thread_id else None
        self.resumed = (bool(self.resume_session_id) or prior is not None) and \
            self.qwen_session_id == self.requested_qwen_session_id
        logger.info("[qwen session/new] sessionId=%s resumed=%s", self.qwen_session_id, self.resumed)

    async def _qwen_session_resume(self, session_id):
        """显式附加一个已存在的 qwen 会话。

        `session/resume` 是「授予所有权」的操作（用 reserveRestore），可恢复 active/archived
        的已有会话；而 `session/new` 的 reserveCreate 会拒绝已存在的 id。返回 (success, error)。
        """
        resp = await self._qwen_request(
            "session/resume",
            {"sessionId": session_id, "cwd": os.getcwd()},
            timeout=60,
        )
        if "error" in resp:
            return False, resp["error"]
        self.qwen_session_id = (resp.get("result") or {}).get("sessionId") or session_id
        self.resumed = True
        self.requested_qwen_session_id = self.qwen_session_id
        logger.info("[qwen session/resume] sessionId=%s", self.qwen_session_id)
        return True, None

    async def _qwen_ensure_session(self):
        """按 resume 意图建立 qwen 会话：

        - 显式 resume_session_id，或 thread 已持久化过 qwen session → 先尝试 `session/resume` 附加已有会话；
        - resume 失败（会话不存在）则回退为 `session/new` 新建。
        """
        target = self.resume_session_id
        if target is None and self.thread_id and self.store:
            target = self.store.get_session_by_thread(self.thread_id)
        if target:
            ok, err = await self._qwen_session_resume(target)
            if ok:
                return
            logger.warning("session/resume(%s) 失败(%s)，回退新建会话", target,
                           (err or {}).get("message", err))
        await self._qwen_session_new()

    async def prompt_qwen(self, content):
        """向 qwen 发送用户消息，返回聚合后的回复文本"""
        if not self.qwen_session_id:
            await self._qwen_ensure_session()
        async with self._accum_lock:
            self._accum = ""
        params = {
            "sessionId": self.qwen_session_id,
            "cwd": os.getcwd(),
            "prompt": [{"type": "text", "text": content}],
        }
        try:
            result = await self._qwen_request("session/prompt", params, timeout=self.prompt_timeout)
        except asyncio.TimeoutError:
            result = None
        async with self._accum_lock:
            accum = self._accum
        if accum:
            return accum
        if result and "error" in result:
            return f"[qwen 错误] {result['error'].get('message', result['error'])}"
        if result and "result" in result:
            r = result["result"]
            if isinstance(r, str):
                return r
            if isinstance(r, dict):
                return r.get("response") or r.get("content") or json.dumps(r, ensure_ascii=False)
        return "[qwen 无内容返回（请检查 API Key 是否已配置）]"

    # ---------------- AgentChat 侧（Socket Mode WebSocket） ----------------
    async def _agentchat_loop(self):
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
                    await self._on_agentchat_input(msg.get("data", {}))
                elif t == "ping":
                    await self.ws.send(json.dumps({
                        "type": "pong",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
                elif t == "error":
                    logger.warning("[agentchat error] %s", json.dumps(msg, ensure_ascii=False)[:200])
        except Exception as e:
            logger.error("[agentchat loop] %s", e)

    async def _on_agentchat_input(self, data):
        inp = data.get("message", {})
        content = inp.get("content", "")
        sender = inp.get("sender_username") or inp.get("sender_id")
        logger.info("[agentchat input] from=%s: %s", sender, content[:80])
        self.store.log_message(self.qwen_session_id, "input", content,
                               extra={"sender": sender})
        try:
            reply = await self.prompt_qwen(content)
        except Exception as e:
            reply = f"[bridge 异常] {e}"
        out = {
            "type": "output",
            "data": {"session_id": self.acp_session_id, "output": reply},
        }
        await self.ws.send(json.dumps(out))
        self.store.log_message(self.qwen_session_id, "output", reply)

    # ---------------- 生命周期 ----------------
    async def start(self):
        env = os.environ.copy()
        if self.api_key:
            env["OPENAI_API_KEY"] = self.api_key
        if self.model:
            env["QWEN_MODEL"] = self.model
        self.proc = await asyncio.create_subprocess_exec(
            *self.cli_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,
            env=env,
        )
        self._reader = asyncio.create_task(self._read_qwen_stdout())
        asyncio.create_task(self._read_qwen_stderr())

        await self._qwen_initialize()
        await self._qwen_ensure_session()
        self.store.save_session(
            self.qwen_session_id, "qwen",
            thread_id=self.thread_id, channel_id=self.channel_id,
            process_state="running",
            extra={
                "bridge_session_id": self.bridge_session_id,
                "resumed": self.resumed,
                "requested_session_id": self.requested_qwen_session_id,
            },
        )

        self.ws = await websockets.connect(self.ws_url)
        await self.ws.send(json.dumps({"type": "connect", "data": {"channel_id": self.channel_id}}))
        logger.info("[bridge] 已连接 AgentChat(channel=%s) 与 qwen(session=%s)",
                    self.channel_id, self.qwen_session_id)
        await self._agentchat_loop()

    async def stop(self):
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
        self.store.mark_stopped(self.qwen_session_id)
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


class ACPBridgeManager:
    """桥接器注册表（一个 channel/agent 一个桥）"""

    def __init__(self):
        self.bridges = {}

    def add(self, key, bridge: QwenACPBridge):
        self.bridges[key] = bridge

    def get(self, key):
        return self.bridges.get(key)

    def list_sessions(self):
        return [{"key": k, "channel": b.channel_id, "qwen_session": b.qwen_session_id}
                for k, b in self.bridges.items()]

    async def stop_all(self):
        for b in self.bridges.values():
            await b.stop()


# 全局单例（守护进程内使用）
bridge_manager = ACPBridgeManager()
