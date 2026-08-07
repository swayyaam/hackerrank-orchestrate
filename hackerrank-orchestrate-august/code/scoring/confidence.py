"""Compute confidence from named features and named weights.

The model never supplies it -- Perception has no confidence field, and
extra="forbid" rejects a response that adds one.
"""

from __future__ import annotations

from config import ConfidenceConfig
from policy.rules import RULES_BY_CODE
from schemas import (
    Action,
    Decision,
    EngagementStance,
    EvidenceSelection,
    EvidenceTier,
    FeatureVector,
    FiringStatus,
    OverrideCode,
    Perception,
    RuleEffect,
    TriggerSource,
)

_ALIGNED: dict[EngagementStance, Action] = {
    EngagementStance.RECEPTIVE: Action.NOTIFY,
    EngagementStance.NEUTRAL: Action.DIGEST,
    EngagementStance.DISMISSIVE: Action.MUTE,
}
_OPPOSED: dict[EngagementStance, Action] = {
    EngagementStance.RECEPTIVE: Action.MUTE,
    EngagementStance.DISMISSIVE: Action.NOTIFY,
}


def _trigger_term(decision: Decision, cfg: ConfidenceConfig) -> float:
    applied = decision.applied
    if applied.code is OverrideCode.PERCEPTION_ACCEPTED:
        return 0.0
    rule = RULES_BY_CODE[applied.code]
    if rule.effect is RuleEffect.DEFAULT:
        return 0.0
    return (
        cfg.W_TRIGGER_STRUCTURAL
        if rule.weighting_source is TriggerSource.STRUCTURAL
        else cfg.W_TRIGGER_PERCEPTUAL
    )


def _evidence_term(evidence: EvidenceSelection, cfg: ConfidenceConfig) -> float:
    return {
        EvidenceTier.RELATIONSHIP_AND_CONTENT: cfg.W_EVIDENCE_RELATIONSHIP_AND_CONTENT,
        EvidenceTier.RELATIONSHIP: cfg.W_EVIDENCE_RELATIONSHIP,
        EvidenceTier.TOPICAL: cfg.W_EVIDENCE_TOPICAL,
        EvidenceTier.NONE: 0.0,
    }[evidence.tier]


def _engagement_term(decision: Decision, features: FeatureVector, cfg: ConfidenceConfig) -> float:
    stance = features.sender_stance
    if _ALIGNED.get(stance) is decision.action:
        return cfg.W_ENGAGEMENT_ALIGNED
    if _OPPOSED.get(stance) is decision.action:
        return -cfg.W_ENGAGEMENT_ALIGNED
    return 0.0


def _contention_count(decision: Decision) -> int:
    return sum(
        1
        for firing in decision.firings
        if firing.status in (FiringStatus.SUPERSEDED, FiringStatus.OVERRIDDEN_BY_CAP)
    )


def score_confidence(
    decision: Decision,
    features: FeatureVector,
    perception: Perception,
    evidence: EvidenceSelection,
    cfg: ConfidenceConfig,
) -> float:
    """Compute confidence from named features and config weights.

    Never reads the model: every term is derived from the decision, the feature
    vector, the evidence tier, or the count of type candidates.
    """
    raw = (
        cfg.W_BASE
        + _trigger_term(decision, cfg)
        + _evidence_term(evidence, cfg)
        + _engagement_term(decision, features, cfg)
        - (cfg.W_MEDIA_UNREAD if features.media_unread else 0.0)
        - cfg.W_TYPE_AMBIGUITY * (len(perception.type_candidates) - 1)
        - cfg.W_CONTENTION * _contention_count(decision)
    )
    clamped = min(cfg.CEILING, max(cfg.FLOOR, raw))
    return round(clamped, cfg.DECIMAL_PLACES)
