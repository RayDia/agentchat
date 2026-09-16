"""
Channel API Routes
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from ..models import Channel, ChannelMember, User, ChannelType
from ..schemas import ChannelCreate, ChannelResponse, ChannelWithMembers, PaginatedResponse
from ..auth import get_current_user

router = APIRouter(prefix="/channels", tags=["Channels"])


@router.get("/invite/{invite_code}")
async def get_invite_info(
    invite_code: str,
    db: Session = Depends(get_db)
):
    """Get channel info from invite code (public endpoint)"""
    # Find channel with matching invite code
    channels = db.query(Channel).all()
    for ch in channels:
        if ch.extra_data:
            import json
            try:
                data = json.loads(ch.extra_data)
                if data.get("invite_code") == invite_code:
                    return {
                        "channel_id": ch.id,
                        "channel_name": ch.name,
                        "description": ch.description,
                        "channel_type": ch.channel_type.value if hasattr(ch.channel_type, 'value') else ch.channel_type,
                        "member_count": len(ch.members)
                    }
            except:
                pass
    
    raise HTTPException(status_code=404, detail="Invalid invite code")


@router.post("/accept-invite/{invite_code}")
async def accept_invite(
    invite_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Accept an invite and join the channel"""
    # Find channel with matching invite code
    channel = None
    for ch in db.query(Channel).all():
        if ch.extra_data:
            import json
            try:
                data = json.loads(ch.extra_data)
                if data.get("invite_code") == invite_code:
                    channel = ch
                    break
            except:
                pass
    
    if not channel:
        raise HTTPException(status_code=404, detail="Invalid invite code")
    
    # Check if already a member
    existing = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel.id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if existing:
        return {"message": "Already a member of this channel"}
    
    # Add user to channel
    member = ChannelMember(
        channel_id=channel.id,
        user_id=current_user.id
    )
    db.add(member)
    db.commit()
    
    return {"message": f"Joined channel '{channel.name}' successfully"}


@router.get("/", response_model=PaginatedResponse)
async def list_channels(
    channel_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all channels the user is a member of"""
    # 获取用户已加入的频道
    query = db.query(Channel).join(ChannelMember).filter(
        ChannelMember.user_id == current_user.id
    )

    if channel_type:
        query = query.filter(Channel.channel_type == channel_type)

    total = query.count()
    channels = query.offset((page - 1) * page_size).limit(page_size).all()
    
    items = []
    for ch in channels:
        ch_dict = ChannelResponse.model_validate(ch).model_dump()
        ch_dict["member_count"] = len(ch.members)
        items.append(ch_dict)
    
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.get("/discover", response_model=PaginatedResponse)
async def discover_channels(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Discover public channels the user is not a member of"""
    # 获取用户尚未加入的公开频道
    query = db.query(Channel).filter(
        Channel.channel_type == ChannelType.PUBLIC,
        ~Channel.id.in_(
            db.query(ChannelMember.channel_id).filter(ChannelMember.user_id == current_user.id)
        )
    )
    
    total = query.count()
    channels = query.offset((page - 1) * page_size).limit(page_size).all()
    
    items = []
    for ch in channels:
        ch_dict = ChannelResponse.model_validate(ch).model_dump()
        ch_dict["member_count"] = len(ch.members)
        items.append(ch_dict)
    
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.post("/", response_model=ChannelResponse)
async def create_channel(
    channel: ChannelCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new channel"""
    new_channel = Channel(
        name=channel.name,
        description=channel.description,
        channel_type=channel.channel_type,
        workspace_id=channel.workspace_id,
        created_by=current_user.id
    )
    db.add(new_channel)
    db.flush()
    
    # Add creator as member
    creator_member = ChannelMember(
        channel_id=new_channel.id,
        user_id=current_user.id,
        role="admin"
    )
    db.add(creator_member)
    
    # Add other members
    if channel.member_ids:
        for user_id in channel.member_ids:
            if user_id != current_user.id:
                member = ChannelMember(
                    channel_id=new_channel.id,
                    user_id=user_id
                )
                db.add(member)
    
    db.commit()
    db.refresh(new_channel)
    
    response = ChannelResponse.model_validate(new_channel)
    response.member_count = len(new_channel.members)
    return response


@router.get("/{channel_id}", response_model=ChannelWithMembers)
async def get_channel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get channel details with members"""
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    # Check if user is a member
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership and channel.channel_type == ChannelType.PRIVATE:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    response = ChannelWithMembers.model_validate(channel)
    response.member_count = len(channel.members)
    return response


@router.post("/{channel_id}/join")
async def join_channel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Join a public channel"""
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    if channel.channel_type == ChannelType.PRIVATE:
        raise HTTPException(status_code=403, detail="Cannot join private channel without invitation")
    
    # Check if already a member
    existing = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Already a member")
    
    member = ChannelMember(
        channel_id=channel_id,
        user_id=current_user.id
    )
    db.add(member)
    db.commit()
    
    return {"message": "Joined channel successfully"}


@router.post("/{channel_id}/leave")
async def leave_channel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Leave a channel"""
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=404, detail="Not a member of this channel")
    
    db.delete(membership)
    db.commit()
    
    return {"message": "Left channel successfully"}


@router.post("/{channel_id}/invite/{user_id}")
async def invite_to_channel(
    channel_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Invite a user to a channel"""
    # Check if current user is admin or creator
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership or membership.role not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Check if user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if already a member
    existing = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == user_id
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="User is already a member")
    
    member = ChannelMember(
        channel_id=channel_id,
        user_id=user_id
    )
    db.add(member)
    db.commit()
    
    return {"message": f"User {user.username} invited successfully"}


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a channel (only creator/admin)"""
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    if channel.created_by != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # Delete all members first
    db.query(ChannelMember).filter(ChannelMember.channel_id == channel_id).delete()
    db.delete(channel)
    db.commit()
    
    return {"message": "Channel deleted successfully"}


@router.post("/{channel_id}/invite-link")
async def generate_invite_link(
    channel_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Generate an invite link for a channel"""
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    # Check if user is admin or creator
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership or membership.role not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Only admins can generate invite links")
    
    # Generate unique invite code
    invite_code = str(uuid.uuid4())[:8]
    
    # Store invite code in channel's extra_data (or create a separate table)
    import json
    extra_data = json.loads(channel.extra_data) if channel.extra_data else {}
    extra_data["invite_code"] = invite_code
    channel.extra_data = json.dumps(extra_data)
    
    db.commit()
    
    # Return the invite link
    invite_link = f"/invite/{invite_code}"
    
    return {
        "invite_code": invite_code,
        "invite_link": invite_link,
        "full_url": f"http://localhost:8000{invite_link}"
    }



