"""
Application Configuration
"""
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from typing import Optional
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "mysql+pymysql://root:mysql123@localhost:3306/agentchat?charset=utf8mb4"
    TDSQL_HOST: str = "localhost"
    TDSQL_PORT: int = 3306
    TDSQL_USER: str = "root"
    TDSQL_PASSWORD: str = ""
    TDSQL_DATABASE: str = "agentchat"

    # JWT
    SECRET_KEY: str = "your-secret-key-here"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # App
    APP_NAME: str = "AgentCollab"
    APP_VERSION: str = "1.0.0"


settings = Settings()
