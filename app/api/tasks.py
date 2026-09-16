"""
Task Management API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
import json

from ..database import get_db
from ..models import Task, AgentTask, User
from ..schemas import TaskCreate, TaskUpdate, TaskResponse, AgentTaskCreate, AgentTaskResponse, PaginatedResponse
from ..auth import get_current_user, require_agent

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("/", response_model=PaginatedResponse)
async def list_tasks(
    status: Optional[str] = None,
    assignee_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List tasks"""
    query = db.query(Task)
    
    # Filter by user's channels or own tasks
    query = query.filter(
        (Task.creator_id == current_user.id) |
        (Task.assignee_id == current_user.id)
    )
    
    if status:
        query = query.filter(Task.status == status)
    if assignee_id:
        query = query.filter(Task.assignee_id == assignee_id)
    if channel_id:
        query = query.filter(Task.channel_id == channel_id)
    
    total = query.count()
    tasks = query.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    
    return PaginatedResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size
    )


@router.post("/", response_model=TaskResponse)
async def create_task(
    task: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new task"""
    new_task = Task(
        title=task.title,
        description=task.description,
        channel_id=task.channel_id,
        creator_id=current_user.id,
        assignee_id=task.assignee_id,
        priority=task.priority,
        due_date=task.due_date
    )
    
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    return TaskResponse.model_validate(new_task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get task details"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 归属校验：此前只查 id，任意登录用户可越权读取他人任务
    if task.creator_id != current_user.id and task.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return TaskResponse.model_validate(task)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_update: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a task"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 归属校验：此前完全没有校验，任意登录用户可改写他人任务
    if task.creator_id != current_user.id and task.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Update fields
    if task_update.title is not None:
        task.title = task_update.title
    if task_update.description is not None:
        task.description = task_update.description
    if task_update.status is not None:
        task.status = task_update.status
    if task_update.assignee_id is not None:
        task.assignee_id = task_update.assignee_id
    if task_update.priority is not None:
        task.priority = task_update.priority
    if task_update.result is not None:
        task.result = task_update.result
    
    db.commit()
    db.refresh(task)
    
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a task"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.creator_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    db.delete(task)
    db.commit()
    
    return {"message": "Task deleted successfully"}


# ============ Agent Tasks ============

@router.post("/agent", response_model=AgentTaskResponse)
async def create_agent_task(
    agent_task: AgentTaskCreate,
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """Create an agent-to-agent task"""
    # Verify target agent exists and is an agent
    target = db.query(User).filter(
        User.id == agent_task.target_agent_id,
        User.is_agent == True
    ).first()
    
    if not target:
        raise HTTPException(status_code=404, detail="Target agent not found")
    
    new_task = AgentTask(
        parent_task_id=agent_task.parent_task_id,
        source_agent_id=current_user.id,
        target_agent_id=agent_task.target_agent_id,
        task_type=agent_task.task_type,
        input_data=json.dumps(agent_task.input_data)
    )
    
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    # TODO: Notify target agent via WebSocket
    
    return AgentTaskResponse.model_validate(new_task)


@router.get("/agent/{task_id}", response_model=AgentTaskResponse)
async def get_agent_task(
    task_id: int,
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """Get agent task details"""
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")
    
    # Only source or target agent can view
    if task.source_agent_id != current_user.id and task.target_agent_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return AgentTaskResponse.model_validate(task)


@router.put("/agent/{task_id}/complete")
async def complete_agent_task(
    task_id: int,
    output_data: dict,
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """Complete an agent task and return results"""
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")
    
    if task.target_agent_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only assigned agent can complete task")
    
    task.output_data = json.dumps(output_data)
    task.status = "completed"
    
    # Update parent task if exists
    if task.parent_task_id:
        parent = db.query(Task).filter(Task.id == task.parent_task_id).first()
        if parent:
            parent.status = "completed"
            parent.result = json.dumps(output_data)
    
    db.commit()
    
    return {"message": "Task completed", "task_id": task_id}


@router.put("/agent/{task_id}/fail")
async def fail_agent_task(
    task_id: int,
    error_message: str,
    current_user: User = Depends(require_agent),
    db: Session = Depends(get_db)
):
    """Report agent task failure"""
    task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Agent task not found")
    
    if task.target_agent_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only assigned agent can report failure")
    
    task.error_message = error_message
    task.status = "failed"
    
    # Update parent task if exists
    if task.parent_task_id:
        parent = db.query(Task).filter(Task.id == task.parent_task_id).first()
        if parent:
            parent.status = "failed"
            parent.result = error_message
    
    db.commit()
    
    return {"message": "Task marked as failed", "task_id": task_id}
