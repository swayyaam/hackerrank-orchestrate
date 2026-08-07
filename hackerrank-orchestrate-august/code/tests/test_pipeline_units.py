"""Unit tests for the pipeline modules. Table-driven; no network, no model."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from config import PolicyConfig, RouterConfig, validate
from config import ConfigError
from errors import EvidenceIntegrityError, MediaPathError, ReasonRegisterError
from features.business import domain_state, identity_consistent, promotions_opted_out
from features.engagement import engagement_stance
from media.locate import resolve_media_path
from policy.engine import decide
from policy.rules import RULE_TABLE, RULES_BY_CODE
from scoring.confidence import score_confidence
from schemas import (
    Action,
    BusinessProfile,
    ConversationType,
    Decision,
    DomainState,
    EngagementStance,
    EvidenceSelection,
    EvidenceTier,
    FeatureVector,
    FiringStatus,
    InteractionEvent,
    MediaKind,
    MessageType,
    OverrideCode,
    Perception,
    RuleEffect,
    TriggerSource,
)
from config import ConfidenceConfig

PKG = Path(__file__).resolve().parents[1]


def _business(*, verified: bool, official: str | None, used: str | None, display="Acme", brand="Acme"):
    return BusinessProfile(
        business_id="b", display_name=display, brand_name=brand, category="retail",
        verified=verified, official_domain=official, domain_used_by_sender=used,
        account_age_days=100, messages_sent_30d=10, user_reports_30d=0,
        domain_used_by_sender_age_days=100,
    )


def _features(**overrides) -> FeatureVector:
    base = dict(
        domain_state=DomainState.UNDETERMINED, business_identity_consistent=True,
        business_verified=False, business_relationship_known=False, promotions_opted_out=False,
        sender_is_group_admin=False, receiver_is_group_admin=False, group_muted_by_receiver=False,
        prior_interaction_count=0, dismissal_ratio=None, sender_stance=EngagementStance.UNKNOWN,
        relationship_established=True, forward_chain_length=0, is_frequently_forwarded=False,
        media_failed=False, media_unread=False, sender_holds_payment_authority=False,
        receiver_baseline_dismissal_rate=None,
    )
    base.update(overrides)
    return FeatureVector(**base)


def _perception(**overrides) -> Perception:
    base = dict(proposed_action=Action.DIGEST, type_candidates=(MessageType.PERSONAL,))
    base.update(overrides)
    return Perception(**base)


# ---- config -------------------------------------------------------------


def test_config_validates_clean() -> None:
    validate(RouterConfig())


def test_config_rejects_inverted_confidence_bounds() -> None:
    cfg = RouterConfig(confidence=ConfidenceConfig(FLOOR=0.9, CEILING=0.4))
    with pytest.raises(ConfigError, match="confidence bounds"):
        validate(cfg)


def test_config_rejects_perceptual_above_structural() -> None:
    cfg = RouterConfig(confidence=ConfidenceConfig(W_TRIGGER_PERCEPTUAL=0.5, W_TRIGGER_STRUCTURAL=0.2))
    with pytest.raises(ConfigError, match="W_TRIGGER_PERCEPTUAL"):
        validate(cfg)


def test_config_rejects_single_observation_pattern() -> None:
    cfg = RouterConfig(policy=PolicyConfig(MIN_PATTERN_OBSERVATIONS=1))
    with pytest.raises(ConfigError, match="MIN_PATTERN_OBSERVATIONS"):
        validate(cfg)


# ---- features -----------------------------------------------------------


@pytest.mark.parametrize(
    "official,used,expected",
    [
        ("acme.com", "acme.com", DomainState.MATCH),
        ("acme.com", "www.acme.com", DomainState.MATCH),
        ("acme.com", "acme-secure.com", DomainState.MISMATCH),
        (None, "acme.com", DomainState.UNDETERMINED),
        ("acme.com", None, DomainState.UNDETERMINED),
        (None, None, DomainState.UNDETERMINED),
    ],
)
def test_domain_state_blank_side_is_undetermined(official, used, expected) -> None:
    """UNDETERMINED is not MISMATCH -- absence of evidence is not evidence."""
    assert domain_state(_business(verified=True, official=official, used=used)) is expected


def test_domain_state_none_business_is_undetermined() -> None:
    assert domain_state(None) is DomainState.UNDETERMINED


def test_identity_consistent_compares_display_to_brand() -> None:
    assert identity_consistent(_business(verified=False, official="a.com", used="a.com"))
    assert not identity_consistent(
        _business(verified=False, official="a.com", used="a.com", display="Acme Helpdesk", brand="Acme")
    )


def _event(**kw) -> InteractionEvent:
    base = dict(user_id="u", message_id="m", opened=True, replied=False,
                reaction_time_minutes=None, dismissed=False, muted_after=False, reported=False)
    base.update(kw)
    return InteractionEvent(**base)


def test_stance_unknown_below_minimum_observations() -> None:
    stance, ratio = engagement_stance([_event()], PolicyConfig())
    assert stance is EngagementStance.UNKNOWN
    assert ratio == pytest.approx(0.0)


def test_stance_dismissive_on_majority_rejection() -> None:
    events = [_event(opened=False, dismissed=True, muted_after=True) for _ in range(3)]
    stance, ratio = engagement_stance(events, PolicyConfig())
    assert stance is EngagementStance.DISMISSIVE
    assert ratio == pytest.approx(1.0)


def test_stance_receptive_on_replies() -> None:
    events = [_event(replied=True) for _ in range(3)]
    stance, _ratio = engagement_stance(events, PolicyConfig())
    assert stance is EngagementStance.RECEPTIVE


def test_stance_handles_unseen_reaction_time() -> None:
    """G5: a reaction time outside the observed set must not change behaviour."""
    for rt in (0, 37, 1440, None):
        stance, _ = engagement_stance([_event(reaction_time_minutes=rt) for _ in range(2)], PolicyConfig())
        assert stance in set(EngagementStance)


# ---- policy -------------------------------------------------------------


def test_every_rule_has_a_code_effect_and_reads() -> None:
    for rule in RULE_TABLE:
        assert rule.code in OverrideCode
        assert rule.effect in RuleEffect
        assert rule.reads, f"{rule.code} declares no inputs"
        if rule.effect is not RuleEffect.DEFAULT and rule.code is not OverrideCode.DIRECT_ADDRESS_GUARD:
            assert rule.justification, f"{rule.code} has no justification string"


def test_rule_table_precedence_is_fixed_and_complete() -> None:
    assert len(RULE_TABLE) == 16
    assert RULE_TABLE[0].code is OverrideCode.CREDENTIAL_SOLICITATION
    assert RULE_TABLE[-1].code is OverrideCode.PERCEPTION_ACCEPTED


def test_mixed_trigger_weights_as_perceptual() -> None:
    """DECISION_ENGINE section 5: a perception claim is load-bearing in a conjunction."""
    for code in (OverrideCode.PROMOTION_OPT_OUT, OverrideCode.UNAUTHORIZED_PAYMENT_REQUEST):
        rule = RULES_BY_CODE[code]
        assert rule.trigger_source is TriggerSource.MIXED
        assert rule.weighting_source is TriggerSource.PERCEPTUAL


def test_no_rule_reads_media_contradicts_text() -> None:
    """THREAT_MODEL N3: mismatch is not a risk signal."""
    for rule in RULE_TABLE:
        assert "P.media_contradicts_text" not in rule.reads
        assert "F.media_failed" not in rule.reads


def test_exactly_one_applied_firing_always() -> None:
    for features in (_features(), _features(group_muted_by_receiver=True),
                     _features(relationship_established=False),
                     _features(business_verified=False, domain_state=DomainState.MISMATCH)):
        decision = decide(_perception(), features, PolicyConfig())
        applied = [f for f in decision.firings if f.status is FiringStatus.APPLIED]
        assert len(applied) == 1


def test_impersonation_hard_overrides_to_mute_scam() -> None:
    decision = decide(
        _perception(), _features(business_verified=False, domain_state=DomainState.MISMATCH), PolicyConfig()
    )
    assert decision.action is Action.MUTE
    assert decision.message_type is MessageType.SCAM
    assert decision.applied.code is OverrideCode.IMPERSONATION


def test_impersonation_does_not_fire_on_undetermined_domain() -> None:
    """The bug the three-state enum exists to prevent."""
    decision = decide(
        _perception(), _features(business_verified=False, domain_state=DomainState.UNDETERMINED),
        PolicyConfig(),
    )
    assert decision.applied.code is not OverrideCode.IMPERSONATION


def test_repeat_dismissal_caps_to_mute() -> None:
    decision = decide(
        _perception(), _features(prior_interaction_count=3, dismissal_ratio=0.9), PolicyConfig()
    )
    assert decision.action is Action.MUTE
    assert decision.applied.code is OverrideCode.REPEAT_DISMISSAL


def test_hard_override_supersedes_later_hard_override() -> None:
    decision = decide(
        _perception(), _features(business_verified=False, domain_state=DomainState.MISMATCH,
                                 prior_interaction_count=3, dismissal_ratio=1.0), PolicyConfig()
    )
    assert decision.applied.code is OverrideCode.IMPERSONATION
    statuses = {f.code: f.status for f in decision.firings}
    assert statuses[OverrideCode.REPEAT_DISMISSAL] is FiringStatus.NOT_EVALUATED


def test_cap_at_digest_does_not_lower_an_already_digest_action() -> None:
    decision = decide(_perception(), _features(group_muted_by_receiver=True), PolicyConfig())
    assert decision.action is Action.DIGEST
    assert decision.applied.code is OverrideCode.PERCEPTION_ACCEPTED
    statuses = {f.code: f.status for f in decision.firings}
    assert statuses[OverrideCode.GROUP_MUTE_CAP] is FiringStatus.FIRED_NOT_DETERMINING


def test_caps_beat_floors_when_both_would_apply() -> None:
    """A notify proposal capped by a muted conversation lands on digest."""
    decision = decide(
        _perception(proposed_action=Action.NOTIFY), _features(group_muted_by_receiver=True), PolicyConfig()
    )
    assert decision.action is Action.DIGEST
    assert decision.applied.code is OverrideCode.GROUP_MUTE_CAP


# ---- confidence ---------------------------------------------------------


def _decision(code: OverrideCode, action=Action.MUTE) -> Decision:
    from schemas import RuleFiring

    rule = RULES_BY_CODE[code]
    firings = tuple(
        RuleFiring(code=r.code, effect=r.effect,
                   status=FiringStatus.APPLIED if r.code is code else FiringStatus.DID_NOT_FIRE,
                   would_set_action=r.sets_action, justification=r.justification)
        for r in RULE_TABLE
    )
    return Decision(action=action, message_type=MessageType.SCAM, firings=firings)


def _selection(tier: EvidenceTier) -> EvidenceSelection:
    return EvidenceSelection(message_ids=("h1",), tier=tier, shortlist_considered=("h1",))


def test_confidence_structural_trigger_scores_above_perceptual() -> None:
    cfg = ConfidenceConfig()
    structural = score_confidence(
        _decision(OverrideCode.IMPERSONATION), _features(), _perception(), _selection(EvidenceTier.NONE), cfg
    )
    default = score_confidence(
        _decision(OverrideCode.PERCEPTION_ACCEPTED, Action.DIGEST), _features(), _perception(),
        _selection(EvidenceTier.NONE), cfg,
    )
    assert structural == pytest.approx(cfg.W_BASE + cfg.W_TRIGGER_STRUCTURAL, abs=1e-9)
    assert default == pytest.approx(cfg.W_BASE, abs=1e-9)


def test_confidence_penalises_media_unread_only() -> None:
    cfg = ConfidenceConfig()
    failed_only = score_confidence(
        _decision(OverrideCode.PERCEPTION_ACCEPTED, Action.DIGEST), _features(media_failed=True),
        _perception(), _selection(EvidenceTier.NONE), cfg,
    )
    unread = score_confidence(
        _decision(OverrideCode.PERCEPTION_ACCEPTED, Action.DIGEST),
        _features(media_failed=True, media_unread=True), _perception(), _selection(EvidenceTier.NONE), cfg,
    )
    assert failed_only == pytest.approx(cfg.W_BASE)
    assert unread == pytest.approx(cfg.W_BASE - cfg.W_MEDIA_UNREAD)


def test_confidence_clamps_at_both_bounds() -> None:
    cfg = ConfidenceConfig()
    high = score_confidence(
        _decision(OverrideCode.IMPERSONATION), _features(sender_stance=EngagementStance.DISMISSIVE),
        _perception(), _selection(EvidenceTier.RELATIONSHIP_AND_CONTENT), cfg,
    )
    assert high <= cfg.CEILING
    low = score_confidence(
        _decision(OverrideCode.PERCEPTION_ACCEPTED, Action.MUTE),
        _features(sender_stance=EngagementStance.RECEPTIVE, media_failed=True, media_unread=True),
        _perception(type_candidates=(MessageType.PERSONAL, MessageType.URGENT, MessageType.EVENT)),
        _selection(EvidenceTier.NONE), cfg,
    )
    assert low >= cfg.FLOOR


def test_confidence_never_reads_the_model() -> None:
    """Perception has no confidence field, and extra fields are rejected."""
    assert "confidence" not in Perception.model_fields
    with pytest.raises(Exception):
        Perception(proposed_action=Action.DIGEST, type_candidates=(MessageType.PERSONAL,), confidence=0.9)


# ---- media --------------------------------------------------------------


def test_media_path_escape_is_rejected(tmp_path) -> None:
    root = tmp_path / "dataset"
    (root / "media").mkdir(parents=True)
    (tmp_path / "outside.txt").write_text("x")
    with pytest.raises(MediaPathError):
        resolve_media_path(root, "../outside.txt", "m1", MediaKind.IMAGE)


def test_media_symlink_escape_is_rejected(tmp_path) -> None:
    root = tmp_path / "dataset"
    (root / "media").mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    (root / "media" / "link.jpg").symlink_to(outside)
    with pytest.raises(MediaPathError):
        resolve_media_path(root, "media/link.jpg", "m1", MediaKind.IMAGE)
