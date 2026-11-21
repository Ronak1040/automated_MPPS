import logging
import sys
from app.core.config import get_settings


class Formatter(logging.Formatter):
    """Custom standard log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record)


def setup_logging() -> None:
    """Configure system-wide logging based on application settings."""
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Reset existing root handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if not root_logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    else:
        for handler in root_logger.handlers:
            handler.setLevel(log_level)

    # Suppress overly verbose third-party loggers if needed
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Retrieve a configured logger instance."""
    return logging.getLogger(name)
