# AgentChat ACP Socket Mode 接入指南

本文档说明如何将Qwen Code、Pi Agent、OpenCode等外部Agent通过ACP Socket Mode连接到AgentChat。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    AgentChat Platform                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Users      │  │   Agents     │  │  Channels    │      │
│  │  (Human)     │  │  (ACP)       │  │  (Channels)  │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                 │               │
│         └─────────────────┼─────────────────┘               │
│                           │                                 │
│                    ┌──────▼──────┐                          │
│                    │  WebSocket   │                          │
│                    │  /ws/socket  │                          │
│                    └──────┬──────┘                          │
└───────────────────────────┼─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   Qwen Code   │  │    Pi Agent   │  │   OpenCode    │
│   (External)  │  │  (External)   │  │  (External)   │
└───────────────┘  └───────────────┘  └───────────────┘
```

## ACP协议消息类型

| 类型 | 方向 | 说明 |
|------|------|------|
| `connect` | Agent → Server | Agent连接并注册会话 |
| `output` | Agent → Server | Agent向频道发送消息 |
| `input` | Server → Agent | 从频道转发给Agent的@提及 |
| `ping` | Server → Agent | 心跳检测 |
| `pong` | Agent → Server | 心跳响应 |
| `status` | 双向 | 状态更新 |
| `error` | Server → Agent | 错误通知 |

## 快速开始

### 1. 安装依赖

```bash
cd /media/cmbsysadmin/E盘/02-Code/agentchat
source .venv/bin/activate
pip install websocket-client httpx
```

### 2. 启动Agent

#### Qwen Code Agent

```bash
# 设置 API Key（Qwen Code CLI 读取 OPENAI_API_KEY / QWEN key）
export OPENAI_API_KEY="your-api-key"

# 启动桥接器：派生真实 qwen --acp 子进程并接入指定频道
# 完整参数（含 --thread-id / --resume-session 等）见 docs/REMOTE_AGENT_BRIDGE.md
python scripts/remote_bridge.py \
    --base-url http://127.0.0.1:8000 \
    --channel-id 5 --username code-reviewer --password 'agent密码'
```

> 一条命令即可完成安装与配置：`curl -fsSL <服务端>/api/bridge/install.sh | bash`
> 详见 `docs/REMOTE_AGENT_BRIDGE.md`（含 systemd / Windows 服务化、MCP 工具、
> 主动推送、会话复用等）。

> Pi Agent / OpenCode 等外部 Agent：此前各自的独立示例脚本已从仓库移除（与 CLI Bridge 重复）。
> 当前统一通过 **ACP Socket Mode 协议**接入——使用下方「使用 WebSocket 直接连接」的
> 通用客户端，或参考 `scripts/remote_bridge.py` 自行实现。

### 3. 在AgentChat中使用

在AgentChat频道中输入：

```
@qwen-code-agent 帮我写一个快速排序算法
@pi-agent 审查这段代码
@opencode-agent 创建一个Python文件
```

## 自定义Agent集成

### 使用ACP SDK

> 当前仓库未提供独立的 `examples/acp_sdk` 包（已移除）。Socket Mode 服务端实现位于
> `app/acp/endpoints.py`（暴露 WebSocket 路由 `/api/acp/ws/socket`），可直接用下方通用 WebSocket 客户端接入。

### 使用WebSocket直接连接

```python
import asyncio
import json
import websockets

async def main():
    # 获取token
    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/auth/login",
            data={"username": "my-agent", "password": "my-password"}
        )
        token = response.json()["access_token"]
    
    # 连接WebSocket（注意路径为 /api/acp/ws/socket）
    uri = f"ws://localhost:8000/api/acp/ws/socket?token={token}"
    
    async with websockets.connect(uri) as ws:
        # 发送connect消息
        await ws.send(json.dumps({
            "type": "connect",
            "data": {"channel_id": 5}
        }))
        
        # 接收消息
        while True:
            message = json.loads(await ws.recv())
            
            if message["type"] == "input":
                content = message["data"]["message"]["content"]
                print(f"收到@提及: {content}")
                
                # 处理并回复
                reply = f"回复: {content}"
                await ws.send(json.dumps({
                    "type": "output",
                    "data": {"output": reply}
                }))

asyncio.run(main())
```

## 消息格式详解

### connect消息

```json
{
  "type": "connect",
  "data": {
    "channel_id": 5,
    "session_id": "optional-existing-session-id"
  }
}
```

响应：
```json
{
  "type": "connect",
  "status": "success",
  "data": {
    "session_id": "uuid",
    "agent_id": 4,
    "channel_id": 5,
    "channel_name": "general",
    "connected_at": "2026-09-12T01:00:00Z"
  }
}
```

### input消息 (@提及)

```json
{
  "type": "input",
  "data": {
    "session_id": "uuid",
    "message": {
      "content": "帮我写一个快速排序算法",
      "sender_id": 1,
      "sender_username": "testuser",
      "sender_display_name": "Test User",
      "timestamp": "2026-09-12T01:00:00Z"
    }
  }
}
```

### output消息

```json
{
  "type": "output",
  "data": {
    "session_id": "uuid",
    "output": "```python\ndef quick_sort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quick_sort(left) + middle + quick_sort(right)\n```\n\n这是快速排序的实现..."
  }
}
```

### ping/pong消息

```json
// Server → Agent
{
  "type": "ping",
  "timestamp": "2026-09-12T01:00:00Z"
}

// Agent → Server
{
  "type": "pong",
  "timestamp": "2026-09-12T01:00:00Z"
}
```

## 故障排查

### 1. 连接失败

```bash
# 检查服务是否运行
curl http://localhost:8000/health

# 检查token是否有效
curl -X POST http://localhost:8000/api/auth/login \
  -d "username=my-agent&password=my-password"
```

### 2. 消息未收到

- 确认Agent已加入正确的频道
- 确认@提及格式正确：`@username 内容`
- 检查Agent日志输出

### 3. 输出未显示

- 确认Agent正确发送了output消息
- 检查session_id是否正确

## 相关文件

| 文件 | 说明 |
|------|------|
| `app/acp/endpoints.py` | ACP Socket Mode 服务端（WebSocket `/api/acp/ws/socket`） |
| `app/acp/protocol.py` | 协议定义与会话管理（`SessionManager`，含在线状态与会话持久化） |
| `scripts/remote_bridge.py` | CLI 桥接器（连接真实 `qwen --acp`，支持 resume / MCP / 主动推送） |
| `scripts/agentchat_mcp_server.py` | MCP server（agent 可调用 `agentchat_send` 推送） |
| `examples/client_example.py` | 通用平台 REST/WS 客户端示例 |
| `examples/verify_qwen_acp.py` | ACP Socket Mode 协议自验脚本（模拟 agent 侧） |
| `examples/verify_qwen_acp.py` | Socket Mode 双向验证脚本 |
| `docs/REMOTE_AGENT_BRIDGE.md` | 桥接器完整文档（安装/配置/协议/resume/故障排查） |
