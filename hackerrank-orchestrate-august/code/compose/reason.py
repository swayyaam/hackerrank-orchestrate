"""Render the one-sentence reason and enforce the register.

An overriding rule supplies its own justification. The default rule has no
single rule to name, so the reason is composed from a clause grammar over
(message_type, action, dominant_feature). The clause fragments were authored
from the type definitions in problem_statement.md, not copied from the sample's
reason column.
"""

from __future__ import annotations

import re
from typing import Callable, Sequence

from config import ReasonConfig
from errors import ReasonRegisterError
from policy.rules import RULE_TABLE, RULES_BY_CODE
from schemas import (
    Action,
    Decision,
    EngagementStance,
    EvidenceSelection,
    EvidenceTier,
    FeatureVector,
    MessageType,
    OverrideCode,
    ThreatEvent,
)

_IDENTIFIER = re.compile(r"\b(?:msg|message|sample_msg|u|group|business|img|vn)_\d+\b")

Predicate = Callable[[FeatureVector], bool]

# dominant_feature: a fixed priority list per action, consulted in order. The
# first predicate that holds wins; the last entry always holds, so the list is
# exhaustive by construction.
_DOMINANT: dict[Action, tuple[tuple[str, Predicate], ...]] = {
    Action.NOTIFY: (
        ("media_unread", lambda f: f.media_unread),
        ("sender_is_group_admin", lambda f: f.sender_is_group_admin),
        ("stance_receptive", lambda f: f.sender_stance is EngagementStance.RECEPTIVE),
        (
            "verified_known_business",
            lambda f: f.business_verified and f.business_relationship_known,
        ),
        ("relationship_established", lambda f: f.relationship_established),
        ("unestablished", lambda f: not f.relationship_established),
        ("fallback", lambda _f: True),
    ),
    Action.DIGEST: (
        ("media_unread", lambda f: f.media_unread),
        ("business_verified", lambda f: f.business_verified),
        ("stance_neutral", lambda f: f.sender_stance is EngagementStance.NEUTRAL),
        ("unestablished", lambda f: not f.relationship_established),
        ("fallback", lambda _f: True),
    ),
    Action.MUTE: (
        ("media_unread", lambda f: f.media_unread),
        ("stance_dismissive", lambda f: f.sender_stance is EngagementStance.DISMISSIVE),
        ("promotions_opted_out", lambda f: f.promotions_opted_out),
        ("frequently_forwarded", lambda f: f.is_frequently_forwarded),
        ("group_muted", lambda f: f.group_muted_by_receiver),
        ("unestablished", lambda f: not f.relationship_established),
        ("fallback", lambda _f: True),
    ),
}
"""Priority lists, consulted in order. Every entry names a fact the row actually
carries; the trailing `fallback` is retained for exhaustiveness but is written to
say something rather than nothing, because a reason that names no cause is not
an explanation."""

_SUBJECT: dict[MessageType, str] = {
    MessageType.PERSONAL: "This is ordinary personal conversation",
    MessageType.URGENT: "The message states a deadline the user has to meet",
    MessageType.EVENT: "The message concerns a scheduled occurrence",
    MessageType.PAYMENT: "The message concerns a monetary transaction",
    MessageType.BUSINESS_UPDATE: "A business is reporting on something the user already did",
    MessageType.PROMOTION: "The message is an offer rather than an update",
    MessageType.GREETING: "The message is a salutation with nothing to act on",
    MessageType.FORWARD: "The message is circulated content passed along unchanged",
    MessageType.SPAM: "The message is unsolicited bulk contact",
    MessageType.SCAM: "The message is trying to obtain something from the user",
    MessageType.UNKNOWN: "The message does not fit a clear category",
}

_QUALIFIER: dict[tuple[Action, str], str] = {
    (Action.NOTIFY, "sender_is_group_admin"): "and a group admin sent it, so it interrupts now.",
    (Action.NOTIFY, "stance_receptive"): "from a sender this user reliably engages with.",
    (Action.NOTIFY, "verified_known_business"): "and it matches the user's recent activity there.",
    (Action.NOTIFY, "relationship_established"): "from a sender the user already knows.",
    (Action.NOTIFY, "unestablished"): "from a sender with no history here, so it interrupts once.",
    (Action.NOTIFY, "fallback"): "and no history here argues against interrupting.",
    (Action.NOTIFY, "media_unread"): "but its attachment could not be read, so it interrupts anyway.",
    (Action.MUTE, "media_unread"): "and its attachment could not be read, so it is suppressed.",
    (Action.DIGEST, "media_unread"): "but its attachment could not be read, so it waits.",
    (Action.DIGEST, "business_verified"): "from a verified business, and it can be read later.",
    (Action.DIGEST, "stance_neutral"): "from a familiar sender, and it can be read later.",
    (Action.DIGEST, "unestablished"): "from a sender with no history here, so it can wait.",
    (Action.DIGEST, "fallback"): "and nothing in it marks it as needing attention now.",
    (Action.MUTE, "stance_dismissive"): "from a sender whose messages this user consistently ignores.",
    (Action.MUTE, "promotions_opted_out"): "and the user withdrew consent for marketing here.",
    (Action.MUTE, "frequently_forwarded"): "and it was circulated widely rather than written here.",
    (Action.MUTE, "group_muted"): "in a conversation the user has muted.",
    (Action.MUTE, "unestablished"): "from a sender with no history here, and it can be suppressed.",
    (Action.MUTE, "fallback"): "and nothing about it merits interrupting this user.",
}


_WARNING_CLAUSE: dict[ThreatEvent, str] = {
    ThreatEvent.FENCE_TOKEN_IN_UNTRUSTED_INPUT: (
        "and it carries instructions aimed at the router, not the user."
    ),
    ThreatEvent.BUSINESS_DOMAIN_MISMATCH: (
        "and the sender is using a domain the named brand does not own."
    ),
    ThreatEvent.BUSINESS_IDENTITY_INCONSISTENT: (
        "and its stated identity does not match the account sending it."
    ),
    ThreatEvent.MEDIA_CONTRADICTS_TEXT: (
        "and its attachment says something different from its text."
    ),
    ThreatEvent.MEDIA_PATH_ESCAPED_ROOT: (
        "and its attachment pointed outside the permitted location."
    ),
    ThreatEvent.MEDIA_LIMIT_EXCEEDED: (
        "and its attachment exceeded the size this system will open."
    ),
    ThreatEvent.UNTRUSTED_FIELD_TRUNCATED: (
        "and it was long enough to be truncated before being read."
    ),
    ThreatEvent.CONTROL_CHARACTERS_STRIPPED: (
        "and it contained hidden characters that had to be removed."
    ),
    ThreatEvent.MODEL_RESPONSE_SCHEMA_REJECTED: (
        "and the first reading of it had to be discarded and retaken."
    ),
}
"""One clause per observation the reason may have to name.

`_WARNING_CLAUSE` is keyed on ThreatEvent and ordered by the enum, so the clause
chosen for a row carrying several is deterministic and matches the order the
warnings are recorded in."""

_HISTORY_QUALIFIER: dict[EngagementStance, str] = {
    EngagementStance.RECEPTIVE: "and this user opened this sender's recent messages.",
    EngagementStance.NEUTRAL: "and this user opened only some of this sender's messages.",
    EngagementStance.DISMISSIVE: "and this user dismissed this sender's recent messages.",
}
"""What actually happened to the messages this row cites as evidence.

Each clause names the receiver's observed behaviour toward this sender, which is
what the ids in `evidence_message_ids` are evidence *of* — so the sentence and
the citation column describe the same thing rather than two unrelated ones. The
stance is derived from the interaction events on exactly those historical rows,
so the clause is supported by the citation and not merely adjacent to it.

The reason may not name an id (the register check rejects any identifier), so
"this sender's recent messages" is as specific as the sentence is permitted to
be. Only stances derived from observed history appear here; UNKNOWN has nothing
to describe -- `_UNRECORDED_ENGAGEMENT` covers that case instead."""

_UNRECORDED_ENGAGEMENT = "and it continues a conversation this user already has."
"""For a row whose evidence is at RELATIONSHIP tier but whose stance is UNKNOWN.

Such a row has prior messages with this counterparty or in this conversation --
that is exactly what the tier asserts -- but no recorded interaction with them,
so nothing can be said about what the user *did*. Before this clause existed the
composer had no way to say "there is history here, but no reaction to it" and
fell through to a structural fact, leaving the sentence and the citation column
describing different things on a row that had relationship evidence to cite.

Deliberately says "a conversation" rather than "this sender": RELATIONSHIP tier
is satisfied by same-counterparty **or** same-conversation, so on a group row the
cited message may be from a different member. The weaker wording is the one both
cases support."""


def dominant_feature(action: Action, features: FeatureVector) -> str:
    """The first priority-list predicate that holds for this row."""
    for name, predicate in _DOMINANT[action]:
        if predicate(features):
            return name
    raise ReasonRegisterError(f"no dominant feature matched for action={action}")


def _compose_default(
    decision: Decision, features: FeatureVector, evidence: EvidenceSelection
) -> str:
    subject = _SUBJECT[decision.message_type]
    feature = dominant_feature(decision.action, features)

    # On this path no rule fired: the action is the model's proposal, and what
    # the system can add is what the receiver's own history says about the
    # sender. That is also what the cited ids are evidence of, so leading with it
    # makes the sentence and the citation column corroborate each other instead
    # of describing two unrelated things.
    #
    # `media_unread` still wins. It is the one case where the system must say it
    # never read the message, and no amount of history substitutes for that.
    if (
        feature != "media_unread"
        and evidence.message_ids
        and evidence.tier >= EvidenceTier.RELATIONSHIP
    ):
        grounded = _HISTORY_QUALIFIER.get(features.sender_stance, _UNRECORDED_ENGAGEMENT)
        return f"{subject} {grounded}"
    return f"{subject} {_QUALIFIER[(decision.action, feature)]}"


def _compose_warned(decision: Decision, warnings: Sequence[ThreatEvent]) -> str | None:
    """Name the row's integrity observation, or None if none has a clause."""
    for event in warnings:
        clause = _WARNING_CLAUSE.get(event)
        if clause is not None:
            return f"{_SUBJECT[decision.message_type]} {clause}"
    return None


def render_reason(
    decision: Decision,
    features: FeatureVector,
    evidence: EvidenceSelection,
    warnings: Sequence[ThreatEvent],
    cfg: ReasonConfig,
) -> str:
    """One sentence in the required register.

    Raises:
        ReasonRegisterError: the composed sentence is outside the length bounds,
            contains more than one sentence, or names an identifier. Never
            truncated -- a silently trimmed reason is a wrong explanation.
    """
    applied = decision.applied
    rule = RULES_BY_CODE[applied.code]

    # Precedence for what the one sentence spends itself on:
    #   1. an integrity observation the applied rule does not already state,
    #   2. the fact that nothing was readable,
    #   3. the applied rule's own justification,
    #   4. the composed default.
    # An integrity rule's justification already names the concern, so warned
    # rows under those rules keep it rather than paraphrasing it worse.
    text: str | None = None
    if warnings and not rule.is_integrity_rule:
        text = _compose_warned(decision, warnings)

    if text is None:
        # An unreadable row is composed even when an overriding rule determined
        # the action. A rule's fixed justification cannot mention the
        # attachment, and a sentence explaining a decision without saying the
        # system never read the message is an incomplete explanation -- the one
        # fact the user most needs. The rule is not lost: its code is on the row
        # record and in the metrics. `media_unread` is narrow (media failed AND
        # no text), so this never displaces a justification on a row that had
        # text to route on.
        if (
            features.media_unread
            or applied.code is OverrideCode.PERCEPTION_ACCEPTED
            or not applied.justification
        ):
            text = _compose_default(decision, features, evidence)
        else:
            text = applied.justification

    if _IDENTIFIER.search(text):
        raise ReasonRegisterError(f"reason names an identifier: {text!r}")
    if text.count(".") > cfg.MAX_SENTENCES:
        raise ReasonRegisterError(f"reason is more than one sentence: {text!r}")
    if not cfg.MIN_CHARS <= len(text) <= cfg.MAX_CHARS:
        raise ReasonRegisterError(
            f"reason length {len(text)} outside [{cfg.MIN_CHARS}, {cfg.MAX_CHARS}]: {text!r}"
        )
    return text


# --------------------------------------------------------------------------
# Measuring the sentence
#
# Both predicates below read the COMPOSED TEXT, not the applied rule's metadata.
# Reading metadata was the earlier defect: a rule flagged `cites_history` scored
# as evidence-citing whatever its sentence said, and a rule that was not so
# flagged scored as not citing even when its sentence described the user's
# history in as many words. The metric then measured rule bookkeeping and
# reported it as a property of the output.
#
# Membership is exact rather than fuzzy: this module owns every clause it can
# emit, so the sets below are drawn from the same tables the composer uses and
# a test asserts they stay in sync.
# --------------------------------------------------------------------------

_HISTORY_CLAUSES: frozenset[str] = frozenset(
    [
        *_HISTORY_QUALIFIER.values(),
        _UNRECORDED_ENGAGEMENT,
        _QUALIFIER[(Action.NOTIFY, "stance_receptive")],
        _QUALIFIER[(Action.NOTIFY, "relationship_established")],
        _QUALIFIER[(Action.NOTIFY, "unestablished")],
        _QUALIFIER[(Action.NOTIFY, "fallback")],
        _QUALIFIER[(Action.DIGEST, "stance_neutral")],
        _QUALIFIER[(Action.DIGEST, "unestablished")],
        _QUALIFIER[(Action.MUTE, "stance_dismissive")],
        _QUALIFIER[(Action.MUTE, "group_muted")],
        _QUALIFIER[(Action.MUTE, "unestablished")],
        RULES_BY_CODE[OverrideCode.REPEAT_DISMISSAL].justification,
        RULES_BY_CODE[OverrideCode.PROMOTION_OPT_OUT].justification,
        RULES_BY_CODE[OverrideCode.COLD_SENDER_CAP].justification,
        RULES_BY_CODE[OverrideCode.GROUP_MUTE_CAP].justification,
        RULES_BY_CODE[OverrideCode.TIME_CRITICAL_FLOOR].justification,
    ]
)
"""Clauses that make a claim about this receiver's prior behaviour toward this
sender -- including its absence, which is equally a claim about history.

Membership is decided by **reading each sentence**, not by the rule's
`cites_history` flag. Deriving it from the flag was the earlier form of the same
defect this module already fixed once: `TIME_CRITICAL_FLOOR` ends "from a sender
this user engages with" and scored as not-citing because its rule is not so
flagged, while `FREQUENTLY_FORWARDED_BROADCAST` is a claim about the *message*
("widely circulated content") and would have scored as citing if it were. The
flag records what a rule reads; this set records what a sentence says."""

_INTEGRITY_CLAUSES: frozenset[str] = frozenset(
    [
        *_WARNING_CLAUSE.values(),
        *(rule.justification for rule in RULE_TABLE if rule.is_integrity_rule),
    ]
)

_STRUCTURAL_CLAUSES: frozenset[str] = frozenset(
    [
        _QUALIFIER[(Action.NOTIFY, "sender_is_group_admin")],
        _QUALIFIER[(Action.NOTIFY, "verified_known_business")],
        _QUALIFIER[(Action.NOTIFY, "media_unread")],
        _QUALIFIER[(Action.DIGEST, "business_verified")],
        _QUALIFIER[(Action.DIGEST, "media_unread")],
        _QUALIFIER[(Action.DIGEST, "fallback")],
        _QUALIFIER[(Action.MUTE, "promotions_opted_out")],
        _QUALIFIER[(Action.MUTE, "frequently_forwarded")],
        _QUALIFIER[(Action.MUTE, "media_unread")],
        _QUALIFIER[(Action.MUTE, "fallback")],
        RULES_BY_CODE[OverrideCode.FREQUENTLY_FORWARDED_BROADCAST].justification,
        RULES_BY_CODE[OverrideCode.DIRECT_REQUEST_FLOOR].justification,
        RULES_BY_CODE[OverrideCode.DIRECT_ADDRESS_GUARD].justification,
    ]
)
"""Clauses that name a structural or content fact rather than a history claim.

Present so the three sets are **exhaustive** over everything the composer can
emit: a clause added without being classified into one of them is caught by
`test_every_emittable_clause_is_classified`, which is what stops a new generic
phrasing from slipping in and scoring as an explanation."""


def names_decisive_factor(reason: str) -> bool:
    """Whether the sentence names *something* — history, integrity, or structure.

    The weaker of the two reason metrics, and the one that must be 100%: a reason
    that names nothing is not an explanation, whatever else is true of it.
    `cites_evidence` is the stronger claim and is bounded by how many rows were
    actually decided on the receiver's history.
    """
    return cites_evidence(reason) or references_integrity(reason) or any(
        clause in reason for clause in _STRUCTURAL_CLAUSES
    )


def cites_evidence(reason: str) -> bool:
    """Whether the sentence actually makes a claim about prior interactions."""
    return any(clause in reason for clause in _HISTORY_CLAUSES)


def references_integrity(reason: str) -> bool:
    """Whether the sentence actually names an integrity concern."""
    return any(clause in reason for clause in _INTEGRITY_CLAUSES)
