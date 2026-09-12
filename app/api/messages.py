"""
Message API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
import json

from ..database import get_db
from ..models import Message, Channel, ChannelMember, User, MessageType
from ..schemas import MessageCreate, MessageResponse, MessageThread, PaginatedResponse, UserResponse
from ..auth import get_current_user

router = APIRouter(prefix="/messages", tags=["Messages"])


@router.get("/channel/{channel_id}", response_model=PaginatedResponse)
async def get_channel_messages(
    channel_id: int,
    before: Optional[int] = None,
    after: Optional[int] = None,
    thread_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get messages in a channel"""
    # Check if user is a member
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    query = db.query(Message).filter(
        Message.channel_id == channel_id,
        Message.is_deleted == False
    )
    
    # Thread filtering
    if thread_id is not None:
        query = query.filter(Message.thread_id == thread_id)
    else:
        # Only get top-level messages (not replies)
        query = query.filter(Message.thread_id == None)
    
    # Cursor-based pagination
    if before:
        query = query.filter(Message.id < before)
        query = query.order_by(Message.id.desc())
    elif after:
        query = query.filter(Message.id > after)
        query = query.order_by(Message.id.asc())
    else:
        # 默认按id升序（从旧到新）
        query = query.order_by(Message.id.asc())
    
    total = query.count()
    
    if not before and not after:
        # 初始加载：获取最新的page_size条消息
        # 先获取最新的，然后反转为从旧到新
        latest_query = db.query(Message).filter(
            Message.channel_id == channel_id,
            Message.is_deleted == False
        )
        if thread_id is not None:
            latest_query = latest_query.filter(Message.thread_id == thread_id)
        else:
            latest_query = latest_query.filter(Message.thread_id == None)
        
        # 获取最新的page_size条
        latest_messages = latest_query.order_by(Message.id.desc()).limit(page_size).all()
        latest_messages.reverse()  # 反转为从旧到新
        messages = latest_messages
    else:
        messages = query.limit(page_size).all()
    
    # Enrich with sender info
    items = []
    for msg in messages:
        msg_dict = MessageResponse.model_validate(msg).model_dump()
        msg_dict["sender"] = UserResponse.model_validate(msg.sender).model_dump() if msg.sender else None
        items.append(msg_dict)
    
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.post("/", response_model=MessageResponse)
async def send_message(
    message: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Send a message to a channel"""
    # Check if user is a member
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == message.channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    # Create message
    new_message = Message(
        channel_id=message.channel_id,
        sender_id=current_user.id,
        content=message.content,
        message_type=message.message_type,
        reply_to_id=message.reply_to_id,
        mentions=json.dumps(message.mentions) if message.mentions else None,
        extra_data=json.dumps(message.metadata) if message.metadata else None
    )
    
    # Set thread_id if this is a reply
    if message.reply_to_id:
        parent = db.query(Message).filter(Message.id == message.reply_to_id).first()
        if parent:
            new_message.thread_id = parent.thread_id or parent.id
    
    db.add(new_message)
    db.commit()
    db.refresh(new_message)
    
    # 处理@提及通知
    from ..services.notification import NotificationService
    notification_service = NotificationService(db)
    await notification_service.process_mentions(new_message, message.channel_id)
    
    return MessageResponse.model_validate(new_message)


@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific message"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if user has access to the channel
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == message.channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not authorized to view this message")
    
    return MessageResponse.model_validate(message)


@router.get("/{message_id}/thread", response_model=MessageThread)
async def get_thread(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get message thread"""
    parent = db.query(Message).filter(Message.id == message_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Get replies
    replies = db.query(Message).filter(
        Message.thread_id == message_id,
        Message.is_deleted == False
    ).order_by(Message.id.asc()).all()
    
    return MessageThread(
        parent=MessageResponse.model_validate(parent),
        replies=[MessageResponse.model_validate(r) for r in replies]
    )


@router.put("/{message_id}", response_model=MessageResponse)
async def update_message(
    message_id: int,
    content: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a message (only sender)"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Can only edit your own messages")
    
    message.content = content
    db.commit()
    db.refresh(message)
    
    return MessageResponse.model_validate(message)


@router.delete("/{message_id}")
async def delete_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a message (soft delete)"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Allow sender or admin to delete
    if message.sender_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    message.is_deleted = True
    message.content = "[Message deleted]"
    db.commit()
    
    return {"message": "Message deleted successfully"}


@router.post("/{message_id}/react")
async def add_reaction(
    message_id: int,
    emoji: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add emoji reaction to a message"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Parse existing reactions
    reactions = {}
    if message.extra_data:
        try:
            metadata = json.loads(message.extra_data)
            reactions = metadata.get("reactions", {})
        except:
            pass
    
    # Add reaction
    if emoji not in reactions:
        reactions[emoji] = []
    
    if current_user.id not in reactions[emoji]:
        reactions[emoji].append(current_user.id)
    else:
        reactions[emoji].remove(current_user.id)
        if not reactions[emoji]:
            del reactions[emoji]
    
    # Update metadata
    metadata = json.loads(message.extra_data) if message.extra_data else {}
    metadata["reactions"] = reactions
    message.extra_data = json.dumps(metadata)
    
    db.commit()
    
    return {"reactions": reactions}


@router.post("/{message_id}/pin")
async def pin_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Pin a message to channel"""
    from datetime import datetime
    
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if user is member
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == message.channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    if message.is_pinned:
        raise HTTPException(status_code=400, detail="Message already pinned")
    
    message.is_pinned = True
    message.pinned_at = datetime.utcnow()
    message.pinned_by = current_user.id
    db.commit()
    
    return {"message": "Message pinned successfully", "pinned_at": message.pinned_at.isoformat()}


@router.post("/{message_id}/unpin")
async def unpin_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Unpin a message from channel"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if user is member
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == message.channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    if not message.is_pinned:
        raise HTTPException(status_code=400, detail="Message not pinned")
    
    message.is_pinned = False
    message.pinned_at = None
    message.pinned_by = None
    db.commit()
    
    return {"message": "Message unpinned successfully"}


@router.get("/channel/{channel_id}/pinned", response_model=List[MessageResponse])
async def get_pinned_messages(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all pinned messages in a channel"""
    # Check if user is member
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    messages = db.query(Message).filter(
        Message.channel_id == channel_id,
        Message.is_pinned == True,
        Message.is_deleted == False
    ).order_by(Message.pinned_at.desc()).all()
    
    return [MessageResponse.model_validate(m) for m in messages]
