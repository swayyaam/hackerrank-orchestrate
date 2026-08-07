"""Deterministic evidence retrieval. No vector index; a sort over a small list."""

from __future__ import annotations

import re
from typing import Sequence

from config import EvidenceConfig
from errors import EvidenceIntegrityError
from policy.rules import RULES_BY_CODE
from schemas import (
    Decision,
    EvidenceSelection,
    EvidenceTier,
    FiringStatus,
    HistoryRecord,
    MessageContext,
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str, cfg: EvidenceConfig) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN.findall(text.casefold()) if len(token) >= cfg.MIN_TOKEN_LENGTH
    )


def content_overlap(left: str, right: str, cfg: EvidenceConfig) -> float:
    """Jaccard over content tokens. 0.0 when either side has no content tokens.

    Used for **ranking**, never for the topical yes/no -- see
    ``shared_content_tokens``. A continuous score needs no cut-point to justify;
    a threshold does.
    """
    a, b = _tokens(left, cfg), _tokens(right, cfg)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def shared_content_tokens(left: str, right: str, cfg: EvidenceConfig) -> int:
    """How many content words the two messages have in common."""
    return len(_tokens(left, cfg) & _tokens(right, cfg))


def _tier(context: MessageContext, record: HistoryRecord, cfg: EvidenceConfig) -> EvidenceTier:
    message = context.message
    same_counterparty = (
        message.business_id is not None and record.business_id == message.business_id
    ) or (message.sender_user_id is not None and record.sender_user_id == message.sender_user_id)
    same_conversation = message.group_id is not None and record.group_id == message.group_id
    related = same_counterparty or same_conversation

    shared = shared_content_tokens(message.message_text, record.message_text, cfg)
    topical = shared >= cfg.MIN_SHARED_CONTENT_TOKENS

    if related and topical:
        return EvidenceTier.RELATIONSHIP_AND_CONTENT
    if related:
        return EvidenceTier.RELATIONSHIP
    if topical:
        return EvidenceTier.TOPICAL
    return EvidenceTier.NONE


def select_evidence(
    context: MessageContext,
    decision: Decision,
    cfg: EvidenceConfig,
) -> EvidenceSelection:
    """Rank this receiver's history and select the ids that support the decision.

    Only records strictly older than the target are eligible, and every record
    must belong to the target's receiving user. Both are invariants rather than
    filters: a violation means the index is wrong, not that the data is sparse.

    The number of ids returned is 1 by default and up to 2 when the applied rule
    asserts a pattern, because a claim of repetition needs more than one
    instance. The full ranked candidate list is returned as shortlist_considered
    and the selection is always a subset of it, which is the invariant behind
    ungrounded_citation_count == 0.

    Raises:
        EvidenceIntegrityError: a candidate belongs to another user or is not
            strictly in the past.
    """
    message = context.message
    eligible: list[tuple[EvidenceTier, HistoryRecord]] = []
    for record in context.history:
        if record.user_id != message.user_id:
            raise EvidenceIntegrityError(
                f"{message.message_id}: history {record.message_id} belongs to "
                f"{record.user_id}, not {message.user_id}"
            )
        if not record.created_at < message.created_at:
            continue
        tier = _tier(context, record, cfg)
        if tier is EvidenceTier.NONE:
            continue
        eligible.append((tier, record))

    # Total order: tier desc, topical strength desc, recency desc, message_id asc.
    # Fully deterministic, so two runs cannot differ.
    #
    # Topical strength is the second key, not recency. A tier says *whether* a
    # candidate is related; within a tier -- and 91 of 110 rows sit in the top
    # tier -- it says nothing about which of several related messages is the
    # better evidence, and recency is not that. Ranking on shared content words
    # is ranking on relatedness, which is what the column is for. It is emphatically
    # NOT ranking on id proximity, which would score well here for a reason that
    # generalises to nothing (see PROBLEM_ANALYSIS D19).
    eligible.sort(
        key=lambda pair: (
            -int(pair[0]),
            -shared_content_tokens(message.message_text, pair[1].message_text, cfg),
            -pair[1].created_at.timestamp(),
            pair[1].message_id,
        )
    )
    shortlist = tuple(record.message_id for _tier_value, record in eligible)

    applied_code = _applied_code(decision)
    rule = RULES_BY_CODE.get(applied_code) if applied_code is not None else None
    limit = (
        cfg.MAX_IDS_PATTERN_RULE
        if rule is not None and rule.asserts_pattern
        else cfg.MAX_IDS_DEFAULT
    )

    if not eligible:
        return EvidenceSelection(
            message_ids=(), tier=EvidenceTier.NONE, shortlist_considered=shortlist
        )

    best_tier = eligible[0][0]
    chosen = tuple(record.message_id for _t, record in eligible[:limit])
    _assert_subset(chosen, shortlist, message.message_id)
    return EvidenceSelection(
        message_ids=chosen, tier=best_tier, shortlist_considered=shortlist
    )


def _applied_code(decision: Decision):
    for firing in decision.firings:
        if firing.status is FiringStatus.APPLIED:
            return firing.code
    return None


def _assert_subset(chosen: Sequence[str], shortlist: Sequence[str], message_id: str) -> None:
    extra = set(chosen) - set(shortlist)
    if extra:
        raise EvidenceIntegrityError(
            f"{message_id}: cited ids not in the shortlist considered: {sorted(extra)}"
        )
