#!/bin/bash
# 开发环境启动：前端 Vite dev server + 后端 API
#
# 注意：后端**不使用 --reload**。uvicorn 的 reload 在重载后会残留旧的
# socket accept 回调，事件循环持续判定监听套接字可读，反复 accept() 失败
# 并打印完整堆栈 —— 实测可达约 2GB/分钟，曾 28 分钟写满 60GB 根分区。
# 需要热重载时请显式执行：python -m uvicorn app.main:app --reload
set -e
cd "$(dirname "$0")"

echo "[dev] 启动前端 Vite dev server…"
(cd frontend && npm run dev) &

echo "[dev] 启动后端 API (0.0.0.0:8000)…"
source .venv/bin/activate
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
