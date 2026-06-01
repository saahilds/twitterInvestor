from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config.settings import Settings


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for structured logs."""

    def __init__(self) -> None:
        super().__init__()
        self._default_record_fields = set(logging.makeLogRecord({}).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event_type": getattr(record, "event_type", None),
        }
        context: dict[str, object] = {}
        for key, value in record.__dict__.items():
            if key in self._default_record_fields or key in payload:
                continue
            if key.startswith("_") or value is None:
                continue
            context[key] = value

        if context:
            payload["context"] = context

        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure root logging for console and rotating file handlers."""
    log_file = Path(settings.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = JsonFormatter()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())
    root_logger.handlers = [stream_handler, file_handler]

    logger = logging.getLogger("trading_bot")
    logger.setLevel(settings.log_level.upper())
    return logger
