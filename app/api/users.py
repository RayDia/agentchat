"""
User Management API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from ..database import get_db
from ..models import User
from ..schemas import UserResponse, UserStatusUpdate, PaginatedResponse
from ..auth import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/channel/{channel_id}/members")
async def get_channel_members(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get members of a channel (excluding current user)"""
    from ..models import ChannelMember
    
    # Get channel members
    members = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id
    ).all()
    
    # Get user details and exclude current user
    result = []
    for member in members:
        if member.user_id == current_user.id:
            continue
        user = db.query(User).filter(User.id == member.user_id).first()
        if user:
            result.append(UserResponse.model_validate(user).model_dump())
    
    return {"members": result, "total": len(result)}


@router.get("/", response_model=PaginatedResponse)
async def list_users(
    role: Optional[str] = None,
    is_agent: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all users"""
    query = db.query(User)
    
    if role:
        query = query.filter(User.role == role)
    if is_agent is not None:
        query = query.filter(User.is_agent == is_agent)
    if search:
        query = query.filter(
            (User.username.contains(search)) |
            (User.display_name.contains(search))
        )
    
    total = query.count()
    users = query.offset((page - 1) * page_size).limit(page_size).all()
    
    return PaginatedResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.get("/agents", response_model=PaginatedResponse)
async def list_agents(
    capability: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all available agents"""
    query = db.query(User).filter(User.is_agent == True, User.is_active == True)
    
    if search:
        query = query.filter(
            (User.username.contains(search)) |
            (User.display_name.contains(search))
        )
    
    total = query.count()
    agents = query.offset((page - 1) * page_size).limit(page_size).all()
    
    # Filter by capability if specified
    items = []
    import json
    for agent in agents:
        agent_dict = UserResponse.model_validate(agent).model_dump()
        if capability and agent.agent_capabilities:
            capabilities = json.loads(agent.agent_capabilities)
            if capability in capabilities:
                items.append(agent_dict)
        elif not capability:
            items.append(agent_dict)
    
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user by ID"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse.model_validate(user)


@router.get("/username/{username}", response_model=UserResponse)
async def get_user_by_username(
    username: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user by username"""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse.model_validate(user)


@router.put("/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Deactivate a user (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    
    user.is_active = False
    db.commit()
    
    return {"message": f"User {user.username} deactivated"}


@router.put("/{user_id}/activate")
async def activate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Activate a user (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.is_active = True
    db.commit()
    
    return {"message": f"User {user.username} activated"}


@router.put("/me/status")
async def update_status(
    status_update: UserStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user status"""
    current_user.status_emoji = status_update.status_emoji
    current_user.status_text = status_update.status_text
    current_user.status_expires_at = status_update.expires_at
    db.commit()
    
    return {
        "message": "Status updated",
        "status_emoji": current_user.status_emoji,
        "status_text": current_user.status_text
    }


@router.delete("/me/status")
async def clear_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Clear user status"""
    current_user.status_emoji = None
    current_user.status_text = None
    current_user.status_expires_at = None
    db.commit()
    
    return {"message": "Status cleared"}


@router.put("/me/online")
async def update_online_status(
    is_online: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update online status (called by frontend)"""
    if is_online:
        current_user.last_seen_at = datetime.utcnow()
    db.commit()
    
    return {"message": "Online status updated"}
