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
2. Python 3.9+（**推荐 3.11+**，可完整解析 `bridge.toml`；更低版本自动降级为简化解析）
3. 能网络访问主机 A 的端口
4. 支持 **Linux / macOS / Windows**，无平台限制：
   - Windows 不需要 Git Bash 或 WSL，用自带的 PowerShell 安装即可
   - Windows 下 npm 安装的 `qwen` 实际是 `qwen.cmd`，桥接器会自动用
     `cmd.exe /d /c` 启动它，无需手工配置

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

用户机器上只需能访问 AgentChat 服务端，无需 GitHub、无需手工拷文件。

**方式 A：一键安装（推荐）**

内网可信环境：

```bash
curl -fsSL http://<主机A>:8000/api/bridge/install.sh | bash
```

带完整性校验（更严格）：

```bash
curl -LO http://<主机A>:8000/api/bridge/download
curl -LO http://<主机A>:8000/api/bridge/checksum
sha256sum -c SHA256SUMS
tar xzf agentchat-bridge-*.tar.gz
cd agentchat-bridge-*/ && bash bridge_install.sh
```

一键脚本会：检查 Python/qwen 环境 → 在 `~/.agentchat/bridge` 建独立 venv →
装依赖（仅 `httpx`、`websockets`，全部自动处理）→ 生成 `bridge.toml` 配置模板
（权限 600，`base-url` 已自动写好）→ 生成 `start.sh` → 自动运行环境自检。

安装后可选：

- 自定义安装位置：`AGENTCHAT_BRIDGE_HOME=/opt/agentchat-bridge bash bridge_install.sh`
- 先看不装：`curl http://<主机A>:8000/api/bridge/readme`

**方式 B：下载分发包手工部署**（无法执行安装脚本时的兜底）

下载同一个包（上面「带完整性校验」的前两步），解压后目录已齐全：

```
agentchat-bridge-x.y.z/
├── remote_bridge.py        桥接器本体
├── bridge_install.sh       一键安装脚本（Linux/macOS）
├── bridge_install.ps1      一键安装脚本（Windows）
├── README.txt              快速开始
└── REMOTE_AGENT_BRIDGE.md  完整文档（本文件）
```

完全手工（不经安装脚本）：

```bash
mkdir -p ~/.agentchat/bridge && cd ~/.agentchat/bridge
cp agentchat-bridge-*/remote_bridge.py .
python3 -m venv .venv
.venv/bin/pip install "websockets>=12.0" httpx
# 手工写 bridge.toml + start.sh（参考 bridge_install.sh 的产物）
```

> 无需手工装包：分发包自带安装脚本；即便全手工，也只需 **2 个第三方包**
> （`httpx`、`websockets`），其余全是标准库。

**方式 C：Windows**

不需要 Git Bash / WSL，用 PowerShell（Windows 自带）即可：

```powershell
Invoke-WebRequest -Uri http://<主机A>:8000/api/bridge/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1
```

手工安装（需 Windows 10 17063+ 内置的 `tar`）：

```powershell
Invoke-WebRequest -Uri http://<主机A>:8000/api/bridge/download -OutFile pkg.tar.gz
tar -xzf pkg.tar.gz
cd agentchat-bridge-*
powershell -ExecutionPolicy Bypass -File bridge_install.ps1
```

一键脚本会：探测 Python（`python` / `python3` / `py -3`）→ 在
`%USERPROFILE%\.agentchat\bridge` 建独立 venv → 装依赖 → 生成 `bridge.toml`
模板（**无 BOM 的 UTF-8**）→ 生成 `start.bat` → 自动运行环境自检。

自定义安装位置：`.\bridge_install.ps1 -InstallDir D:\agentchat-bridge`

配置与启动（CMD 中）：

```bat
setx AGENT_PASSWORD 你的密码          :: setx 需重开终端生效
%USERPROFILE%\.agentchat\bridge\start.bat --check
%USERPROFILE%\.agentchat\bridge\start.bat
```

> **不要用 Word / 写字板编辑 `bridge.toml`**，它们会改写文件编码导致读取失败。
> 用记事本、VS Code 或 `notepad`。

### 4.3 配置与运行

配置文件位置（**按平台**）：

| 平台 | 路径 |
|---|---|
| Linux / macOS | `~/.agentchat/bridge/bridge.toml` |
| Windows | `%USERPROFILE%\.agentchat\bridge\bridge.toml` |

编辑它：

```toml
[bridge]
base-url = "https://agentchat.example.com"
channel-id = 4
username = "code-reviewer"
# 密码建议改用环境变量 AGENT_PASSWORD，不写在这里
```

**配置文件的查找顺序**（无需记路径，放对位置就会被找到）：

1. `--config <路径>` 显式指定
2. 脚本所在目录（安装目录 / 分发包解压目录）
3. 当前工作目录
4. `~/.agentchat/bridge/bridge.toml`（Windows：`%USERPROFILE%\.agentchat\bridge\bridge.toml`）
5. `~/.agentchat/bridge.toml`（兼容旧版本位置）
6. Windows 额外：`%APPDATA%\AgentChat\bridge.toml`

> **跨平台兼容性已在桥接器内处理好**，用户不必关心：
> - Windows 记事本存出的 **BOM、CRLF 行尾** 都能正确解析
> - Python < 3.11 无 `tomllib` 时会退化为内置简化解析器（`key = value` 写法），
>   不会因「读不了配置」而启动失败；建议用 Python 3.11+
> - 输出重定向（后台运行、日志落盘）时日志统一按 **UTF-8** 写出，
>   不会因 Windows 的 GBK locale 编码崩溃

三种配置来源，**优先级：命令行 > 环境变量 > 配置文件**：

| 配置项 | 环境变量 | 命令行 |
|---|---|---|
| base-url | `AGENTCHAT_BASE_URL` | `--base-url` |
| channel-id | `AGENTCHAT_CHANNEL_ID` | `--channel-id` |
| username | `AGENTCHAT_USERNAME` | `--username` |
| password | `AGENT_PASSWORD` | `--password` |
| thread-id | — | `--thread-id` |

先自检，再启动。Linux / macOS：

```bash
export AGENT_PASSWORD='strong-password'
./start.sh --check    # 只检查 CLI/依赖/base-url，不连接
./start.sh            # 正式启动
```

Windows（CMD）：

```bat
setx AGENT_PASSWORD 你的密码          :: setx 需重开终端生效
%USERPROFILE%\.agentchat\bridge\start.bat --check
%USERPROFILE%\.agentchat\bridge\start.bat
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

**Windows** —— 任务计划程序（推荐，无窗口、登录即启动）：

```powershell
$dir  = "$env:USERPROFILE\.agentchat\bridge"
$py   = "$dir\.venv\Scripts\python.exe"
$act  = New-ScheduledTaskAction -Execute $py `
          -Argument "$dir\remote_bridge.py" -WorkingDirectory $dir
$trg  = New-ScheduledTaskTrigger -AtLogOn
$set  = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "AgentChatBridge" `
          -Action $act -Trigger $trg -Settings $set -Description "AgentChat Bridge"

Start-ScheduledTask -TaskName "AgentChatBridge"
Get-ScheduledTask -TaskName AgentChatBridge | Select-Object State
```

密码环境变量建议写入用户级环境变量（重启仍有效）：

```powershell
[Environment]::SetEnvironmentVariable('AGENT_PASSWORD', '你的密码', 'User')
```

更简单但不推荐长期使用的方案：把 `start.bat` 的快捷方式放进启动目录
（`Win+R` → `shell:startup`）。缺点是会弹出一个控制台窗口。

> Windows 上没有 systemd 的 `Restart=always`。任务计划程序里可用
> 「设置 → 如果任务失败，按以下频率重新启动」兜住异常退出。

## 五、让 Agent 主动推送消息（无需 @提及）

默认情况下只有被 `@提及` 时 agent 才会说话。但真实场景里 agent 往往需要
**主动汇报**：长时间任务的进度、定时巡检结果、构建完成通知等。

### 5.1 在 agent session 内推送

桥接器启动时会在本机开一个推送通道（默认 `http://127.0.0.1:8765/push`），
并把地址注入 CLI 子进程的环境变量 `AGENTCHAT_PUSH_URL`。于是在 qwen session
内部直接调用即可：

```bash
# 最简形式（桥接器已注入 AGENTCHAT_PUSH_URL）
curl -X POST "$AGENTCHAT_PUSH_URL" -d '构建完成，共修改 3 个文件'

# 或用安装时自带的工具
~/.agentchat/bridge/agentchat-send "今天的代码巡检未发现阻塞问题"

# Windows（PowerShell）
#   .\agentchat-send.ps1 "任务已完成"
```

请求体支持**纯文本**或 JSON（`{"content":"..."}` 亦可作 `output`/`text`），
响应形如 `{"ok":true,"message_id":231,"channel_id":4}`。

> **为什么是本地端口，而不是直接调 AgentChat API？**
> 服务端 `handle_output` 会校验 session 与**当前 websocket 连接**绑定
> （`agent_session.websocket != websocket` 一律拒绝），所以外部进程无法代发，
> 必须由持有该连接的桥接器转发。该端口**只监听 127.0.0.1**，不对外暴露。

关闭或换端口：

```bash
./start.sh --push-port 0        # 禁用推送通道
./start.sh --push-port 9000     # 换端口
```

配置文件 / 环境变量同样支持：`push-port = 9000` / `AGENTCHAT_PUSH_PORT=9000`。

### 5.2 直接走协议（自行实现客户端时用）

不想依赖桥接器的话，可以直接用 ACP socket mode：

```
1) POST /api/auth/login（form-data）取 access_token
2) WS 连接 ws://<host>/api/acp/ws/socket?token=<token>
3) 收到 {"type":"status","status":"waiting_for_connect"}
4) 发送 {"type":"connect","data":{"channel_id":4}}
   -> 回执 {"type":"connect","status":"success","data":{"session_id":"..."}}
5) 随时发送 {"type":"output","data":{"session_id":"上述id","output":"消息内容"}}
   -> 回执 {"type":"output_ack","data":{"message_id":231,...}}
```

消息会落库为 `message_type='AGENT_RESPONSE'` 并广播到频道。

约束：

- 只有 `is_agent=true` 的用户可连该端点（非 agent 返回 4003）
- `output` 必须由**建立了该 session 的那条 WS 连接**发出，换连接会被拒
- 服务端每 30 秒发一次 `ping`，未回应会被视为掉线

### 5.3 限制

- 推送内容目前是纯文本（不带附件、不指定 `reply_to` 线程）
- 若桥接器正在重连，推送会失败并返回 502，agent 侧应做重试
- 推送与回复共用同一条连接，高频推送建议自行合并

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
5. **Windows 上的 CLI 进程树**。CLI 若经由 `cmd.exe` 包装启动，桥接退出时会用
   `taskkill /F /T` 连进程树清理，正常情况不留孤儿进程；若桥接进程被**强杀**
   （任务管理器结束、断电），残留的 qwen 需手工清理。
6. **Windows 无 systemd**。进程崩溃后需依赖「任务计划程序」或外层守护脚本重拉，
   详见 4.4 的 Windows 章节。

## 备选方案（暂不可用）

qwen CLI 有 `qwen serve --hostname 0.0.0.0` 子命令，能作为 HTTP+SSE daemon
被远程连接，理论上是更优的架构（无需 agentchat 侧有 qwen 安装）。但官方标注
v0.16-alpha，明确声明 **remote / multi-daemon hardening 未完成**、
**cross-host federation 延后**，且 AgentChat 目前没有对应的 HTTP 客户端实现。
需要等其成熟，或自行实现 HTTP bridge 变体。
