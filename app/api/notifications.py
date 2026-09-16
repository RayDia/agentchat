"""
Notification System
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from ..database import get_db
from ..models import User, Notification, Message, Channel
from ..schemas import PaginatedResponse
from ..auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/", response_model=PaginatedResponse)
async def list_notifications(
    is_read: Optional[bool] = None,
    notification_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List user notifications"""
    query = db.query(Notification).filter(
        Notification.user_id == current_user.id
    )
    
    if is_read is not None:
        query = query.filter(Notification.is_read == is_read)
    
    if notification_type:
        query = query.filter(Notification.type == notification_type)
    
    total = query.count()
    notifications = query.order_by(Notification.created_at.desc()) \
                        .offset((page - 1) * page_size) \
                        .limit(page_size) \
                        .all()
    
    return PaginatedResponse(
        items=[{
            "id": n.id,
            "type": n.type,
            "title": n.title,
            "content": n.content,
            "link": n.link,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat()
        } for n in notifications],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.get("/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get unread notification count"""
    count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).scalar()
    
    return {"unread_count": count}


@router.put("/{notification_id}/read")
async def mark_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark notification as read"""
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    notification.is_read = True
    db.commit()
    
    return {"message": "Notification marked as read"}


@router.put("/read-all")
async def mark_all_as_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark all notifications as read"""
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).update({"is_read": True})
    
    db.commit()
    
    return {"message": "All notifications marked as read"}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a notification"""
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    db.delete(notification)
    db.commit()
    
    return {"message": "Notification deleted"}


@router.delete("/clear")
async def clear_all_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Clear all notifications"""
    db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).delete()
    
    db.commit()
    
    return {"message": "All notifications cleared"}


# ============ 通知服务 ============

class NotificationService:
    """Notification service for creating and managing notifications"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_notification(
        self,
        user_id: int,
        type: str,
        title: str,
        content: str = None,
        link: str = None
    ) -> Notification:
        """Create a new notification"""
        notification = Notification(
            user_id=user_id,
            type=type,
            title=title,
            content=content,
            link=link
        )
        self.db.add(notification)
        self.db.commit()
        return notification
    
    def notify_mention(
        self,
        mentioned_user_id: int,
        message: Message,
        channel: Channel
    ):
        """Notify user about @mention"""
        return self.create_notification(
            user_id=mentioned_user_id,
            type="mention",
            title=f"在 #{channel.name} 中被@提及",
            content=message.content[:200],
            link=f"/channels/{channel.id}?message={message.id}"
        )
    
    def notify_task_assigned(
        self,
        user_id: int,
        task_title: str,
        task_id: int
    ):
        """Notify user about task assignment"""
        return self.create_notification(
            user_id=user_id,
            type="task_assigned",
            title="新任务分配",
            content=f"你被分配了任务: {task_title}",
            link=f"/tasks/{task_id}"
        )
    
    def notify_task_completed(
        self,
        user_id: int,
        task_title: str,
        task_id: int
    ):
        """Notify task creator about task completion"""
        return self.create_notification(
            user_id=user_id,
            type="task_completed",
            title="任务已完成",
            content=f"任务 '{task_title}' 已完成",
            link=f"/tasks/{task_id}"
        )
    
    def notify_agent_response(
        self,
        user_id: int,
        agent_name: str,
        task_id: int
    ):
        """Notify about agent task response"""
        return self.create_notification(
            user_id=user_id,
            type="agent_response",
            title="Agent 任务响应",
            content=f"Agent {agent_name} 已完成任务",
            link=f"/tasks/agent/{task_id}"
        )
