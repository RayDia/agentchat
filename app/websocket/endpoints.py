"""
WebSocket Endpoints
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError, jwt
from ..config import settings
from ..database import SessionLocal
from ..models import User, Message, ChannelMember
from .manager import manager
from ..services.mention_service import dispatch_mentions
import json
from datetime import datetime

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
        print("[WS] 消息广播完成")

        # 转发@提及给Agent：在线实时推送，离线入队待 agent 上线补发
        try:
            stats = await dispatch_mentions(db, new_message, user)
            if stats["queued"]:
                # 告知发送者消息已排队，避免"发完没人理"的无反馈体验
                await manager.send_to_user(user.id, {
                    "type": "mention_queued",
                    "data": {
                        "message_id": new_message.id,
                        "channel_id": channel_id,
                        "agent_ids": stats["queued"],
                        "reason": "agent_offline",
                    },
                })
        except Exception as e:
            # 投递失败不应影响消息本身（已落库并广播）
            print(f"[WS] @提及投递异常: {e}")

    finally:
        db.close()



async def forward_mentions_to_agents(channel_id: int, content: str, sender_id: int, sender_username: str, sender_display_name: str):
    """兼容旧调用点的薄封装。

    实际投递逻辑已收敛到 app/services/mention_service.dispatch_mentions，
    以便 WS 与 HTTP 两条路径行为一致（此前 HTTP 路径完全不转发 @提及，
    是消息丢失的第二条路径）。新代码请直接调用 dispatch_mentions。
    """
    from ..database import SessionLocal
    from ..models import Message, User
    from ..services.mention_service import dispatch_mentions

    db = SessionLocal()
    try:
        # 找到该频道最近一条由 sender 发出、内容匹配的消息作为投递载体
        msg = (
            db.query(Message)
            .filter(Message.channel_id == channel_id,
                    Message.sender_id == sender_id,
                    Message.content == content)
            .order_by(Message.id.desc())
            .first()
        )
        sender = db.query(User).filter(User.id == sender_id).first()
        if msg is None or sender is None:
            return
        await dispatch_mentions(db, msg, sender)
    finally:
        db.close()


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
