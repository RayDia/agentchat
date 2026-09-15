"""
Authentication API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta

from ..database import get_db
from ..models import User
from ..schemas import UserCreate, UserResponse, Token, AgentRegister, PasswordChange
from ..auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
    settings
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=Token)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if username exists
    existing_user = db.query(User).filter(User.username == user.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email exists
    if user.email:
        existing_email = db.query(User).filter(User.email == user.email).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
    
    # Create new user
    new_user = User(
        username=user.username,
        email=user.email,
        hashed_password=get_password_hash(user.password),
        display_name=user.display_name or user.username,
        role=user.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create access token
    access_token = create_access_token(
        data={"sub": new_user.username, "user_id": new_user.id},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    return Token(
        access_token=access_token,
        user=UserResponse.model_validate(new_user)
    )


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Login and get access token"""
    user = db.query(User).filter(User.username == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    
    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.id},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    return Token(
        access_token=access_token,
        user=UserResponse.model_validate(user)
    )


@router.post("/register-agent", response_model=Token)
async def register_agent(agent: AgentRegister, db: Session = Depends(get_db)):
    """Register a new agent"""
    import json
    
    # Check if username exists
    existing_user = db.query(User).filter(User.username == agent.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Create new agent user
    new_agent = User(
        username=agent.username,
        hashed_password=get_password_hash(agent.password),
        display_name=agent.display_name,
        role="agent",
        is_agent=True,
        agent_capabilities=json.dumps(agent.capabilities)
    )
    db.add(new_agent)
    db.commit()
    db.refresh(new_agent)
    
    # Create access token
    access_token = create_access_token(
        data={"sub": new_agent.username, "user_id": new_agent.id, "is_agent": True},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    return Token(
        access_token=access_token,
        user=UserResponse.model_validate(new_agent)
    )


@router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """修改当前登录用户的密码。

    传了 old_password 则校验（推荐，防止 token 被盗后直接改密）；
    不传则视为已由 token 授权直接改。
    """
    if payload.old_password is not None:
        if not verify_password(payload.old_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect old password"
            )

    current_user.hashed_password = get_password_hash(payload.new_password)
    db.commit()
    return {"detail": "Password updated", "username": current_user.username}


@router.post("/agents/{agent_id}/password")
async def set_agent_password(
    agent_id: int,
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为 Agent 用户设置密码（供远端桥接走 /api/auth/login 使用）。

    权限：Agent 本人（凭自己的 token）或管理员。
    这里不校验 old_password —— 目的是给"注册时无密码/密码遗失"的 agent
    补设密码，属于运维动作，由 token 身份把关。
    """
    target = db.query(User).filter(User.id == agent_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not target.is_agent:
        raise HTTPException(status_code=400, detail="Target user is not an agent")

    # 允许：本人 或 管理员
    if current_user.id != target.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the agent itself or an admin may set this password"
        )

    target.hashed_password = get_password_hash(payload.new_password)
    db.commit()
    return {"detail": "Agent password updated", "agent_id": target.id,
            "username": target.username}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    display_name: str = None,
    email: str = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update current user profile"""
    if display_name:
        current_user.display_name = display_name
    if email:
        current_user.email = email
    
    db.commit()
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)
