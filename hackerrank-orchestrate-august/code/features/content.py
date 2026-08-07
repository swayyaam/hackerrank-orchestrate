"""Structural content facts that need no model."""

from __future__ import annotations

from config import PolicyConfig
from schemas import MediaArtifact, MessageContext


def forward_chain_length(context: MessageContext) -> int:
    return context.message.forwarded_count


def is_frequently_forwarded(context: MessageContext, cfg: PolicyConfig) -> bool:
    """Whether the message has passed through enough chats to count as broadcast.

    Justification: FORWARD_CHAIN_THRESHOLD is source (b) -- WhatsApp's own
    "forwarded many times" definition.
    """
    return context.message.forwarded_count >= cfg.FORWARD_CHAIN_THRESHOLD


def media_failed(media: MediaArtifact | None) -> bool:
    """Whether attached media failed to decode or read. False when none attached."""
    return media is not None and not media.readable


def media_unread(context: MessageContext) -> bool:
    """NARROW: the row's only content was media we could not read.

    media_failed AND the message carries no text. A captioned image that fails
    to decode sets media_failed but NOT media_unread -- the caption still
    carries the message, so no confidence penalty applies.
    """
    return media_failed(context.media) and not context.message.has_text
