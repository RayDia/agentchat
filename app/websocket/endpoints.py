"""
WebSocket Endpoints
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from ..config import settings
from ..database import SessionLocal
from ..models import User, Message, Channel, ChannelMember
from .manager import manager
from ..acp import session_manager
import json
from datetime import datetime, timezone
import re

router = APIRouter(tags=["WebSocket"])


async def authenticate_websocket(token: str = Query(None)) -> User:
    """Authenticate WebSocket connection using token"""
    if not token:
        return None
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        user_id: int = payload.get("user_id")
        
        if not username and not user_id:
            return None
        
        db = SessionLocal()
        try:
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
            else:
                user = db.query(User).filter(User.username == username).first()
            
            return user
        finally:
            db.close()
    except JWTError:
        return None


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(None)
):
    """Main WebSocket endpoint for real-time communication"""
    # Authenticate
    user = await authenticate_websocket(token)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    
    # Connect
    await manager.connect(websocket, user)
    
    try:
        while True:
            # Receive message
            data = await websocket.receive_text()
            message = json.loads(data)
            
            message_type = message.get("type")
            
            if message_type == "message":
                await handle_chat_message(user, message)
            elif message_type == "typing":
                await handle_typing_indicator(user, message)
            elif message_type == "read":
                await handle_read_receipt(user, message)
            elif message_type == "join_channel":
                await handle_join_channel(user, message)
            elif message_type == "leave_channel":
                await handle_leave_channel(user, message)
            elif message_type == "agent_task":
                await handle_agent_task(user, message)
            elif message_type == "agent_response":
                await handle_agent_response(user, message)
            else:
                await websocket.send_json({
                    "type": "error",
                    "data": {"message": f"Unknown message type: {message_type}"}
                })
    
    except WebSocketDisconnect:
        is_last = manager.disconnect(websocket, user.id)
        if is_last:
            await manager.broadcast_presence(user.id, "offline")
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "data": {"message": str(e)}
        })
        manager.disconnect(websocket, user.id)


async def handle_chat_message(user: User, message: dict):
    """Handle incoming chat message"""
    data = message.get("data", {})
    channel_id = data.get("channel_id")
    content = data.get("content")
    message_type = data.get("message_type", "text")
    reply_to_id = data.get("reply_to_id")
    
    print(f"[WS] 收到消息: user={user.id}, channel={channel_id}, content={content[:20]}")
    
    if not channel_id or not content:
        return
    
    db = SessionLocal()
    try:
        # Verify user is member of channel
        membership = db.query(ChannelMember).filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.user_id == user.id
        ).first()
        
        if not membership:
            await manager.send_to_user(user.id, {
                "type": "error",
                "data": {"message": "Not a member of this channel"}
            })
            return
        
        # Create message in database
        new_message = Message(
            channel_id=channel_id,
            sender_id=user.id,
            content=content,
            message_type=message_type,
            reply_to_id=reply_to_id
        )
        
        # Set thread_id if this is a reply
        if reply_to_id:
            parent = db.query(Message).filter(Message.id == reply_to_id).first()
            if parent:
                new_message.thread_id = parent.thread_id or parent.id
        
        db.add(new_message)
        db.commit()
        db.refresh(new_message)
        
        # Broadcast to channel members
        broadcast_message = {
            "type": "message",
            "data": {
                "id": new_message.id,
                "channel_id": channel_id,
                "sender_id": user.id,
                "sender": {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "avatar_url": user.avatar_url
                },
                "content": content,
                "message_type": message_type,
                "reply_to_id": reply_to_id,
                "thread_id": new_message.thread_id,
                "created_at": new_message.created_at.isoformat() if new_message.created_at else None
            }
        }
        
        print(f"[WS] 广播消息到频道 {channel_id}, 排除用户 {user.id}")
        print(f"[WS] 当前频道成员: {manager.channel_members.get(channel_id, set())}")
        
        await manager.send_to_channel(channel_id, broadcast_message)  # 包含发送者
        print(f"[WS] 消息广播完成")
        
        # 转发@提及给Agent
        await forward_mentions_to_agents(channel_id, content, user.id, user.username, user.display_name or user.username)
    
    finally:
        db.close()



async def forward_mentions_to_agents(channel_id: int, content: str, sender_id: int, sender_username: str, sender_display_name: str):
    """将@提及消息转发给频道的Agent会话"""
    from ..acp import session_manager
    
    sessions = session_manager.get_sessions_by_channel(channel_id)
    
    for session in sessions:
        if not session.is_alive or not session.websocket:
            continue
        
        # 检查是否提及了该Agent
        pattern = rf'@{re.escape(session.agent_username)}\b'
        if re.search(pattern, content, re.IGNORECASE):
            # 提取@提及后的内容。
            # 必须加 re.DOTALL：默认 `.` 不匹配换行，否则多行消息（很常见，
            # 例如消息里带命令、代码、堆栈）只有第一行会被转发给 agent。
            match = re.search(rf'@{re.escape(session.agent_username)}\s*(.*)',
                              content, re.IGNORECASE | re.DOTALL)
            task_content = match.group(1) if match else content
            
            input_message = {
                "type": "input",
                "data": {
                    "session_id": session.session_id,
                    "message": {
                        "content": task_content,
                        "sender_id": sender_id,
                        "sender_username": sender_username,
                        "sender_display_name": sender_display_name,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                }
            }
            
            try:
                await session.websocket.send_json(input_message)
            except Exception as e:
                print(f"[ACP] 转发@提及给Agent失败: {e}")
                session.is_alive = False


async def handle_typing_indicator(user: User, message: dict):
    """Handle typing indicator"""
    data = message.get("data", {})
    channel_id = data.get("channel_id")
    is_typing = data.get("is_typing", True)
    
    if not channel_id:
        return
    
    broadcast_message = {
        "type": "typing",
        "data": {
            "channel_id": channel_id,
            "user_id": user.id,
            "username": user.username,
            "is_typing": is_typing
        }
    }
    
    await manager.send_to_channel(channel_id, broadcast_message, exclude_user=user.id)


async def handle_read_receipt(user: User, message: dict):
    """Handle read receipt"""
    data = message.get("data", {})
    channel_id = data.get("channel_id")
    last_message_id = data.get("last_message_id")
    
    if not channel_id or not last_message_id:
        return
    
    # Update channel membership with last read message
    db = SessionLocal()
    try:
        membership = db.query(ChannelMember).filter(
            ChannelMember.channel_id == channel_id,
            ChannelMember.user_id == user.id
        ).first()
        
        if membership:
            # You could add a last_read_message_id field to ChannelMember
            pass
    finally:
        db.close()


async def handle_join_channel(user: User, message: dict):
    """Handle user joining a channel"""
    data = message.get("data", {})
    channel_id = data.get("channel_id")
    
    print(f"[WS] 用户 {user.id} 加入频道 {channel_id}")
    
    if not channel_id:
        return
    
    manager.join_channel(channel_id, user.id)
    print(f"[WS] 频道 {channel_id} 当前成员: {manager.channel_members.get(channel_id, set())}")
    
    # Notify others
    broadcast_message = {
        "type": "user_joined",
        "data": {
            "channel_id": channel_id,
            "user_id": user.id,
            "username": user.username
        }
    }
    
    await manager.send_to_channel(channel_id, broadcast_message, exclude_user=user.id)


async def handle_leave_channel(user: User, message: dict):
    """Handle user leaving a channel"""
    data = message.get("data", {})
    channel_id = data.get("channel_id")
    
    if not channel_id:
        return
    
    manager.leave_channel(channel_id, user.id)
    
    # Notify others
    broadcast_message = {
        "type": "user_left",
        "data": {
            "channel_id": channel_id,
            "user_id": user.id,
            "username": user.username
        }
    }
    
    await manager.send_to_channel(channel_id, broadcast_message, exclude_user=user.id)


async def handle_agent_task(user: User, message: dict):
    """Handle agent task request"""
    data = message.get("data", {})
    target_agent_id = data.get("target_agent_id")
    task_type = data.get("task_type")
    payload = data.get("payload")
    
    if not target_agent_id or not task_type:
        return
    
    if not user.is_agent:
        await manager.send_to_user(user.id, {
            "type": "error",
            "data": {"message": "Only agents can send agent tasks"}
        })
        return
    
    # Check if target agent is online
    if not manager.is_agent_online(target_agent_id):
        await manager.send_to_user(user.id, {
            "type": "error",
            "data": {"message": "Target agent is offline"}
        })
        return
    
    # Forward task to target agent
    await manager.send_agent_task(user.id, target_agent_id, {
        "task_type": task_type,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat()
    })


async def handle_agent_response(user: User, message: dict):
    """Handle agent task response"""
    data = message.get("data", {})
    target_agent_id = data.get("target_agent_id")
    response_data = data.get("response_data")
    
    if not target_agent_id:
        return
    
    # Forward response
    await manager.send_agent_response(user.id, target_agent_id, response_data)
