"""
Database Models for Agent Collaboration Platform
"""
from sqlalchemy import (Column, Integer, String, Text, DateTime, ForeignKey,
                        Boolean, Enum, UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import enum


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"
    AGENT = "agent"


class ChannelType(str, enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    DM = "dm"
    AGENT = "agent"  # Agent-to-Agent communication


class MessageType(str, enum.Enum):
    TEXT = "text"
    FILE = "file"
    AGENT_RESPONSE = "agent_response"
    SYSTEM = "system"
    COMMAND = "command"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(200))
    avatar_url = Column(String(500))
    role = Column(Enum(UserRole), default=UserRole.USER)
    is_active = Column(Boolean, default=True)
    is_agent = Column(Boolean, default=False)  # True if this is an agent
    agent_capabilities = Column(Text)  # JSON string of agent capabilities
    # User status fields
    status_emoji = Column(String(10))  # e.g., 🚀, 😴
    status_text = Column(String(100))  # Custom status text
    status_expires_at = Column(DateTime(timezone=True))  # Status expiration
    # Online status
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    channels = relationship("Channel", secondary="channel_members", back_populates="members")
    messages = relationship("Message", back_populates="sender", foreign_keys="Message.sender_id")
    tasks = relationship("Task", back_populates="creator", foreign_keys="Task.creator_id")
    notifications = relationship("Notification", back_populates="user")


class Channel(Base):
    __tablename__ = "channels"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    channel_type = Column(Enum(ChannelType), default=ChannelType.PUBLIC)
    created_by = Column(Integer, ForeignKey("users.id"))
    extra_data = Column(Text)  # JSON string for additional data (invite codes, etc.)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    members = relationship("User", secondary="channel_members", back_populates="channels")
    messages = relationship("Message", back_populates="channel")
    tasks = relationship("Task", back_populates="channel")


class ChannelMember(Base):
    __tablename__ = "channel_members"
    
    channel_id = Column(Integer, ForeignKey("channels.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    role = Column(String(50), default="member")
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    is_muted = Column(Boolean, default=False)
    
    # Relationships
    channel = relationship("Channel", backref="member_associations")
    user = relationship("User", backref="channel_associations")


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    message_type = Column(Enum(MessageType), default=MessageType.TEXT)
    reply_to_id = Column(Integer, ForeignKey("messages.id"))
    thread_id = Column(Integer)
    mentions = Column(Text)  # JSON array of mentioned user IDs
    extra_data = Column(Text)  # JSON string for additional data (renamed from metadata)
    is_pinned = Column(Boolean, default=False)  # 消息是否被固定
    pinned_at = Column(DateTime(timezone=True))  # 固定时间
    pinned_by = Column(Integer, ForeignKey("users.id"))  # 固定者
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_deleted = Column(Boolean, default=False)
    
    # Relationships
    channel = relationship("Channel", back_populates="messages")
    sender = relationship("User", back_populates="messages", foreign_keys=[sender_id])
    replies = relationship("Message", backref="parent_message", remote_side=[id])


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    channel_id = Column(Integer, ForeignKey("channels.id"))
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assignee_id = Column(Integer, ForeignKey("users.id"))  # Can be user or agent
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    priority = Column(Integer, default=0)  # 0: low, 1: medium, 2: high
    due_date = Column(DateTime(timezone=True))
    result = Column(Text)  # Task result or output
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    channel = relationship("Channel", back_populates="tasks")
    creator = relationship("User", foreign_keys=[creator_id])
    assignee = relationship("User", foreign_keys=[assignee_id])


class AgentTask(Base):
    """
    Detailed task information for agent-to-agent collaboration
    """
    __tablename__ = "agent_tasks"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    parent_task_id = Column(Integer, ForeignKey("tasks.id"))
    source_agent_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_agent_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    task_type = Column(String(100))  # e.g., "code_review", "data_analysis", "generation"
    input_data = Column(Text)  # JSON input for the task
    output_data = Column(Text)  # JSON output from the task
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    parent_task = relationship("Task")
    source_agent = relationship("User", foreign_keys=[source_agent_id])
    target_agent = relationship("User", foreign_keys=[target_agent_id])


class Notification(Base):
    """
    User notifications
    """
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String(50), nullable=False)  # mention, task, message, etc.
    title = Column(String(200), nullable=False)
    content = Column(Text)
    link = Column(String(500))
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="notifications")


class PendingMentionStatus(str, enum.Enum):
    """待投递消息的状态"""
    PENDING = "PENDING"       # 等待 agent 上线
    DELIVERED = "DELIVERED"   # 已投递
    FAILED = "FAILED"         # 重试超限
    EXPIRED = "EXPIRED"       # 超过保留时长或积压超限被丢弃


class PendingMention(Base):
    """
    待投递的 @提及 消息队列。

    agent 离线时被 @ 的消息此前会被静默丢弃（forward_mentions 只遍历内存中
    的活跃会话）。本表把「投递意图」持久化：消息本身仍在 messages 表，
    这里只记录「哪个 agent 还没收到哪条消息」，agent 上线后按序补发。
    """
    __tablename__ = "pending_mentions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # 冗余正文：补发时无需回查 messages，也避免原消息被编辑后语义漂移
    content = Column(Text, nullable=False)
    status = Column(Enum(PendingMentionStatus),
                    default=PendingMentionStatus.PENDING, nullable=False)
    attempts = Column(Integer, default=0)
    delivered_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 幂等：同一条消息对同一个 agent 只应入队一次
    __table_args__ = (
        UniqueConstraint("message_id", "agent_id",
                         name="uq_pending_mention_msg_agent"),
        Index("ix_pending_agent_status", "agent_id", "status", "created_at"),
    )

    message = relationship("Message")
    agent = relationship("User", foreign_keys=[agent_id])
    sender = relationship("User", foreign_keys=[sender_id])


class ACPSessionRecord(Base):
    """
    ACP agent 会话的持久化记录。

    此前 SessionManager 把会话放在纯内存字典里，服务端一重启会话全丢：
    - agent 的"在线"状态无法跨重启判断
    - 客户端 resume（--resume-session）失败，只能重建会话
    本表保存会话元数据；websocket 连接本身无法持久化，重启后 is_alive
    一律按 False 恢复（需 agent 重新 connect）。

    注意表名用 acp_agent_sessions 而非 acp_sessions：后者已被 app/acp/models.py
    的 ACPBridgeSession（旧 CLI bridge，已成死代码但表里有历史数据）占用，
    且字段结构不同，不能共用。
    """
    __tablename__ = "acp_agent_sessions"

    session_id = Column(String(64), primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    agent_username = Column(String(100), nullable=False)
    agent_display_name = Column(String(100))
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    channel_name = Column(String(100))
    connected_at = Column(DateTime(timezone=True), default=func.now())
    last_heartbeat = Column(DateTime(timezone=True), default=func.now())
    is_alive = Column(Boolean, default=True)
