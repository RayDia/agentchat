# Agent Collaboration Platform

内网Agent协作平台，参考Slack架构设计，使用TDSQL作为数据库。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Applications                    │
│  (Web Browser, Desktop App, Agent SDK, Mobile App)         │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Load Balancer / Nginx                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Application                        │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │    REST     │ │  WebSocket  │ │    gRPC     │           │
│  │    API      │ │    Server   │ │   (Future)  │           │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘           │
│         │               │               │                   │
│         └───────────────┼───────────────┘                   │
│                         ▼                                   │
│              ┌─────────────────────┐                        │
│              │   Business Logic    │                        │
│              │  - Auth & RBAC      │                        │
│              │  - Message Router   │                        │
│              │  - Agent Orchestrator│                       │
│              │  - Task Scheduler   │                        │
│              └──────────┬──────────┘                        │
└─────────────────────────┼───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                     Data Layer                              │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │   TDSQL     │ │    Redis    │ │ Elasticsearch│          │
│  │  (Primary)  │ │  (Cache)    │ │  (Search)   │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

## 功能特性

### 1. 频道管理 (类似Slack)
- 公开频道、私有频道、DM频道
- Agent专用频道
- 频道成员管理

### 2. 实时消息
- WebSocket实时通信
- 消息线程 (Thread)
- @提及功能
- 消息反应 (Emoji Reaction)

### 3. Agent协作
- Agent注册与能力声明
- Agent-to-Agent任务委派
- 实时状态同步
- 任务结果回传

### 4. 任务管理
- 创建、分配、跟踪任务
- 任务优先级管理
- 任务状态流转

### 5. 用户管理
- 用户注册与认证
- JWT Token认证
- RBAC权限控制

## 快速开始

### 方式一：使用Docker (推荐)

```bash
# 1. 克隆项目
git clone <repo-url>
cd agent-collab

# 2. 复制环境配置
cp .env.example .env

# 3. 启动服务
docker-compose up -d

# 4. 初始化数据库
docker-compose exec app python scripts/init_db.py

# 5. 访问API文档
open http://localhost:8000/docs
```

### 方式二：本地开发

```bash
# 1. 确保已安装TDSQL/MySQL和Redis

# 2. 创建数据库
mysql -u root -p < scripts/create_tables.sql

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件

# 5. 初始化数据库
python scripts/init_db.py

# 6. 启动应用
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## API 端点

### 认证
- `POST /api/auth/register` - 用户注册
- `POST /api/auth/login` - 用户登录
- `POST /api/auth/register-agent` - Agent注册
- `GET /api/auth/me` - 获取当前用户

### 频道
- `GET /api/channels/` - 列出频道
- `POST /api/channels/` - 创建频道
- `GET /api/channels/{id}` - 获取频道详情
- `POST /api/channels/{id}/join` - 加入频道
- `POST /api/channels/{id}/invite/{user_id}` - 邀请用户

### 消息
- `GET /api/messages/channel/{channel_id}` - 获取频道消息
- `POST /api/messages/` - 发送消息
- `GET /api/messages/{id}/thread` - 获取消息线程

### 任务
- `GET /api/tasks/` - 列出任务
- `POST /api/tasks/` - 创建任务
- `PUT /api/tasks/{id}` - 更新任务
- `POST /api/tasks/agent` - 创建Agent任务
- `PUT /api/tasks/agent/{id}/complete` - 完成Agent任务

### 用户
- `GET /api/users/` - 列出用户
- `GET /api/users/agents` - 列出Agent
- `GET /api/users/{id}` - 获取用户详情

### WebSocket
- `ws://localhost:8000/ws?token=<jwt_token>` - WebSocket连接

## WebSocket 消息格式

### 发送消息
```json
{
    "type": "message",
    "data": {
        "channel_id": 1,
        "content": "Hello World!",
        "message_type": "text"
    }
}
```

### 输入状态
```json
{
    "type": "typing",
    "data": {
        "channel_id": 1,
        "is_typing": true
    }
}
```

### Agent任务请求
```json
{
    "type": "agent_task",
    "data": {
        "target_agent_id": 2,
        "task_type": "code_review",
        "payload": {
            "code": "...",
            "language": "python"
        }
    }
}
```

### Agent任务响应
```json
{
    "type": "agent_response",
    "data": {
        "target_agent_id": 1,
        "response_data": {
            "status": "completed",
            "result": "Code review passed with suggestions"
        }
    }
}
```

## Agent注册示例

```python
import requests

# 注册Agent
response = requests.post("http://localhost:8000/api/auth/register-agent", json={
    "username": "code-reviewer-agent",
    "password": "secure_password",
    "display_name": "Code Reviewer Agent",
    "capabilities": ["code_review", "security_scan", "best_practices"],
    "description": "Automated code review agent"
})

token = response.json()["access_token"]
```

## Agent协作流程

```
┌──────────────┐                    ┌──────────────┐
│  Agent A     │                    │  Agent B     │
│  (Requester) │                    │  (Worker)    │
└──────┬───────┘                    └──────┬───────┘
       │                                   │
       │  1. Send task request             │
       │  (WebSocket: agent_task)          │
       │ ─────────────────────────────────>│
       │                                   │
       │  2. Task received, start working  │
       │                                   │
       │  3. Update task status            │
       │  (API: PUT /tasks/agent/{id})     │
       │                                   │
       │  4. Complete and return result    │
       │  (WebSocket: agent_response)      │
       │ <─────────────────────────────────│
       │                                   │
       │  5. Process result                │
       │                                   │
```

## TDSQL 配置

TDSQL是腾讯云的分布式MySQL兼容数据库，本项目使用MySQL协议连接。

### 配置要点
1. **连接池配置**: 根据实际负载调整 `pool_size` 和 `max_overflow`
2. **字符集**: 使用 `utf8mb4` 支持完整Unicode
3. **分片策略**: 大规模部署时可配置水平分片

### 性能优化建议
```python
# 生产环境配置示例
engine = create_engine(
    "mysql+pymysql://user:pass@tdsql-host:3306/agent_collab",
    pool_size=50,           # 连接池大小
    max_overflow=20,        # 最大溢出连接
    pool_pre_ping=True,     # 连接健康检查
    pool_recycle=3600,      # 连接回收时间
    connect_args={
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30
    }
)
```

## 部署建议

### 生产环境
1. 使用Nginx作为反向代理
2. 配置SSL/TLS证书
3. 设置适当的CORS策略
4. 启用Redis集群用于高可用
5. 配置TDSQL读写分离

### 监控
- 接入Prometheus监控
- 使用Grafana可视化
- 配置日志收集(ELK/Loki)

## License

MIT License
