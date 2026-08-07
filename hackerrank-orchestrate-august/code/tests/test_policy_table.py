"""Phase 3: the complete rule table, and the six conflict cases.

Every case here is constructed from typed values, never from a dataset row —
a table-driven test that reads the corpus would be pinned to this corpus.
"""

from __future__ import annotations

import pytest

from compose.reason import cites_evidence, references_integrity, render_reason
from config import PolicyConfig, ReasonConfig
from features.integrity import threat_events
from policy.engine import decide
from policy.rules import RULE_TABLE, RULES_BY_CODE
from schemas import (
    Action,
    CacheOutcome,
    CallTelemetry,
    DomainState,
    EngagementStance,
    EvidenceSelection,
    EvidenceTier,
    FeatureVector,
    FiringStatus,
    MessageType,
    OverrideCode,
    Perception,
    RuleEffect,
    ThreatEvent,
)

CFG = PolicyConfig()


def features(**overrides) -> FeatureVector:
    base = dict(
        domain_state=DomainState.UNDETERMINED,
        business_identity_consistent=True,
        business_verified=False,
        business_relationship_known=False,
        promotions_opted_out=False,
        sender_is_group_admin=False,
        receiver_is_group_admin=False,
        group_muted_by_receiver=False,
        prior_interaction_count=0,
        dismissal_ratio=None,
        sender_stance=EngagementStance.UNKNOWN,
        relationship_established=True,
        forward_chain_length=0,
        is_frequently_forwarded=False,
        media_failed=False,
        media_unread=False,
        sender_holds_payment_authority=False,
        receiver_baseline_dismissal_rate=None,
    )
    base.update(overrides)
    return FeatureVector(**base)


def perception(action: Action = Action.DIGEST, types=(MessageType.PERSONAL,), **claims) -> Perception:
    return Perception(proposed_action=action, type_candidates=types, **claims)


def status_of(decision, code: OverrideCode) -> FiringStatus:
    return next(f.status for f in decision.firings if f.code is code)


# --------------------------------------------------------------------------
# Table completeness
# --------------------------------------------------------------------------


def test_every_rule_in_the_table_is_implemented() -> None:
    unimplemented = [r.code.value for r in RULE_TABLE if not r.implemented]
    assert unimplemented == [], f"declared but never fires: {unimplemented}"


def test_the_table_is_in_precedence_order() -> None:
    """Hard overrides, then the guard, then caps, then floors, then the default."""
    order = [RuleEffect.HARD_OVERRIDE, RuleEffect.GUARD, RuleEffect.CAP,
             RuleEffect.FLOOR, RuleEffect.DEFAULT]
    seen = [rule.effect for rule in RULE_TABLE]
    positions = [order.index(effect) for effect in seen]
    assert positions == sorted(positions), seen


def test_exactly_one_firing_is_applied_on_every_decision() -> None:
    for p, f in (
        (perception(), features()),
        (perception(Action.NOTIFY, addresses_recipient_directly=True,
                    requests_response_or_action=True),
         features(sender_stance=EngagementStance.RECEPTIVE)),
        (perception(requests_authentication_secret=True), features()),
        (perception(Action.MUTE), features(group_muted_by_receiver=True)),
    ):
        decision = decide(p, f, CFG)
        applied = [x for x in decision.firings if x.status is FiringStatus.APPLIED]
        assert len(applied) == 1, [x.code.value for x in applied]


# --------------------------------------------------------------------------
# One case per newly-implemented rule
# --------------------------------------------------------------------------


def test_direct_address_guard_suppresses_conversation_level_caps() -> None:
    decision = decide(
        perception(Action.NOTIFY, addresses_recipient_directly=True),
        features(sender_stance=EngagementStance.RECEPTIVE, group_muted_by_receiver=True),
        CFG,
    )
    assert status_of(decision, OverrideCode.GROUP_MUTE_CAP) is FiringStatus.SUPPRESSED_BY_GUARD
    assert decision.action is Action.NOTIFY


def test_the_guard_does_not_rescue_a_dismissed_sender() -> None:
    """Otherwise a muted sender re-acquires reach just by adding a mention.

    The property is that the mention buys nothing: the conversation-level cap
    stands and the notify proposal is held down, exactly as it would be without
    the mention.
    """
    dismissed = features(sender_stance=EngagementStance.DISMISSIVE, group_muted_by_receiver=True)
    with_mention = decide(
        perception(Action.NOTIFY, addresses_recipient_directly=True), dismissed, CFG
    )
    without = decide(perception(Action.NOTIFY), dismissed, CFG)

    assert status_of(with_mention, OverrideCode.DIRECT_ADDRESS_GUARD) is FiringStatus.DID_NOT_FIRE
    assert status_of(with_mention, OverrideCode.GROUP_MUTE_CAP) is FiringStatus.APPLIED
    assert with_mention.action is without.action is Action.DIGEST


def test_the_guard_does_not_suppress_tier_zero() -> None:
    """A direct address does not make a credential request safe."""
    decision = decide(
        perception(Action.NOTIFY, addresses_recipient_directly=True,
                   requests_authentication_secret=True),
        features(sender_stance=EngagementStance.RECEPTIVE, group_muted_by_receiver=True),
        CFG,
    )
    assert decision.applied.code is OverrideCode.CREDENTIAL_SOLICITATION
    assert decision.action is Action.MUTE


def test_promotion_opt_out_mutes_marketing_but_not_transactional_mail() -> None:
    opted_out = features(promotions_opted_out=True)
    marketing = decide(perception(Action.DIGEST, solicits_purchase=True), opted_out, CFG)
    assert marketing.applied.code is OverrideCode.PROMOTION_OPT_OUT
    assert marketing.action is Action.MUTE

    transactional = decide(perception(Action.DIGEST, reports_transaction_status=True), opted_out, CFG)
    assert transactional.applied.code is not OverrideCode.PROMOTION_OPT_OUT


def test_frequently_forwarded_needs_all_three_conjuncts() -> None:
    forwarded = features(is_frequently_forwarded=True)
    hit = decide(perception(is_circulated_content=True), forwarded, CFG)
    assert hit.applied.code is OverrideCode.FREQUENTLY_FORWARDED_BROADCAST

    # A forwarded message written to this person is still for this person.
    addressed = decide(
        perception(is_circulated_content=True, addresses_recipient_directly=True), forwarded, CFG
    )
    assert addressed.applied.code is not OverrideCode.FREQUENTLY_FORWARDED_BROADCAST

    # A long chain that does not read as circulated content is not a broadcast.
    not_circulated = decide(perception(), forwarded, CFG)
    assert not_circulated.applied.code is not OverrideCode.FREQUENTLY_FORWARDED_BROADCAST


def test_time_critical_floor_excludes_manufactured_commercial_urgency() -> None:
    engaged = features(sender_stance=EngagementStance.RECEPTIVE)
    genuine = decide(
        perception(states_deadline_within_day=True, deadline_has_stated_consequence=True),
        engaged, CFG,
    )
    assert genuine.action is Action.NOTIFY

    sale = decide(
        perception(states_deadline_within_day=True, deadline_has_stated_consequence=True,
                   solicits_purchase=True),
        engaged, CFG,
    )
    assert status_of(sale, OverrideCode.TIME_CRITICAL_FLOOR) is FiringStatus.DID_NOT_FIRE


def test_an_announced_time_with_no_consequence_is_not_a_deadline() -> None:
    """A fire-alarm test, a delivery window, a moved class: a time is stated and
    nothing is required of the recipient. PROBLEM_ANALYSIS 4.6 makes the
    consequence, not the clock, what distinguishes urgent from event."""
    decision = decide(
        perception(states_deadline_within_day=True, deadline_has_stated_consequence=False),
        features(sender_stance=EngagementStance.RECEPTIVE),
        CFG,
    )
    assert status_of(decision, OverrideCode.TIME_CRITICAL_FLOOR) is FiringStatus.DID_NOT_FIRE


def test_direct_request_floor_demands_positive_engagement_history() -> None:
    """RECEPTIVE, not merely 'not dismissive' — a floor is the strongest claim."""
    for stance, floors in (
        (EngagementStance.RECEPTIVE, True),
        (EngagementStance.NEUTRAL, False),
        (EngagementStance.UNKNOWN, False),
    ):
        decision = decide(
            perception(Action.DIGEST, addresses_recipient_directly=True,
                       requests_response_or_action=True),
            features(sender_stance=stance),
            CFG,
        )
        fired = status_of(decision, OverrideCode.DIRECT_REQUEST_FLOOR) is not FiringStatus.DID_NOT_FIRE
        assert fired is floors, stance


def test_being_addressed_is_not_being_asked() -> None:
    """"Reached home, don't call now, we can talk tomorrow" names the recipient
    and requests nothing. The floor must not interrupt on it -- while the guard,
    which reads the same claim, must still fire, because a mention should defeat
    a conversation-level mute whether or not it asks anything."""
    addressed_only = perception(
        Action.DIGEST, addresses_recipient_directly=True, requests_response_or_action=False
    )
    receptive = features(sender_stance=EngagementStance.RECEPTIVE, group_muted_by_receiver=True)
    decision = decide(addressed_only, receptive, CFG)

    assert status_of(decision, OverrideCode.DIRECT_REQUEST_FLOOR) is FiringStatus.DID_NOT_FIRE
    # The guard's effect is observable on what it suppressed, not on its own
    # status: a guard is never the determining rule (DECISION_ENGINE case F).
    assert status_of(decision, OverrideCode.GROUP_MUTE_CAP) is FiringStatus.SUPPRESSED_BY_GUARD
    assert decision.action is Action.DIGEST


# --------------------------------------------------------------------------
# The six conflict cases from DECISION_ENGINE section 4
# --------------------------------------------------------------------------


def test_case_a_first_hard_override_wins_and_the_second_is_superseded() -> None:
    decision = decide(
        perception(requests_authentication_secret=True),
        features(business_verified=False, domain_state=DomainState.MISMATCH),
        CFG,
    )
    assert decision.applied.code is OverrideCode.CREDENTIAL_SOLICITATION
    assert status_of(decision, OverrideCode.IMPERSONATION) is FiringStatus.SUPERSEDED
    assert (decision.action, decision.message_type) == (Action.MUTE, MessageType.SCAM)


def test_case_b_caps_beat_floors() -> None:
    decision = decide(
        perception(Action.DIGEST, states_deadline_within_day=True,
                   deadline_has_stated_consequence=True),
        features(sender_stance=EngagementStance.RECEPTIVE, group_muted_by_receiver=True),
        CFG,
    )
    assert decision.applied.code is OverrideCode.GROUP_MUTE_CAP
    assert decision.action is Action.DIGEST
    assert status_of(decision, OverrideCode.TIME_CRITICAL_FLOOR) is FiringStatus.OVERRIDDEN_BY_CAP


def test_case_c_one_perception_claim_flips_case_b() -> None:
    """B and C differ in `addresses_recipient_directly` and must oppose."""
    common = features(sender_stance=EngagementStance.RECEPTIVE, group_muted_by_receiver=True)
    dated = dict(states_deadline_within_day=True, deadline_has_stated_consequence=True)
    b = decide(perception(Action.DIGEST, **dated), common, CFG)
    c = decide(
        perception(Action.DIGEST, addresses_recipient_directly=True, **dated), common, CFG
    )
    assert b.action is Action.DIGEST
    assert c.action is Action.NOTIFY
    assert status_of(c, OverrideCode.GROUP_MUTE_CAP) is FiringStatus.SUPPRESSED_BY_GUARD


def test_case_d_a_consent_cap_beats_a_floor() -> None:
    decision = decide(
        perception(Action.DIGEST, solicits_purchase=True, states_deadline_within_day=True,
                   deadline_has_stated_consequence=True),
        features(promotions_opted_out=True, sender_stance=EngagementStance.RECEPTIVE),
        CFG,
    )
    assert decision.applied.code is OverrideCode.PROMOTION_OPT_OUT
    assert decision.action is Action.MUTE
    # The floor excludes purchase solicitations, so it never even fires.
    assert status_of(decision, OverrideCode.TIME_CRITICAL_FLOOR) is FiringStatus.DID_NOT_FIRE


def test_case_e_two_caps_the_earlier_rule_owns_the_winning_value() -> None:
    decision = decide(
        perception(Action.NOTIFY, is_circulated_content=True),
        features(
            prior_interaction_count=10,
            dismissal_ratio=0.9,
            sender_stance=EngagementStance.DISMISSIVE,
            is_frequently_forwarded=True,
            group_muted_by_receiver=True,
        ),
        CFG,
    )
    assert decision.action is Action.MUTE
    assert decision.applied.code is OverrideCode.REPEAT_DISMISSAL
    assert status_of(decision, OverrideCode.GROUP_MUTE_CAP) is FiringStatus.FIRED_NOT_DETERMINING


def test_case_f_a_guard_that_suppresses_nothing_is_not_the_applied_rule() -> None:
    """Otherwise the guard's count stops meaning 'suppression was overridden'."""
    decision = decide(
        perception(Action.DIGEST, addresses_recipient_directly=True,
                   requests_response_or_action=True),
        features(sender_stance=EngagementStance.RECEPTIVE),
        CFG,
    )
    assert decision.applied.code is OverrideCode.DIRECT_REQUEST_FLOOR
    assert decision.action is Action.NOTIFY
    assert status_of(decision, OverrideCode.DIRECT_ADDRESS_GUARD) is not FiringStatus.APPLIED


# --------------------------------------------------------------------------
# Integrity warnings are observations, not rule fires
# --------------------------------------------------------------------------


def telemetry(retries: int = 0, prompt_events=()) -> CallTelemetry:
    return CallTelemetry(
        cache_media=CacheOutcome.NOT_APPLICABLE,
        cache_perception=CacheOutcome.HIT,
        schema_retry_count=retries,
        tokens_input=0,
        tokens_output=0,
        wall_clock_ms=0.0,
        prompt_threat_events=prompt_events,
    )


def test_a_warning_is_recorded_even_when_no_rule_fires_on_it() -> None:
    """A verified business on an unowned domain raises no rule but is still a fact.

    IMPERSONATION requires an *unverified* sender, so deriving warnings from rule
    fires would make exactly this case invisible.
    """
    events = threat_events(
        perception(),
        features(business_verified=True, domain_state=DomainState.MISMATCH),
        None,
        telemetry(),
    )
    assert ThreatEvent.BUSINESS_DOMAIN_MISMATCH in events

    decision = decide(
        perception(), features(business_verified=True, domain_state=DomainState.MISMATCH), CFG
    )
    assert decision.applied.code is not OverrideCode.IMPERSONATION


def test_a_router_directive_is_recorded_as_a_warning_and_not_only_as_a_fire() -> None:
    events = threat_events(perception(contains_router_directive=True), features(), None, telemetry())
    assert ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT in events


def test_a_schema_retry_is_recorded_as_a_warning() -> None:
    assert ThreatEvent.MODEL_RESPONSE_SCHEMA_REJECTED in threat_events(
        perception(), features(), None, telemetry(retries=1)
    )


def test_warnings_are_deduplicated_and_ordered_stably() -> None:
    a = threat_events(
        perception(contains_router_directive=True), features(), None,
        telemetry(prompt_events=(ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT,)),
    )
    assert a.count(ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT) == 1
    b = threat_events(
        perception(contains_router_directive=True), features(), None,
        telemetry(prompt_events=(ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT,)),
    )
    assert a == b


def test_an_operational_media_failure_is_not_a_threat_observation() -> None:
    """A file too long for the endpoint says nothing about intent."""
    from schemas import MediaArtifact, MediaKind

    media = MediaArtifact(
        media_id="k", kind=MediaKind.VOICE, container_format="m4a", duration_seconds=110.0,
        readable=False, content=None, failure_reason="duration_exceeded",
    )
    assert threat_events(perception(), features(), media, telemetry()) == ()


# --------------------------------------------------------------------------
# A warned row must say so
# --------------------------------------------------------------------------


def evidence(tier: EvidenceTier = EvidenceTier.NONE, ids=()) -> EvidenceSelection:
    return EvidenceSelection(message_ids=tuple(ids), tier=tier, shortlist_considered=tuple(ids))


def test_a_warned_row_names_the_warning_in_its_reason() -> None:
    decision = decide(
        perception(), features(business_verified=True, domain_state=DomainState.MISMATCH), CFG
    )
    warnings = (ThreatEvent.BUSINESS_DOMAIN_MISMATCH,)
    reason = render_reason(decision, features(), evidence(), warnings, ReasonConfig())
    assert "domain" in reason
    assert references_integrity(reason)


def test_an_integrity_rule_keeps_its_own_justification() -> None:
    """It already names the concern; paraphrasing it would only say it worse."""
    decision = decide(perception(requests_authentication_secret=True), features(), CFG)
    reason = render_reason(
        decision, features(), evidence(), (ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT,), ReasonConfig()
    )
    assert reason == RULES_BY_CODE[OverrideCode.CREDENTIAL_SOLICITATION].justification
    assert references_integrity(reason)


@pytest.mark.parametrize("event", sorted(ThreatEvent, key=lambda e: e.value))
def test_every_warning_that_can_reach_a_row_has_a_clause(event: ThreatEvent) -> None:
    """A warning with no clause would be recorded and then never explained."""
    from compose.reason import _WARNING_CLAUSE

    unreachable = {ThreatEvent.MEDIA_DECODE_FAILED, ThreatEvent.EVIDENCE_OWNER_MISMATCH,
                   ThreatEvent.EVIDENCE_NOT_IN_PAST}
    if event in unreachable:
        pytest.skip("raised as an exception or a log event, never recorded on a row")
    assert event in _WARNING_CLAUSE


# --------------------------------------------------------------------------
# The reason grounds itself in the evidence it cites
# --------------------------------------------------------------------------


def test_a_cited_row_makes_a_claim_about_prior_behaviour() -> None:
    decision = decide(perception(Action.DIGEST), features(sender_stance=EngagementStance.NEUTRAL), CFG)
    reason = render_reason(
        decision,
        features(sender_stance=EngagementStance.NEUTRAL),
        evidence(EvidenceTier.RELATIONSHIP, ("message_0001",)),
        (),
        ReasonConfig(),
    )
    assert cites_evidence(reason), reason


def test_an_uncited_row_makes_no_such_claim() -> None:
    """The sentence must not describe history the citation column does not hold."""
    f = features(sender_stance=EngagementStance.UNKNOWN, relationship_established=True)
    decision = decide(perception(Action.DIGEST), f, CFG)
    reason = render_reason(decision, f, evidence(), (), ReasonConfig())
    assert not cites_evidence(reason), reason


def test_the_metrics_read_the_sentence_not_the_rule_metadata() -> None:
    """The earlier defect: a rule flagged cites_history scored as citing whatever
    its sentence said, and one not so flagged scored as not citing even when its
    sentence described the user's history in as many words."""
    from compose.reason import _HISTORY_CLAUSES, _QUALIFIER

    assert _QUALIFIER[(Action.MUTE, "stance_dismissive")] in _HISTORY_CLAUSES
    assert not cites_evidence("This is ordinary personal conversation and it can wait.")
    assert cites_evidence(
        "This is ordinary personal conversation "
        + _QUALIFIER[(Action.MUTE, "stance_dismissive")]
    )


def _emittable_clauses() -> dict[str, str]:
    """Every clause the composer appends to a subject, keyed by where it lives."""
    from compose.reason import (
        _HISTORY_QUALIFIER,
        _QUALIFIER,
        _UNRECORDED_ENGAGEMENT,
        _WARNING_CLAUSE,
    )

    clauses = {f"_QUALIFIER{k}": v for k, v in _QUALIFIER.items()}
    clauses |= {f"_HISTORY_QUALIFIER[{k.name}]": v for k, v in _HISTORY_QUALIFIER.items()}
    clauses["_UNRECORDED_ENGAGEMENT"] = _UNRECORDED_ENGAGEMENT
    clauses |= {f"_WARNING_CLAUSE[{k.name}]": v for k, v in _WARNING_CLAUSE.items()}
    return clauses


def _emittable_justifications() -> dict[str, str]:
    """Rule justifications, which are emitted whole rather than after a subject."""
    from policy.rules import RULE_TABLE

    return {
        f"{rule.code.value}.justification": rule.justification
        for rule in RULE_TABLE
        if rule.justification
    }


def test_every_emittable_clause_is_classified() -> None:
    """The three clause sets must be exhaustive over what the composer can say.

    Without this, adding a new generic phrasing is invisible: it emits fine, it
    passes the register check, and it scores as an explanation in neither metric
    while `names_decisive_factor` silently drops below 100%. The point of the
    metric is that a reason naming nothing is a defect, so the set of things that
    count as naming something has to be closed.
    """
    from compose.reason import (
        _HISTORY_CLAUSES,
        _INTEGRITY_CLAUSES,
        _STRUCTURAL_CLAUSES,
    )

    known = _HISTORY_CLAUSES | _INTEGRITY_CLAUSES | _STRUCTURAL_CLAUSES
    emittable = _emittable_clauses() | _emittable_justifications()
    unclassified = {
        where: clause for where, clause in emittable.items() if clause not in known
    }
    assert not unclassified, f"clauses the metrics cannot see: {unclassified}"


@pytest.mark.parametrize("where,clause", sorted(_emittable_clauses().items()))
def test_every_subject_and_clause_pairing_stays_in_register(where: str, clause: str) -> None:
    """Any subject may precede any clause, so the bound is on the worst pairing.

    A clause that fits after a short subject and overflows after a long one is a
    latent `ReasonRegisterError` on whichever corpus row happens to combine them
    -- a crash, not a degraded sentence, because the register is never trimmed.
    """
    from compose.reason import _SUBJECT

    cfg = ReasonConfig()
    for message_type, subject in _SUBJECT.items():
        text = f"{subject} {clause}"
        assert cfg.MIN_CHARS <= len(text) <= cfg.MAX_CHARS, (
            f"{where} + {message_type.value} = {len(text)} chars: {text!r}"
        )
        assert text.count(".") <= cfg.MAX_SENTENCES, text


@pytest.mark.parametrize("where,text", sorted(_emittable_justifications().items()))
def test_every_rule_justification_stays_in_register(where: str, text: str) -> None:
    """Justifications are emitted whole, so they are bounded on their own."""
    cfg = ReasonConfig()
    assert cfg.MIN_CHARS <= len(text) <= cfg.MAX_CHARS, f"{where} = {len(text)} chars: {text!r}"
    assert text.count(".") <= cfg.MAX_SENTENCES, text


# --------------------------------------------------------------------------
# Confidence: each term in isolation, and both clamp ends
#
# Phase 4 verification 3. Each test moves exactly one input and asserts the
# direction and size of the change, so a weight cannot drift without a test
# naming it. None of these reads the corpus.
# --------------------------------------------------------------------------


def _confidence(decision, feats, perc, ev, cfg=None):
    from config import ConfidenceConfig
    from scoring.confidence import score_confidence

    return score_confidence(decision, feats, perc, ev, cfg or ConfidenceConfig())


def test_a_structural_trigger_outweighs_a_perceptual_one() -> None:
    """A fact recorded in the input is more reliable than a content judgement."""
    from config import ConfidenceConfig

    c = ConfidenceConfig()
    structural = decide(
        perception(), features(business_verified=False, domain_state=DomainState.MISMATCH), CFG
    )
    perceptual = decide(perception(requests_authentication_secret=True), features(), CFG)
    assert RULES_BY_CODE[structural.applied.code].weighting_source.value == "structural"
    assert RULES_BY_CODE[perceptual.applied.code].weighting_source.value == "perceptual"

    s = _confidence(structural, features(), perception(), evidence())
    p = _confidence(perceptual, features(), perception(), evidence())
    assert s > p
    assert abs((s - p) - (c.W_TRIGGER_STRUCTURAL - c.W_TRIGGER_PERCEPTUAL)) < 1e-9


def test_evidence_strength_orders_the_three_tiers() -> None:
    from config import ConfidenceConfig

    c = ConfidenceConfig()
    d, f, p = decide(perception(), features(), CFG), features(), perception()
    none = _confidence(d, f, p, evidence(EvidenceTier.NONE))
    topical = _confidence(d, f, p, evidence(EvidenceTier.TOPICAL, ("h1",)))
    rel = _confidence(d, f, p, evidence(EvidenceTier.RELATIONSHIP, ("h1",)))
    both = _confidence(d, f, p, evidence(EvidenceTier.RELATIONSHIP_AND_CONTENT, ("h1",)))
    assert none < topical < rel < both
    assert abs((topical - none) - c.W_EVIDENCE_TOPICAL) < 1e-9
    assert abs((rel - none) - c.W_EVIDENCE_RELATIONSHIP) < 1e-9
    assert abs((both - none) - c.W_EVIDENCE_RELATIONSHIP_AND_CONTENT) < 1e-9


def test_unreadable_media_is_the_largest_single_penalty() -> None:
    """It is the only term standing for a decision made without the substance."""
    from config import ConfidenceConfig

    c = ConfidenceConfig()
    d = decide(perception(), features(), CFG)
    readable = _confidence(d, features(), perception(), evidence())
    unread = _confidence(d, features(media_unread=True), perception(), evidence())
    assert readable - unread == pytest.approx(c.W_MEDIA_UNREAD)
    assert c.W_MEDIA_UNREAD > max(
        c.W_TYPE_AMBIGUITY, c.W_CONTENTION, c.W_ENGAGEMENT_ALIGNED,
        c.W_EVIDENCE_RELATIONSHIP_AND_CONTENT,
    )


def test_each_extra_type_candidate_costs_the_ambiguity_weight() -> None:
    from config import ConfidenceConfig

    c = ConfidenceConfig()
    d, f, ev = decide(perception(), features(), CFG), features(), evidence()
    one = _confidence(d, f, perception(types=(MessageType.PERSONAL,)), ev)
    two = _confidence(d, f, perception(types=(MessageType.PERSONAL, MessageType.URGENT)), ev)
    three = _confidence(
        d, f, perception(types=(MessageType.PERSONAL, MessageType.URGENT, MessageType.EVENT)), ev
    )
    assert one - two == pytest.approx(c.W_TYPE_AMBIGUITY)
    assert two - three == pytest.approx(c.W_TYPE_AMBIGUITY)


def test_contention_lowers_confidence() -> None:
    """A decision several rules disagreed about is less certain than one reached
    unopposed."""
    unopposed = decide(perception(requests_authentication_secret=True), features(), CFG)
    contended = decide(
        perception(requests_authentication_secret=True),
        features(business_verified=False, domain_state=DomainState.MISMATCH),
        CFG,
    )
    assert unopposed.applied.code is contended.applied.code
    assert _confidence(contended, features(), perception(), evidence()) < _confidence(
        unopposed, features(), perception(), evidence()
    )


def test_confidence_clamps_at_both_ends() -> None:
    from config import ConfidenceConfig

    c = ConfidenceConfig()
    d = decide(perception(), features(), CFG)

    # Drive far above the ceiling: every positive term at once, weights inflated.
    high = _confidence(
        d, features(), perception(), evidence(EvidenceTier.RELATIONSHIP_AND_CONTENT, ("h1",)),
        ConfidenceConfig(W_BASE=0.9, W_EVIDENCE_RELATIONSHIP_AND_CONTENT=0.9),
    )
    assert high == c.CEILING

    # And far below the floor.
    low = _confidence(
        d, features(media_unread=True), perception(), evidence(),
        ConfidenceConfig(W_BASE=0.1, W_MEDIA_UNREAD=0.9),
    )
    assert low == c.FLOOR


def test_no_input_produces_a_confidence_outside_the_contract() -> None:
    """Whatever the terms do, the emitted number is a probability."""
    from config import ConfidenceConfig

    c = ConfidenceConfig()
    for f in (features(), features(media_unread=True), features(sender_stance=EngagementStance.DISMISSIVE)):
        for tier in EvidenceTier:
            for types in ((MessageType.PERSONAL,), (MessageType.PERSONAL, MessageType.SCAM)):
                value = _confidence(
                    decide(perception(types=types), f, CFG), f, perception(types=types),
                    evidence(tier, ("h1",) if tier else ()),
                )
                assert 0.0 <= value <= 1.0
                assert c.FLOOR <= value <= c.CEILING


# --------------------------------------------------------------------------
# RISK_TYPE_CAP: a scam or spam row can never be actioned notify
#
# The defect this closes: two corpus rows the model typed `scam` AND proposed
# muting were raised to `notify` by TIME_CRITICAL_FLOOR and DIRECT_REQUEST_FLOOR
# -- a same-day deadline and a direct request for action being exactly what a
# scam manufactures. The floors were reading the attack's own payload as grounds
# to interrupt. The statement is explicit that clear scam or safety risk is muted
# regardless of the user's usual engagement.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("risk_type", [MessageType.SCAM, MessageType.SPAM])
@pytest.mark.parametrize("proposed", [Action.NOTIFY, Action.DIGEST, Action.MUTE])
def test_a_risk_typed_row_never_notifies_whatever_the_model_proposed(
    risk_type: MessageType, proposed: Action
) -> None:
    """Covers both routes into the defect: a floor raising the action, and the
    model proposing `notify` outright with no floor involved."""
    decision = decide(perception(proposed, (risk_type,)), features(), CFG)
    assert decision.action is not Action.NOTIFY, decision.action
    assert decision.message_type is risk_type


@pytest.mark.parametrize(
    "floor_claims",
    [
        {"states_deadline_within_day": True, "deadline_has_stated_consequence": True},
        {"requests_response_or_action": True, "addresses_recipient_directly": True},
    ],
    ids=["time_critical", "direct_request"],
)
def test_neither_floor_can_raise_a_scam_row(floor_claims: dict) -> None:
    """The two rules that produced the original defect, exercised directly."""
    p = perception(Action.MUTE, (MessageType.SCAM,), **floor_claims)
    f = features(sender_stance=EngagementStance.RECEPTIVE, relationship_established=True)
    decision = decide(p, f, CFG)
    assert decision.action is Action.MUTE
    assert status_of(decision, OverrideCode.RISK_TYPE_CAP) in {
        FiringStatus.APPLIED,
        FiringStatus.FIRED_NOT_DETERMINING,
    }


def test_the_risk_cap_is_not_suppressible_by_any_guard() -> None:
    """`DIRECT_ADDRESS_GUARD` disarms the two conversation-level caps. A scam
    addresses you personally on purpose, so letting a guard reach this rule would
    rebuild the defect through the guard."""
    from policy.rules import RULE_TABLE as TABLE

    for rule in TABLE:
        if rule.effect is RuleEffect.GUARD:
            assert OverrideCode.RISK_TYPE_CAP not in rule.suppresses, rule.code


def test_no_cap_or_floor_may_set_a_type() -> None:
    """The assumption RISK_TYPE_CAP's trigger rests on.

    It reads `type_candidates[0]` because that IS the emitted type on the
    floor/cap path. A cap that declared `sets_type` could rewrite the type after
    the cap ran, reopening the hole without touching the rule or the engine. Only
    Tier 0 hard overrides may set a type, and they also set `mute`.
    """
    from policy.rules import RULE_TABLE as TABLE

    for rule in TABLE:
        if rule.effect in (RuleEffect.CAP, RuleEffect.FLOOR):
            assert rule.sets_type is None, f"{rule.code.value} sets a type"
        if rule.sets_type is not None:
            assert rule.effect is RuleEffect.HARD_OVERRIDE, rule.code
            assert rule.sets_action is Action.MUTE, f"{rule.code.value} types without muting"


def test_the_engine_raises_rather_than_emitting_a_notified_scam_row() -> None:
    """The post-condition backstop. Constructed by bypassing the rule table, so
    it asserts the engine's own guarantee rather than the cap's."""
    from errors import PolicyError
    from policy.engine import _build
    from schemas import RuleFiring

    firings = {
        rule.code: RuleFiring(
            code=rule.code,
            effect=rule.effect,
            status=(
                FiringStatus.APPLIED
                if rule.code is OverrideCode.PERCEPTION_ACCEPTED
                else FiringStatus.DID_NOT_FIRE
            ),
            would_set_action=rule.sets_action,
            justification=rule.justification,
        )
        for rule in RULE_TABLE
    }
    with pytest.raises(PolicyError, match="resolved to notify"):
        _build(Action.NOTIFY, MessageType.SCAM, firings)
    # The same construction with a non-risk type must still build.
    assert _build(Action.NOTIFY, MessageType.URGENT, firings).action is Action.NOTIFY
