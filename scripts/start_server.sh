#!/usr/bin/env bash
# AgentChat 后端服务 —— 启动 / 停止 / 重启 / 状态
#
# 用法：
#   bash scripts/start_server.sh start|stop|restart|status
#
# 重要：这里**刻意不使用 uvicorn --reload**。
# --reload 在热重载后会残留旧的 socket accept 回调，事件循环认为监听套接字
# 一直可读，于是反复调用 accept() 失败并打印完整堆栈，实测可达约 2GB/分钟，
# 28 分钟写满 60GB 导致根分区 100%。生产/常驻环境请一律用本脚本启动。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/backend.log"
PID_FILE="$LOG_DIR/backend.pid"
PY="$REPO_ROOT/.venv/bin/python"
APP="app.main:app"

info() { printf '\033[1;34m[信息]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[完成]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; }

running_pid() {
    # 优先按 pid 文件取，失效则回退到 ps 匹配
    local pid=""
    [ -f "$PID_FILE" ] && pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
        echo "$pid"; return 0
    fi
    pid="$(ps -eo pid,args | grep "[u]vicorn $APP" | awk '{print $1}' | head -1)"
    [ -n "$pid" ] && echo "$pid" || true
}

is_up() {
    timeout 5 curl -sS -o /dev/null "http://127.0.0.1:${PORT}/api/bridge/version" 2>/dev/null
}

do_start() {
    if [ -n "$(running_pid)" ]; then
        ok "服务已在运行（PID $(running_pid)），无需重复启动"
        return 0
    fi
    [ -x "$PY" ] || { err "未找到虚拟环境解释器: $PY"; exit 1; }
    mkdir -p "$LOG_DIR"

    info "启动服务: ${HOST}:${PORT}（无 --reload）"
    setsid nohup "$PY" -m uvicorn "$APP" --host "$HOST" --port "$PORT" \
        >> "$LOG_FILE" 2>&1 < /dev/null &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    for _ in $(seq 1 30); do
        sleep 1
        if is_up; then
            ok "服务已就绪（PID $pid）"
            ok "日志: $LOG_FILE"
            return 0
        fi
    done
    err "服务启动后 30 秒仍未就绪，请查看日志尾部："
    tail -20 "$LOG_FILE" >&2 || true
    return 1
}

do_stop() {
    local pid
    pid="$(running_pid)"
    if [ -z "$pid" ]; then
        info "服务未在运行"
        return 0
    fi
    info "停止服务 PID $pid …"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        sleep 1
        if ! ps -p "$pid" >/dev/null 2>&1; then
            rm -f "$PID_FILE"
            ok "已停止"
            return 0
        fi
    done
    err "优雅停止超时，改用 SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
}

do_status() {
    local pid
    pid="$(running_pid)"
    if [ -z "$pid" ]; then
        err "服务未运行"
        return 1
    fi
    ok "运行中 PID $pid"
    ps -o pid,etime,%cpu,%mem,args -p "$pid" | tail -n +1
    if is_up; then ok "健康检查通过 http://127.0.0.1:${PORT}"; else err "健康检查失败"; fi
    if [ -f "$LOG_FILE" ]; then
        info "日志大小: $(du -h "$LOG_FILE" | cut -f1)"
    fi
}

case "${1:-start}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; sleep 1; do_start ;;
    status)  do_status ;;
    *)       echo "用法: $0 start|stop|restart|status"; exit 1 ;;
esac
