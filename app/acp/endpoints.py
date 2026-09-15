"""
ACP Socket Mode WebSocket Endpoints
用于外部Agent(如Qwen Code、Pi Agent、OpenCode)通过Socket Mode连接
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from typing import Optional
import json
import asyncio
from datetime import datetime, timezone, timedelta

from ..database import SessionLocal
from ..models import User, Message, Channel, ChannelMember
from ..auth import get_current_user
from ..config import settings
from ..websocket.manager import manager as connection_manager
from .protocol import (
    ACPProtocol,
    SessionManager,
    session_manager
)

router = APIRouter(prefix="/api/acp", tags=["ACP Socket Mode"])


async def authenticate_socket_token(token: str) -> Optional[User]:
    """使用JWT token认证Socket连接"""
    if not token:
        return None
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("user_id")
        
        if not user_id:
            return None
        
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            return user
        finally:
            db.close()
    except JWTError:
        return None


@router.websocket("/ws/socket")
async def socket_mode_endpoint(
    websocket: WebSocket,
    token: str = Query(None),
    session_id: str = Query(None)
):
    """
    ACP Socket Mode Endpoint
    用于外部Agent连接到AgentChat
    """
    # 认证
    user = await authenticate_socket_token(token)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    
    # 检查是否是Agent
    if not user.is_agent:
        await websocket.close(code=4003, reason="Only agents can connect via socket mode")
        return
    
    # 接受连接
    await websocket.accept()
    
    # 如果提供session_id，恢复会话；否则创建新会话
    agent_session = None
    if session_id:
        agent_session = session_manager.get_session(session_id)
        if agent_session and agent_session.agent_id == user.id:
            agent_session.websocket = websocket
            agent_session.is_alive = True
            agent_session.last_heartbeat = datetime.now(timezone.utc)
        else:
            agent_session = None
    
    if not agent_session:
        # 需要connect消息来创建会话
        await websocket.send_json(ACPProtocol.create_status_message("waiting_for_connect"))
    
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                message = json.loads(data)
                agent_session = await handle_socket_message(
                    user, message, websocket, agent_session
                )
            except asyncio.TimeoutError:
                # 发送心跳
                await websocket.send_json(ACPProtocol.create_ping_message())
    
    except WebSocketDisconnect:
        # 清理会话
        if agent_session:
            session_manager.remove_session(agent_session.session_id)
            await broadcast_session_status(agent_session.channel_id, "agent_disconnected", {
                "agent_id": user.id,
                "agent_username": user.username,
                "session_id": agent_session.session_id
            })
    except Exception as e:
        await websocket.send_json(ACPProtocol.create_error_message(
            "internal_error",
            str(e)
        ))
        if agent_session:
            session_manager.remove_session(agent_session.session_id)


async def handle_socket_message(user: User, message: dict, websocket: WebSocket, agent_session: Optional['AgentSession']):
    """处理来自Agent的消息

    返回（可能被 connect 更新后的）会话，便于调用方维护心跳与断线清理。
    """
    message_type = message.get("type")
    
    if message_type == ACPProtocol.TYPE_CONNECT:
        return await handle_connect(user, message, websocket)
    elif message_type == ACPProtocol.TYPE_OUTPUT:
        await handle_output(user, message, websocket)
    elif message_type == ACPProtocol.TYPE_PING:
        await websocket.send_json(ACPProtocol.create_pong_message(message.get("timestamp", "")))
        if agent_session:
            session_manager.update_heartbeat(agent_session.session_id)
    elif message_type == "pong":
        # Agent 对服务端 ping 的回应，仅更新心跳
        if agent_session:
            session_manager.update_heartbeat(agent_session.session_id)
    elif message_type == ACPProtocol.TYPE_STATUS:
        await handle_status(user, message, websocket)
    else:
        await websocket.send_json(ACPProtocol.create_error_message(
            "unknown_type",
            f"Unknown message type: {message_type}"
        ))
    return agent_session


async def handle_connect(user: User, message: dict, websocket: WebSocket):
    """处理Agent连接请求"""
    data = message.get("data", {})
    channel_id = data.get("channel_id")
    request_session_id = data.get("session_id")
    
    if not channel_id:
        await websocket.send_json(ACPProtocol.create_error_message(
            "missing_channel_id",
            "channel_id is required"
        ))
        return None
    
    # 验证频道存在且Agent是成员
    db = SessionLocal()
    try:
        channel = db.query(Channel).filter(Channel.id == channel_id).first()
        if not channel:
            await websocket.send_json(ACPProtocol.create_error_message(
                "channel_not_found",
                f"Channel {channel_id} not found"
            ))
            return None
        
        # 检查Agent是否是频道成员
        membership = db.query(ChannelMember).filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.user_id == user.id
        ).first()
        
        # 频道类型在库里存的是大写（PUBLIC/PRIVATE），比较时必须忽略大小写，
        # 否则公开频道会被误判成私有，导致非成员 agent 接入被拒。
        channel_type = (channel.channel_type or "").lower()
        if not membership and channel_type != "public":
            await websocket.send_json(ACPProtocol.create_error_message(
                "not_member",
                "You are not a member of this channel"
            ))
            return None
    finally:
        db.close()
    
    # 创建或恢复会话
    if request_session_id:
        existing_session = session_manager.get_session(request_session_id)
        if existing_session and existing_session.agent_id == user.id:
            existing_session.websocket = websocket
            existing_session.is_alive = True
            existing_session.last_heartbeat = datetime.now(timezone.utc)
            agent_session = existing_session
        else:
            agent_session = session_manager.create_session(
                agent_id=user.id,
                agent_username=user.username,
                agent_display_name=user.display_name or user.username,
                channel_id=channel_id,
                channel_name=channel.name,
                websocket=websocket
            )
    else:
        agent_session = session_manager.create_session(
            agent_id=user.id,
            agent_username=user.username,
            agent_display_name=user.display_name or user.username,
            channel_id=channel_id,
            channel_name=channel.name,
            websocket=websocket
        )
    
    # 确保Agent在WebSocket管理器中也加入频道（占位：真实转发依赖 session_manager）
    
    # 发送连接成功消息
    await websocket.send_json(ACPProtocol.create_connect_message(
        agent_id=user.id,
        session_id=agent_session.session_id,
        channel_id=channel_id,
        channel_name=channel.name
    ))
    
    # 广播Agent上线
    await broadcast_session_status(channel_id, "agent_connected", agent_session.to_dict())
    return agent_session


async def handle_output(user: User, message: dict, websocket: WebSocket):
    """处理Agent输出 - 发送到频道"""
    data = message.get("data", {})
    session_id = data.get("session_id")
    output = data.get("output", "")
    
    if not session_id or not output:
        await websocket.send_json(ACPProtocol.create_error_message(
            "missing_fields",
            "session_id and output are required"
        ))
        return
    
    # 验证会话
    agent_session = session_manager.get_session(session_id)
    if not agent_session or agent_session.agent_id != user.id or agent_session.websocket != websocket:
        await websocket.send_json(ACPProtocol.create_error_message(
            "invalid_session",
            "Invalid or expired session"
        ))
        return
    
    # 发送消息到频道
    db = SessionLocal()
    try:
        new_message = Message(
            channel_id=agent_session.channel_id,
            sender_id=user.id,
            content=output,
            message_type="agent_response"
        )
        db.add(new_message)
        db.commit()
        db.refresh(new_message)
        
        # 广播到频道
        broadcast_msg = {
            "type": "message",
            "data": {
                "id": new_message.id,
                "channel_id": agent_session.channel_id,
                "sender_id": user.id,
                "sender": {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name or user.username,
                    "is_agent": True
                },
                "content": output,
                "message_type": "agent_response",
                "created_at": new_message.created_at.isoformat() if new_message.created_at else None
            }
        }
        
        await connection_manager.send_to_channel(agent_session.channel_id, broadcast_msg)
        
        # 返回成功
        await websocket.send_json({
            "type": "output_ack",
            "data": {
                "message_id": new_message.id,
                "session_id": session_id
            }
        })
    finally:
        db.close()


async def handle_status(user: User, message: dict, websocket: WebSocket):
    """处理状态更新"""
    data = message.get("data", {})
    status = data.get("status", "")
    session_id = data.get("session_id")
    
    agent_session = session_manager.get_session(session_id) if session_id else None
    if agent_session:
        agent_session.is_alive = status == "online"
        await websocket.send_json(ACPProtocol.create_status_message("ok", {"status": status}))


async def broadcast_session_status(channel_id: int, event: str, data: dict):
    """广播会话状态变化"""
    # 通知频道中的其他Agent和User（聊天连接管理器，人类客户端也能收到）
    await connection_manager.send_to_channel(channel_id, {
        "type": "agent_status",
        "event": event,
        "data": data
    })


# API端点用于查询会话状态
@router.get("/sessions")
async def list_sessions():
    """获取所有活跃的Agent会话"""
    return session_manager.get_stats()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取特定会话信息"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@router.post("/sessions/{session_id}/disconnect")
async def disconnect_session(session_id: str):
    """主动断开Agent会话"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session.is_alive = False
    if session.websocket:
        try:
            await session.websocket.close()
        except:
            pass
    
    session_manager.remove_session(session_id)
    return {"message": "Session disconnected"}
