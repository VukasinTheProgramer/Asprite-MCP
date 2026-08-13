"""Structured logging to ~/.aseprite-mcp/logs/ (CLAUDE.md style rules).

**File only, never a stream.** stdout is the MCP transport — a StreamHandler
anywhere in this process corrupts the JSON-RPC framing and the client drops the
connection. That is why the logger sets `propagate = False`: without it a caller
who has run `logging.basicConfig()` gets root's stderr/stdout handler attached to
our records for free.

One line of JSON per record, so a log is greppable and machine-readable without
a parser. Rotated, because the previous hand-rolled `run_lua.log` appended
forever with nothing to bound it.

Before this, only `run_lua` wrote anything at all: bridge failures, command
timeouts and Aseprite tracebacks vanished entirely. The model saw the error text
and the user's disk recorded nothing — which is precisely the wrong way round
for the failure nobody can reproduce.
"""

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any

LOGGER_NAME = "aseprite_mcp"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """Idempotent: safe to call per server run, and per test."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False  # keep our records off root's stdout/stderr handlers

    target = str((logs_dir / "aseprite-mcp.log").resolve())
    for h in logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == target:
            return logger

    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """A child of the configured logger. Records go nowhere until
    `setup_logging` has run, which is deliberate: importing a module must never
    create files as a side effect."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
