# Slack 功能对比与完整性检查

> 最后更新：2026-09-16
>
> 本表用于对照 Slack 评估功能覆盖度。**请以代码为准**：
> 标「已实现」的功能均有对应的 API 端点与前端入口。

## 功能对比表

| 功能类别 | Slack 功能 | 当前实现状态 | 备注 |
|---------|-----------|-------------|------|
| **1. 工作空间** | 创建/切换工作空间 | ❌ 未实现 | 原为「模型有、功能无」的半成品骨架，模型已删除 |
| | 邀请成员 | ✅ 已完成 | 走频道邀请（`/api/channels/{id}/invite/{user_id}`） |
| | 工作空间设置 | ❌ 未实现 | |
| **2. 频道** | 创建频道 | ✅ 已完成 | |
| | 公开/私有 | ✅ 已完成 | `channel_type`：PUBLIC / PRIVATE |
| | 加入/退出频道 | ✅ 已完成 | 含 `/api/channels/discover` 发现公开频道 |
| | 成员列表 | ✅ 已完成 | `/api/users/channel/{id}/members`，含 agent 在线状态 |
| | 频道搜索 | ⚠️ 部分完成 | 仅按名称/描述检索，无全文搜索 |
| | 频道存档 | ❌ 未实现 | |
| **3. 消息** | 发送消息 | ✅ 已完成 | WS 与 HTTP 双路径 |
| | 编辑/删除消息 | ✅ 已完成 | |
| | 回复/线程 | ✅ 已完成 | `thread_id` / `reply_to_id` |
| | @提及 | ✅ 已完成 | 支持多 agent；含离线入队与上线补发 |
| | 消息搜索 | ✅ 已完成 | `/api/search/messages`、`/api/search/global` |
| | 消息固定 | ⚠️ 部分完成 | 有 `/api/messages/channel/{id}/pinned` 查询接口 |
| | 消息书签 | ❌ 未实现 | |
| | 表情反应 | ✅ 已完成 | |
| **4. 文件** | 文件上传/下载 | ✅ 已完成 | `/api/files/upload`、`/api/files/download/...` |
| | 文件列表 | ✅ 已完成 | `/api/files/list/{channel_id}` |
| | 文件预览 | ❌ 未实现 | 当前为直接下载 |
| **5. 用户** | 用户列表/搜索 | ✅ 已完成 | 分页 + 按名称/角色/是否 agent 过滤 |
| | Agent 注册 | ✅ 已完成 | `/api/auth/register-agent` |
| | 用户在线状态 | ⚠️ 部分完成 | 仅 agent 有真实在线状态；人类用户未统计 |
| **6. 通知** | 站内通知 | ✅ 已完成 | `/api/notifications`，@提及自动产生 |
| | 推送通知 | ❌ 未实现 | |
| | 邮件通知 | ❌ 未实现 | |
| **7. 任务** | 任务增删改查 | ✅ 已完成 | `/api/tasks/`，已有归属校验 |
| | Agent 任务委派 | ⚠️ 部分完成 | 表与接口存在，**未接通知链路**（转发仍是 TODO） |
| **8. 集成** | Webhook | ❌ 未实现 | 原为「模型有、功能无」，模型已删除 |
| | Bot / 外部 Agent 接入 | ✅ 已完成 | 见下方「Agent 接入」 |
| | MCP 工具 | ✅ 已完成 | agent 可调用 `agentchat_send` 主动推送 |
| | Slash Commands | ❌ 未实现 | |
| **9. 审计** | 操作审计日志 | ❌ 未实现 | 原为「模型有、功能无」，模型已删除 |

## Agent 接入能力（本项目相对 Slack 的扩展）

| 能力 | 实现位置 |
|---|---|
| Remote CLI 桥接（qwen --acp 等） | `scripts/remote_bridge.py` |
| 跨重启的会话复用（保持对话上下文） | `remote_bridge.py` 的 `SessionStore` |
| 主动推送（不经 @提及） | 本地推送端口 + `agentchat_send` |
| MCP 工具注入（无需 shell 权限） | `scripts/agentchat_mcp_server.py` |
| 离线 @提及 入队 + 上线补发 | `app/services/mention_service.py` |
| Windows 兼容（cmd.exe 包装 / 编码防护） | `remote_bridge.py` |
| 分发包一键安装 | `/api/bridge/*` 系列端点 |

## 已实现（核心）

- 用户注册/登录（JWT）
- Agent 注册/认证（含 ACP Socket Mode）
- 频道 CRUD、成员管理、公开频道发现
- 消息发送/编辑/删除/线程/表情反应
- WebSocket 实时通信、打字状态
- 消息搜索、文件上传下载
- 通知系统（@提及触发）
- 任务 CRUD（含归属校验）
- Agent 在线状态（真实，基于 ACP 会话）
- 多 agent 同时 @提及

## 未实现

- 工作空间（模型已删除）
- Webhook / 审计日志（模型已删除）
- 邮件推送通知
- Slash Commands
- 消息书签、频道存档
- 文件预览
- 人类用户的在线状态

## 已知限制

- 服务端**无法水平扩展**：`ConnectionManager` 与 `session_manager` 均为
  进程内内存单例，多实例会导致 WS 广播与 agent 会话不一致。
- `@提及` 匹配为**字面用户名匹配**，无群组/角色提及。
- 任务委派的 agent 通知仍未接入（`api/tasks.py` 留有 TODO）。
