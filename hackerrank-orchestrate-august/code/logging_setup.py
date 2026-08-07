"""Structured JSON Lines logging.

One object per line, fixed field set. Untrusted text is never logged verbatim --
callers pass a digest via ``content_digest``. Event names are always enum
members, never free strings.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


def content_digest(text: str, chars: int) -> dict[str, Any]:
    """Digest untrusted text for logging: never the content, always the hash.

    The digest lets repeated payloads be correlated across a run without any of
    them being stored.
    """
    return {
        "sha256_prefix": hashlib.sha256(text.encode("utf-8")).hexdigest()[:chars],
        "length": len(text),
    }


class StructuredLogger:
    """Append-only JSONL logger. Holds no state beyond its sink."""

    def __init__(self, sink: TextIO) -> None:
        self._sink = sink

    def _emit(
        self,
        level: str,
        event: str,
        *,
        path: str,
        action_taken: str | None = None,
        message_id: str | None = None,
        media_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        record = {
            # Wall clock is permitted here: this module is one of the four
            # stamping modules exempted by ENGINEERING_CONVENTIONS section 7.
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": level,
            "event": event,
            "message_id": message_id,
            "media_id": media_id,
            "path": path,
            "detail": detail or {},
            "action_taken": action_taken,
        }
        self._sink.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._sink.flush()

    def info(self, event: str, **kwargs: Any) -> None:
        self._emit("INFO", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        if kwargs.get("action_taken") is None:
            raise ValueError("action_taken is mandatory on WARNING and ERROR")
        self._emit("WARNING", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        if kwargs.get("action_taken") is None:
            raise ValueError("action_taken is mandatory on WARNING and ERROR")
        self._emit("ERROR", event, **kwargs)


def build_logger(events_path: Path | None) -> StructuredLogger:
    """Open the JSONL sink. ``None`` sends records to stderr."""
    if events_path is None:
        return StructuredLogger(sys.stderr)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    return StructuredLogger(events_path.open("a", encoding="utf-8", newline="\n"))
