"""Apply the rule table to (Perception, FeatureVector). Pure."""

from __future__ import annotations

from config import PolicyConfig
from errors import PolicyError
from policy.rules import RISK_TYPES, RULE_TABLE, Rule
from schemas import (
    ACTION_RANK,
    Action,
    Decision,
    FeatureVector,
    FiringStatus,
    MessageType,
    OverrideCode,
    Perception,
    RuleEffect,
    RuleFiring,
)


def _firing(rule: Rule, status: FiringStatus) -> RuleFiring:
    return RuleFiring(
        code=rule.code,
        effect=rule.effect,
        status=status,
        would_set_action=rule.sets_action,
        justification=rule.justification,
    )


def decide(perception: Perception, features: FeatureVector, cfg: PolicyConfig) -> Decision:
    """Apply the rule table. Exactly one firing has status APPLIED.

    Caps beat floors: a cap encodes the receiving user's expressed preference,
    a floor encodes the system's own judgement of importance. The one exception
    is DIRECT_ADDRESS_GUARD, which suppresses the two conversation-level caps.

    Raises:
        PolicyError: no firing ended with status APPLIED, which means the table
            or this function is broken.
    """
    firings: dict[OverrideCode, RuleFiring] = {}

    # 1. Guards. They set no action; they only mark suppressions.
    suppressed: set[OverrideCode] = set()
    for rule in RULE_TABLE:
        if rule.effect is not RuleEffect.GUARD:
            continue
        if rule.trigger(perception, features, cfg):
            suppressed |= rule.suppresses
            firings[rule.code] = _firing(rule, FiringStatus.FIRED_NOT_DETERMINING)
        else:
            firings[rule.code] = _firing(rule, FiringStatus.DID_NOT_FIRE)

    # 2. Hard overrides, in precedence order. First to fire wins; stop.
    hard_winner: Rule | None = None
    for rule in RULE_TABLE:
        if rule.effect is not RuleEffect.HARD_OVERRIDE:
            continue
        if not rule.trigger(perception, features, cfg):
            firings[rule.code] = _firing(rule, FiringStatus.DID_NOT_FIRE)
            continue
        if hard_winner is None:
            hard_winner = rule
            firings[rule.code] = _firing(rule, FiringStatus.APPLIED)
        else:
            firings[rule.code] = _firing(rule, FiringStatus.SUPERSEDED)

    if hard_winner is not None:
        assert hard_winner.sets_action is not None
        action = hard_winner.sets_action
        message_type = hard_winner.sets_type or perception.type_candidates[0]
        for rule in RULE_TABLE:
            if rule.code not in firings:
                firings[rule.code] = _firing(rule, FiringStatus.NOT_EVALUATED)
        return _build(action, message_type, firings)

    # 3. Start from the proposal, raise by floors, then lower by caps.
    action = perception.proposed_action
    message_type = perception.type_candidates[0]

    floors: list[Rule] = []
    caps: list[Rule] = []
    for rule in RULE_TABLE:
        if rule.effect not in (RuleEffect.FLOOR, RuleEffect.CAP):
            continue
        if rule.code in suppressed:
            firings[rule.code] = _firing(
                rule,
                FiringStatus.SUPPRESSED_BY_GUARD
                if rule.trigger(perception, features, cfg)
                else FiringStatus.DID_NOT_FIRE,
            )
            continue
        if rule.trigger(perception, features, cfg):
            (floors if rule.effect is RuleEffect.FLOOR else caps).append(rule)
        else:
            firings[rule.code] = _firing(rule, FiringStatus.DID_NOT_FIRE)

    determining: Rule | None = None

    if floors:
        best = max(floors, key=lambda r: ACTION_RANK[_action_of(r)])
        if ACTION_RANK[_action_of(best)] > ACTION_RANK[action]:
            action = _action_of(best)
            determining = best

    if caps:
        lowest = min(caps, key=lambda r: ACTION_RANK[_action_of(r)])
        if ACTION_RANK[_action_of(lowest)] < ACTION_RANK[action]:
            action = _action_of(lowest)
            if determining is not None:
                firings[determining.code] = _firing(determining, FiringStatus.OVERRIDDEN_BY_CAP)
                determining = None
            determining = lowest
            if lowest.sets_type is not None:
                message_type = lowest.sets_type

    for rule in (*floors, *caps):
        if rule.code in firings:
            continue
        firings[rule.code] = _firing(
            rule,
            FiringStatus.APPLIED if rule is determining else FiringStatus.FIRED_NOT_DETERMINING,
        )

    default_rule = RULE_TABLE[-1]
    if determining is None:
        firings[default_rule.code] = _firing(default_rule, FiringStatus.APPLIED)
    else:
        firings[default_rule.code] = _firing(default_rule, FiringStatus.FIRED_NOT_DETERMINING)

    for rule in RULE_TABLE:
        if rule.code not in firings:
            firings[rule.code] = _firing(rule, FiringStatus.DID_NOT_FIRE)

    return _build(action, message_type, firings)


def _action_of(rule: Rule) -> Action:
    assert rule.sets_action is not None
    return rule.sets_action


def _build(
    action: Action, message_type: MessageType, firings: dict[OverrideCode, RuleFiring]
) -> Decision:
    ordered = tuple(firings[rule.code] for rule in RULE_TABLE)
    applied = [f for f in ordered if f.status is FiringStatus.APPLIED]
    if len(applied) != 1:
        raise PolicyError(
            f"expected exactly one APPLIED firing, found {len(applied)}: "
            f"{[f.code for f in applied]}"
        )
    # Post-condition, not a rule. RISK_TYPE_CAP is what makes this hold, and it
    # holds only while no floor or cap declares `sets_type` -- an assumption a
    # future rule could break without touching this file. The statement's
    # instruction ("clear scam or safety risk should be muted regardless of the
    # user's usual engagement") is worth failing a run over rather than emitting
    # one row that contradicts it, so this raises instead of correcting the
    # action: a silent repair here would hide the fact that the rule table had
    # stopped being sufficient.
    if action is Action.NOTIFY and message_type in RISK_TYPES:
        raise PolicyError(
            f"a {message_type.value} row resolved to notify, which the rule table "
            f"must prevent; applied={applied[0].code.value}"
        )
    return Decision(action=action, message_type=message_type, firings=ordered)
