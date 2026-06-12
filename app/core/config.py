"""Core configuration for IPO Audit System."""

import logging
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App settings
    APP_NAME: str = "IPO审计系统"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database settings
    DATABASE_URL: str = "sqlite+aiosqlite:///./ipo_audit.db"

    # Redis settings
    REDIS_URL: str = "redis://localhost:6379/0"

    # File settings
    UPLOAD_DIR: Path = Path("./uploads")
    OUTPUT_DIR: Path = Path("./outputs")
    TEMPLATE_DIR: Path = Path("./templates")

    # Excel settings
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: set = {".xlsx", ".xls", ".csv"}

    # AI settings (MiniMax API)
    MINIMAX_API_KEY: str = ""
    MINIMAX_API_BASE: str = "https://api.minimaxi.com/v1"

    # AI settings (DeepSeek API) — used by the sales-ledger module
    # The API key MUST be supplied via .env; never commit a real key.
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # AI settings (Volcano Engine / 火山引擎) — used when MiniMax is stuck
    VOLCANO_ENGINE_API_KEY: str = ""
    VOLCANO_ENGINE_API_BASE: str = "https://ark.cn-beijing.volces.com/api/v3/"
    VOLCANO_ENGINE_MODEL: str = "glm-5.1"

    # Regulatory case scraping — consistent UPPER_SNAKE naming
    CSRC_URL: str = "http://www.csrc.gov.cn"
    SSE_URL: str = "http://www.sse.com.cn"
    SZSE_URL: str = "http://www.szse.cn"

    # 法律法规来源 URL — 自动抓取官方政策文件用
    # 这些站点结构经常变，URL 模板放在配置里方便后续微调
    MOF_URL: str = "http://www.mof.gov.cn"                      # 财政部
    MOF_ACCOUNTING_URL: str = "http://kjs.mof.gov.cn"           # 财政部会计司
    STA_URL: str = "http://www.chinatax.gov.cn"                 # 国家税务总局
    SAFE_URL: str = "http://www.safe.gov.cn"                    # 国家外汇管理局
    PBOC_URL: str = "http://www.pbc.gov.cn"                     # 中国人民银行

    # 抓取行为
    REGULATION_FETCH_TIMEOUT: int = 30                          # 单请求秒
    REGULATION_FETCH_RETRY: int = 2                             # 重试次数
    REGULATION_MAX_PAGES: int = 5                               # 默认抓取页数上限
    REGULATION_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )

    # 知识库 (Knowledge Base)
    KNOWLEDGE_BASE_DIR: Path = Path("./uploads/knowledge_base")  # 书籍原文件保存目录
    KB_CHUNK_SIZE: int = 600                                     # 每个切块的字符数
    KB_CHUNK_OVERLAP: int = 80                                   # 相邻切块重叠字符
    KB_EMBEDDING_PROVIDER: str = "tfidf"                         # tfidf / minimax / deepseek
    KB_EMBEDDING_MODEL: str = "embo-01"                          # MiniMax embedding 模型名
    KB_EMBEDDING_DIM: int = 1024                                 # 远端嵌入维度（仅 minimax/deepseek 用）
    KB_DEFAULT_TOP_K: int = 5                                    # 检索默认返回条数
    KB_MAX_BOOK_SIZE: int = 200 * 1024 * 1024                    # 单本书最大 200MB

    # === 舆情跟踪 (Sentiment Tracking) — v0.2 新增 ===
    SENTIMENT_OUTPUT_DIR: Path = Path("./outputs/sentiment")     # 简报/季报 .docx 落盘根
    SENTIMENT_SCAN_CRON: str = "30 8 * * 1-6"                   # APScheduler cron 表达式（5 字段，工作日 + 周六 8:30）
    SENTIMENT_SCAN_TIMEZONE: str = "Asia/Shanghai"               # 调度时区
    SENTIMENT_FETCH_TIMEOUT: int = 30                            # 单请求超时（秒）
    SENTIMENT_FETCH_RETRY: int = 2                               # 重试次数
    SENTIMENT_MAX_EVENTS_PER_PROJECT_PER_DAY: int = 200          # 防抓爆 — 单项目/单日事件数上限
    SENTIMENT_LLM_TEMPERATURE: float = 0.1                       # 主生成温度
    SENTIMENT_LLM_MAX_TOKENS: int = 6000                         # 主生成 max_tokens
    SENTIMENT_VERIFY_LLM_TEMPERATURE: float = 0.0                # 校验轮温度（0 = 严格确定）
    SENTIMENT_BRIEFING_EVENT_LOOKBACK_HOURS: int = 36            # 简报"今天事件"回溯窗口（h）

    # 付费信源 API Key（留空 = 对应信源自动跳过，scheduler 写 last_run_status=skipped）
    # 仅做配置登记，不向用户推荐任何特定服务，付费决策由用户自行决定。
    TAVILY_API_KEY: str = ""
    BOCHA_API_KEY: str = ""
    SERPAPI_API_KEY: str = ""

    # CORS settings
    CORS_ORIGINS: str = "http://localhost:8501,http://localhost:3000"  # comma-separated

    def ensure_dirs(self) -> None:
        """Create required directories if they don't exist.

        Call this explicitly at startup instead of relying on module-level
        side effects so that importing the config never touches the filesystem.
        """
        for d in (
            self.UPLOAD_DIR,
            self.OUTPUT_DIR,
            self.TEMPLATE_DIR,
            self.KNOWLEDGE_BASE_DIR,
            self.SENTIMENT_OUTPUT_DIR,
        ):
            d.mkdir(parents=True, exist_ok=True)
            logger.debug("Ensured directory exists: %s", d)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
