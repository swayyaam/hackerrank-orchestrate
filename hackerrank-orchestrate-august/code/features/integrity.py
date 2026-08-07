"""Derive the row's integrity warnings from assembled facts.

A warning is an **observation**, not a decision. It is recorded whether or not a
rule fired on it, and that independence is the point: a domain mismatch on a
*verified* business raises no rule (``IMPERSONATION`` requires unverified) but is
still a fact the output should carry. Deriving warnings from rule fires instead
would make exactly the observations that no rule covers invisible — which is the
subset most worth seeing.

Pure. No I/O, no clock, no model. Every warning is a function of values already
computed upstream, so the same row always produces the same set.
"""

from __future__ import annotations

from schemas import (
    CallTelemetry,
    DomainState,
    FeatureVector,
    MediaArtifact,
    Perception,
    ThreatEvent,
)

MEDIA_FAILURE_EVENTS: dict[str, ThreatEvent] = {
    "path_rejected": ThreatEvent.MEDIA_PATH_ESCAPED_ROOT,
    "limit_exceeded": ThreatEvent.MEDIA_LIMIT_EXCEEDED,
}
"""Media failure causes that are threat observations rather than plain absence.

A missing file, an unsupported container, or audio longer than the transcription
endpoint accepts are operational facts already reported through
``media_failure_cause``; they say nothing about intent. A path that escaped the
dataset root or a file that breached a resource cap do."""


def threat_events(
    perception: Perception,
    features: FeatureVector,
    media: MediaArtifact | None,
    telemetry: CallTelemetry,
    prompt_events: tuple[ThreatEvent, ...] = (),
) -> tuple[ThreatEvent, ...]:
    """Every integrity observation for one row, in a stable order.

    ``prompt_events`` are raised during prompt construction — a fence token in
    the message text, a truncated field, stripped control characters — and are
    passed in rather than recomputed, because only the module that built the
    prompt knows what it had to clean.
    """
    events: list[ThreatEvent] = list(prompt_events)

    if perception.contains_router_directive:
        events.append(ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT)
    if perception.media_contradicts_text:
        events.append(ThreatEvent.MEDIA_CONTRADICTS_TEXT)

    # Identity observations are recorded on their own terms. IMPERSONATION fires
    # only on an *unverified* sender, so a verified account presenting from an
    # unowned domain would otherwise leave no trace at all.
    if features.domain_state is DomainState.MISMATCH:
        events.append(ThreatEvent.BUSINESS_DOMAIN_MISMATCH)
    if not features.business_identity_consistent:
        events.append(ThreatEvent.BUSINESS_IDENTITY_INCONSISTENT)

    if media is not None and not media.readable:
        mapped = MEDIA_FAILURE_EVENTS.get(media.failure_reason or "")
        if mapped is not None:
            events.append(mapped)

    if telemetry.schema_retry_count > 0:
        events.append(ThreatEvent.MODEL_RESPONSE_SCHEMA_REJECTED)

    # Deduplicate while keeping declaration order, so the record is stable and
    # two runs of the same row produce byte-identical warning lists.
    ordered = [event for event in ThreatEvent if event in set(events)]
    return tuple(ordered)
