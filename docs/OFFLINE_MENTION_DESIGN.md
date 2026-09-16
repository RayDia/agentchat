# Agent 离线时 @提及 的处理设计

## 一、问题现状（已实证）

**结论：agent 离线时被 @ 的消息会被静默丢弃。**

实测过程（agent 未启动桥接器时，在频道 4 发 `@code-reviewer 离线测试：请回复我`）：

```
消息落库: id=288  sender_id=1  type=TEXT  '@code-reviewer 离线测试：请回复我'
服务端日志: 无任何关于该 @提及 的记录
agent 回复: 无
```

### 根因链路

| 环节 | 位置 | 问题 |
|---|---|---|
| 转发入口 | `app/websocket/endpoints.py:183` `forward_mentions_to_agents` | 只遍历**内存中**的会话 |
| 会话查找 | `app/acp/protocol.py:200` `get_sessions_by_channel` | 查内存字典 `channel_sessions`，agent 离线则为空 |
| 循环体 | `endpoints.py:189-221` | 列表为空 → 零次执行，**无日志、无标记、无入队** |
| 上线流程 | `app/acp/endpoints.py:147` `handle_connect` | 只建会话 + 广播，**不查不补**离线期间的消息 |

三个放大问题：

1. **会话是纯内存单例**（`protocol.py:250`）。服务端重启 → 全部会话消失 → 期间所有 @ 都无法投递。
2. **HTTP 兜底路径完全不过滤**：前端 WS 失败时会 fallback 到 `POST /api/messages/`
   （`frontend/src/components/ChatArea.jsx:192`），而 `app/api/messages.py:98-140`
   **只创建站内通知，不调用 `forward_mentions_to_agents`** —— 第二条丢失路径。
3. **无多实例能力**：会话状态在进程内存，服务端无法水平扩展（已记于已知限制）。

### 现有可复用的结构

- `messages` 表：消息已可靠落库（这是最重要的基础——**数据没丢，丢的是"投递意图"**）
- `channel_members` 表：可据此判断某 agent 是否应收到该频道的消息
- `users.is_agent`：识别 agent 账号
- `agent_tasks` 表：字段基本够用，但当前 **0 行**、且 `api/tasks.py:183` 留着
  `# TODO: Notify target agent via WebSocket` —— 是"建了没用"的半死表

---

## 二、方案对比

### 方案 A：待投递队列 + 上线补发（推荐）

**核心思路**：把「投递」变成有状态的动作。消息落库时，若目标 agent 不在线，
写一条**待投递记录**；agent 上线时按序补发。

```
人类发消息（含 @agent）
   ├─ 落库 messages                    ← 已有
   ├─ 广播给在线人类                    ← 已有
   └─ 对每个被 @ 的 agent：
        在线？→ 实时推送（现有路径）
        离线？→ 写入 pending_mentions 表   ← 新增
                              ↓
agent connect 时
   └─ 查询自己的 pending_mentions（按时间序）
        └─ 逐条推送 → 成功则标记 delivered
```

**新增表**（也可复用 `agent_tasks`，见下）：

```sql
CREATE TABLE pending_mentions (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    message_id   BIGINT NOT NULL,          -- 指向 messages.id
    channel_id   INT    NOT NULL,
    agent_id     INT    NOT NULL,          -- 目标 agent
    sender_id    INT    NOT NULL,
    content      TEXT   NOT NULL,          -- 冗余正文，避免联表
    status       ENUM('PENDING','DELIVERED','FAILED','EXPIRED') DEFAULT 'PENDING',
    attempts     INT    DEFAULT 0,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    delivered_at DATETIME NULL,
    KEY idx_agent_status (agent_id, status, created_at)
);
```

**关键设计点**：

| 点 | 决策 | 理由 |
|---|---|---|
| 投递粒度 | 逐条 agent × message | 一个频道可能有多个 agent，各自独立状态 |
| 幂等 | `(message_id, agent_id)` 唯一键 | 重复投递不产生重复回复 |
| 顺序 | 按 `created_at` 升序补发 | 保证对话上下文连贯 |
| 积压上限 | 每 agent 最多 N 条（如 50），超出丢弃最旧并告警 | 防止 agent 长期离线后上线瞬间被灌爆 |
| 过期 | 超过 T 小时（如 24h）标记 EXPIRED | 陈旧指令往往已无意义 |
| 补发节奏 | 逐条发送 + 等 agent 回复后再发下一条 | 避免 LLM 会话上下文错乱 |
| 失败处理 | `attempts` 计数，超阈值标 FAILED 并通知发送者 | 避免无限重试 |

**必须同时修的两个副作用**：

1. **HTTP 兜底路径也要转发**：把 `forward_mentions_to_agents` 的逻辑抽成独立函数，
   在 `api/messages.py:send_message` 里同样调用（走"在线推送 / 离线入队"分支）。
2. **`@提及` 检测要统一**：目前 WS 路径用正则匹配 `@username`，HTTP 路径没有。
   抽成公共模块，避免两条路径行为不一致。

**优点**：改动集中、数据不丢、语义清晰、可观测（能查询"有多少待投递"）。
**缺点**：agent 不在线时人类仍得不到即时反馈（需配合方案 D 提示）。

### 方案 B：仅持久化会话（最小改动）

把 `SessionManager` 的内存字典换成数据库表，让 agent 上线时能"恢复"会话。
**不够**：会话恢复 ≠ 消息补发。仍需记录"哪些消息未投递"，本质上是方案 A 的子集。
**结论**：作为方案 A 的配套（顺手把会话落库），不建议单独做。

### 方案 C：agent 侧主动拉取（不动服务端）

让桥接器在 connect 成功后，调用 `GET /api/messages/channel/{id}?since=last_seen`
拉取离线期间的消息，自己判断哪些是 @ 自己的。

**优点**：服务端零改动，桥接器已具备改造条件。
**缺点**：
- 需要桥接器维护 `last_seen` 游标（放哪？本地文件易丢）
- **HTTP 兜底那条路径的问题依然存在**
- 语义模糊：拉历史 ≠ 明确知道"我被指派了什么"
- 多 agent 场景下每个 agent 都要拉全量消息，浪费

**结论**：可作为**过渡方案**（改动小、当天可用），但长期应与方案 A 合流。

### 方案 D：人类侧即时反馈（必要补充，非替代）

即使做了 A/C，人类发完消息仍不知道 agent 会不会回。建议：

- 前端把"@了离线 agent"的消息标记为**待处理**状态（灰色时钟图标）
- 待 agent 回复后转为正常；超时未回复则显示警示
- 消息发送时若检测到被 @ 的 agent 离线，**即时提示发送者**
  （"code-reviewer 当前离线，消息已排队，上线后会处理"）

这需要服务端在广播消息时带上 `mentioned_agents: [{id, online}]`。

### 方案 E：消息队列/外部中间件（Redis Stream 等）

把投递交给 Redis Stream / RabbitMQ。

**优点**：天然支持重试、多消费者、水平扩展（顺便解决"服务端不能多实例"的限制）。
**缺点**：引入外部依赖，内网部署复杂度上升。
**结论**：如果只在**单机内网**跑，方案 A 的 MySQL 表足够；若未来要多实例，
再迁到 Redis（表结构可直接映射成 Stream）。

---

## 三、推荐落地路径

**分三期，每期都可独立上线**：

### 第一期：止血（半天）

1. 抽公共 `mention` 模块：统一的 @ 解析 + 目标 agent 判定
2. **让 HTTP 路径也转发**（修掉第二条丢失路径）
3. 离线时写日志 + 给发送者返回"agent 离线"提示（先不入队）
4. 前端展示"待处理"标记

> 收益：不再静默失败，人类能看到真实状态。风险极低。

### 第二期：队列与补发（1–2 天）

5. 建 `pending_mentions` 表
6. 落库后按「在线→推送 / 离线→入队」分流
7. `handle_connect` 时补发（逐条、等回复、更新状态）
8. 加过期与积压上限

> 收益：消息不再丢失，agent 上线即可处理积压。

### 第三期：健壮性（按需）

9. 把 `SessionManager` 会话落库，支持服务端重启
10. 清理死代码：`app/acp/manager.py`（`SocketManager` 全仓无引用）
11. 决定 `agent_tasks` 表去留（当前 0 行、转发是 TODO）——建议**复用为投递队列**
    或明确删除，避免"看起来有实际上没有"的误导
12. 若要水平扩展，投递层迁 Redis Stream

---

## 四、需要拍板的点

1. **积压上限与过期时间**：每 agent 最多补发多少条？多久算过期？
   （建议：50 条 / 24 小时）
2. **补发是否等回复**：逐条等回复（上下文好、慢）vs 一次性全发（快、可能混乱）。
   建议逐条。
3. **人类等待预期**：agent 离线时是否要阻断发送？建议**不阻断**，只提示。
4. **是否复用 `agent_tasks`**：它语义偏"agent 间协作任务"，而 @提及更像
   "人类给 agent 的指令"。两者混用可能造成语义混乱，建议**新建 `pending_mentions`**，
   `agent_tasks` 单独决定去留。

---

## 五、当前状态备忘

- 该问题**尚未修复**，代码未改动，本文件仅为设计提案。
- 实测证据：消息 `id=288` 在 agent 离线时被 @，落库但无任何投递动作。
- 相关已知限制：服务端无法水平扩展（`ConnectionManager` 与 `session_manager`
  均为进程内内存单例）。
