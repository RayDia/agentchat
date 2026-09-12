"""
ACP会话和消息模型 - 使用MySQL/TDSQL存储
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, func
from ..database import Base


class ACPBridgeSession(Base):
    """ACP CLI Bridge 会话表"""
    __tablename__ = "acp_sessions"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String(36), unique=True, index=True, nullable=False)
    cli_type = Column(String(50), nullable=False)  # qwen, pi, opencode
    thread_id = Column(String(100), index=True)  # AgentChat线程ID
    channel_id = Column(Integer, index=True)  # AgentChat频道ID
    process_state = Column(String(20), default="stopped")  # running, stopped
    extra_data = Column(Text, nullable=True)  # JSON字符串，存储额外信息
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "cli_type": self.cli_type,
            "thread_id": self.thread_id,
            "channel_id": self.channel_id,
            "process_state": self.process_state,
            "extra_data": self.extra_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class ACPBridgeMessage(Base):
    """ACP CLI Bridge 消息历史表"""
    __tablename__ = "acp_messages"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String(36), index=True, nullable=False)
    direction = Column(String(10), nullable=False)  # input, output
    content = Column(Text, nullable=False)
    extra_data = Column(Text, nullable=True)  # JSON字符串
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "direction": self.direction,
            "content": self.content,
            "extra_data": self.extra_data,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
