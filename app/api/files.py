"""
File Management API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import Optional
import os
import json
import hashlib
from datetime import datetime

from ..database import get_db
from ..models import Message, ChannelMember, User
from ..schemas import MessageResponse
from ..auth import get_current_user

router = APIRouter(prefix="/files", tags=["Files"])

# 配置上传目录
UPLOAD_DIR = "uploads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


@router.post("/upload")
async def upload_file(
    channel_id: int = Form(...),
    content: str = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload a file to a channel"""
    # 检查用户是否是频道成员
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    # 检查文件大小
    file_content = await file.read()
    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")
    
    # 生成文件名
    file_hash = hashlib.md5(file_content).hexdigest()
    file_ext = os.path.splitext(file.filename)[1]
    stored_filename = f"{file_hash}{file_ext}"
    
    # 创建目录
    channel_dir = os.path.join(UPLOAD_DIR, str(channel_id))
    os.makedirs(channel_dir, exist_ok=True)
    
    # 保存文件
    file_path = os.path.join(channel_dir, stored_filename)
    with open(file_path, "wb") as f:
        f.write(file_content)
    
    # 创建文件消息
    metadata = {
        "file_name": file.filename,
        "file_path": file_path,
        "file_size": len(file_content),
        "file_type": file.content_type,
        "stored_filename": stored_filename
    }
    
    message = Message(
        channel_id=channel_id,
        sender_id=current_user.id,
        content=content or f"[文件] {file.filename}",
        message_type="file",
        metadata=json.dumps(metadata)
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    
    return {
        "message": MessageResponse.model_validate(message),
        "file_url": f"/api/files/download/{channel_id}/{stored_filename}"
    }


@router.get("/download/{channel_id}/{filename}")
async def download_file(
    channel_id: int,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Download a file"""
    # 检查用户是否是频道成员
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    file_path = os.path.join(UPLOAD_DIR, str(channel_id), filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # 从数据库获取原始文件名
    message = db.query(Message).filter(
        Message.channel_id == channel_id,
        Message.message_type == "file",
        Message.extra_data.contains(filename)
    ).first()
    
    original_name = filename
    if message and message.extra_data:
        metadata = json.loads(message.extra_data)
        original_name = metadata.get("file_name", filename)
    
    return FileResponse(
        path=file_path,
        filename=original_name,
        media_type="application/octet-stream"
    )


@router.get("/list/{channel_id}")
async def list_files(
    channel_id: int,
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List files in a channel"""
    # 检查用户是否是频道成员
    membership = db.query(ChannelMember).filter(
        ChannelMember.channel_id == channel_id,
        ChannelMember.user_id == current_user.id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this channel")
    
    # 获取文件消息
    messages = db.query(Message).filter(
        Message.channel_id == channel_id,
        Message.message_type == "file",
        Message.is_deleted == False
    ).order_by(Message.created_at.desc()) \
     .offset((page - 1) * page_size) \
     .limit(page_size) \
     .all()
    
    files = []
    for msg in messages:
        if msg.extra_data:
            metadata = json.loads(msg.extra_data)
            files.append({
                "id": msg.id,
                "file_name": metadata.get("file_name"),
                "file_size": metadata.get("file_size"),
                "file_type": metadata.get("file_type"),
                "uploaded_by": msg.sender_id,
                "uploaded_at": msg.created_at.isoformat(),
                "download_url": f"/api/files/download/{channel_id}/{metadata.get('stored_filename')}"
            })
    
    return {"files": files}


@router.delete("/{message_id}")
async def delete_file(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a file (soft delete message, optionally delete physical file)"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="File message not found")
    
    if message.sender_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    # 软删除消息
    message.is_deleted = True
    message.content = "[文件已删除]"
    db.commit()
    
    # 注意：物理文件可以选择保留或删除
    # if message.extra_data:
    #     metadata = json.loads(message.extra_data)
    #     file_path = metadata.get("file_path")
    #     if file_path and os.path.exists(file_path):
    #         os.remove(file_path)
    
    return {"message": "File deleted successfully"}
