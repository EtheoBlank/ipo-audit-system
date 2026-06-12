"""Application-wide logging configuration.

Import and call ``setup_logging()`` once at startup (e.g. in the FastAPI
lifespan handler) so every module can simply do ``logging.getLogger(__name__)``
and get consistent, structured output.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

# Default log format — readable in the console, parseable by log aggregators
_DEFAULT_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level: str | int = logging.INFO,
    log_format: Optional[str] = None,
    date_format: Optional[str] = None,
) -> None:
    """Configure the root logger with a stream handler.

    This is intentionally lightweight — for production you'd swap in
    structured JSON logging (e.g. python-json-logger) or send to
    Logstash / Datadog.
    """
    fmt = log_format or _DEFAULT_FMT
    dfmt = date_format or _DEFAULT_DATE_FMT

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=dfmt))

    root = logging.getLogger()
    # Avoid adding duplicate handlers if setup_logging is called more than once
    if not root.handlers:
        root.addHandler(handler)

    root.setLevel(level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO))

    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
