"""
Search API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional

from ..database import get_db
from ..models import Message, Channel, ChannelMember, User
from ..schemas import MessageResponse, ChannelResponse, UserResponse
from ..auth import get_current_user

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("/messages")
async def search_messages(
    query: str = Query(..., min_length=1),
    channel_id: Optional[int] = None,
    user_id: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Search messages"""
    # 获取用户有权限访问的频道
    user_channel_ids = [
        ch.id for ch in db.query(Channel)
        .join(ChannelMember)
        .filter(ChannelMember.user_id == current_user.id)
        .all()
    ]
    
    if not user_channel_ids:
        return {"results": [], "total": 0}
    
    # 构建搜索查询
    search_query = db.query(Message).filter(
        Message.channel_id.in_(user_channel_ids),
        Message.is_deleted == False,
        Message.content.contains(query)
    )
    
    # 可选过滤
    if channel_id:
        if channel_id not in user_channel_ids:
            raise HTTPException(status_code=403, detail="Not a member of this channel")
        search_query = search_query.filter(Message.channel_id == channel_id)
    
    if user_id:
        search_query = search_query.filter(Message.sender_id == user_id)
    
    # 日期过滤
    if from_date:
        from datetime import datetime
        from_date_obj = datetime.fromisoformat(from_date)
        search_query = search_query.filter(Message.created_at >= from_date_obj)
    
    if to_date:
        from datetime import datetime
        to_date_obj = datetime.fromisoformat(to_date)
        search_query = search_query.filter(Message.created_at <= to_date_obj)
    
    # 计算总数
    total = search_query.count()
    
    # 分页获取结果
    messages = search_query.order_by(Message.created_at.desc()) \
                          .offset((page - 1) * page_size) \
                          .limit(page_size) \
                          .all()
    
    # 丰富结果信息
    results = []
    for msg in messages:
        msg_dict = MessageResponse.model_validate(msg).model_dump()
        # 添加发送者信息
        sender = db.query(User).filter(User.id == msg.sender_id).first()
        if sender:
            msg_dict["sender"] = UserResponse.model_validate(sender).model_dump()
        # 添加频道信息
        channel = db.query(Channel).filter(Channel.id == msg.channel_id).first()
        if channel:
            msg_dict["channel_name"] = channel.name
        results.append(msg_dict)
    
    return {
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "query": query
    }


@router.get("/channels")
async def search_channels(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Search channels"""
    # 获取用户有权限访问的频道
    user_channel_ids = [
        ch.id for ch in db.query(Channel)
        .join(ChannelMember)
        .filter(ChannelMember.user_id == current_user.id)
        .all()
    ]
    
    search_query = db.query(Channel).filter(
        Channel.id.in_(user_channel_ids),
        Channel.name.contains(query)
    )
    
    total = search_query.count()
    channels = search_query.offset((page - 1) * page_size).limit(page_size).all()
    
    return {
        "results": [ChannelResponse.model_validate(ch) for ch in channels],
        "total": total,
        "query": query
    }


@router.get("/users")
async def search_users(
    query: str = Query(..., min_length=1),
    include_agents: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Search users"""
    search_query = db.query(User).filter(
        User.is_active == True,
        or_(
            User.username.contains(query),
            User.display_name.contains(query),
            User.email.contains(query)
        )
    )
    
    if not include_agents:
        search_query = search_query.filter(User.is_agent == False)
    
    total = search_query.count()
    users = search_query.offset((page - 1) * page_size).limit(page_size).all()
    
    return {
        "results": [UserResponse.model_validate(u) for u in users],
        "total": total,
        "query": query
    }


@router.get("/global")
async def global_search(
    query: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Global search across messages, channels, and users"""
    # 获取用户有权限访问的频道
    user_channel_ids = [
        ch.id for ch in db.query(Channel)
        .join(ChannelMember)
        .filter(ChannelMember.user_id == current_user.id)
        .all()
    ]
    
    results = {
        "messages": [],
        "channels": [],
        "users": []
    }
    
    # 搜索消息
    messages = db.query(Message).filter(
        Message.channel_id.in_(user_channel_ids),
        Message.is_deleted == False,
        Message.content.contains(query)
    ).order_by(Message.created_at.desc()).limit(10).all()
    
    results["messages"] = [
        {
            "id": msg.id,
            "content": msg.content[:100],  # 截断内容
            "channel_id": msg.channel_id,
            "created_at": msg.created_at.isoformat()
        }
        for msg in messages
    ]
    
    # 搜索频道
    channels = db.query(Channel).filter(
        Channel.id.in_(user_channel_ids),
        Channel.name.contains(query)
    ).limit(10).all()
    
    results["channels"] = [
        {"id": ch.id, "name": ch.name, "description": ch.description}
        for ch in channels
    ]
    
    # 搜索用户
    users = db.query(User).filter(
        User.is_active == True,
        or_(
            User.username.contains(query),
            User.display_name.contains(query)
        )
    ).limit(10).all()
    
    results["users"] = [
        {"id": u.id, "username": u.username, "display_name": u.display_name}
        for u in users
    ]
    
    return {"results": results, "query": query}
