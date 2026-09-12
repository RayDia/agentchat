"""
ACP Socket Mode Manager
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio


@dataclass
class AgentConnection:
    """Agent WebSocket连接"""
    session_id: str
    agent_id: int
    channel_id: int
    websocket: Any = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_ping: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_alive: bool = True
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "channel_id": self.channel_id,
            "connected_at": self.connected_at.isoformat(),
            "last_ping": self.last_ping.isoformat(),
            "is_alive": self.is_alive
        }


class SocketManager:
    """管理ACP Socket Mode连接"""
    
    def __init__(self):
        self.connections: Dict[str, AgentConnection] = {}
        self.agent_connections: Dict[int, List[str]] = {}
        self.channel_connections: Dict[int, List[str]] = {}
    
    def add_connection(
        self,
        agent_id: int,
        channel_id: int,
        websocket,
        session_id: str = None
    ) -> AgentConnection:
        """添加Agent连接"""
        if not session_id:
            import uuid
            session_id = str(uuid.uuid4())
        
        conn = AgentConnection(
            session_id=session_id,
            agent_id=agent_id,
            channel_id=channel_id,
            websocket=websocket
        )
        
        self.connections[session_id] = conn
        
        if agent_id not in self.agent_connections:
            self.agent_connections[agent_id] = []
        self.agent_connections[agent_id].append(session_id)
        
        if channel_id not in self.channel_connections:
            self.channel_connections[channel_id] = []
        self.channel_connections[channel_id].append(session_id)
        
        return conn
    
    def remove_connection(self, session_id: str) -> Optional[AgentConnection]:
        """移除Agent连接"""
        conn = self.connections.pop(session_id, None)
        if conn:
            if conn.agent_id in self.agent_connections:
                if session_id in self.agent_connections[conn.agent_id]:
                    self.agent_connections[conn.agent_id].remove(session_id)
            
            if conn.channel_id in self.channel_connections:
                if session_id in self.channel_connections[conn.channel_id]:
                    self.channel_connections[conn.channel_id].remove(session_id)
        
        return conn
    
    def get_connection(self, session_id: str) -> Optional[AgentConnection]:
        """获取连接"""
        return self.connections.get(session_id)
    
    def get_connections_by_agent(self, agent_id: int) -> List[AgentConnection]:
        """获取Agent的所有连接"""
        return [self.connections[sid] for sid in self.agent_connections.get(agent_id, [])]
    
    def get_connections_by_channel(self, channel_id: int) -> List[AgentConnection]:
        """获取频道的所有连接"""
        return [self.connections[sid] for sid in self.channel_connections.get(channel_id, [])]
    
    def send_to_agent(self, session_id: str, message: dict) -> bool:
        """发送消息到指定Agent"""
        conn = self.connections.get(session_id)
        if conn and conn.websocket:
            try:
                asyncio.create_task(conn.websocket.send_json(message))
                return True
            except:
                conn.is_alive = False
                return False
        return False
    
    def send_to_channel(self, channel_id: int, message: dict) -> int:
        """发送消息到频道的所有Agent"""
        count = 0
        for sid in self.channel_connections.get(channel_id, []):
            if self.send_to_agent(sid, message):
                count += 1
        return count
    
    def ping_all(self):
        """向所有连接发送心跳"""
        import json
        for conn in self.connections.values():
            if conn.is_alive and conn.websocket:
                try:
                    asyncio.create_task(conn.websocket.send_json({
                        "type": "ping",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }))
                except:
                    conn.is_alive = False
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_connections": len(self.connections),
            "active_connections": len([c for c in self.connections.values() if c.is_alive]),
            "total_agents": len(self.agent_connections),
            "total_channels": len(self.channel_connections),
            "connections": [c.to_dict() for c in self.connections.values() if c.is_alive]
        }
    
    def cleanup_dead_connections(self):
        """清理死连接"""
        dead_sessions = [sid for sid, conn in self.connections.items() if not conn.is_alive]
        for sid in dead_sessions:
            self.remove_connection(sid)
        return len(dead_sessions)


# 全局管理器实例
manager = SocketManager()
