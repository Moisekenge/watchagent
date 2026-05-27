"""Structured (single-line JSON) logging.

The logging contract for this project (enforced by .cursor/rules):

* one JSON object per line on stdout — friendly to ``docker logs`` and any
  log shipper;
* every record carries ``ts`` (UTC ISO-8601), ``level``, ``logger``, ``message``;
* call sites attach structured context via the standard ``extra=`` kwarg, e.g.
  ``logger.warning("poll failed", extra={"city": "Ottawa", "http_status": 503})``.
  Any key passed in ``extra`` that is not a reserved ``LogRecord`` attribute is
  merged into the JSON payload verbatim.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Attribute names that live on every LogRecord by default. Anything outside this
# set was supplied by the caller via ``extra=`` and should be surfaced.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Uvicorn ships its own handlers; route them through ours for consistency.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
