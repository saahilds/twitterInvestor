from __future__ import annotations

import json
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.db_models import ExecutionLog


class ExecutionAuditLogger:
    """Writes execution events to logs and execution_logs table."""

    def __init__(self, session_factory: Callable[[], Session], logger: logging.Logger) -> None:
        self.session_factory = session_factory
        self.logger = logger

    def write(self, level: str, event_type: str, message: str, payload: dict | None = None) -> None:
        payload = payload or {}
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(message, extra={"event_type": event_type, **payload})

        with self.session_factory() as db:
            db.add(
                ExecutionLog(
                    level=level.upper(),
                    event_type=event_type,
                    message=message[:255],
                    payload_json=json.dumps(payload, default=str),
                )
            )
            db.commit()
