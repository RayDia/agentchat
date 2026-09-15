"""
Pydantic Schemas for Request/Response Validation
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum


# ============ User Schemas ============
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: Optional[str] = None
    display_name: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)
    role: str = "user"


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    email: Optional[str] = None


class UserResponse(UserBase):
    id: int
    role: str
    is_active: bool
    is_agent: bool
    agent_capabilities: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class AgentRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)
    display_name: str
    capabilities: List[str]  # List of agent capabilities
    description: Optional[str] = None


class PasswordChange(BaseModel):
    """改密请求。

    old_password 可选：本人凭 token 改自己密码、且已是凭 token 认证的情况下
    可不填；管理端代改他人密码时也不需要。若提供则会被校验。
    """
    new_password: str = Field(..., min_length=6)
    old_password: Optional[str] = None


# ============ Token Schemas ============
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenData(BaseModel):
    username: Optional[str] = None
    user_id: Optional[int] = None


# ============ Workspace Schemas ============
class WorkspaceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceResponse(WorkspaceBase):
    id: int
    owner_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ Channel Schemas ============
class ChannelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    channel_type: str = "public"


class ChannelCreate(ChannelBase):
    workspace_id: Optional[int] = None
    member_ids: Optional[List[int]] = []


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ChannelResponse(ChannelBase):
    id: int
    workspace_id: Optional[int]
    created_by: Optional[int]
    created_at: datetime
    member_count: Optional[int] = 0
    
    class Config:
        from_attributes = True


class ChannelWithMembers(ChannelResponse):
    members: List[UserResponse] = []


# ============ Message Schemas ============
class MessageBase(BaseModel):
    content: str = Field(..., min_length=1)
    message_type: str = "text"
    reply_to_id: Optional[int] = None
    mentions: Optional[List[int]] = []


class MessageCreate(MessageBase):
    channel_id: int
    metadata: Optional[dict] = None  # 用于API输入


class MessageResponse(MessageBase):
    id: int
    channel_id: int
    sender_id: int
    sender: Optional[UserResponse] = None
    thread_id: Optional[int] = None
    is_pinned: Optional[bool] = False
    pinned_at: Optional[datetime] = None
    pinned_by: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime]
    is_deleted: bool
    extra_data: Optional[str] = None
    
    class Config:
        from_attributes = True


class MessageThread(BaseModel):
    parent: MessageResponse
    replies: List[MessageResponse] = []


# ============ Task Schemas ============
class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    priority: int = Field(0, ge=0, le=2)
    due_date: Optional[datetime] = None


class TaskCreate(TaskBase):
    channel_id: Optional[int] = None
    assignee_id: Optional[int] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    assignee_id: Optional[int] = None
    priority: Optional[int] = None
    result: Optional[str] = None


class TaskResponse(TaskBase):
    id: int
    channel_id: Optional[int]
    creator_id: int
    assignee_id: Optional[int]
    status: str
    result: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# ============ Agent Task Schemas ============
class AgentTaskCreate(BaseModel):
    target_agent_id: int
    task_type: str
    input_data: dict
    parent_task_id: Optional[int] = None


class AgentTaskResponse(BaseModel):
    id: int
    parent_task_id: Optional[int]
    source_agent_id: int
    target_agent_id: int
    task_type: str
    input_data: Optional[dict]
    output_data: Optional[dict]
    status: str
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ User Status Schemas ============
class UserStatusUpdate(BaseModel):
    status_emoji: Optional[str] = None
    status_text: Optional[str] = None
    expires_at: Optional[datetime] = None


class UserStatusResponse(BaseModel):
    status_emoji: Optional[str]
    status_text: Optional[str]
    status_expires_at: Optional[datetime]
    last_seen_at: Optional[datetime]


# ============ Notification Schemas ============
class NotificationResponse(BaseModel):
    id: int
    type: str
    title: str
    content: Optional[str]
    link: Optional[str]
    is_read: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class NotificationCount(BaseModel):
    unread_count: int


# ============ WebSocket Schemas ============
class WSMessage(BaseModel):
    type: str  # "message", "typing", "read", "presence", "agent_task"
    data: dict


class AgentCollaborationRequest(BaseModel):
    """
    Request for agent-to-agent collaboration
    """
    source_agent: str
    target_agent: str
    task_type: str
    payload: dict
    callback_url: Optional[str] = None


class AgentCollaborationResponse(BaseModel):
    """
    Response from agent collaboration
    """
    task_id: int
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


# ============ Pagination ============
class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int
