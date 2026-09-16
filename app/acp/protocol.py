"""
ACP (Agent Communication Protocol) Protocol Definitions
用于外部Agent(如Qwen Code、Pi Agent、OpenCode)通过Socket Mode连接
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger("AgentChat.ACP")


@dataclass
class AgentSession:
    """Agent会话 - 将外部Agent连接到指定频道"""
    session_id: str
    agent_id: int
    agent_username: str
    agent_display_name: str
    channel_id: int
    channel_name: str
    websocket: Any = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_alive: bool = True
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "agent_username": self.agent_username,
            "agent_display_name": self.agent_display_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "connected_at": self.connected_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "is_alive": self.is_alive
        }


class ACPProtocol:
    """
    Agent Communication Protocol (ACP)
    
    消息类型:
    - connect: Agent连接并注册会话
    - output: Agent向频道发送消息
    - input: 从频道转发给Agent的消息(@提及)
    - ping: 心跳检测
    - pong: 心跳响应
    - error: 错误通知
    - status: 状态更新
    """
    
    # 消息类型常量
    TYPE_CONNECT = "connect"
    TYPE_OUTPUT = "output"
    TYPE_INPUT = "input"
    TYPE_PING = "ping"
    TYPE_PONG = "pong"
    TYPE_ERROR = "error"
    TYPE_STATUS = "status"
    
    @staticmethod
    def create_connect_message(agent_id: int, session_id: str, channel_id: int, channel_name: str) -> dict:
        """创建连接成功消息"""
        return {
            "type": "connect",
            "status": "success",
            "data": {
                "session_id": session_id,
                "agent_id": agent_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "connected_at": datetime.now(timezone.utc).isoformat()
            }
        }
    
    @staticmethod
    def create_error_message(error_code: str, message: str, details: Optional[dict] = None) -> dict:
        """创建错误消息"""
        result = {
            "type": "error",
            "code": error_code,
            "message": message
        }
        if details:
            result["details"] = details
        return result
    
    @staticmethod
    def create_input_message(session_id: str, content: str, sender_id: int, sender_username: str, sender_display_name: str) -> dict:
        """创建输入消息(@提及转发)"""
        return {
            "type": "input",
            "data": {
                "session_id": session_id,
                "message": {
                    "content": content,
                    "sender_id": sender_id,
                    "sender_username": sender_username,
                    "sender_display_name": sender_display_name,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }
        }
    
    @staticmethod
    def create_output_message(output: str, session_id: str) -> dict:
        """创建输出消息格式验证"""
        return {
            "type": "output",
            "data": {
                "session_id": session_id,
                "output": output
            }
        }
    
    @staticmethod
    def create_ping_message() -> dict:
        """创建心跳消息"""
        return {
            "type": "ping",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    @staticmethod
    def create_pong_message(timestamp: str) -> dict:
        """创建心跳响应"""
        return {
            "type": "pong",
            "timestamp": timestamp
        }
    
    @staticmethod
    def create_status_message(status: str, details: Optional[dict] = None) -> dict:
        """创建状态消息"""
        result = {
            "type": "status",
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if details:
            result["data"] = details
        return result


class SessionManager:
    """管理所有活跃的Agent会话"""
    
    def __init__(self):
        self.sessions: Dict[str, AgentSession] = {}
        self.agent_sessions: Dict[int, List[str]] = {}  # agent_id -> [session_ids]
        self.channel_sessions: Dict[int, List[str]] = {}  # channel_id -> [session_ids]
    
    def create_session(
        self,
        agent_id: int,
        agent_username: str,
        agent_display_name: str,
        channel_id: int,
        channel_name: str,
        websocket
    ) -> AgentSession:
        """创建新的Agent会话"""
        session_id = str(uuid.uuid4())
        session = AgentSession(
            session_id=session_id,
            agent_id=agent_id,
            agent_username=agent_username,
            agent_display_name=agent_display_name,
            channel_id=channel_id,
            channel_name=channel_name,
            websocket=websocket
        )
        
        # 存储会话
        self.sessions[session_id] = session
        
        # 按agent_id索引
        if agent_id not in self.agent_sessions:
            self.agent_sessions[agent_id] = []
        self.agent_sessions[agent_id].append(session_id)
        
        # 按channel_id索引
        if channel_id not in self.channel_sessions:
            self.channel_sessions[channel_id] = []
        self.channel_sessions[channel_id].append(session_id)

        self._persist(session)
        return session

    # ---------------- 持久化（服务端重启后可恢复会话元数据）----------------

    def _persist(self, session: AgentSession) -> None:
        """把会话写入 acp_sessions 表。

        失败只告警不抛出：持久化是增强能力，不应让 agent 连不上。
        """
        try:
            from ..database import SessionLocal
            from ..models import ACPSessionRecord
            db = SessionLocal()
            try:
                row = db.query(ACPSessionRecord).filter(
                    ACPSessionRecord.session_id == session.session_id).first()
                if row is None:
                    row = ACPSessionRecord(session_id=session.session_id)
                    db.add(row)
                row.agent_id = session.agent_id
                row.agent_username = session.agent_username
                row.agent_display_name = session.agent_display_name
                row.channel_id = session.channel_id
                row.channel_name = session.channel_name
                row.connected_at = session.connected_at
                row.last_heartbeat = session.last_heartbeat
                row.is_alive = bool(session.is_alive and session.websocket)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("会话持久化失败(%s): %s", session.session_id, e)

    def load_persisted_sessions(self) -> int:
        """启动时把历史会话载入内存（is_alive=False，等待 agent 重连）。

        这样 agent 用 --resume-session 重连时能匹配到原会话，
        而不是被迫新建；同时保留会话历史便于排查。
        """
        count = 0
        try:
            from ..database import SessionLocal
            from ..models import ACPSessionRecord
            db = SessionLocal()
            try:
                # 上次进程退出时不可能还持有连接，先全部置为离线
                db.query(ACPSessionRecord).filter(
                    ACPSessionRecord.is_alive.is_(True)
                ).update({ACPSessionRecord.is_alive: False},
                         synchronize_session=False)
                db.commit()

                for row in db.query(ACPSessionRecord).all():
                    if row.session_id in self.sessions:
                        continue
                    session = AgentSession(
                        session_id=row.session_id,
                        agent_id=row.agent_id,
                        agent_username=row.agent_username,
                        agent_display_name=row.agent_display_name or row.agent_username,
                        channel_id=row.channel_id,
                        channel_name=row.channel_name or "",
                        websocket=None,           # 连接无法持久化
                        connected_at=row.connected_at or datetime.now(timezone.utc),
                        last_heartbeat=row.last_heartbeat or datetime.now(timezone.utc),
                        is_alive=False,
                    )
                    self.sessions[session.session_id] = session
                    self.agent_sessions.setdefault(session.agent_id, []).append(
                        session.session_id)
                    self.channel_sessions.setdefault(session.channel_id, []).append(
                        session.session_id)
                    count += 1
            finally:
                db.close()
        except Exception as e:
            logger.warning("载入历史会话失败: %s", e)
        return count
    
    def get_session(self, session_id: str) -> Optional[AgentSession]:
        """获取会话"""
        return self.sessions.get(session_id)
    
    def get_sessions_by_agent(self, agent_id: int) -> List[AgentSession]:
        """获取Agent的所有会话"""
        return [self.sessions[sid] for sid in self.agent_sessions.get(agent_id, [])]
    
    def get_sessions_by_channel(self, channel_id: int) -> List[AgentSession]:
        """获取频道的所有Agent会话"""
        return [self.sessions[sid] for sid in self.channel_sessions.get(channel_id, [])]

    def get_online_agent_ids(self, channel_id: Optional[int] = None) -> set:
        """返回当前在线的 agent id 集合（有存活会话且有 websocket）。

        注意：判定 agent 在线必须用本 manager（ACP socket mode 的会话），
        不能用 websocket/manager.py 的 active_connections —— 那是浏览器
        聊天连接，agent 根本不走那条路，混用会导致状态恒为离线。
        """
        result = set()
        for session in self.sessions.values():
            if not session.is_alive or not session.websocket:
                continue
            if channel_id is not None and session.channel_id != channel_id:
                continue
            result.add(session.agent_id)
        return result
    
    def remove_session(self, session_id: str) -> Optional[AgentSession]:
        """移除会话"""
        session = self.sessions.pop(session_id, None)
        if session:
            # 从索引中移除
            if session.agent_id in self.agent_sessions:
                self.agent_sessions[session.agent_id].remove(session_id)
                if not self.agent_sessions[session.agent_id]:
                    del self.agent_sessions[session.agent_id]
            
            if session.channel_id in self.channel_sessions:
                self.channel_sessions[session.channel_id].remove(session_id)
                if not self.channel_sessions[session.channel_id]:
                    del self.channel_sessions[session.channel_id]

            self._mark_offline(session_id)

        return session

    def _mark_offline(self, session_id: str) -> None:
        """把数据库里的会话记录标为离线（保留记录以便 resume）。"""
        try:
            from ..database import SessionLocal
            from ..models import ACPSessionRecord
            db = SessionLocal()
            try:
                db.query(ACPSessionRecord).filter(
                    ACPSessionRecord.session_id == session_id
                ).update({ACPSessionRecord.is_alive: False},
                         synchronize_session=False)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("标记会话离线失败(%s): %s", session_id, e)
    
    def update_heartbeat(self, session_id: str):
        """更新心跳时间"""
        session = self.sessions.get(session_id)
        if session:
            session.last_heartbeat = datetime.now(timezone.utc)
            session.is_alive = True
    
    def mark_dead(self, session_id: str):
        """标记会话死亡"""
        session = self.sessions.get(session_id)
        if session:
            session.is_alive = False
    
    def get_active_sessions(self) -> List[AgentSession]:
        """获取所有活跃会话"""
        return [s for s in self.sessions.values() if s.is_alive]
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_sessions": len(self.sessions),
            "active_sessions": len([s for s in self.sessions.values() if s.is_alive]),
            "total_agents": len(self.agent_sessions),
            "total_channels": len(self.channel_sessions),
            "sessions": [s.to_dict() for s in self.get_active_sessions()]
        }


# 全局会话管理器
session_manager = SessionManager()
