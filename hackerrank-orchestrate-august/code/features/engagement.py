"""Convert interaction events into a bounded engagement score and a stance.

Every field is read independently and combined monotonically, so a profile
never seen before still produces a sensible score. There is no branch on the
joint outcome tuple and no comparison against an observed reaction time.
"""

from __future__ import annotations

from typing import Sequence

from config import PolicyConfig
from schemas import EngagementStance, InteractionEvent

# Score contributions. Ordinal weights expressing that replying is stronger
# evidence of value than opening, and that reporting is the strongest possible
# rejection. Their absolute size is immaterial -- only their order and sign
# matter, because the score is thresholded, never compared to a constant.
_REPLIED = 2.0
_OPENED = 1.0
_DISMISSED = -1.0
_MUTED = -1.0
_REPORTED = -2.0


def engagement_score(event: InteractionEvent) -> float:
    """A monotone score for one interaction. Positive means engaged."""
    score = 0.0
    if event.replied:
        score += _REPLIED
    if event.opened:
        score += _OPENED
    if event.dismissed:
        score += _DISMISSED
    if event.muted_after:
        score += _MUTED
    if event.reported:
        score += _REPORTED
    return score


def dismissal_ratio(events: Sequence[InteractionEvent]) -> float | None:
    """Share of interactions the receiver dismissed, muted, or reported.

    None for an empty sequence -- an unknown ratio is not a zero one.
    """
    if not events:
        return None
    rejected = sum(1 for e in events if e.dismissed or e.muted_after or e.reported)
    return rejected / len(events)


def engagement_stance(
    events: Sequence[InteractionEvent], cfg: PolicyConfig
) -> tuple[EngagementStance, float | None]:
    """Return the stance and the dismissal ratio.

    Justification: a stance is a claim about a pattern, so it is only asserted
    once MIN_STANCE_OBSERVATIONS interactions exist (source (b): one instance is
    an event, not a pattern). Below that the stance is UNKNOWN, which is
    distinct from NEUTRAL -- the guard and floor rules treat them differently.
    """
    ratio = dismissal_ratio(events)
    if len(events) < cfg.MIN_STANCE_OBSERVATIONS:
        return EngagementStance.UNKNOWN, ratio

    assert ratio is not None
    if ratio > cfg.DISMISSAL_MAJORITY:
        return EngagementStance.DISMISSIVE, ratio

    mean_score = sum(engagement_score(e) for e in events) / len(events)
    if mean_score >= _REPLIED:
        return EngagementStance.RECEPTIVE, ratio
    return EngagementStance.NEUTRAL, ratio
