import json
import logging
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def configure_logging(level: str = "INFO") -> None:
    """Configure a minimal JSON-lines logger."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _json_default(value: Any) -> Any:
    """Serialize common Python values used in logs."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return str(value)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one structured log line as JSON."""
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, default=_json_default, ensure_ascii=True))
