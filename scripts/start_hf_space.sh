#!/usr/bin/env bash
# scripts/start_hf_space.sh
# HF Space 容器入口: 后台拉 uvicorn (8000), 前台跑 streamlit (7860)
#
# 设计原则:
#   * uvicorn 必先启 — streamlit 一上来就 fetch /health, API 还没就绪会报错
#   * uvicorn 是后台进程, streamlit 必须是前台 (绑住容器, HF Space 硬性要求)
#   * 收到 SIGTERM/SIGINT 时干净关掉 uvicorn, 避免孤儿进程 + 端口泄漏
#   * 直接调 .venv/bin 里的二进制, 不用 `uv run`, 避免每次启动触发
#     隐式 sync + editable build 浪费 30+ 秒
#   * 120s 健康检查 (cold start + 隐式 sync + 大量依赖 import 可能慢)

set -euo pipefail

API_PORT="${PORT:-8000}"
STREAMLIT_PORT="${STREAMLIT_SERVER_PORT:-7860}"
HEALTH_URL="http://127.0.0.1:${API_PORT}/health"
UVICORN_LOG="/tmp/uvicorn.log"
VENV_BIN="/app/.venv/bin"

# ---------- 日志辅助 ----------
log() { echo "[start_hf_space] $*"; }

# ---------- 收尾: 杀掉 uvicorn 子进程 ----------
UVICORN_PID=""
cleanup() {
    local exit_code=$?
    log "收到退出信号, 清理 uvicorn (PID=${UVICORN_PID:-?})..."
    if [[ -n "${UVICORN_PID}" ]] && kill -0 "${UVICORN_PID}" 2>/dev/null; then
        kill -TERM "${UVICORN_PID}" 2>/dev/null || true
        # 最多等 5 秒
        for _ in 1 2 3 4 5; do
            kill -0 "${UVICORN_PID}" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "${UVICORN_PID}" 2>/dev/null || true
    fi
    log "退出 (code=${exit_code})"
    exit "${exit_code}"
}
trap cleanup SIGTERM SIGINT EXIT

# ---------- 1. 后台拉 uvicorn ----------
log "启动 FastAPI (uvicorn) on 0.0.0.0:${API_PORT}..."
# 直接调 venv 里的 uvicorn, 跳过 `uv run` 的隐式 sync + editable build
PYTHONPATH=/app "${VENV_BIN}/uvicorn" app.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT}" \
    --no-access-log \
    --log-level info \
    > "${UVICORN_LOG}" 2>&1 &
UVICORN_PID=$!
log "uvicorn PID=${UVICORN_PID}, 日志: ${UVICORN_LOG}"

# ---------- 2. 等 API 就绪 (最多 120s) ----------
log "等待 ${HEALTH_URL} 就绪..."
for i in $(seq 1 120); do
    if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
        log "❌ uvicorn 进程已退出, 请看 ${UVICORN_LOG}:"
        cat "${UVICORN_LOG}" || true
        exit 1
    fi
    if curl -fsS -m 2 "${HEALTH_URL}" >/dev/null 2>&1; then
        log "✅ API 就绪 (用时 ${i}s)"
        break
    fi
    sleep 1
done

if ! curl -fsS -m 2 "${HEALTH_URL}" >/dev/null 2>&1; then
    log "❌ 120s 内 API 仍未就绪, 退出"
    tail -50 "${UVICORN_LOG}" || true
    exit 1
fi

# ---------- 3. 前台跑 streamlit (绑住容器) ----------
log "启动 Streamlit on 0.0.0.0:${STREAMLIT_PORT}..."
# 不让 SIGTERM 传到 uv 包装层, 由 trap cleanup 处理
exec "${VENV_BIN}/streamlit" run frontend/app.py \
    --server.port "${STREAMLIT_PORT}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false \
    --browser.gatherUsageStats false
