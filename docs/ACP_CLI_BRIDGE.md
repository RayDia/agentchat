# ACP CLI Bridge 集成方案

将真实 CLI（如 `qwen --acp`）通过 **ACP（Agent Client Protocol）** 接入 AgentChat 频道，
实现「用户在频道 @提及 Agent → CLI 生成回复 → 回复广播回频道」的双向消息闭环。

## 架构

```
  AgentChat 用户 ──(WebSocket /ws)──▶ Channels
                                        │  @提及 转发(input 帧)
                                        ▼
                            ACP Socket Mode 端点
                         /api/acp/ws/socket   ◀── AgentChat 侧 ACP server
                                        │  (WebSocket ACP 帧协议)
                                        ▼
                            ┌──────────────────────────┐
                            │   ACP CLI Bridge         │   ← Python 守护进程
                            │   (examples/daemon)      │
                            │   app/acp/cli_bridge.py  │
                            └──────────┬───────────────┘
                                       │  qwen --acp (stdio JSON-RPC 2.0)
                  ┌────────────────────┼────────────────────┐
                  ▼                    ▼                    ▼
            ┌──────────┐         ┌──────────┐         ┌──────────┐
            │  qwen    │         │   Pi     │         │ OpenCode  │  (未来)
            │ --acp    │         │ --mode   │         │          │
            └──────────┘         │  rpc     │         └──────────┘
                                 └──────────┘
                                       │
                                       ▼
                            MySQL 持久化（thread → session）
                            acp_sessions / acp_messages
```

- **AgentChat 侧**：`app/acp/endpoints.py` 提供 `/api/acp/ws/socket`（ACP Socket Mode）。
  协议帧：`connect` / `input` / `output` / `output_ack` / `status` / `ping` / `pong`。
- **Bridge 侧**：`app/acp/cli_bridge.py` 作为 ACP Socket Mode **client** 连接 AgentChat，
  同时作为 **JSON-RPC client** 派生并驱动 `qwen --acp` 子进程（stdio）。
- **存储**：`ACPMysqlStore` 把 bridge session 持久化到 MySQL（`acp_sessions` / `acp_messages`），
  实现 thread → session 映射。

> 早期版本中的 `app/acp/socket_bridge.py`（自定义 `/ws/bridge` 协议）已移除：
> 它与真实 `qwen --acp` 的 stdio JSON-RPC 不兼容，且功能与 Socket Mode 重复。

## 核心组件

### 1. `app/acp/cli_bridge.py` — 桥接器核心

```python
from app.acp.cli_bridge import QwenACPBridge, ACPBridgeManager, ACPMysqlStore, make_agent_token

bridge = QwenACPBridge(
    base_url="http://localhost:8000",
    channel_id=4,             # 用户在此频道 @提及 Agent
    agent_id=4,               # AgentChat 中 Agent 用户的 id（is_agent=True）
    agent_username="code-reviewer",
    api_key=None,             # 传给 CLI 的 API Key（如 OPENAI_API_KEY / QWEN key）
    model=None,
    # —— resume 支持（二选一，可都不传=每次新建会话）——
    thread_id=None,           # 同一 thread_id 在桥接重启时自动 resume 到已有 qwen 会话
    resume_session_id=None,  # 显式恢复某个指定的 qwen session id
)
await bridge.start()          # 派生 qwen + JSON-RPC 握手 + 连接 AgentChat + 进入消息循环
```

- `QwenACPBridge`：单个 CLI 会话的桥接器（一个 channel/agent 一个实例）。
- `ACPBridgeManager`：进程内桥接器注册表（全局单例 `bridge_manager`）。
- `ACPMysqlStore`：可选持久化；表不存在时自动降级为内存，不阻塞桥接。
  通过 `thread_id` 与 `qwen_session_id` 实现「同一 thread → 同一 qwen 会话」的稳定映射，
  以及 `resumed` 标记（本次是否从已有会话恢复）。
- `make_agent_token(user_id)`：为 Agent 用户签发 JWT（与 `authenticate_socket_token` 一致）。

### 2. `examples/acp_bridge_daemon.py` — 守护进程入口

```bash
python examples/acp_bridge_daemon.py \
    --base-url http://127.0.0.1:8000 \
    --channel-id 4 --agent-id 4 --agent-username code-reviewer \
    --api-key "$OPENAI_API_KEY"
```

参数：`--base-url` / `--channel-id`(必填) / `--agent-id`(必填) / `--agent-username` /
`--api-key`(可省略，默认读 `OPENAI_API_KEY` 环境变量) / `--model` / `--cli-cmd`(覆盖默认 qwen 命令) /
`--thread-id`(同一 thread 重启自动 resume) / `--resume-session`(显式恢复某 qwen session)。

### 3. Resume 已有会话

默认每次启动 Bridge 都会新建一个 qwen 会话。如需**恢复已有会话**（延续上下文与历史），
有两种方式：

**(a) 按 thread 自动 resume（推荐）**

为 Bridge 固定一个 `thread_id`。Bridge 会把该 thread 映射到一个稳定的 qwen session id 并持久化；
同一 `thread_id` 再次启动时，`session/new` 通过 `_meta` 请求同一 id，qwen 若已落盘则 **attach（恢复）**
该会话，否则新建。因此「崩溃重启 / 主动重启」后，同一个 thread 始终接着上一次的对话继续。

```bash
# 第一次启动：新建会话并持久化 thread-1 -> qwen session
python examples/acp_bridge_daemon.py \
    --channel-id 4 --agent-id 4 --agent-username code-reviewer \
    --api-key "$OPENAI_API_KEY" --thread-id thread-1

# 重启后（同一 thread-id）：自动 resume 到上面的 qwen 会话
python examples/acp_bridge_daemon.py \
    --channel-id 4 --agent-id 4 --agent-username code-reviewer \
    --api-key "$OPENAI_API_KEY" --thread-id thread-1
```

日志会打印 `resumed=True/False`（`examples/test_real_qwen_bridge.py` 同样会打印）。

**(b) 显式 resume 某个 qwen session id**

当你已知某个 qwen session id（来自 `acp_sessions` 表或 `demo_resume.py` 的输出），
可直接指定恢复，无需 thread 绑定：

```bash
python examples/acp_bridge_daemon.py \
    --channel-id 4 --agent-id 4 --agent-username code-reviewer \
    --api-key "$OPENAI_API_KEY" --resume-session <qwen-session-id>
```

> **实现要点**：`qwen --acp` 是 JSON-RPC server。
> - **新建会话**：`session/new` 通过 `params["_meta"]["qwen-code/sessionId"]` 指定一个（全新）id；
>   若 id 已存在会报 `session_id_conflict`，Bridge 自动回退为全新 id。
> - **附加已有会话**（显式 `--resume-session` 或同 thread 的已有会话）：Bridge 改调 `session/resume`
>   （授予所有权，可恢复 active/archived 会话），从而真正接着原会话继续；若该会话不存在则回退为新建。
> 跨进程（每次启动都是新 qwen 子进程）resume 已用真实 `qwen --acp` 实测验证通过。

**快捷验证（无需 AgentChat 服务端，只需 qwen 已安装）：**

```bash
python examples/demo_resume.py
# 输出 PASS: 跨进程 resume 成功（qwen 恢复了已落盘的会话）
```

## 使用流程

### 1. 启动 AgentChat 服务

```bash
cd /media/cmbsysadmin/E盘/02-Code/agentchat
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 启动 Bridge（接入真实 qwen）

```bash
python examples/acp_bridge_daemon.py \
    --channel-id 4 --agent-id 4 --agent-username code-reviewer \
    --api-key "$OPENAI_API_KEY"
```

启动后 Bridge 会：
1. 派生 `qwen --acp --channel ACP --output-format stream-json` 子进程；
2. 完成 JSON-RPC 握手（`initialize` → `session/new`）；
3. 作为 ACP Socket Mode client 连接 `/api/acp/ws/socket` 并发送 `connect`（携带 channel_id）。

### 3. 在频道里 @提及 Agent

用户在频道 4 发送 `@code-reviewer 你好`，AgentChat 将消息作为 `input` 帧转发给 Bridge；
Bridge 调用 `qwen session/prompt`，把 qwen 通过 `session/update` 流式返回的内容聚合后，
以 `output` 帧回传；AgentChat 将其写入频道并广播给频道内所有用户。

## ACP 协议（qwen --acp，实测）

`qwen --acp` 是 **JSON-RPC 2.0 server**，Bridge 作为 client 走 stdio：

```
bridge -> initialize{protocolVersion:1, capabilities, clientInfo}
qwen   -> initialize result{protocolVersion, agentInfo, agentCapabilities, authMethods}
bridge -> session/new{cwd, mcpServers:[], _meta:{qwen-code/sessionId:<id>}}   # 新建（指定新 id）
bridge -> session/resume{sessionId:<id>, cwd}                                  # 附加已有会话（resume）
qwen   -> session/new|session/resume result{sessionId, models, ...}  + session/update 通知
        （session/new 指定已存在 id 会 session_id_conflict，Bridge 回退新 id；session/resume 恢复已有会话）
bridge -> session/prompt{sessionId, cwd, prompt:[{type:"text", text:"..."}]}
qwen   -> session/update 通知（流式内容） + session/prompt 结果(stopReason:end_turn)
```

> 注意：`session/prompt` 的 `prompt` 必须是**数组**（`[{type:"text",text:"..."}]`），
> 不能是字符串，否则 qwen 报 `expected array, received string`。

真实 LLM 文本需要有效的 API Key（`OPENAI_API_KEY` 或 QWEN key）。
无 Key 时 qwen 仍完成握手与连接，但 `session/prompt` 无内容返回。

## 与 AgentChat 侧 ACP 帧协议的关系

| 方向 | 协议 |
|------|------|
| AgentChat → Bridge | ACP Socket Mode 帧：`input` / `ping` / `status` |
| Bridge → AgentChat | ACP Socket Mode 帧：`connect` / `output` / `pong` |
| Bridge → qwen | JSON-RPC 2.0 over stdio：`initialize` / `session/new` / `session/prompt` |
| qwen → Bridge | JSON-RPC 结果 + `session/update` 通知 |

Bridge 负责两套协议的翻译，是架构中的「acp-bridge」。

## 故障排查

```bash
# 检查 AgentChat 健康
curl http://localhost:8000/health

# 检查 Bridge 进程与 qwen 子进程
ps aux | grep -E "acp_bridge_daemon|qwen --acp"

# 查看 qwen 握手/报错（Bridge 日志会打印 [qwen stderr] / [qwen initialize] / [qwen session/new]）
# 持久化（可选）
mysql> SELECT session_id, thread_id, channel_id, process_state, extra_data FROM acp_sessions ORDER BY id DESC LIMIT 10;
mysql> SELECT * FROM acp_messages ORDER BY id DESC LIMIT 10;

# 验证 resume（无需 AgentChat 服务端，仅需 qwen 已安装）
python examples/demo_resume.py
# 验证 真实 qwen 桥接端到端（需 AgentChat 服务端 + 可选 API Key）
python examples/test_real_qwen_bridge.py --thread-id thread-1
```
