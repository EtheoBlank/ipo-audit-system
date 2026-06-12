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

# --no-install-project 让 uv 只装 runtime 依赖, 不构建 hatchling 项目本身 —
# 源码由下一段 COPY 进来, 通过 PYTHONPATH 让 uvicorn 找到 app.main。
# 这样绕开 hatchling wheel build 的环境问题, 同时保持 uv.lock 锁定的精确版本。
# --no-dev 不装 pytest/ruff 等开发依赖, 不带 .[ocr] / .[scraper] 避免 paddlepaddle (4GB+) 与 chromium 二进制
RUN uv sync --frozen --no-dev --no-install-project

# ---------- 源码层 ----------
# templates/ 仓库里只有 .gitkeep (空目录占位),COPY 不会失败;
# 实际目录由 app/core/config.py:ensure_dirs() 在启动时建,或由 HF 持久卷覆盖。
# README.md 必须 COPY — pyproject.toml [project] readme = "README.md",而 start_hf_space.sh
# 里的 `uv run uvicorn ...` 会触发隐式 sync + editable build,hatchling 校验 readme 字段时
# 若 README.md 不在 CWD 就直接 OSError("Readme file does not exist")。
COPY README.md ./
COPY app ./app
COPY frontend ./frontend
COPY scripts ./scripts
COPY templates ./templates

# 运行时需要但 .gitignore 不跟踪的空目录 — 占位避免 .dockerignore 误删
RUN mkdir -p uploads outputs && \
    touch uploads/.gitkeep outputs/.gitkeep

# HF Space 持久化卷挂载在 /data (由 Space runtime 自动挂, 容器内可见)。
# 把 /data 下的子目录在镜像里也建好, 这样 uvicorn 启动时 makedirs 不会失败
# (运行时若 /data 已挂载, 这些 mkdir 不会破坏现有数据)。
RUN mkdir -p /data/uploads /data/outputs /data/templates /data/uploads/knowledge_base /data/outputs/sentiment && \
    chmod -R 777 /data

# ---------- 启动 ----------
# 7860 = Streamlit (HF Space 唯一外露端口)
# 8000 = FastAPI    (容器内服务, 不外露)
EXPOSE 7860 8000

# 让 uvicorn / streamlit 找到源码 (绕开 hatchling wheel build)
ENV PYTHONPATH=/app \
    HOST=0.0.0.0 \
    PORT=8000 \
    API_BASE_URL=http://localhost:8000 \
    DEBUG=false \
    STREAMLIT_SERVER_PORT=7860 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# HF Space Docker 硬性要求: CMD 必须是前台进程, 绑住容器
# bash 启动脚本负责后台拉 uvicorn, 前台跑 streamlit
CMD ["bash", "scripts/start_hf_space.sh"]
