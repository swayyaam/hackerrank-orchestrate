"""All enums and pydantic models. A leaf: imports config only."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(frozen=True, extra="forbid")


# ==========================================================================
# Enums
# ==========================================================================


class Action(StrEnum):
    """Final routing decision. Values are the contract literals."""

    NOTIFY = "notify"
    DIGEST = "digest"
    MUTE = "mute"


ACTION_RANK: dict[Action, int] = {Action.MUTE: 0, Action.DIGEST: 1, Action.NOTIFY: 2}


class MessageType(StrEnum):
    """Message category. All eleven contract values, including PAYMENT."""

    PERSONAL = "personal"
    URGENT = "urgent"
    EVENT = "event"
    PAYMENT = "payment"
    BUSINESS_UPDATE = "business_update"
    PROMOTION = "promotion"
    GREETING = "greeting"
    FORWARD = "forward"
    SPAM = "spam"
    SCAM = "scam"
    UNKNOWN = "unknown"


class ConversationType(StrEnum):
    PERSONAL = "personal"
    GROUP = "group"
    BUSINESS = "business"


class MediaKind(StrEnum):
    NONE = ""
    IMAGE = "image"
    VOICE = "voice"


class MemberRole(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"


class DomainState(StrEnum):
    """Three-state, because either domain field may be blank.

    UNDETERMINED is not MISMATCH. A business with no recorded official_domain
    cannot be shown to be sending from a domain it does not own.
    """

    MATCH = "match"
    MISMATCH = "mismatch"
    UNDETERMINED = "undetermined"


class EngagementStance(StrEnum):
    """How the receiver has historically treated messages from this source."""

    RECEPTIVE = "receptive"
    NEUTRAL = "neutral"
    DISMISSIVE = "dismissive"
    UNKNOWN = "unknown"


class EvidenceTier(IntEnum):
    """Strength of the retrieved evidence. Ordered; used as a confidence term."""

    NONE = 0
    TOPICAL = 1
    RELATIONSHIP = 2
    RELATIONSHIP_AND_CONTENT = 3


class RuleEffect(StrEnum):
    HARD_OVERRIDE = "hard_override"
    CAP = "cap"
    FLOOR = "floor"
    GUARD = "guard"
    DEFAULT = "default"


class TriggerSource(StrEnum):
    """Whether a rule's trigger reads Perception. Drives the confidence term.

    MIXED counts as PERCEPTUAL for weighting (DECISION_ENGINE section 5): a
    perception claim is load-bearing in the conjunction.
    """

    STRUCTURAL = "structural"
    PERCEPTUAL = "perceptual"
    MIXED = "mixed"


class FiringStatus(StrEnum):
    APPLIED = "applied"
    SUPERSEDED = "superseded"
    SUPPRESSED_BY_GUARD = "suppressed_by_guard"
    OVERRIDDEN_BY_CAP = "overridden_by_cap"
    FIRED_NOT_DETERMINING = "fired_not_determining"
    NOT_EVALUATED = "not_evaluated"
    DID_NOT_FIRE = "did_not_fire"


class OverrideCode(StrEnum):
    """One code per policy rule. Recorded on OutputRow and counted post hoc."""

    CREDENTIAL_SOLICITATION = "CREDENTIAL_SOLICITATION"
    ROUTER_DIRECTIVE = "ROUTER_DIRECTIVE"
    IMPERSONATION = "IMPERSONATION"
    COERCIVE_PAYMENT = "COERCIVE_PAYMENT"
    UNSAFE_INSTRUCTION = "UNSAFE_INSTRUCTION"
    UNAUTHORIZED_PAYMENT_REQUEST = "UNAUTHORIZED_PAYMENT_REQUEST"
    DIRECT_ADDRESS_GUARD = "DIRECT_ADDRESS_GUARD"
    RISK_TYPE_CAP = "RISK_TYPE_CAP"
    PROMOTION_OPT_OUT = "PROMOTION_OPT_OUT"
    REPEAT_DISMISSAL = "REPEAT_DISMISSAL"
    FREQUENTLY_FORWARDED_BROADCAST = "FREQUENTLY_FORWARDED_BROADCAST"
    GROUP_MUTE_CAP = "GROUP_MUTE_CAP"
    COLD_SENDER_CAP = "COLD_SENDER_CAP"
    TIME_CRITICAL_FLOOR = "TIME_CRITICAL_FLOOR"
    DIRECT_REQUEST_FLOOR = "DIRECT_REQUEST_FLOOR"
    PERCEPTION_ACCEPTED = "PERCEPTION_ACCEPTED"


class ThreatEvent(StrEnum):
    """Emitted by mitigations. Recorded on the row; counted post hoc."""

    FENCE_TOKEN_IN_UNTRUSTED_INPUT = "FENCE_TOKEN_IN_UNTRUSTED_INPUT"
    UNTRUSTED_FIELD_TRUNCATED = "UNTRUSTED_FIELD_TRUNCATED"
    CONTROL_CHARACTERS_STRIPPED = "CONTROL_CHARACTERS_STRIPPED"
    MODEL_RESPONSE_SCHEMA_REJECTED = "MODEL_RESPONSE_SCHEMA_REJECTED"
    MEDIA_PATH_ESCAPED_ROOT = "MEDIA_PATH_ESCAPED_ROOT"
    MEDIA_DECODE_FAILED = "MEDIA_DECODE_FAILED"
    MEDIA_LIMIT_EXCEEDED = "MEDIA_LIMIT_EXCEEDED"
    MEDIA_CONTRADICTS_TEXT = "MEDIA_CONTRADICTS_TEXT"
    BUSINESS_DOMAIN_MISMATCH = "BUSINESS_DOMAIN_MISMATCH"
    BUSINESS_IDENTITY_INCONSISTENT = "BUSINESS_IDENTITY_INCONSISTENT"
    EVIDENCE_OWNER_MISMATCH = "EVIDENCE_OWNER_MISMATCH"
    EVIDENCE_NOT_IN_PAST = "EVIDENCE_NOT_IN_PAST"


class LogEvent(StrEnum):
    """Operational events that are not threat mitigations."""

    RUN_STARTED = "RUN_STARTED"
    RUN_COMPLETED = "RUN_COMPLETED"
    ROW_FAILED = "ROW_FAILED"
    CACHE_ENTRY_CORRUPT = "CACHE_ENTRY_CORRUPT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MEDIA_NOT_EXTRACTED = "MEDIA_NOT_EXTRACTED"
    PERCEPTION_NOT_CACHED = "PERCEPTION_NOT_CACHED"


class CacheOutcome(StrEnum):
    """Result of one cache lookup.

    Lives here rather than in cache/store.py because CallTelemetry carries it
    and schemas.py must remain a leaf.
    """

    HIT = "hit"
    MISS = "miss"
    CORRUPT = "corrupt"
    NOT_APPLICABLE = "n/a"


class MediaOutcome(StrEnum):
    NONE = "none"
    OK = "ok"
    FAILED = "failed"


class SenderRelationshipClass(StrEnum):
    """Derived at record-write time; the consistency scan groups on it."""

    UNESTABLISHED = "unestablished"
    ESTABLISHED_RECEPTIVE = "established_receptive"
    ESTABLISHED_NEUTRAL = "established_neutral"
    ESTABLISHED_DISMISSIVE = "established_dismissive"


class IntegrityState(StrEnum):
    """The row's risk posture. Derived at record-write time."""

    CLEAN = "clean"
    WARNED = "warned"
    FLAGGED = "flagged"


# ==========================================================================
# Input records
# ==========================================================================


class IncomingMessage(BaseModel):
    """One row of messages.csv, or one sample row projected onto the same shape."""

    model_config = STRICT

    message_id: str
    user_id: str
    conversation_type: ConversationType
    group_id: str | None
    business_id: str | None
    sender_user_id: str | None
    created_at: datetime
    message_text: str
    media_kind: MediaKind
    media_id: str | None
    forwarded_count: int = Field(ge=0)

    @property
    def has_text(self) -> bool:
        return bool(self.message_text.strip())


class UserProfile(BaseModel):
    model_config = STRICT

    user_id: str
    do_not_disturb_window: str
    messages_opened_30d: int
    messages_replied_30d: int
    notifications_dismissed_30d: int
    messages_reported_30d: int


class GroupProfile(BaseModel):
    model_config = STRICT

    group_id: str
    group_name: str
    group_type: str
    member_count: int
    admin_count: int
    created_at: datetime
    messages_30d: int


class Membership(BaseModel):
    model_config = STRICT

    group_id: str
    user_id: str
    role: MemberRole
    joined_at: datetime
    messages_sent_30d: int
    messages_read_30d: int
    replies_sent_30d: int
    notifications_dismissed_30d: int
    group_muted_by_user: bool


class BusinessProfile(BaseModel):
    model_config = STRICT

    business_id: str
    display_name: str
    brand_name: str
    category: str
    verified: bool
    official_domain: str | None
    domain_used_by_sender: str | None
    account_age_days: int
    messages_sent_30d: int
    user_reports_30d: int
    domain_used_by_sender_age_days: int


class BusinessRelationship(BaseModel):
    model_config = STRICT

    user_id: str
    business_id: str
    why_user_knows_account: str
    last_activity_at: datetime
    allows_promotions: bool
    promotions_opted_out_at: datetime | None
    activity_count_180d: int
    messages_opened_30d: int
    messages_dismissed_30d: int
    messages_replied_30d: int
    last_reply_at: datetime | None


class InteractionEvent(BaseModel):
    model_config = STRICT

    user_id: str
    message_id: str
    opened: bool
    replied: bool
    reaction_time_minutes: int | None
    dismissed: bool
    muted_after: bool
    reported: bool


class HistoryRecord(BaseModel):
    """A historical message joined to its interaction event. 1:1 in this dataset."""

    model_config = STRICT

    message_id: str
    user_id: str
    conversation_type: ConversationType
    group_id: str | None
    business_id: str | None
    sender_user_id: str | None
    created_at: datetime
    message_text: str
    media_kind: MediaKind
    media_id: str | None
    forwarded_count: int
    event: InteractionEvent


class EvalTruthRow(BaseModel):
    """The five label columns of one sample row, copied into the run directory."""

    model_config = STRICT

    message_id: str
    action: Action
    message_type: MessageType
    reason: str
    confidence: float
    evidence_message_ids: str


# ==========================================================================
# Assembled context
# ==========================================================================


class MediaArtifact(BaseModel):
    """What was recovered from an attached image or voice note."""

    model_config = STRICT

    media_id: str
    kind: MediaKind
    container_format: str | None
    duration_seconds: float | None
    readable: bool
    content: str | None
    failure_reason: str | None


class MessageContext(BaseModel):
    """Everything known about one message. The unit of work for the pipeline.

    Frozen. The MediaArtifact is extracted BEFORE construction and passed to
    build_context, so the model is complete at birth -- nothing mutates it.
    """

    model_config = STRICT

    message: IncomingMessage
    receiver: UserProfile
    group: GroupProfile | None
    receiver_membership: Membership | None
    sender_membership: Membership | None
    business: BusinessProfile | None
    business_relationship: BusinessRelationship | None
    history: tuple[HistoryRecord, ...]
    media: MediaArtifact | None
    daily_load_baseline: float | None


# ==========================================================================
# Model output
# ==========================================================================


class PromptBundle(BaseModel):
    """Exactly what will be sent for one message, plus what shapes the request.

    Everything in here except ``nonce`` and ``threat_events`` enters the
    perception cache key; the nonce is normalised out (it is regenerated every
    run, so keying on it would make the hit rate exactly zero).
    """

    model_config = STRICT

    system_text: str
    user_text: str
    nonce: str
    template_version: str
    response_schema_sha256: str
    threat_events: tuple[ThreatEvent, ...] = ()


class RawModelResponse(BaseModel):
    """One model reply, before validation. The body is untrusted."""

    model_config = STRICT

    text: str
    tokens_input: int = 0
    tokens_output: int = 0
    stop_reason: str | None = None


class Perception(BaseModel):
    """The model's read of one message. Proposals and content claims only.

    The model may not emit a confidence, an evidence id, or a reason string.
    ``extra="forbid"`` rejects any response that tries.
    """

    model_config = STRICT

    proposed_action: Action
    type_candidates: tuple[MessageType, ...] = Field(min_length=1, max_length=3)

    addresses_recipient_directly: bool = False
    requests_response_or_action: bool = False
    deadline_has_stated_consequence: bool = False
    states_deadline_within_day: bool = False
    requests_authentication_secret: bool = False
    requests_payment_or_payment_proof: bool = False
    directs_off_platform_for_money_or_identity: bool = False
    threatens_adverse_consequence: bool = False
    contains_router_directive: bool = False
    solicits_purchase: bool = False
    reports_transaction_status: bool = False
    is_circulated_content: bool = False
    is_salutation_only: bool = False
    advises_action_with_safety_consequence: bool = False

    media_summary: str | None = None
    media_contradicts_text: bool = False


# ==========================================================================
# Derived features
# ==========================================================================


class FeatureVector(BaseModel):
    """Everything the policy layer needs that is computed in code, not perceived."""

    model_config = STRICT

    domain_state: DomainState
    business_identity_consistent: bool
    business_verified: bool
    business_relationship_known: bool
    promotions_opted_out: bool

    sender_is_group_admin: bool
    receiver_is_group_admin: bool
    group_muted_by_receiver: bool

    prior_interaction_count: int
    dismissal_ratio: float | None
    sender_stance: EngagementStance
    relationship_established: bool

    forward_chain_length: int
    is_frequently_forwarded: bool
    media_failed: bool
    media_unread: bool

    sender_holds_payment_authority: bool
    receiver_baseline_dismissal_rate: float | None


# ==========================================================================
# Decision and output
# ==========================================================================


class RuleFiring(BaseModel):
    model_config = STRICT

    code: OverrideCode
    effect: RuleEffect
    status: FiringStatus
    would_set_action: Action | None
    justification: str


class Decision(BaseModel):
    model_config = STRICT

    action: Action
    message_type: MessageType
    firings: tuple[RuleFiring, ...]

    @property
    def applied(self) -> RuleFiring:
        """The single firing with status APPLIED. Always exactly one."""
        for firing in self.firings:
            if firing.status is FiringStatus.APPLIED:
                return firing
        raise ValueError("no firing has status APPLIED")


class EvidenceSelection(BaseModel):
    model_config = STRICT

    message_ids: tuple[str, ...]
    tier: EvidenceTier
    shortlist_considered: tuple[str, ...]


class OutputRow(BaseModel):
    """The in-process output record. emit.writer projects this to six CSV columns."""

    model_config = STRICT

    message_id: str
    action: Action
    message_type: MessageType
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_message_ids: tuple[str, ...]

    override_codes: tuple[OverrideCode, ...]
    firings: tuple[RuleFiring, ...]
    threat_events: tuple[ThreatEvent, ...]


class CallTelemetry(BaseModel):
    """Per-row cost and cache facts. Aggregated by nobody in the pipeline."""

    model_config = STRICT

    cache_media: CacheOutcome
    cache_perception: CacheOutcome
    schema_retry_count: int
    tokens_input: int
    tokens_output: int
    wall_clock_ms: float

    prompt_threat_events: tuple[ThreatEvent, ...] = ()
    """What prompt construction had to clean out of this row's untrusted input.
    Rides here because only the module that built the prompt knows it, and the
    row must record it whether the call was made or served from cache."""


class RoutingResult(BaseModel):
    """Everything route_one produced for one message. The sole input to emit.

    The per-row record is a pure projection of this object -- emit.record
    computes nothing that is not already here.
    """

    model_config = STRICT

    output: OutputRow
    perception: Perception
    features: FeatureVector
    evidence: EvidenceSelection
    telemetry: CallTelemetry
    context_summary: ContextSummary


class ContextSummary(BaseModel):
    """The handful of context facts the record needs that are not in the
    FeatureVector: they identify the row rather than describe its features."""

    model_config = STRICT

    conversation_type: ConversationType
    media_id: str | None
    media_kind: MediaKind
    media_outcome: MediaOutcome
    media_failure_cause: str | None
    has_text: bool


class RunFailure(BaseModel):
    """One failed row: enough for the metrics engine to attribute the cause."""

    model_config = STRICT

    message_id: str
    error_type: str


class RunResult(BaseModel):
    """What run() returns. Row-level facts only; no aggregation, no rates."""

    model_config = STRICT

    run_id: str
    run_dir: Path
    rows_written: int
    rows_expected: int
    eval_rows_written: int
    eval_rows_expected: int
    failures: tuple[RunFailure, ...]


RoutingResult.model_rebuild()
