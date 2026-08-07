"""The rule table: one Rule per policy rule, in precedence order.

Phase 0 implemented the four structural-trigger rules plus the default. Phase 2
added the whole of Tier 0 -- the six hard overrides -- once perception was real:
five of them read content claims, and until a model supplied those claims they
could only ever have fired on a constant. Phase 3 completes the table with the
guard, the two remaining caps, and the two floors, so every rule in
DECISION_ENGINE section 2 is live and ``implemented`` is True throughout.

Every Rule declares ``reads``, so a test can assert what a rule's trigger
consults without executing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from config import PolicyConfig
from schemas import (
    Action,
    EngagementStance,
    FeatureVector,
    MessageType,
    OverrideCode,
    Perception,
    RuleEffect,
    TriggerSource,
)

Trigger = Callable[[Perception, FeatureVector, PolicyConfig], bool]


@dataclass(frozen=True)
class Rule:
    """One policy rule. Data, not behaviour beyond its trigger predicate."""

    code: OverrideCode
    effect: RuleEffect
    trigger_source: TriggerSource
    justification: str
    reads: frozenset[str]
    trigger: Trigger
    sets_action: Action | None = None
    sets_type: MessageType | None = None
    suppresses: frozenset[OverrideCode] = field(default_factory=frozenset)
    asserts_pattern: bool = False
    cites_history: bool = False
    is_integrity_rule: bool = False
    implemented: bool = True

    @property
    def weighting_source(self) -> TriggerSource:
        """MIXED counts as PERCEPTUAL for the confidence trigger term."""
        return (
            TriggerSource.PERCEPTUAL
            if self.trigger_source is TriggerSource.MIXED
            else self.trigger_source
        )


def _never(_p: Perception, _f: FeatureVector, _c: PolicyConfig) -> bool:
    """Trigger for a rule declared but not implemented in this phase."""
    return False


# --------------------------------------------------------------------------
# Tier 0 -- Safety
# --------------------------------------------------------------------------

CREDENTIAL_SOLICITATION = Rule(
    code=OverrideCode.CREDENTIAL_SOLICITATION,
    effect=RuleEffect.HARD_OVERRIDE,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification=(
        "The message asks the user to hand over an authentication secret, "
        "which no legitimate sender requires."
    ),
    reads=frozenset({"P.requests_authentication_secret"}),
    trigger=lambda p, _f, _c: p.requests_authentication_secret,
    sets_action=Action.MUTE,
    sets_type=MessageType.SCAM,
    is_integrity_rule=True,
)

ROUTER_DIRECTIVE = Rule(
    code=OverrideCode.ROUTER_DIRECTIVE,
    effect=RuleEffect.HARD_OVERRIDE,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification=(
        "The message contains instructions aimed at the routing system rather "
        "than at the recipient."
    ),
    reads=frozenset({"P.contains_router_directive"}),
    trigger=lambda p, _f, _c: p.contains_router_directive,
    sets_action=Action.MUTE,
    sets_type=MessageType.SCAM,
    is_integrity_rule=True,
)


def _impersonation(_p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    from schemas import DomainState

    return (not f.business_verified) and (
        f.domain_state is DomainState.MISMATCH or not f.business_identity_consistent
    )


IMPERSONATION = Rule(
    code=OverrideCode.IMPERSONATION,
    effect=RuleEffect.HARD_OVERRIDE,
    trigger_source=TriggerSource.STRUCTURAL,
    justification="An unverified business account is presenting under a brand or domain it does not own.",
    reads=frozenset({"F.business_verified", "F.domain_state", "F.business_identity_consistent"}),
    trigger=_impersonation,
    sets_action=Action.MUTE,
    sets_type=MessageType.SCAM,
    is_integrity_rule=True,
)

def _coercive_payment(p: Perception, _f: FeatureVector, _c: PolicyConfig) -> bool:
    """All three, deliberately (DECISION_ENGINE rule 4).

    A payment request alone is ordinary; a deadline alone is ordinary; a warning
    alone is ordinary. It is the conjunction -- pay, or else, by today -- that no
    legitimate sender needs, and requiring all three is what keeps the rule from
    muting invoices.
    """
    return (
        p.requests_payment_or_payment_proof
        and p.threatens_adverse_consequence
        and p.states_deadline_within_day
    )


COERCIVE_PAYMENT = Rule(
    code=OverrideCode.COERCIVE_PAYMENT,
    effect=RuleEffect.HARD_OVERRIDE,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification=(
        "The message pairs a payment demand with a threatened consequence and "
        "an immediate deadline."
    ),
    reads=frozenset(
        {
            "P.requests_payment_or_payment_proof",
            "P.threatens_adverse_consequence",
            "P.states_deadline_within_day",
        }
    ),
    trigger=_coercive_payment,
    sets_action=Action.MUTE,
    sets_type=MessageType.SCAM,
    is_integrity_rule=True,
)

UNSAFE_INSTRUCTION = Rule(
    code=OverrideCode.UNSAFE_INSTRUCTION,
    effect=RuleEffect.HARD_OVERRIDE,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification="The message advises an action that carries a plausible health or safety consequence.",
    reads=frozenset({"P.advises_action_with_safety_consequence"}),
    trigger=lambda p, _f, _c: p.advises_action_with_safety_consequence,
    sets_action=Action.MUTE,
    is_integrity_rule=True,
)

# --------------------------------------------------------------------------
# Tier 1 -- Payment authority
# --------------------------------------------------------------------------

def _unauthorized_payment(p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    """Mixed trigger: two perception claims gated by one structural fact.

    The structural conjunct is what stops a compromised model from muting a
    legitimate payment request -- but it cannot save the rule on its own, which
    is why THREAT_MODEL classes this one as defeatable.
    """
    return (
        p.requests_payment_or_payment_proof
        and p.directs_off_platform_for_money_or_identity
        and not f.sender_holds_payment_authority
    )


UNAUTHORIZED_PAYMENT_REQUEST = Rule(
    code=OverrideCode.UNAUTHORIZED_PAYMENT_REQUEST,
    effect=RuleEffect.HARD_OVERRIDE,
    trigger_source=TriggerSource.MIXED,
    justification=(
        "A payment instruction arrived from a sender with no established "
        "authority for payments in this conversation."
    ),
    reads=frozenset(
        {
            "P.requests_payment_or_payment_proof",
            "P.directs_off_platform_for_money_or_identity",
            "F.sender_holds_payment_authority",
        }
    ),
    trigger=_unauthorized_payment,
    sets_action=Action.MUTE,
    sets_type=MessageType.SCAM,
    is_integrity_rule=True,
)

# --------------------------------------------------------------------------
# Tier 2 -- Guards
# --------------------------------------------------------------------------

def _direct_address(p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    """A muted conversation is a statement about ambient traffic, not a refusal
    to hear from anyone in it. The DISMISSIVE conjunct stops a sender the user
    has repeatedly muted from re-acquiring reach by adding a mention."""
    return p.addresses_recipient_directly and f.sender_stance is not EngagementStance.DISMISSIVE


DIRECT_ADDRESS_GUARD = Rule(
    code=OverrideCode.DIRECT_ADDRESS_GUARD,
    effect=RuleEffect.GUARD,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification=(
        "The message addresses the user directly, so conversation-level "
        "suppression does not apply."
    ),
    reads=frozenset({"P.addresses_recipient_directly", "F.sender_stance"}),
    trigger=_direct_address,
    suppresses=frozenset({OverrideCode.GROUP_MUTE_CAP, OverrideCode.COLD_SENDER_CAP}),
)

# --------------------------------------------------------------------------
# Tier 2b -- The risk-type cap
# --------------------------------------------------------------------------

RISK_TYPES: frozenset[MessageType] = frozenset({MessageType.SCAM, MessageType.SPAM})
"""The two types the statement says are never worth an interruption.

`scam` is "clear scam or safety risk", which the statement says to mute
*regardless of the user's usual engagement*; `spam` is unsolicited bulk contact,
which is the definition of low value to this user. Nothing else belongs here --
`urgent` and `payment` can be risky in context, but they can equally be
legitimate and interrupting, which is exactly the judgement the rest of the
table exists to make."""


def _risk_type_cap(p: Perception, _f: FeatureVector, _c: PolicyConfig) -> bool:
    """The row's emitted type is one the statement says must not interrupt.

    Reads `type_candidates[0]` rather than a resolved type because that IS the
    emitted type on this path: the engine sets `message_type` from it before any
    floor or cap runs, and no floor or cap declares `sets_type`. Only the Tier 0
    hard overrides do, and they return before this rule is reached -- while also
    setting `mute`, so they can never produce the state this cap exists to
    prevent. `test_no_cap_or_floor_may_set_a_type` pins that assumption, so a
    future rule that breaks it fails loudly instead of silently reopening the
    hole.
    """
    return p.type_candidates[0] in RISK_TYPES


# --------------------------------------------------------------------------
# Tier 3 -- Consent and relationship caps
# --------------------------------------------------------------------------

def _promotion_opt_out(p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    """The solicits_purchase conjunct scopes the cap to marketing: an opted-out
    user must still receive transactional messages from that business."""
    return f.promotions_opted_out and p.solicits_purchase


RISK_TYPE_CAP = Rule(
    code=OverrideCode.RISK_TYPE_CAP,
    effect=RuleEffect.CAP,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification="The message reads as a scam or as unsolicited bulk contact.",
    reads=frozenset({"P.type_candidates"}),
    trigger=_risk_type_cap,
    sets_action=Action.MUTE,
    is_integrity_rule=True,
)
"""A row typed `scam` or `spam` may not be actioned `notify`.

**Why this is a cap and not a floor exclusion.** The defect it closes was two
rows the model typed `scam` *and proposed muting*, which `TIME_CRITICAL_FLOOR`
and `DIRECT_REQUEST_FLOOR` then raised to `notify` -- a same-day deadline and a
direct request for action being precisely what a scam manufactures, so the two
floors were reading the attack's own payload as grounds to interrupt. Merely
suppressing those floors would have fixed those two rows and left the hole open:
a model that *proposed* `notify` on a `scam`-typed row would still have emitted
it, since no floor need be involved. A cap is evaluated against whatever the
action already is, so it closes both routes with one rule.

**Why it is not suppressible.** `DIRECT_ADDRESS_GUARD` suppresses the two
conversation-level caps by code, and this rule is deliberately not in that set.
"The message addresses you personally" is what a scam does; letting it disarm the
safety cap would rebuild the defect through the guard.

**Placed first among the caps** so that when it is the determining rule, the
explanation the row carries is the safety one. It costs nothing elsewhere: caps
only determine a row when they *lower* the action, so on a row already muted for
another reason this fires `FIRED_NOT_DETERMINING` and the existing explanation
stands."""

PROMOTION_OPT_OUT = Rule(
    code=OverrideCode.PROMOTION_OPT_OUT,
    effect=RuleEffect.CAP,
    trigger_source=TriggerSource.MIXED,
    justification="The user has opted out of marketing messages from this sender.",
    reads=frozenset({"F.promotions_opted_out", "P.solicits_purchase"}),
    trigger=_promotion_opt_out,
    sets_action=Action.MUTE,
    cites_history=True,
)


def _repeat_dismissal(_p: Perception, f: FeatureVector, c: PolicyConfig) -> bool:
    return (
        f.prior_interaction_count >= c.MIN_PATTERN_OBSERVATIONS
        and f.dismissal_ratio is not None
        and f.dismissal_ratio > c.DISMISSAL_MAJORITY
    )


REPEAT_DISMISSAL = Rule(
    code=OverrideCode.REPEAT_DISMISSAL,
    effect=RuleEffect.CAP,
    trigger_source=TriggerSource.STRUCTURAL,
    justification="Comparable messages from this sender were repeatedly dismissed or muted by this user.",
    reads=frozenset({"F.prior_interaction_count", "F.dismissal_ratio"}),
    trigger=_repeat_dismissal,
    sets_action=Action.MUTE,
    asserts_pattern=True,
    cites_history=True,
)

def _frequently_forwarded(p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    """A long chain alone is not enough: a forwarded message written to this
    person is still for this person, so the direct-address conjunct excuses it."""
    return (
        f.is_frequently_forwarded
        and not p.addresses_recipient_directly
        and p.is_circulated_content
    )


FREQUENTLY_FORWARDED_BROADCAST = Rule(
    code=OverrideCode.FREQUENTLY_FORWARDED_BROADCAST,
    effect=RuleEffect.CAP,
    trigger_source=TriggerSource.MIXED,
    justification="This is widely circulated content that was not written for this particular user.",
    reads=frozenset(
        {"F.is_frequently_forwarded", "P.addresses_recipient_directly", "P.is_circulated_content"}
    ),
    trigger=_frequently_forwarded,
    sets_action=Action.MUTE,
    asserts_pattern=True,
)


def _group_mute(_p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    return f.group_muted_by_receiver


GROUP_MUTE_CAP = Rule(
    code=OverrideCode.GROUP_MUTE_CAP,
    effect=RuleEffect.CAP,
    trigger_source=TriggerSource.STRUCTURAL,
    justification=(
        "The user has muted this conversation, so the message is shown later "
        "instead of interrupting."
    ),
    reads=frozenset({"F.group_muted_by_receiver"}),
    trigger=_group_mute,
    sets_action=Action.DIGEST,
)


def _cold_sender(_p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    return not f.relationship_established


COLD_SENDER_CAP = Rule(
    code=OverrideCode.COLD_SENDER_CAP,
    effect=RuleEffect.CAP,
    trigger_source=TriggerSource.STRUCTURAL,
    justification="There is no established relationship with this sender, so the message can wait.",
    reads=frozenset({"F.relationship_established"}),
    trigger=_cold_sender,
    sets_action=Action.DIGEST,
)

# --------------------------------------------------------------------------
# Tier 4 -- Priority floors
# --------------------------------------------------------------------------

def _time_critical(p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    """A time alone is not a deadline for the recipient.

    PROBLEM_ANALYSIS 4.6 defines urgency as a stated window *with a consequence
    attached to missing it*, and this floor is the strongest positive claim the
    engine makes about time. Without the consequence conjunct it fired on every
    announced schedule -- a fire-alarm test, a delivery window, a moved dance
    class -- none of which asks anything of the recipient. Commercial urgency is
    excluded separately: "offer ends tonight" is a sales technique, not a
    consequence.
    """
    return (
        p.states_deadline_within_day
        and p.deadline_has_stated_consequence
        and f.sender_stance is not EngagementStance.DISMISSIVE
        and not p.solicits_purchase
    )


TIME_CRITICAL_FLOOR = Rule(
    code=OverrideCode.TIME_CRITICAL_FLOOR,
    effect=RuleEffect.FLOOR,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification="The message carries a same-day deadline from a sender this user engages with.",
    reads=frozenset(
        {
            "P.states_deadline_within_day",
            "P.deadline_has_stated_consequence",
            "F.sender_stance",
            "P.solicits_purchase",
        }
    ),
    trigger=_time_critical,
    sets_action=Action.NOTIFY,
)

def _direct_request(p: Perception, f: FeatureVector, _c: PolicyConfig) -> bool:
    """Addressed is not the same as asked.

    The rule's own justification is "asking this user directly for a response or
    an action", but `addresses_recipient_directly` asserts only that the message
    speaks to this recipient. That claim cannot be narrowed -- the guard needs
    the broad reading, since a mention should defeat a conversation-level mute
    whether or not it asks anything -- so the request is a separate conjunct.
    Without it the floor interrupted on "don't call now, we can talk tomorrow".

    RECEPTIVE, not merely "not dismissive": a floor to notify is the strongest
    positive claim the engine makes, so it demands positive evidence that this
    user has actually engaged with this sender before.
    """
    return (
        p.addresses_recipient_directly
        and p.requests_response_or_action
        and f.sender_stance is EngagementStance.RECEPTIVE
    )


DIRECT_REQUEST_FLOOR = Rule(
    code=OverrideCode.DIRECT_REQUEST_FLOOR,
    effect=RuleEffect.FLOOR,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification="The sender is asking this user directly for a response or an action.",
    reads=frozenset(
        {"P.addresses_recipient_directly", "P.requests_response_or_action", "F.sender_stance"}
    ),
    trigger=_direct_request,
    sets_action=Action.NOTIFY,
)

# --------------------------------------------------------------------------
# Tier 5 -- Default
# --------------------------------------------------------------------------


def _always(_p: Perception, _f: FeatureVector, _c: PolicyConfig) -> bool:
    return True


PERCEPTION_ACCEPTED = Rule(
    code=OverrideCode.PERCEPTION_ACCEPTED,
    effect=RuleEffect.DEFAULT,
    trigger_source=TriggerSource.PERCEPTUAL,
    justification="",  # composed by compose.reason -- there is no single rule to name
    reads=frozenset({"P.proposed_action", "P.type_candidates"}),
    trigger=_always,
)


RULE_TABLE: tuple[Rule, ...] = (
    CREDENTIAL_SOLICITATION,
    ROUTER_DIRECTIVE,
    IMPERSONATION,
    COERCIVE_PAYMENT,
    UNSAFE_INSTRUCTION,
    UNAUTHORIZED_PAYMENT_REQUEST,
    DIRECT_ADDRESS_GUARD,
    RISK_TYPE_CAP,
    PROMOTION_OPT_OUT,
    REPEAT_DISMISSAL,
    FREQUENTLY_FORWARDED_BROADCAST,
    GROUP_MUTE_CAP,
    COLD_SENDER_CAP,
    TIME_CRITICAL_FLOOR,
    DIRECT_REQUEST_FLOOR,
    PERCEPTION_ACCEPTED,
)

RULES_BY_CODE: dict[OverrideCode, Rule] = {rule.code: rule for rule in RULE_TABLE}
