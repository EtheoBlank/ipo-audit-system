# IPO 审计系统 — Hugging Face Space (Docker SDK) 部署镜像
#
# 单容器双进程:
#   * Streamlit 监听 7860 — 浏览器用户入口 (HF Space 唯一外露端口)
#   * uvicorn    监听 8000 — 内部 API, 仅供 Streamlit 服务端调用
#
# 构建:
#   docker build -t ipo-audit:latest .
# 启动:
#   docker run -p 7860:7860 -p 8000:8000 --env-file .env ipo-audit:latest
#
# 注: HF Space 不需要映射 8000 (容器内端口); 这里只用于本地验证。

# ---------- 基础镜像 ----------
# uv 官方镜像, 与项目工具链 (uv.lock) 一致, 比 pip 快 10x。
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# 容器内 locale / 时区 (舆情调度器 cron 用 Asia/Shanghai)
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ---------- 依赖层 (单独 cache) ----------
# 先复制锁文件, 利用 Docker layer cache: pyproject.toml/uv.lock 不变时这一层不重建
COPY pyproject.toml uv.lock ./

# --frozen 严格按 uv.lock 装, --no-dev 不装 pytest/ruff 等开发依赖,
# 不带 .[ocr] / .[scraper] 避免 paddlepaddle (4GB+) 与 chromium 二进制
# (代码已是 lazy import + 降级, 启动不会爆, 对应功能在 README 标注不可用)
RUN uv sync --frozen --no-dev

# ---------- 源码层 ----------
COPY app ./app
COPY frontend ./frontend
COPY templates ./templates
COPY scripts ./scripts

# 运行时需要但 .gitignore 不跟踪的空目录 — 占位避免 .dockerignore 误删
RUN mkdir -p uploads outputs && \
    touch uploads/.gitkeep outputs/.gitkeep

# ---------- 启动 ----------
# 7860 = Streamlit (HF Space 唯一外露端口)
# 8000 = FastAPI    (容器内服务, 不外露)
EXPOSE 7860 8000

# 默认环境变量 — 可被 HF Space 的 Variables and secrets 覆盖
ENV HOST=0.0.0.0 \
    PORT=8000 \
    API_BASE_URL=http://localhost:8000 \
    DEBUG=false \
    STREAMLIT_SERVER_PORT=7860 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# HF Space Docker 硬性要求: CMD 必须是前台进程, 绑住容器
# bash 启动脚本负责后台拉 uvicorn, 前台跑 streamlit
CMD ["bash", "scripts/start_hf_space.sh"]
