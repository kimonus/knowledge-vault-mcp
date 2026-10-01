import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_FIELDS = frozenset({"authorization", "token", "content", "normalized_content", "embedding"})


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _BEARER.sub("Bearer [REDACTED]", value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold() in _SECRET_FIELDS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_event(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    del logger, method_name
    return redact_value(event_dict)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
