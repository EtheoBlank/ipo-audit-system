"""Core configuration for IPO Audit System."""
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Regulatory case scraping
    CSRC_URL: str = "http://www.csrc.gov.cn"
    SseUrl: str = "http://www.sse.com.cn"
    SzseUrl: str = "http://www.szse.cn"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()

# Ensure directories exist
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)