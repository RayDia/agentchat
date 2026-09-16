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
    """Initialize database tables

    注意：acp_sessions / acp_messages 两张旧 CLI bridge 表（app/acp/models.py）
    已成为死代码，不再注册到 Base.metadata；对应的库表已重命名为
    _deprecated_* 归档，不再由 ORM 管理。
    """
    Base.metadata.create_all(bind=engine)
