"""
Agent Collaboration Platform - Main Application
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import os
import sys

# 全局日志配置。此前项目从未调用 basicConfig，root logger 停留在默认的
# WARNING 级别，所有 logger.info/debug 全部被丢弃 —— 排查问题时"日志里
# 什么都没有"，极易误判为功能未执行。
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("AgentChat.Main")

from .config import settings
from .database import init_db
from .api import auth, channels, messages, tasks, users, files, search, notifications
from .api import bridge_dist
from .websocket import endpoints as ws_endpoints
from .acp import router as acp_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    init_db()
    print("Database initialized")

    # 载入历史 ACP 会话（全部按离线恢复：连接无法跨进程持久化，
    # 等 agent 用 --resume-session 重连时再匹配到原会话）
    from .acp import session_manager
    restored = session_manager.load_persisted_sessions()
    if restored:
        logger.info("已载入 %d 个历史 agent 会话（均置为离线）", restored)

    yield
    # Shutdown
    print("Shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    description="Internal Agent Collaboration Platform - Reference Slack Architecture",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

# CORS middleware
# allow_origins=["*"] 与 allow_credentials=True 是互斥组合：浏览器会直接拒绝
# 带凭据的跨域请求（既不安全也不生效）。改为按需配置白名单。
_origins = [o.strip() for o in (settings.CORS_ORIGINS or "").split(",") if o.strip()]
if not _origins:
    _origins = ["*"]
_allow_credentials = _origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

if _origins == ["*"]:
    print("[WARN] CORS 允许任意来源且未启用凭据；生产环境请在 .env 设置 "
          "CORS_ORIGINS=https://your-domain", file=sys.stderr)

# Include API routers
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(bridge_dist.router, prefix="/api")

# Include WebSocket endpoints
app.include_router(ws_endpoints.router)

# Include ACP Socket Mode endpoints
app.include_router(acp_router)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.get("/{full_path:path}")
async def serve_react_app(full_path: str):
    """Serve React app with client-side routing support"""
    react_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    react_dist = os.path.normpath(react_dist)
    
    # Handle directory listing (e.g., /assets/)
    if full_path and os.path.isdir(os.path.join(react_dist, full_path)):
        return JSONResponse(content={"error": "Directory listing not allowed"})
    
    # Check if it's a static asset file request
    if full_path:
        file_path = os.path.join(react_dist, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
    
    # Otherwise serve the React index.html for client-side routing
    index_path = os.path.join(react_dist, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    
    return {"error": "React build not found"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    return JSONResponse(
        status_code=500,
        content={"message": f"Internal server error: {str(exc)}"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
