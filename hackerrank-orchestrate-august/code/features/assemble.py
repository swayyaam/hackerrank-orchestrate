"""Compose every derived feature into one FeatureVector. Pure; no model, no I/O."""

from __future__ import annotations

from config import PolicyConfig
from features import business as business_features
from features import content as content_features
from features import group as group_features
from features import sender as sender_features
from features.engagement import engagement_stance
from schemas import ConversationType, DomainState, FeatureVector, MessageContext


def _holds_payment_authority(context: MessageContext, domain: DomainState) -> bool:
    """Whether the sender has recognised authority for payments in this conversation.

    Maps the problem statement's own poles -- "legitimate from a trusted admin
    but risky from a new sender" -- onto recorded facts: a group admin, or a
    verified business sending from its own domain that the user already deals
    with. A personal conversation has no institutional relationship to be
    authoritative within, so it is never payment-authoritative.
    """
    conversation = context.message.conversation_type
    if conversation is ConversationType.GROUP:
        return group_features.is_admin(context.sender_membership)
    if conversation is ConversationType.BUSINESS:
        return (
            context.business is not None
            and context.business.verified
            and domain is DomainState.MATCH
            and context.business_relationship is not None
        )
    return False


def build_features(context: MessageContext, cfg: PolicyConfig) -> FeatureVector:
    """Compose all derived features. Pure; no model, no I/O."""
    domain = business_features.domain_state(context.business)
    interactions = sender_features.prior_interactions(context)
    stance, ratio = engagement_stance([r.event for r in interactions], cfg)

    return FeatureVector(
        domain_state=domain,
        business_identity_consistent=business_features.identity_consistent(context.business),
        business_verified=context.business is not None and context.business.verified,
        business_relationship_known=context.business_relationship is not None,
        promotions_opted_out=business_features.promotions_opted_out(context.business_relationship),
        sender_is_group_admin=group_features.is_admin(context.sender_membership),
        receiver_is_group_admin=group_features.is_admin(context.receiver_membership),
        group_muted_by_receiver=group_features.muted_by_receiver(context.receiver_membership),
        prior_interaction_count=len(interactions),
        dismissal_ratio=ratio,
        sender_stance=stance,
        relationship_established=sender_features.relationship_established(context, interactions),
        forward_chain_length=content_features.forward_chain_length(context),
        is_frequently_forwarded=content_features.is_frequently_forwarded(context, cfg),
        media_failed=content_features.media_failed(context.media),
        media_unread=content_features.media_unread(context),
        sender_holds_payment_authority=_holds_payment_authority(context, domain),
        receiver_baseline_dismissal_rate=context.daily_load_baseline,
    )
