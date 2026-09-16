"""@提及 解析与向 agent 投递的统一入口。

背景：此前投递逻辑只存在于 WS 路径（app/websocket/endpoints.py 的
forward_mentions_to_agents），且只遍历**内存中的活跃会话**——agent 离线时
静默丢弃，连日志都没有；HTTP 的 POST /api/messages/ 更是完全不转发。

本模块把「谁被 @了」「该 agent 在线吗」「离线怎么办」收敛到一处，
让 WS 与 HTTP 两条路径行为一致，并引入待投递队列解决离线丢失。

投递策略：
  在线 → 实时推送（沿用原逻辑）
  离线 → 写入 pending_mentions，agent 上线时按序补发
"""
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import ChannelMember, Message, PendingMention, User

logger = logging.getLogger("AgentChat.Mention")

# 积压保护：单个 agent 最多保留多少条待投递，超出丢弃最旧的
MAX_PENDING_PER_AGENT = 50
# 超过该小时数的待投递视为过期（陈旧指令通常已无意义）
PENDING_EXPIRE_HOURS = 24


def extract_mention_body(content: str, agent_username: str) -> str:
    """取出 @agent 之后的正文。

    必须用 re.DOTALL：默认 `.` 不匹配换行，否则多行消息（带命令、代码、
    堆栈时很常见）只有第一行会传给 agent —— 这是曾经的真实缺陷。
    """
    if not content or not agent_username:
        return content or ""
    pattern = rf'@{re.escape(agent_username)}\s*(.*)'
    match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else content


def is_mentioned(content: str, agent_username: str) -> bool:
    """消息是否 @了该 agent。用 \\b 避免 @code-reviewer2 误匹配 @code-reviewer。"""
    if not content or not agent_username:
        return False
    return re.search(rf'@{re.escape(agent_username)}\b', content,
                     re.IGNORECASE) is not None


def find_mentioned_agents(db: Session, channel_id: int, content: str):
    """找出该频道里被 @到的 agent 账号（只依据频道成员关系，不看在不在线）。

    返回 [User]，顺序稳定（按 id）。
    """
    if not content or "@" not in content:
        return []
    agents = (
        db.query(User)
        .join(ChannelMember, ChannelMember.user_id == User.id)
        .filter(ChannelMember.channel_id == channel_id,
                User.is_agent.is_(True))
        .order_by(User.id)
        .all()
    )
    return [a for a in agents if is_mentioned(content, a.username)]


def build_input_payload(agent_username: str, session_id: str, content: str,
                        sender_id: int, sender_username: str,
                        sender_display_name: str) -> dict:
    """构造推送给 agent 的 input 帧（与既有协议一致）。"""
    return {
        "type": "input",
        "data": {
            "session_id": session_id,
            "message": {
                "content": extract_mention_body(content, agent_username),
                "sender_id": sender_id,
                "sender_username": sender_username,
                "sender_display_name": sender_display_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
    }


def _live_session_for(agent_id: int, channel_id: int):
    """取该 agent 在该频道上「当前可用」的会话；不可用返回 None。

    session_manager 延迟导入：app.acp.endpoints 会导入本模块做补发，
    而 app.acp 包导入时会加载 protocol（session_manager 的宿主），
    顶部直接 import 会形成循环。
    """
    from ..acp import session_manager
    for s in session_manager.get_sessions_by_channel(channel_id):
        if s.agent_id == agent_id and s.is_alive and s.websocket:
            return s
    return None


def enqueue(db: Session, message: Message, agent_id: int) -> None:
    """把一条无法即时投递的 @提及 写入待投递队列（幂等）。"""
    exists = db.query(PendingMention).filter(
        PendingMention.message_id == message.id,
        PendingMention.agent_id == agent_id,
    ).first()
    if exists:
        logger.debug("待投递已存在，跳过: msg=%s agent=%s", message.id, agent_id)
        return

    db.add(PendingMention(
        message_id=message.id,
        channel_id=message.channel_id,
        agent_id=agent_id,
        sender_id=message.sender_id,
        content=message.content,
        status="PENDING",
    ))
    db.flush()
    logger.info("[mention] agent %s 离线，消息 %s 已入队待投递",
                agent_id, message.id)

    _enforce_backlog_limit(db, agent_id)


def _enforce_backlog_limit(db: Session, agent_id: int) -> None:
    """积压超过上限时，把最旧的标记为 EXPIRED（而非直接删除，便于排查）。"""
    pending = (
        db.query(PendingMention)
        .filter(PendingMention.agent_id == agent_id,
                PendingMention.status == "PENDING")
        .order_by(PendingMention.created_at.asc(), PendingMention.id.asc())
        .all()
    )
    overflow = len(pending) - MAX_PENDING_PER_AGENT
    if overflow <= 0:
        return
    for row in pending[:overflow]:
        row.status = "EXPIRED"
        logger.warning("[mention] 待投递积压超上限(%s)，丢弃最旧: msg=%s agent=%s",
                       MAX_PENDING_PER_AGENT, row.message_id, agent_id)


async def dispatch_mentions(db: Session, message: Message, sender: User) -> dict:
    """消息落库后的统一投递入口（WS 与 HTTP 路径都调用这里）。

    返回统计信息，便于调用方回执/日志：
        {"mentioned": [agent_id...], "delivered": [...], "queued": [...]}
    """
    agents = find_mentioned_agents(db, message.channel_id, message.content or "")
    result = {"mentioned": [], "delivered": [], "queued": []}
    if not agents:
        return result

    for agent in agents:
        result["mentioned"].append(agent.id)
        session = _live_session_for(agent.id, message.channel_id)

        if session is None:
            enqueue(db, message, agent.id)
            result["queued"].append(agent.id)
            continue

        payload = build_input_payload(
            agent.username, session.session_id, message.content,
            sender.id, sender.username, sender.display_name or sender.username)
        try:
            await session.websocket.send_json(payload)
            result["delivered"].append(agent.id)
            logger.info("[mention] 已实时投递给 agent %s（消息 %s）",
                        agent.id, message.id)
        except Exception as e:
            # 推送失败：会话已不可用，转投队列，避免消息丢失
            logger.warning("[mention] 实时投递失败(%s)，转投队列: agent=%s msg=%s",
                           e, agent.id, message.id)
            session.is_alive = False
            enqueue(db, message, agent.id)
            result["queued"].append(agent.id)

    db.commit()
    return result


async def flush_pending_for_session(session) -> dict:
    """agent 上线后补发其待投递消息。

    逐条发送、每条之间短暂间隔：LLM 会话上下文按顺序构建，
    一次性灌入多条容易导致上下文错乱。
    """
    import asyncio

    db = SessionLocal()
    stats = {"delivered": 0, "failed": 0, "expired": 0}
    try:
        now = datetime.now(timezone.utc)
        rows = (
            db.query(PendingMention)
            .filter(PendingMention.agent_id == session.agent_id,
                    PendingMention.channel_id == session.channel_id,
                    PendingMention.status == "PENDING")
            .order_by(PendingMention.created_at.asc(), PendingMention.id.asc())
            .all()
        )
        if not rows:
            return stats

        logger.info("[mention] agent %s 上线，开始补发 %d 条待投递",
                    session.agent_id, len(rows))

        for row in rows:
            # 过期检查
            created = row.created_at
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_h = (now - created).total_seconds() / 3600
                if age_h > PENDING_EXPIRE_HOURS:
                    row.status = "EXPIRED"
                    stats["expired"] += 1
                    continue

            agent = db.query(User).filter(User.id == row.agent_id).first()
            sender = db.query(User).filter(User.id == row.sender_id).first()
            if agent is None or sender is None:
                row.status = "FAILED"
                stats["failed"] += 1
                continue

            payload = build_input_payload(
                agent.username, session.session_id, row.content,
                sender.id, sender.username, sender.display_name or sender.username)
            try:
                await session.websocket.send_json(payload)
                row.status = "DELIVERED"
                row.delivered_at = now
                row.attempts = (row.attempts or 0) + 1
                stats["delivered"] += 1
                logger.info("[mention] 补发成功: msg=%s agent=%s",
                            row.message_id, row.agent_id)
            except Exception as e:
                row.attempts = (row.attempts or 0) + 1
                if row.attempts >= 3:
                    row.status = "FAILED"
                    stats["failed"] += 1
                logger.warning("[mention] 补发失败(%s): msg=%s attempts=%s",
                               e, row.message_id, row.attempts)
                break   # 连接可能已断，停止后续补发

            db.commit()
            await asyncio.sleep(0.2)

        db.commit()
    finally:
        db.close()
    return stats


def pending_count(db: Session, agent_id: int) -> int:
    return db.query(PendingMention).filter(
        PendingMention.agent_id == agent_id,
        PendingMention.status == "PENDING",
    ).count()
