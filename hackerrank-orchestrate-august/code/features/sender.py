"""The receiver's engagement stance toward the sender of one message."""

from __future__ import annotations

from typing import Sequence

from schemas import HistoryRecord, MessageContext


def prior_interactions(context: MessageContext) -> tuple[HistoryRecord, ...]:
    """This receiver's history with the same sender or the same business.

    Same-sender for personal and group messages, same-business for business
    messages. Both are "have we met this counterparty before", which is what
    the stance is a claim about.
    """
    message = context.message
    if message.business_id is not None:
        return tuple(r for r in context.history if r.business_id == message.business_id)
    if message.sender_user_id is not None:
        return tuple(r for r in context.history if r.sender_user_id == message.sender_user_id)
    return ()


def relationship_established(
    context: MessageContext, interactions: Sequence[HistoryRecord]
) -> bool:
    """Whether any prior relationship with this counterparty is on record."""
    return bool(interactions) or context.business_relationship is not None
