# 两套 CLI 桥接方案对比（实测）

## 背景

仓库里存在**两套**把本地 CLI（qwen --acp）接入 AgentChat 的实现：

| | 旧方案 | 新方案 |
|---|---|---|
| 核心实现 | `app/acp/cli_bridge.py`（655 行） | `scripts/remote_bridge.py`（1262 行） |
| 持久化模型 | `app/acp/models.py`（57 行） | 无（会话记录用 `app/models.py`） |
| 入口 | `examples/acp_bridge_daemon.py` 等 6 个 | 命令行 `main()` + 分发包 |
| 文档 | `docs/ACP_CLI_BRIDGE.md` | `docs/REMOTE_AGENT_BRIDGE.md` |
| 定位 | 服务端内置的桥接器 | 可分发的独立客户端 |

---

## 一、能力矩阵（源码级核对）

| 能力 | 旧 | 新 |
|---|---|---|
| 登录换 token（不持有 SECRET_KEY） | 有 | 有 |
| **按 thread_id 复用会话（跨重启保持上下文）** | **有** | **—** |
| **DB 持久化线程→会话映射** | **有** | **—** |
| **多会话管理（ACPBridgeManager）** | **有** | **—** |
| 本地 JWT 签发（需共享 SECRET_KEY） | 有 | — |
| 输入队列（LLM 推理期间不丢消息） | — | 有 |
| 工具权限审批（session/request_permission） | — | 有 |
| 主动推送（不经 @提及） | — | 有 |
| 本地推送 HTTP 服务 | — | 有 |
| MCP 工具注入 | — | 有 |
| Windows 兼容（cmd.exe 包装 / 编码防护） | — | 有 |
| 配置文件（toml）+ argparse + 自检 | — | 有 |

**统计：旧独有 4 项，新独有 10 项，共有 1 项。**

---

## 二、旧方案独有能力的实际价值（实测）

### 2.1 会话复用 —— 真实有效，且是刚需

**实验设计（关键）**：必须排除 qwen 自身的持久化，否则会得出错误结论。

第一次实验用「记暗号」失败——agent 把暗号写进了
`~/.qwen/memories/user/secret-code.md`，所以它答对**不能**证明会话被保持。

改用**明确的纯临时上下文**（并要求"不要写入文件或记忆"）重测：

```
【新方案】
第一轮  sessionId = 372c1a75-...  →  "好的，临时值 705c69 已记住。"
        （确认未写入 memories 目录）
重启桥接器（qwen 进程被终止，全新进程）
第二轮  sessionId = 全新          →  "I don't have access to your previous
                                     message in this conversation"
结论：会话未保持，上下文丢失 ✅ 实测确认
```

这说明新方案每次重启都是全新会话——**agent 记不住上一轮的对话**。

### 2.2 但旧方案的该能力已被破坏

归档废弃表时，`acp_sessions` 被重命名为 `_deprecated_acp_sessions`。
旧方案的 `ACPMysqlStore` 仍按原名查询，运行时直接报错：

```
_HAS_STORE = True                                    ← 模块导入时通过
WARNING ACPBridge: get_session_by_thread 失败:
  (1146, "Table 'agentchat.acp_sessions' doesn't exist")   ← 运行时失败
```

即：**旧方案的 thread 复用、会话持久化、多会话管理目前均已失效**
（`ACPBridgeManager` 依赖同一套存储）。`_HAS_STORE` 在导入期检查表定义
存在性，而表被重命名后 ORM 定义仍在，所以它是 `True` —— 属检测缺陷。

结论：旧方案剩下的 4 项独有能力中，**3 项已不可用**；
仅「本地 JWT 签发」与归档无关（但那需要共享 SECRET_KEY，远端不推荐）。

---

## 三、结论与建议

### 3.1 两套方案的关系

- **不是简单重复**：旧方案有会话复用/多桥接管理，新方案有推送/MCP/Windows/配置体系
- **旧方案已不可用**：其持久化依赖的表已归档，核心优势（会话复用）失效
- **新方案是实际在用的**：分发包、文档、MCP 集成都围绕它构建

### 3.2 建议：补齐新方案，然后删除旧方案

**第一步（推荐）：把「会话复用」补进 `scripts/remote_bridge.py`**

这是旧方案唯一有实质价值、新方案确实缺失的能力。实现方式：

1. 桥接器已有 `--thread-id` 参数（默认 `agent-{username}-ch{channel_id}`）
2. 服务端已有 `acp_agent_sessions` 表（本次新增，字段含 `session_id` /
   `agent_id` / `channel_id`）
3. 需要的是：**桥接器本地记住「thread_id → qwen session_id」并持久化**

最小实现：桥接器把映射写本地文件（如 `~/.agentchat/bridge/sessions.json`），
启动时读取；或用桥接器本地 sqlite。**不要**依赖服务端表——那是服务端自己的
会话记录，语义不同（服务端记录的是"agent 连上了哪个频道"，不是 qwen 会话）。

有了它，重启后走 `session/resume` 附加原会话，上下文即可保持。

**第二步：删除旧方案**

- 删 `app/acp/cli_bridge.py`、`app/acp/models.py`
- 删 `examples/`（6 个文件）
- 删 `docs/ACP_CLI_BRIDGE.md`
- 更新 `docs/ACP_SOCKET_MODE.md` 中引用 examples / cli_bridge 的段落
- 归档表 `_deprecated_acp_sessions` / `_deprecated_acp_messages` 可彻底 DROP
  （届时已无任何代码引用）

**为什么不建议反过来（保留旧方案）**：新方案承载了 MCP 集成、Windows 兼容、
配置体系、分发包，这些是当前交付的主线；旧方案只有 1 项优势，且要修 3 处
失效点才能恢复。

---

## 四、本文件中的实测数据来源

| 数据 | 验证方式 |
|---|---|
| 能力矩阵 | 源码特征检索（两文件对同一关键词的存在性） |
| 新方案上下文丢失 | 起真实 qwen 桥接器 → 建立临时值 → 杀进程重启 → 询问，实测答不出 |
| 旧方案存储失效 | 实际实例化 `QwenACPBridge`，捕获 `Table doesn't exist` 报错 |
| agent 自身持久化干扰 | 检查 `~/.qwen/memories/` 目录内容 |
