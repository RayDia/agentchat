"""
Database Initialization Script
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine, Base
from app.models import *  # Import all models to ensure they're registered


def init_database():
    """Initialize database tables"""
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")


def drop_database():
    """Drop all database tables"""
    print("Dropping database tables...")
    Base.metadata.drop_all(bind=engine)
    print("Database tables dropped!")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "drop":
        drop_database()
    else:
        init_database()
