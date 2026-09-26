"""Classifying a model response before anything downstream trusts it.

One failure mode motivates this module. A completion that runs out of budget
comes back as **HTTP 200 with ``finish_reason=length`` and empty content**. It
looks like a successful call that found nothing to say. For an extraction role
that difference is the difference between "this message asserts no change" and
"this message was never read", and the second silently drops evidence.

``scripts/probe_multilingual.py`` measured this on real data: at a 2048-token
budget, 37 of 45 non-English messages returned empty; at 8192, 13 did. None of
them was a language problem and none of them was an empty result.

So: **empty content is never a valid empty result.** It is retryable once the
budget has been raised, and a hard flag after that. Only the model explicitly
returning an empty structure counts as "nothing found".
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ResponseOutcome",
    "ResponseVerdict",
    "classify_response",
]


class ResponseOutcome(StrEnum):
    """What a provider response actually was."""

    #: Content present and usable.
    USABLE = "usable"
    #: HTTP 200, no content. Truncated or refused. NEVER an empty result.
    EMPTY_CONTENT = "empty_content"
    #: Provider signalled a retryable condition.
    TRANSIENT_ERROR = "transient_error"
    #: Provider signalled something retrying will not fix.
    PERMANENT_ERROR = "permanent_error"


class ResponseVerdict(BaseModel):
    """The decision about one response, and the reason for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ResponseOutcome
    retryable: bool
    detail: str
    finish_reason: str | None = None
    completion_tokens: int | None = None

    @property
    def is_usable(self) -> bool:
        return self.outcome is ResponseOutcome.USABLE

    @property
    def must_flag(self) -> bool:
        """True when this response must be recorded as a failure, not consumed."""
        return not self.is_usable


#: finish_reason values that mean the budget ran out rather than the model stopped.
TRUNCATION_REASONS = frozenset({"length", "max_tokens"})


def classify_response(
    body: dict[str, Any],
    status_code: int,
    *,
    retryable_status: frozenset[int] | set[int] | tuple[int, ...] = (),
    attempts_remaining: int = 0,
) -> ResponseVerdict:
    """Decide what happened, erring toward flagging rather than trusting.

    ``attempts_remaining`` controls only whether an empty response is offered as
    retryable; an empty response with no attempts left is a hard flag. It is
    never reported as a successful empty result either way.
    """
    if status_code != 200:
        transient = status_code in set(retryable_status)
        return ResponseVerdict(
            outcome=(
                ResponseOutcome.TRANSIENT_ERROR if transient
                else ResponseOutcome.PERMANENT_ERROR
            ),
            retryable=transient and attempts_remaining > 0,
            detail=f"HTTP {status_code}",
        )

    choices = body.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    content = message.get("content")
    finish_reason = choice.get("finish_reason")
    usage = body.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")

    if content is not None and str(content).strip():
        return ResponseVerdict(
            outcome=ResponseOutcome.USABLE,
            retryable=False,
            detail="content present",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
        )

    truncated = finish_reason in TRUNCATION_REASONS
    detail = (
        "HTTP 200 with empty content and finish_reason="
        f"{finish_reason!r}; the completion budget was consumed before any "
        "visible output. This is a truncated response, NOT an empty result."
        if truncated
        else "HTTP 200 with empty content; not a valid empty result"
    )
    return ResponseVerdict(
        outcome=ResponseOutcome.EMPTY_CONTENT,
        retryable=attempts_remaining > 0,
        detail=detail,
        finish_reason=finish_reason,
        completion_tokens=completion_tokens,
    )
