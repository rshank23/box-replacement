from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any
from uuid import UUID

import structlog

#: Correlation id for the unit of work currently being processed.
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

#: Keys whose values must never reach the logs (Part 11 / least disclosure).
_REDACTED_KEYS = {
    "password",
    "vault_password",
    "secret",
    "jwt_secret",
    "token",
    "session_id",
    "authorization",
    "api_key",
}
_REDACTED = "***REDACTED***"


def bind_correlation_id(correlation_id: str | UUID | None) -> None:
    correlation_id_var.set(str(correlation_id) if correlation_id else None)


def _add_correlation_id(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    cid = correlation_id_var.get()
    if cid and "correlation_id" not in event_dict:
        event_dict["correlation_id"] = cid
    return event_dict


def _redact_secrets(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(level: str = "INFO", service: str = "backend") -> None:
    """Configure structlog + stdlib logging to emit a single JSON object per line."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_correlation_id,
            _redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)


__all__ = ["bind_correlation_id", "configure_logging", "correlation_id_var", "get_logger"]
