"""
Application Configuration
"""
import os
import secrets
import sys

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

# 这些值一旦生效，任何人都能用它们自签 JWT 从而冒充任意用户
# （SECRET_KEY 曾长期是下面的默认占位值，属于可比完全接管的高危问题）
_INSECURE_SECRET_KEYS = {
    "",
    "your-secret-key-here",
    "change-me",
    "secret",
    "changeme",
    "test",
    "dev",
}
_MIN_SECRET_LEN = 32


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
    # 默认改为「未设置」，由下方校验强制从 .env 提供，不再内置可用密钥
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # App
    APP_NAME: str = "AgentCollab"
    APP_VERSION: str = "1.0.0"

    # CORS：逗号分隔的来源白名单；"*" 表示全部放行（仅开发用）
    CORS_ORIGINS: str = "*"

    # 安全开关：设为 1/true 时，即使 SECRET_KEY 不安全也只告警不退出
    ALLOW_INSECURE_SECRET: str = "0"


def _is_insecure(key: str) -> bool:
    return (key.strip() in _INSECURE_SECRET_KEYS) or (len(key.strip()) < _MIN_SECRET_LEN)


def _ensure_secret_key(settings_obj: "Settings") -> None:
    """启动时强校验 SECRET_KEY，避免默认值导致 JWT 可被伪造。"""
    allow = str(settings_obj.ALLOW_INSECURE_SECRET).lower() in ("1", "true", "yes")
    key = settings_obj.SECRET_KEY or ""

    if not _is_insecure(key):
        return

    reason = "使用了已知的默认占位值" if key.strip() in _INSECURE_SECRET_KEYS \
        else f"长度不足 {_MIN_SECRET_LEN} 位（当前 {len(key.strip())}）"

    msg = (
        "SECRET_KEY 不安全（%s）。任何人都能据此伪造 JWT 冒充任意用户。\n"
        "请生成一个随机密钥写入 .env：\n"
        "    python3 -c \"import secrets;print('SECRET_KEY='+secrets.token_urlsafe(48))\" >> .env\n"
    ) % reason

    if not allow:
        print("[FATAL] " + msg, file=sys.stderr)
        # 生产环境直接拒绝启动；开发可用 ALLOW_INSECURE_SECRET=1 临时绕过
        if _is_production():
            raise RuntimeError(msg)
        # 非生产环境自动生成一次性密钥，保证进程可用但 token 重启即失效
        settings_obj.SECRET_KEY = secrets.token_urlsafe(48)
        print("[WARN] 已临时生成随机 SECRET_KEY（重启后失效，请尽快写入 .env）",
              file=sys.stderr)
    else:
        print("[WARN] 已按 ALLOW_INSECURE_SECRET 忽略 SECRET_KEY 风险，切勿用于生产",
              file=sys.stderr)


def _is_production() -> bool:
    return str(os.environ.get("APP_ENV", "")).lower() in ("prod", "production")


settings = Settings()
_ensure_secret_key(settings)
