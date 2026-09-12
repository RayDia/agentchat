"""
Database Configuration and Session Management for TDSQL
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

# Create engine with connection pooling for TDSQL
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables"""
    # 确保 ACP 桥接持久化表被注册（thread -> session）
    try:
        from .acp.models import ACPBridgeSession, ACPBridgeMessage  # noqa: F401
    except Exception:
        pass
    Base.metadata.create_all(bind=engine)
