"""Structured logging: one JSON object per line on stdout.

Every record carries ``ts`` (ISO-8601, UTC), ``level``, ``service`` and ``msg``. Fields passed with
``extra={...}`` are added as top-level keys. Secrets must never be passed to the logger.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime"}
_CORE_KEYS = ("ts", "level", "service", "msg")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            # An extra field must not overwrite a core field; keep it under a prefixed name instead.
            payload[f"extra_{key}" if key in _CORE_KEYS else key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        # ensure_ascii keeps the line pure ASCII so it survives any stdout encoding.
        return json.dumps(payload, ensure_ascii=True, default=str)


class _StdoutHandler(logging.StreamHandler):
    """StreamHandler that looks up ``sys.stdout`` on every emit instead of binding it once."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout


def configure(service: str) -> None:
    """Send the root logger to stdout as JSON lines, tagged with ``service``. Safe to call twice."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _StdoutHandler)]:
        root.removeHandler(handler)
    handler = _StdoutHandler()
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
