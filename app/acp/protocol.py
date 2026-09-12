"""
ACP (Agent Communication Protocol) Protocol Definitions
用于外部Agent(如Qwen Code、Pi Agent、OpenCode)通过Socket Mode连接
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field


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
        
        return session
    
    def get_session(self, session_id: str) -> Optional[AgentSession]:
        """获取会话"""
        return self.sessions.get(session_id)
    
    def get_sessions_by_agent(self, agent_id: int) -> List[AgentSession]:
        """获取Agent的所有会话"""
        return [self.sessions[sid] for sid in self.agent_sessions.get(agent_id, [])]
    
    def get_sessions_by_channel(self, channel_id: int) -> List[AgentSession]:
        """获取频道的所有Agent会话"""
        return [self.sessions[sid] for sid in self.channel_sessions.get(channel_id, [])]
    
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
        
        return session
    
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
