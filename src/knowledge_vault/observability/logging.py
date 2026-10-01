import logging
import re
import sys
import traceback
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import structlog

_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_FIELDS = frozenset({"authorization", "token", "content", "normalized_content", "embedding"})
_MAX_FRAMES = 12


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


def describe_exception(exc: BaseException) -> dict[str, Any]:
    """Describe an exception without its message.

    Exception text can quote request data (database errors echo the offending values), so logs
    carry only the exception types, an SQLSTATE when there is one, and code locations.
    """
    described: dict[str, Any] = {
        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "error_frames": [
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)[-_MAX_FRAMES:]
        ],
    }
    origin = getattr(exc, "orig", None) or exc.__cause__
    if origin is not None:
        described["error_cause"] = f"{type(origin).__module__}.{type(origin).__qualname__}"
        sqlstate = getattr(origin, "sqlstate", None)
        if isinstance(sqlstate, str):
            described["sqlstate"] = sqlstate
    return described


def _sanitize_exc_info(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace a traceback (which embeds the exception message) with a content-free summary."""
    del logger, method_name
    exc_info = event_dict.pop("exc_info", None)
    if isinstance(exc_info, BaseException):
        event_dict.update(describe_exception(exc_info))
    elif isinstance(exc_info, tuple) and len(exc_info) == 3 and exc_info[1] is not None:
        event_dict.update(describe_exception(exc_info[1]))
    elif exc_info is True:
        current = sys.exc_info()[1]
        if current is not None:
            event_dict.update(describe_exception(current))
    return event_dict


class _StdoutHandler(logging.StreamHandler[Any]):
    """Write to whatever `sys.stdout` currently is, like structlog's print logger does."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging(
    level: str = "INFO", *, service: str = "knowledge-vault", version: str = "unknown"
) -> None:
    """Emit every log line, including third-party stdlib records, as redacted JSON."""

    def add_identity(
        logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        del logger, method_name
        event_dict.setdefault("service", service)
        event_dict.setdefault("version", version)
        return event_dict

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_identity,
    ]
    final: list[Any] = [_sanitize_exc_info, redact_event, structlog.processors.JSONRenderer()]

    handler = _StdoutHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *final],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    structlog.configure(
        processors=[*shared, *final],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
