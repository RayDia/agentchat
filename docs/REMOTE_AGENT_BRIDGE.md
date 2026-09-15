# 远端 Agent 接入指南

在**只装了 qwen CLI 的远端机器**上，把该机的 agent 接入 AgentChat。

## 为什么需要桥接进程

`qwen --acp` 是 **JSON-RPC over stdio** 的进程，只能被父进程通过管道操控，
**不能作为独立 server 被远程连接**。因此必须有桥接进程与它同机运行：

```
   AgentChat 服务端（主机 A）
            ▲ WSS /api/acp/ws/socket
            │
   主机 B: 桥接进程 ──stdio──> qwen --acp 子进程 ──> LLM
```

**主机 B 与 qwen 必须同机**（stdio 限制）；**主机 B 与 A 可以异机**（走网络）。

## 一、内网分发：让用户从 AgentChat 服务端下载

内网环境通常无法访问 GitHub，因此 AgentChat **自带分发能力**——服务端直接
对外提供桥接器下载，用户只需能访问 AgentChat 即可拿到安装包。

### 服务端一次性构建

分发包是构建产物（`.gitignore` 已排除 `dist/`），部署后需在服务端构建一次：

```bash
cd /path/to/agentchat
bash scripts/build_bridge_dist.sh 1.0.0
# 产出 dist/bridge/agentchat-bridge-1.0.0.tar.gz + SHA256SUMS + version
```

升级桥接器时重新执行该脚本并递增版本号即可。

### 分发端点（无需认证）

| 端点 | 用途 |
|---|---|
| `GET /api/bridge/readme` | **纯文本安装说明**（自动带当前服务端地址） |
| `GET /api/bridge/install.sh` | 一键安装脚本（自动注入服务端地址） |
| `GET /api/bridge/download` | 下载分发包 tar.gz |
| `GET /api/bridge/checksum` | 下载 SHA256SUMS |
| `GET /api/bridge/version` | 查询版本与校验和（JSON） |

未构建时会返回 `404` 并提示执行构建脚本，不会静默失败。

### 告诉用户怎么装

把这一行发给用户（`<AGENTCHAT>` 换成实际地址）：

```bash
curl -fsSL http://<AGENTCHAT>:8000/api/bridge/install.sh | bash
```

> 管道执行远程脚本存在被中间篡改的风险。内网可信环境下可接受；
> 若要求更严，改用下面的「下载 + 校验」方式。

**推荐给用户的方式（带完整性校验）**：

```bash
curl -LO http://<AGENTCHAT>:8000/api/bridge/download
curl -LO http://<AGENTCHAT>:8000/api/bridge/checksum
sha256sum -c SHA256SUMS
tar xzf agentchat-bridge-*.tar.gz
cd agentchat-bridge-*/ && bash bridge_install.sh
```

用户也可以先自己看说明，再决定装不装：

```bash
curl http://<AGENTCHAT>:8000/api/bridge/readme
```

### 为什么走服务端而不是文件服务器

- 用户**本来就能访问 AgentChat**，所以一定能下载，无需额外开通权限
- 安装脚本能**自动识别服务端地址**并写进配置，避免用户填错
- 版本与服务端绑定，升级时用户拉到的就是匹配版本
- 无需额外维护网盘/nginx/对象存储

---

## 二、认证方式：登录换 token（推荐）

桥接进程通过 `POST /api/auth/login` 用 agent 账号密码换取 access token，
因此**远端机器不需要持有服务端的 `SECRET_KEY`**，避免密钥扩散。

> 另一种方式是在远端 `.env` 里配置与服务端相同的 `SECRET_KEY`，用
> `make_agent_token()` 自签。但那意味着密钥散落在每台远端机器上，泄露一台
> 等于全部沦陷。**不推荐。**

## 三、前置条件

### 主机 A（AgentChat 服务端）

1. 服务监听 `0.0.0.0`（当前默认即是）
2. agent 用户已注册（`is_agent=True`），且**已设置密码**
3. 该 agent 是目标频道的**成员**
4. **生产环境必须挂 HTTPS/WSS** —— WebSocket 端点不校验来源 IP、CORS 全开，
   token 在未加密链路中是明文传输的

### 主机 B（远端机器）

1. 已装 qwen CLI 且能正常出 LLM 回复
2. Python 3.9+
3. 能网络访问主机 A 的端口

## 四、快速开始

### 4.1 在主机 A 上准备 agent 账号

注册 agent（若尚无）：

```bash
curl -X POST http://<主机A>:8000/api/auth/register-agent \
  -H 'Content-Type: application/json' \
  -d '{"username":"code-reviewer","password":"strong-password",
       "display_name":"Code Reviewer","capabilities":["code_review"]}'
```

若 agent 已存在但**没有密码或密码遗失**，用改密接口补设（无需手工改数据库）。

`POST /api/auth/agents/{agent_id}/password`，权限：**agent 本人**或**管理员**。

```bash
# 用 agent 自己的 token（或管理员 token）
curl -X POST http://<主机A>:8000/api/auth/agents/4/password \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"new_password":"strong-password"}'
# -> {"detail":"Agent password updated","agent_id":4,"username":"code-reviewer"}
```

普通用户改自己的密码用 `POST /api/auth/change-password`（建议带上旧密码校验）：

```bash
curl -X POST http://<主机A>:8000/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"old_password":"old-pass","new_password":"new-pass"}'
```

错误码：`403` 非本人且非管理员 / `400` 目标不是 agent / `404` agent 不存在 / `422` 密码短于 6 位 / `401` 旧密码错误。

把 agent 加入目标频道（假设 channel 4）：

```sql
INSERT INTO channel_members (channel_id, user_id) VALUES (4, 4);
```

验证登录可用：

```bash
curl -X POST http://<主机A>:8000/api/auth/login \
  -d 'username=code-reviewer&password=strong-password'
# 应返回 {"access_token":"...","user":{...}}
```

### 4.2 在主机 B 上部署

**方式 A：一键安装（推荐）**

拷贝 `scripts/bridge_install.sh` 和 `scripts/remote_bridge.py` 到用户机器后执行：

```bash
bash bridge_install.sh
```

脚本会：检查 Python/qwen 环境 → 在 `~/.agentchat/bridge` 建独立 venv →
装依赖（仅 `httpx`、`websockets`）→ 生成 `bridge.toml` 配置模板（权限 600）→
生成 `start.sh` 启动脚本 → 自动运行环境自检。

自定义安装位置：`AGENTCHAT_BRIDGE_HOME=/opt/agentchat-bridge bash bridge_install.sh`

**方式 B：手工部署**

```bash
mkdir -p ~/.agentchat/bridge && cd ~/.agentchat/bridge
# 拷贝 remote_bridge.py 到此目录
python3 -m venv .venv
.venv/bin/pip install "websockets>=12.0" httpx
```

> 仅需 **2 个第三方包**，其余全是标准库。不再依赖 `python-jose`。

### 4.3 配置与运行

编辑 `~/.agentchat/bridge/bridge.toml`：

```toml
[bridge]
base-url = "https://agentchat.example.com"
channel-id = 4
username = "code-reviewer"
# 密码建议改用环境变量 AGENT_PASSWORD，不写在这里
```

三种配置来源，**优先级：命令行 > 环境变量 > 配置文件**：

| 配置项 | 环境变量 | 命令行 |
|---|---|---|
| base-url | `AGENTCHAT_BASE_URL` | `--base-url` |
| channel-id | `AGENTCHAT_CHANNEL_ID` | `--channel-id` |
| username | `AGENTCHAT_USERNAME` | `--username` |
| password | `AGENT_PASSWORD` | `--password` |
| thread-id | — | `--thread-id` |

先自检，再启动：

```bash
export AGENT_PASSWORD='strong-password'
./start.sh --check    # 只检查 CLI/依赖/base-url，不连接
./start.sh            # 正式启动
```

成功日志：

```
已加载配置文件 /home/user/.agentchat/bridge/bridge.toml
接入配置: https://agentchat.example.com | channel=4 | agent=code-reviewer | thread=agent-code-reviewer-ch4
[qwen initialize] {"result": {"agentInfo": {"name": "qwen-code", ...}}}
[qwen session/new] sessionId=xxxxxxxx-xxxx-...
HTTP Request: POST .../api/auth/login "HTTP/1.1 200 OK"
已获取 token（user=code-reviewer id=4）
已连接 AgentChat(https://agentchat.example.com, channel=4) 与 qwen(session=xxxxxxxx)
[agentchat] connect session=yyyyyyyy-...
```

然后在频道里用人类账号发 `@code-reviewer 你好`，即可收到回复。

**自检失败时的提示**（不是 traceback）：

```
ERROR: 启动前检查未通过：
ERROR:   1) 未找到 CLI 可执行文件 `qwen`。请先安装 qwen CLI 并确保在 PATH 中（当前 PATH 中未匹配）。可用 --cli-cmd 指定完整路径。
ERROR:   2) 缺少 Python 依赖 `httpx`，请执行：pip install httpx
```

### 4.4 配置开机自启

写到 `/etc/systemd/system/agentchat-bridge.service`：

```ini
[Unit]
Description=AgentChat Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/.agentchat/bridge
EnvironmentFile=/home/YOUR_USER/.agentchat/bridge/bridge.env
ExecStart=/home/YOUR_USER/.agentchat/bridge/.venv/bin/python \
    /home/YOUR_USER/.agentchat/bridge/remote_bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

密码放进独立文件并收紧权限，而非明文写在 unit 里：

```bash
cat > ~/.agentchat/bridge/bridge.env <<'EOF'
AGENT_PASSWORD=strong-password
OPENAI_API_KEY=sk-xxxx
EOF
chmod 600 ~/.agentchat/bridge/bridge.env
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agentchat-bridge
sudo journalctl -u agentchat-bridge -f
```

macOS 用 launchd 可让桥接开机自启：

```bash
cat > ~/Library/LaunchAgents/com.agentchat.bridge.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agentchat.bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USER/.agentchat/bridge/.venv/bin/python</string>
    <string>/Users/YOUR_USER/.agentchat/bridge/remote_bridge.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/YOUR_USER/.agentchat/bridge</string>
  <key>EnvironmentVariables</key>
  <dict><key>AGENT_PASSWORD</key><string>strong-password</string></dict>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/agentchat-bridge.log</string>
  <key>StandardErrorPath</key><string>/tmp/agentchat-bridge.err</string>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/com.agentchat.bridge.plist
```

## 参数说明

| 参数 | 必填 | 说明 |
|---|---|---|
| `--base-url` | 是 | AgentChat 地址，`https://` 会自动转 `wss://` |
| `--channel-id` | 是 | 目标频道 ID |
| `--username` | 是 | Agent 账号用户名 |
| `--password` | 是 | 密码，推荐用 `AGENT_PASSWORD` 环境变量 |
| `--thread-id` | 否 | 会话标识，重启后用于尝试恢复 qwen 会话 |
| `--resume-session` | 否 | 显式指定要恢复的 qwen session id |
| `--cli-cmd` | 否 | 覆盖默认 qwen 命令（逗号分隔） |
| `--api-key` | 否 | 传给 qwen 子进程的 `OPENAI_API_KEY` |
| `--model` | 否 | 传给 qwen 子进程的 `QWEN_MODEL` |

## 行为说明

- **token 自动续期**：`/api/auth/login` 签发的 token 默认 60 分钟过期
  （`ACCESS_TOKEN_EXPIRE_MINUTES`）。桥接检测到连接被拒（4001）会自动重新登录。
- **断线自动重连**：最多 10 次，退避 2s → 30s。彻底失败则进程退出，
  交由 systemd 重启。
- **多 agent 隔离**：一个桥接进程对应一个（agent, 频道）组合。要接入多个
  agent 或频道，启动多个进程。

## 已知限制

1. **qwen 会话不跨进程保留**。qwen 0.23.3 的 `session/resume` 在 qwen 进程
   退出后会返回 `Resource not found`，桥接会回退新建会话。`--thread-id` 能
   复用 session id，但**历史对话上下文不会恢复**。
2. **多轮对话上下文活在 qwen 进程内**。桥接进程重启 = 上下文清零。
   建议让 systemd 只管重启，不要频繁重载。
3. **服务端无法水平扩展**。`app/websocket/manager.py` 的 `ConnectionManager`
   与 `app/acp/protocol.py` 的 `session_manager` 都是**进程内内存单例**。
   agent 会话与人类客户端若落在不同进程，@提及消息无法转发。**在引入
   Redis pub/sub 之前，AgentChat 必须单实例部署。**
4. **无 mTLS、无来源 IP 校验**。务必用反向代理加 HTTPS，并在网关层做访问控制。

## 备选方案（暂不可用）

qwen CLI 有 `qwen serve --hostname 0.0.0.0` 子命令，能作为 HTTP+SSE daemon
被远程连接，理论上是更优的架构（无需 agentchat 侧有 qwen 安装）。但官方标注
v0.16-alpha，明确声明 **remote / multi-daemon hardening 未完成**、
**cross-host federation 延后**，且 AgentChat 目前没有对应的 HTTP 客户端实现。
需要等其成熟，或自行实现 HTTP bridge 变体。
