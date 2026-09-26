"""Message text to typed deltas.

The model reads the message; the guard in :mod:`bow.perception.untrusted`
decides what survives. Empty content is never a valid empty result — that is
handled in :mod:`bow.providers.response` and surfaces here as a failure, not as
"no deltas found".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from bow.perception.routing import Route, choose_route
from bow.perception.untrusted import (
    GuardResult,
    MessageDelta,
    MessageExtraction,
    guard_deltas,
)

__all__ = ["MessageOutcome", "extract_message", "render_candidates"]

_OPEN, _CLOSE = "<message>", "</message>"
_MAX_CANDIDATES = 12


class MessageOutcome:
    """What one message produced, and why."""

    def __init__(self, message_id: str, guard: GuardResult | None, route: Route,
                 detail: str, failed: bool = False) -> None:
        self.message_id = message_id
        self.guard = guard
        self.route = route
        self.detail = detail
        self.failed = failed

    @property
    def kept(self) -> tuple[MessageDelta, ...]:
        return self.guard.kept if self.guard else ()


def render_candidates(events: Sequence) -> str:
    """A compact typed view of the rows a message could plausibly target."""
    rows = [
        f"{e.event_id} | {e.event_type.value} | {e.category} | {e.direction.value} | "
        f"{e.amount if e.amount is not None else 'BLANK'} {e.currency} | "
        f"{e.event_date.isoformat()} | {e.status.value}"
        for e in events[:_MAX_CANDIDATES]
    ]
    return "\n".join(rows) if rows else "(no candidate events)"


def extract_message(
    message,
    candidate_events: Sequence,
    events_by_id: dict,
    client,
    *,
    prompt_path: Path,
    in_set: tuple[str, ...],
) -> MessageOutcome:
    """Detect, route, extract, then guard. Untrusted text never enters the system turn."""
    detected = client.detect_language(message.message_text)
    route = choose_route(detected, in_set=in_set)
    role = "message_extract" if route is Route.IN_SET else "message_extract_oos"

    system = prompt_path.read_text(encoding="utf-8")
    user = (
        "Candidate events (event_id | type | category | direction | amount | "
        f"date | status):\n{render_candidates(candidate_events)}\n\n"
        f"{_OPEN}\n{message.message_text}\n{_CLOSE}"
    )
    content, detail = client.complete(role, system, user)
    if content is None:
        return MessageOutcome(message.message_id, None, route, detail, failed=True)

    try:
        extraction = MessageExtraction.model_validate_json(content)
    except Exception as exc:  # noqa: BLE001
        return MessageOutcome(
            message.message_id, None, route, f"schema: {type(exc).__name__}", failed=True
        )

    guard = guard_deltas(
        extraction.deltas,
        message_user_id=message.user_id,
        events_by_id=events_by_id,
    )
    return MessageOutcome(message.message_id, guard, route, detail)
