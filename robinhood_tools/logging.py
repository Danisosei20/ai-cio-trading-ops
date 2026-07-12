from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    REDACT = {"account_number", "token", "password", "cookie", "authorization"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        context = getattr(record, "context", {})
        payload.update({key: value for key, value in context.items() if key.lower() not in self.REDACT})
        return json.dumps(payload, default=str, sort_keys=True)


def get_logger(name: str = "ai_cio") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
