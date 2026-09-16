"""
Authentication and Authorization Module
"""
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import hashlib
import hmac
import secrets
from .config import settings
from .database import get_db
from .models import User
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash.

    格式为 `<版本>:<salt>:<hash>`。当前 new/old 两个版本使用同一算法
    （PBKDF2-HMAC-SHA256，10 万轮），此处合并处理以免出现两份完全相同的
    代码；若将来引入算法升级，应在此按版本分派。

    比较使用 hmac.compare_digest 而非 ==，避免通过响应时间侧信道推断哈希。
    """
    if not hashed_password or ":" not in hashed_password:
        return False
    try:
        _version, salt, stored_hash = hashed_password.split(":", 2)
    except ValueError:
        return False
    computed_hash = hashlib.pbkdf2_hmac(
        'sha256', plain_password.encode(), salt.encode(), 100000).hex()
    return hmac.compare_digest(computed_hash, stored_hash)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    salt = secrets.token_hex(16)
    computed_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"new:{salt}:{computed_hash.hex()}"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """Dependency for getting the current authenticated user"""
    payload = decode_token(token)
    username: str = payload.get("sub")
    user_id: int = payload.get("user_id")
    
    if username is None and user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
    
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
    else:
        user = db.query(User).filter(User.username == username).first()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Dependency for getting the current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency for requiring admin role"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def require_agent(current_user: User = Depends(get_current_user)) -> User:
    """Dependency for requiring agent role"""
    if not current_user.is_agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent privileges required",
        )
    return current_user
