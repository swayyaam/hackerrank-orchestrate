# SYSTEM_DESIGN.md

Module boundaries, data flow, and interfaces for the Message Notification Router.

Companion documents: [DATASET_ANALYSIS.md](DATASET_ANALYSIS.md), [PROBLEM_ANALYSIS.md](PROBLEM_ANALYSIS.md), [GENERALIZATION_POLICY.md](GENERALIZATION_POLICY.md), [DECISION_ENGINE.md](DECISION_ENGINE.md), [THREAT_MODEL.md](THREAT_MODEL.md), [ENGINEERING_CONVENTIONS.md](ENGINEERING_CONVENTIONS.md).

---

## 1. Architectural premise

**The model perceives. Code decides.**

The model is given one message's content and asked what it *is*: what the sender is asking for, whether a deadline is stated, whether an authentication secret is requested, what the attached image shows. It returns a proposal — an action and a ranked list of type candidates — and a set of boolean claims about content.

It does not own the outcome. A deterministic policy layer takes the proposal, combines it with features computed in code from structured fields, and applies an ordered rule table to produce the final `action` and `message_type`. Confidence is computed from named features. Evidence is retrieved deterministically. Nothing the model emits is written to `output.csv` unmodified except its choice of type, and only when no rule overrides it.

Three consequences follow, and they are the reason for the split:

1. **Security.** A successful prompt injection can corrupt `Perception` and nothing else. The policy rules that fire on `Perception` claims can only ever force `mute`, so corrupting them never escalates a message; and two rules — `IMPERSONATION` and `REPEAT_DISMISSAL` — trigger on structured fields (`verified`, domain state, the receiver's own event history) that no model output can reach, and survive total model compromise end to end. The precise defeat condition for every other rule is enumerated in [THREAT_MODEL.md §1](THREAT_MODEL.md#1-trust-model).
2. **Testability.** Policy, features, confidence, and evidence are pure functions over typed inputs. They are tested by constructing a `MessageContext` and a `Perception` in the test body. No module below `perception/client.py` makes a network call.
3. **Auditability.** Every decision carries the ordered list of rules that fired and what each did. A wrong output is traceable to a named rule or to a named perception claim.

### Explicitly excluded

No vector database, no embedding index, no agent framework, no multi-agent graph, no tool-calling loop. Per-user history is 3–32 rows (median 15, max 32 — [DATASET_ANALYSIS.md §4](DATASET_ANALYSIS.md#4-join-integrity)), so evidence retrieval is a sort over a small in-memory list. One model call per message, plus at most one per undecoded media artifact.

---

## 2. Module layout and responsibilities

> **`code/` is the SOURCE ROOT**, by project convention, and it is the entire submission artifact: everything a reviewer needs to run, test, or understand the system lives under it, including `tests/`, `pyproject.toml`, and `docs/`.
>
> It is deliberately **not itself a package** — there is no `code/__init__.py`. Every subpackage below is top-level within it, so imports read `from policy.engine import decide` and the suite runs with `code/` as the working directory, referencing nothing above. A directory without `__init__.py` is only a namespace portion, so the standard-library `code` module still wins the name `code` and `pdb` (hence pytest) is unaffected.
>
> **`code/main.py` is THE canonical entry point** — `AGENTS.md` §6.4 names it, and there is no second one. `cd code && python main.py <cmd>` and `python code/main.py <cmd>` both work.
>
> The metrics engine is a subpackage at `code/metrics/` with its own CLI (`python -m metrics`), separate because it is computed post hoc and is **not** part of the pipeline — see [METRICS.md](METRICS.md).

```
code/
├── config.py                   Every threshold, cap, weight, limit, and model identifier. Imports nothing from the package.
├── schemas.py                  All pydantic models and enums. Imports config only for bounded-value constraints.
├── errors.py                   Typed exception hierarchy.
├── logging_setup.py            Structured JSONL logger construction.
│
├── ingest/
│   ├── loader.py               Read the 13 CSVs into validated models and build the lookup indexes (targets and eval rows).
│   └── context.py              Assemble the complete MessageContext for one message_id from the indexes and its MediaArtifact.
│
├── media/
│   ├── locate.py               Resolve a media_id to a path confined to the dataset root.
│   ├── decode.py               Identify container by content and normalise audio to a canonical PCM buffer.
│   └── extract.py              Produce a MediaArtifact: transcript for audio, visual description for images.
│
├── perception/
│   ├── prompt.py               Build the perception prompt with untrusted content in fenced, nonce-delimited blocks.
│   ├── client.py               The only module permitted to make a network call; retries, timeouts, determinism settings.
│   └── parse.py                Validate the model response into a Perception, rejecting unknown fields.
│
├── features/
│   ├── business.py             Derive domain state, identity consistency, and relationship facts for a business sender.
│   ├── group.py                Derive membership role, mute state, and group relationship facts.
│   ├── sender.py               Derive the receiver's engagement stance toward this sender from past outcomes.
│   ├── engagement.py           Convert a set of interaction events into a bounded engagement score and stance.
│   ├── content.py              Derive structural content facts that need no model: forward chain length, media readability.
│   └── assemble.py             Compose the six above into one FeatureVector.
│
├── policy/
│   ├── rules.py                The rule table: one Rule object per policy rule, in precedence order.
│   └── engine.py               Apply the rule table to (Perception, FeatureVector) and return a Decision.
│
├── scoring/
│   ├── confidence.py           Compute confidence from named features and named weights.
│   └── evidence.py             Rank and select historical message IDs deterministically.
│
├── compose/
│   └── reason.py               Render the one-sentence reason from the applied rule and enforce the register.
│
├── cache/
│   └── store.py                The only module that reads or writes the cache directory. See CACHING.md.
│
└── emit/
    ├── writer.py               Project OutputRow to the six contract columns and write output.csv in input order.
    └── record.py               Project RoutingResult into the per-row RowRecord; write manifest and eval-truth copy. Computes no metric.
```

`emit/` writes records; it does not aggregate them. Every count, rate, and distribution in [METRICS.md](METRICS.md) is derived post hoc by `code/metrics/` from the saved run directory — the pipeline never increments a counter.

### Dependency direction

Strictly downward. `config` and `schemas` and `errors` are leaves that the whole package may import. No module imports a module that appears later in the pipeline. `policy/` imports `schemas` and `config` only — never `perception/`, never `ingest/`. There are no cycles, and this is enforced by a test that walks the import graph.

Pipeline order (media extraction precedes context construction — the artifact is an *input* to `build_context`):

```
ingest.loader → media/ → ingest.context → perception/ → features/ → policy/ → scoring/ → compose/ → emit/
```

`media/` does not import `ingest/` — `extract` takes an `IncomingMessage`, which lives in `schemas.py`. `ingest.context` does not import `media/` — it takes the `MediaArtifact` as a parameter. The order above is execution order; the import rule holds either way.

---

## 3. Data flow for a single message

```
                       dataset/*.csv
                            │
                   ingest.loader.load_dataset()
                            │  Dataset (indexed, validated, loaded once;
                            │           110 target rows + 30 eval rows)
                            ▼
                media.extract.extract(message, cfg)
                            │  MediaArtifact | None      ← runs BEFORE context
                            ▼
        ingest.context.build_context(dataset, message_id, artifact)
                            │  MessageContext (frozen; carries .media)
              ┌─────────────┴────────────────────────────┐
              ▼                                          ▼
 perception.prompt.build_prompt(context)    features.assemble.build_features(context)
              │  PromptBundle                            │  FeatureVector (needs no model;
              ▼                                          │   reads context.media)
 perception.client.perceive(bundle)                      │
              │  raw response                            │
              ▼                                          │
 perception.parse.to_perception(raw)                     │
              │  Perception                              │
              └─────────────┬────────────────────────────┘
                            ▼
              policy.engine.decide(perception, features)
                            │  Decision  (action, message_type, firings[])
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
   scoring.evidence   scoring.confidence   compose.reason
   .select_evidence   .score_confidence    .render_reason
             │              │              │
             └──────────┬───┴──────────────┘
                        ▼
                    OutputRow
                        ▼
        RoutingResult = OutputRow
                      + Perception
                      + FeatureVector
                      + EvidenceSelection (ids, tier, shortlist_considered)
                      + CallTelemetry (cache outcomes, retries, tokens, wall clock)
                        │
          ┌─────────────┴──────────────┐
          ▼                            ▼
 emit.writer                  emit.record
   (six contract columns,       (RowRecord projection, manifest,
    from OutputRow alone)        eval-truth copy — no aggregation)
   → dataset/output.csv         → runs/<id>/rows.jsonl
   → runs/<id>/output.csv       → runs/<id>/eval_rows.jsonl
                                → runs/<id>/manifest.json
                                → runs/<id>/eval_truth.csv
```

`load_dataset()` runs once for the whole run. Everything from `media.extract` onward is per-message and independent, so messages may be processed in any order; the writer restores input order at the end. The run routes **both** sets through this identical flow: the 110 target rows (→ `rows.jsonl` and `output.csv`) and the 30 eval rows from `sample_messages.csv` (→ `eval_rows.jsonl` only; eval rows never enter the submission).

### Where override codes go

`OutputRow` carries `override_codes: list[OverrideCode]`. **`output.csv` cannot carry a seventh column** — the contract fixes six, in order ([PROBLEM_ANALYSIS.md §2](PROBLEM_ANALYSIS.md#2-required-output-contract)). So `emit.writer` projects `RoutingResult.output` down to the six contract columns and drops everything else, while `emit.record` projects the whole `RoutingResult` — every firing and its status, the model proposal, the features, the shortlist, the telemetry — into the `RowRecord` written to `runs/<id>/rows.jsonl`, keyed by `message_id`. The codes are on the output row as an in-process object and in the run record; they are not in the submitted CSV, because putting them there would break the contract.

**`emit.record` counts nothing.** It projects one `RowRecord` per `RoutingResult`, writes the manifest, and copies the eval labels to `eval_truth.csv`. Every rate, distribution, and total is computed post hoc by `code/metrics/` from the saved run directory ([METRICS.md](METRICS.md)). No counter is incremented in the routing loop and no pipeline module imports a metric.

---

## 4. Enums

```python
# schemas.py
from __future__ import annotations

from enum import StrEnum, IntEnum


class Action(StrEnum):
    """Final routing decision. Values are the contract literals."""

    NOTIFY = "notify"
    DIGEST = "digest"
    MUTE = "mute"


# Ordering used by floors and caps. Higher rank = more interruptive.
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


class FiringStatus(StrEnum):
    APPLIED = "applied"
    SUPERSEDED = "superseded"
    SUPPRESSED_BY_GUARD = "suppressed_by_guard"
    OVERRIDDEN_BY_CAP = "overridden_by_cap"
    NOT_EVALUATED = "not_evaluated"


class CacheOutcome(StrEnum):
    """Result of one cache lookup. Lives here (not in cache/store.py) because
    CallTelemetry carries it and schemas.py must stay a leaf."""

    HIT = "hit"
    MISS = "miss"
    CORRUPT = "corrupt"          # present but unreadable; treated as a miss
    NOT_APPLICABLE = "n/a"       # e.g. media layer on a row with no media


class OverrideCode(StrEnum):
    """One code per policy rule. Recorded on OutputRow and counted in the report."""

    CREDENTIAL_SOLICITATION = "CREDENTIAL_SOLICITATION"
    ROUTER_DIRECTIVE = "ROUTER_DIRECTIVE"
    IMPERSONATION = "IMPERSONATION"
    COERCIVE_PAYMENT = "COERCIVE_PAYMENT"
    UNSAFE_INSTRUCTION = "UNSAFE_INSTRUCTION"
    UNAUTHORIZED_PAYMENT_REQUEST = "UNAUTHORIZED_PAYMENT_REQUEST"
    DIRECT_ADDRESS_GUARD = "DIRECT_ADDRESS_GUARD"
    PROMOTION_OPT_OUT = "PROMOTION_OPT_OUT"
    REPEAT_DISMISSAL = "REPEAT_DISMISSAL"
    FREQUENTLY_FORWARDED_BROADCAST = "FREQUENTLY_FORWARDED_BROADCAST"
    GROUP_MUTE_CAP = "GROUP_MUTE_CAP"
    COLD_SENDER_CAP = "COLD_SENDER_CAP"
    TIME_CRITICAL_FLOOR = "TIME_CRITICAL_FLOOR"
    DIRECT_REQUEST_FLOOR = "DIRECT_REQUEST_FLOOR"
    PERCEPTION_ACCEPTED = "PERCEPTION_ACCEPTED"


class ThreatEvent(StrEnum):
    """Emitted by mitigations. Counted in the report; see THREAT_MODEL.md."""

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
```

---

## 5. Schemas

### 5.1 Input records

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)


class IncomingMessage(BaseModel):
    """One row of messages.csv, validated."""

    model_config = STRICT

    message_id: str
    user_id: str
    conversation_type: ConversationType
    group_id: str | None
    business_id: str | None
    sender_user_id: str | None
    created_at: datetime          # naive; no conversion performed (PROBLEM_ANALYSIS D15)
    message_text: str             # empty for voice messages
    media_kind: MediaKind
    media_id: str | None
    forwarded_count: int = Field(ge=0)


class UserProfile(BaseModel):
    model_config = STRICT

    user_id: str
    do_not_disturb_window: str    # parsed but unused by the router (PROBLEM_ANALYSIS D7)
    messages_opened_30d: int
    messages_replied_30d: int
    notifications_dismissed_30d: int
    messages_reported_30d: int


class GroupProfile(BaseModel):
    model_config = STRICT

    group_id: str
    group_name: str               # free text; never enters the prompt (THREAT_MODEL T5)
    group_type: str               # categorical; may enter the prompt
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
    display_name: str             # free text; never enters the prompt (THREAT_MODEL T4)
    brand_name: str               # free text; never enters the prompt
    category: str                 # categorical; may enter the prompt
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
    why_user_knows_account: str   # near-free text; never enters the prompt (THREAT_MODEL T7)
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
    reaction_time_minutes: int | None   # ordinal; never compared to a literal (POLICY 2.2)
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
    message_text: str             # never enters the prompt (THREAT_MODEL T6)
    media_kind: MediaKind
    media_id: str | None
    forwarded_count: int
    event: InteractionEvent
```

### 5.2 Assembled context

```python
class MediaArtifact(BaseModel):
    """What was recovered from an attached image or voice note."""

    model_config = STRICT

    media_id: str
    kind: MediaKind
    container_format: str          # detected from content, not from the filename
    duration_seconds: float | None
    readable: bool                 # False when decode or extraction failed
    content: str | None            # transcript or visual description; untrusted
    failure_reason: str | None


class MessageContext(BaseModel):
    """Everything known about one message. The unit of work for the pipeline.

    Frozen. The MediaArtifact is extracted BEFORE construction and passed to
    build_context, so the model is complete at birth — nothing mutates it.
    """

    model_config = STRICT

    message: IncomingMessage
    receiver: UserProfile
    group: GroupProfile | None
    receiver_membership: Membership | None
    sender_membership: Membership | None
    business: BusinessProfile | None
    business_relationship: BusinessRelationship | None
    history: tuple[HistoryRecord, ...]      # this receiver's history, ascending by created_at
    media: MediaArtifact | None
    daily_load_baseline: float | None       # 14-day dismissal rate (PROBLEM_ANALYSIS D8)
```

### 5.3 Model output

```python
class Perception(BaseModel):
    """The model's read of one message. Proposals and content claims only.

    The model may not emit a confidence, an evidence id, or a reason string.
    ``extra="forbid"`` rejects any response that tries.
    """

    model_config = STRICT

    proposed_action: Action
    type_candidates: tuple[MessageType, ...] = Field(min_length=1, max_length=3)

    # Content claims. Each is a factual question about the message, answerable
    # from the content alone, phrased so the answer does not presuppose a decision.
    addresses_recipient_directly: bool
    states_deadline_within_day: bool
    requests_authentication_secret: bool
    requests_payment_or_payment_proof: bool
    directs_off_platform_for_money_or_identity: bool
    threatens_adverse_consequence: bool
    contains_router_directive: bool
    solicits_purchase: bool
    reports_transaction_status: bool
    is_circulated_content: bool
    is_salutation_only: bool
    advises_action_with_safety_consequence: bool

    # Media observations. Recorded; media_contradicts_text never raises risk
    # (PROBLEM_ANALYSIS D11).
    media_summary: str | None = None
    media_contradicts_text: bool = False
```

### 5.4 Derived features

```python
class FeatureVector(BaseModel):
    """Everything the policy layer needs that is computed in code, not perceived."""

    model_config = STRICT

    # Business integrity
    domain_state: DomainState
    business_identity_consistent: bool
    business_verified: bool
    business_relationship_known: bool
    promotions_opted_out: bool

    # Group relationship
    sender_is_group_admin: bool
    receiver_is_group_admin: bool
    group_muted_by_receiver: bool

    # Sender engagement
    prior_interaction_count: int
    dismissal_ratio: float | None          # None when prior_interaction_count == 0
    sender_stance: EngagementStance
    relationship_established: bool

    # Structural content
    forward_chain_length: int
    is_frequently_forwarded: bool
    media_failed: bool                     # any attached media failed to decode or read
    media_unread: bool                     # NARROW: media_failed AND the message has no
                                           # text — the row's ONLY content was unreadable.
                                           # A captioned image that fails decode sets
                                           # media_failed but NOT media_unread.

    # Composite authority test used by the payment rules
    sender_holds_payment_authority: bool

    # Receiver baseline
    receiver_baseline_dismissal_rate: float | None
```

### 5.5 Decision and output

```python
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
    firings: tuple[RuleFiring, ...]        # every rule evaluated, in precedence order

    @property
    def applied(self) -> RuleFiring:
        """The single firing with status APPLIED. Always exactly one."""
        ...


class EvidenceSelection(BaseModel):
    model_config = STRICT

    message_ids: tuple[str, ...]           # empty tuple renders as the literal "none"
    tier: EvidenceTier
    shortlist_considered: tuple[str, ...]  # every candidate id ranked, best first.
                                           # message_ids is always a subset of this —
                                           # the invariant behind ungrounded_citation_count == 0.


class OutputRow(BaseModel):
    """The in-process output record. emit.writer projects this to six CSV columns."""

    model_config = STRICT

    message_id: str
    action: Action
    message_type: MessageType
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_message_ids: tuple[str, ...]

    # Carried on the row, written to the audit sidecar, dropped by the CSV writer.
    override_codes: tuple[OverrideCode, ...]
    firings: tuple[RuleFiring, ...]
    threat_events: tuple[ThreatEvent, ...]


class CallTelemetry(BaseModel):
    """Per-row cost and cache facts. Recorded by route_one; aggregated by nobody
    in the pipeline — code/metrics/ derives every rate from these post hoc."""

    model_config = STRICT

    cache_media: CacheOutcome
    cache_perception: CacheOutcome
    schema_retry_count: int
    tokens_input: int
    tokens_output: int
    wall_clock_ms: float


class RoutingResult(BaseModel):
    """Everything route_one produced for one message. The sole input to emit.

    METRICS.md's per-row record is a pure projection of this object —
    emit.record computes nothing that is not already here.
    """

    model_config = STRICT

    output: OutputRow
    perception: Perception
    features: FeatureVector
    evidence: EvidenceSelection            # ids + tier + shortlist_considered
    telemetry: CallTelemetry


class RunFailure(BaseModel):
    """One failed row: enough for the metrics engine to attribute the cause.

    error_type is the RouterError subclass name (e.g. "PerceptionSchemaError"),
    which is how hard_parse_failure_count is derived post hoc — a hard-failed
    row produces no RowRecord, so the cause must live here.
    """

    model_config = STRICT

    message_id: str
    error_type: str


class RunResult(BaseModel):
    """What run() returns. Row-level facts only; no aggregation, no rates.

    Aggregates are the metrics engine's job. This carries just enough for the
    process exit code and for the manifest.
    """

    model_config = STRICT

    run_id: str
    run_dir: Path
    rows_written: int
    rows_expected: int
    eval_rows_written: int
    eval_rows_expected: int
    failures: tuple[RunFailure, ...]
```

There is no `RunReport` in the pipeline. The full aggregate schema lives in [METRICS.md §5](METRICS.md#5-report-schema) and is produced by `code/metrics/`, never by `code/`.

---

## 6. Interfaces

Every signature below is total: it either returns the declared type or raises a typed error from `errors.py`. None returns `None` to signal failure.

### ingest

```python
def load_dataset(root: Path) -> Dataset:
    """Read and validate all 13 CSVs; build lookup indexes. Raises DatasetError.

    sample_messages.csv is loaded twice over: its 11 input columns are projected
    into IncomingMessage rows (the eval set, routed through the identical
    pipeline), and its 5 label columns are held separately as the eval truth,
    which emit copies into the run directory as eval_truth.csv.
    """


def build_context(
    dataset: Dataset,
    message_id: str,
    media: MediaArtifact | None,
) -> MessageContext:
    """Assemble the full context for one message. Raises ContextError on a broken join.

    The MediaArtifact is extracted before this call and passed in, so the frozen
    MessageContext is complete at construction (see §3).
    """
```

`Dataset` is a frozen container of the parsed tables plus prebuilt indexes:
`history_by_user: Mapping[str, tuple[HistoryRecord, ...]]`,
`membership_by_pair: Mapping[tuple[str, str], Membership]`,
`relationship_by_pair: Mapping[tuple[str, str], BusinessRelationship]`,
`message_by_id: Mapping[str, IncomingMessage]` — covering **both** the 110 target
rows and the 30 eval rows (their id namespaces do not collide) — and
`eval_truth: tuple[EvalTruthRow, ...]` (the 30 label rows: `message_id, action,
message_type, reason, confidence, evidence_message_ids`) for the run-directory copy.

### media

```python
def resolve_media_path(dataset: Dataset, media_id: str, kind: MediaKind) -> Path:
    """Resolve to a real path under the dataset root. Raises MediaPathError on escape."""


def decode_audio(path: Path) -> DecodedAudio:
    """Identify the container by content and decode. Never trusts the extension."""


def extract(message: IncomingMessage, cfg: RouterConfig) -> MediaArtifact | None:
    """Return the artifact for the message's attached media; None if it has none.

    Runs BEFORE build_context (§3) and takes only the message row — it needs
    the media_id and kind, not the assembled context. At route time this is a
    media-cache read (CACHING.md §2); it never calls the model and never writes
    the cache. Always returns a MediaArtifact for a message that has media;
    readable=False with a failure_reason rather than raising, so one bad file
    cannot fail the run.
    """
```

### perception

```python
def build_prompt(context: MessageContext) -> PromptBundle:
    """Build system and user messages. Untrusted content goes in nonce-fenced blocks.

    The media artifact is read from context.media — it is inside the context
    since C2 moved extraction before construction; no separate parameter.
    """


def perceive(bundle: PromptBundle) -> RawModelResponse:
    """The single network boundary. Deterministic settings; bounded retries."""


def to_perception(raw: RawModelResponse) -> Perception:
    """Validate into Perception. Raises PerceptionSchemaError on extra or missing fields."""
```

### features

```python
def build_features(context: MessageContext) -> FeatureVector:
    """Compose all derived features. Pure; no model, no I/O."""


def engagement_stance(events: Sequence[InteractionEvent]) -> tuple[EngagementStance, float | None]:
    """Return the stance and the dismissal ratio. Ratio is None for an empty sequence."""


def domain_state(business: BusinessProfile) -> DomainState:
    """MATCH, MISMATCH, or UNDETERMINED when either domain field is blank."""
```

### policy

```python
def decide(perception: Perception, features: FeatureVector) -> Decision:
    """Apply the rule table. Pure. Exactly one firing has status APPLIED."""
```

### scoring

```python
def select_evidence(
    context: MessageContext,
    decision: Decision,
) -> EvidenceSelection:
    """Deterministically rank this receiver's history and select 0..MAX ids.

    Only rows strictly older than the target are eligible. Every returned id
    belongs to context.message.user_id; violations raise EvidenceIntegrityError.
    The full ranked candidate list is returned as shortlist_considered, and
    message_ids is always a subset of it — the record carries both so the
    ungrounded-citation invariant is checkable post hoc.
    """


def score_confidence(
    decision: Decision,
    features: FeatureVector,
    perception: Perception,
    evidence: EvidenceSelection,
) -> float:
    """Compute confidence from named features and config weights. Never reads the model."""
```

### compose / emit

```python
def render_reason(decision: Decision, features: FeatureVector) -> str:
    """One sentence in the required register. Raises ReasonRegisterError if out of bounds."""


def write_output(rows: Sequence[OutputRow], path: Path, order: Sequence[str]) -> int:
    """Write the six contract columns in the given message_id order. Returns rows written.

    Takes OutputRow alone — output.csv is built from the OutputRow and nothing else.
    """


def write_records(results: Sequence[RoutingResult], path: Path) -> int:
    """Project one RowRecord per RoutingResult to the given .jsonl path.

    Pure projection: every RowRecord field is copied or mechanically derived
    from the RoutingResult (METRICS.md §3). Computes nothing else — no counts,
    no rates, no totals. Called once for rows.jsonl and once for eval_rows.jsonl.
    """


def write_manifest(run_dir: Path, manifest: RunManifest) -> None:
    """Write manifest.json: config hash, git commit, timestamp, mode, counts."""


def write_eval_truth(dataset: Dataset, run_dir: Path) -> None:
    """Copy the 30 sample label rows into runs/<id>/eval_truth.csv, so labeled
    metrics stay computable from the run directory alone (retroactivity)."""
```

### The pipeline

```python
def route_one(
    dataset: Dataset,
    message_id: str,
    client: PerceptionClient,
) -> RoutingResult:
    """Run every stage for one message. The only function that composes the modules.

    Returns the OutputRow plus the Perception, FeatureVector, EvidenceSelection
    (including the shortlist considered), and CallTelemetry — everything the
    per-row record needs, so emit projects and never recomputes.
    """


def run(root: Path, out_path: Path, client: PerceptionClient) -> RunResult:
    """Load once, route the 110 target rows AND the 30 eval rows, write
    output.csv (targets only), rows.jsonl, eval_rows.jsonl, eval_truth.csv,
    and manifest.json."""
```

`PerceptionClient` is a protocol with a single method, so tests inject a stub and no test touches the network:

```python
class PerceptionClient(Protocol):
    def complete(self, bundle: PromptBundle) -> RawModelResponse: ...
```

---

## 7. Config

Every threshold, cap, weight, and limit lives in `config.py`. No numeric or string literal with behavioural meaning appears anywhere else. Modules import the config object, never individual values, so a test can construct an alternative config without monkeypatching module globals.

```python
# config.py — excerpt. Full weights are specified in DECISION_ENGINE.md §5.
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyConfig:
    # A pattern needs at least two instances; one instance is an event.
    MIN_PATTERN_OBSERVATIONS: int = 2
    # "Usually" is the weakest claim a strict majority supports.
    DISMISSAL_MAJORITY: float = 0.5
    # WhatsApp itself labels a message "forwarded many times" at five hops.
    FORWARD_CHAIN_THRESHOLD: int = 5
    # A stance needs a minimum sample before it is asserted at all.
    MIN_STANCE_OBSERVATIONS: int = 2


@dataclass(frozen=True)
class ConfidenceConfig:
    W_BASE: float = 0.55
    W_TRIGGER_STRUCTURAL: float = 0.20
    W_TRIGGER_PERCEPTUAL: float = 0.10
    W_EVIDENCE_RELATIONSHIP_AND_CONTENT: float = 0.12
    W_EVIDENCE_RELATIONSHIP: float = 0.07
    W_EVIDENCE_TOPICAL: float = 0.03
    W_ENGAGEMENT_ALIGNED: float = 0.08
    W_MEDIA_UNREAD: float = 0.15
    W_TYPE_AMBIGUITY: float = 0.04
    W_CONTENTION: float = 0.03
    FLOOR: float = 0.35
    CEILING: float = 0.95
    DECIMAL_PLACES: int = 2


@dataclass(frozen=True)
class EvidenceConfig:
    MAX_IDS_DEFAULT: int = 1
    MAX_IDS_PATTERN_RULE: int = 2
    MIN_SHARED_CONTENT_TOKENS: int = 2
    SEPARATOR: str = ";"
    NONE_LITERAL: str = "none"


@dataclass(frozen=True)
class LimitsConfig:
    MAX_UNTRUSTED_CHARS: int = 4_000
    MAX_MEDIA_BYTES: int = 25_000_000
    MAX_AUDIO_SECONDS: float = 300.0
    MAX_IMAGE_PIXELS: int = 40_000_000
    MODEL_TIMEOUT_SECONDS: float = 60.0
    MODEL_MAX_ATTEMPTS: int = 3


@dataclass(frozen=True)
class ReasonConfig:
    MIN_CHARS: int = 55
    MAX_CHARS: int = 120
    MAX_SENTENCES: int = 1


@dataclass(frozen=True)
class RouterConfig:
    """The aggregate handed to orchestration and sliced per module. Functions
    take the narrowest config they need; cross-cutting entry points (extract,
    route_one) take this."""

    policy: PolicyConfig
    confidence: ConfidenceConfig
    evidence: EvidenceConfig
    limits: LimitsConfig
    reason: ReasonConfig
    cache: CacheConfig            # defined alongside the cache module; CACHING.md §8
    dataset_root: Path
```

Each constant carries the reason for its value as a comment, sourced per [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance) as either (a) the contract or (b) an independent domain quantity. No constant is sourced from an observed value in `dataset/`. `MIN_CHARS`/`MAX_CHARS` are the one deliberate exception and are sourced (a): the statement scopes the sample to "format and style", which licenses matching the observed register.

---

## 8. Trace: `msg_022` from CSV row to output row

A real message, carried through every stage. All input values are the actual dataset values.

### Stage 0 — CSV row

```
message_id       msg_022
user_id          u_011
conversation_type group
group_id         group_011
business_id      (blank)
sender_user_id   u_044
created_at       2026-07-28 20:13
message_text     "Payment due today. Complete before 5 PM. If already paid, ignore;
                  receipts will be matched in evening. Use this link and send
                  screenshot here so I can update it faster."
media_type       (blank)
media_id         (blank)
forwarded_count  0
```

### Stage 1 — `media.extract`

Runs first, on the message row alone. No `media_id` → returns `None`. (For a media-bearing row this would be a media-cache read producing the `MediaArtifact` before any context exists.)

### Stage 2 — `ingest.context.build_context(dataset, "msg_022", media=None)`

Takes the Stage 1 artifact as a parameter and produces the frozen `MessageContext`:

| field | value |
|---|---|
| `receiver` | `u_011` — opened 92, replied 28, dismissed 18, reported 1 |
| `group` | `group_011` "Society Maintenance Desk", type `society`, 118 members, 487 msgs/30d |
| `receiver_membership` | role `member`, joined 2024-08-23, read 15, replied 7, dismissed 0, **muted `False`** |
| `sender_membership` | `u_044`: role **`member`**, joined 2024-05-16, sent 8, replied 1 |
| `business` / `business_relationship` | `None` |
| `history` | 31 records for `u_011`; **2 from sender `u_044`**, both in `group_011` |
| `media` | `None` — the Stage 1 artifact, fixed at construction |

The two same-sender records:

| id | created_at | outcome |
|---|---|---|
| `message_0099` | 2026-03-04 07:57 | opened `False`, dismissed `True`, muted_after `True` |
| `message_0100` | 2026-03-14 12:03 | opened `False`, dismissed `True`, muted_after `True` |

### Stage 3 — `perception.prompt.build_prompt(context)`

The user message carries the message text inside a nonce-fenced block, plus **categorical context only** — `conversation_type=group`, `group_type=society`, `sender_role=member`, `receiver_role=member`. The group's *name* ("Society Maintenance Desk") is not sent; it is free text and closed off by architecture ([THREAT_MODEL T5](THREAT_MODEL.md#3-paths-closed-by-architecture)). No historical message text is sent.

### Stage 4 — `perception.parse.to_perception`

```python
Perception(
    proposed_action=Action.NOTIFY,          # the model reads it as a payment deadline
    type_candidates=(MessageType.PAYMENT, MessageType.SCAM),
    addresses_recipient_directly=False,
    states_deadline_within_day=True,
    requests_authentication_secret=False,
    requests_payment_or_payment_proof=True,
    directs_off_platform_for_money_or_identity=True,   # "use this link", "send screenshot here"
    threatens_adverse_consequence=False,               # no threatened penalty
    contains_router_directive=False,
    solicits_purchase=False,
    reports_transaction_status=False,
    is_circulated_content=False,
    is_salutation_only=False,
    advises_action_with_safety_consequence=False,
    media_summary=None,
    media_contradicts_text=False,
)
```

Note the model proposes `notify`. It is wrong, and it does not matter — it does not own the decision.

### Stage 5 — `features.assemble.build_features`

```python
FeatureVector(
    domain_state=DomainState.UNDETERMINED,     # not a business conversation
    business_identity_consistent=True,
    business_verified=False,
    business_relationship_known=False,
    promotions_opted_out=False,
    sender_is_group_admin=False,               # u_044 role == member
    receiver_is_group_admin=False,
    group_muted_by_receiver=False,             # group_muted_by_user == 0
    prior_interaction_count=2,                 # message_0099, message_0100
    dismissal_ratio=1.0,                       # both dismissed and muted
    sender_stance=EngagementStance.DISMISSIVE, # 2 >= MIN_STANCE_OBSERVATIONS, ratio > 0.5
    relationship_established=True,             # prior_interaction_count > 0
    forward_chain_length=0,
    is_frequently_forwarded=False,             # 0 < FORWARD_CHAIN_THRESHOLD
    media_failed=False,                        # no media attached
    media_unread=False,
    sender_holds_payment_authority=False,      # group sender, not admin
    receiver_baseline_dismissal_rate=0.29,
)
```

### Stage 6 — `policy.engine.decide`

Evaluation in precedence order (full table in [DECISION_ENGINE.md §2](DECISION_ENGINE.md#2-precedence-table)):

| # | rule | fires? | why |
|---|---|---|---|
| 1 | `CREDENTIAL_SOLICITATION` | no | `requests_authentication_secret` is `False` |
| 2 | `ROUTER_DIRECTIVE` | no | `contains_router_directive` is `False` |
| 3 | `IMPERSONATION` | no | not a business conversation |
| 4 | `COERCIVE_PAYMENT` | no | `threatens_adverse_consequence` is `False` |
| 5 | `UNSAFE_INSTRUCTION` | no | — |
| 6 | **`UNAUTHORIZED_PAYMENT_REQUEST`** | **yes** | payment request **and** off-platform direction **and** `sender_holds_payment_authority` is `False` |

Rule 6 is a `HARD_OVERRIDE`. Evaluation stops; caps and floors are marked `NOT_EVALUATED`.

```python
Decision(
    action=Action.MUTE,
    message_type=MessageType.SCAM,
    firings=(
        RuleFiring(code=OverrideCode.UNAUTHORIZED_PAYMENT_REQUEST,
                   effect=RuleEffect.HARD_OVERRIDE,
                   status=FiringStatus.APPLIED,
                   would_set_action=Action.MUTE,
                   justification="A payment instruction arrived from a sender with no "
                                 "established authority for payments in this conversation."),
        # ... rules 1-5 recorded with status NOT_EVALUATED / did-not-fire,
        #     rules 7-15 recorded NOT_EVALUATED
    ),
)
```

This is the discrimination that matters, and it is made on relationships rather than on content wording: `msg_021` in `group_002` is the *same* payment-deadline shape from a sender whose `Membership.role` is `admin`, and it directs recipients to official channels rather than off-platform — so rule 6's second and third conditions both fail and it is not muted.

### Stage 7 — `scoring.evidence.select_evidence`

Candidates: 31 history records for `u_011`, filtered to `created_at < 2026-07-28 20:13` (all 31 qualify). Ranked by tier, then by recency:

| candidate | tier | note |
|---|---|---|
| `message_0100` | `RELATIONSHIP_AND_CONTENT` | same sender, same group, overlapping content (payment / QR / off-notice) |
| `message_0099` | `RELATIONSHIP_AND_CONTENT` | same sender, same group, overlapping content |
| `message_0237`, `message_0151`, … | `RELATIONSHIP` | same group, different sender |

`UNAUTHORIZED_PAYMENT_REQUEST` has `asserts_pattern=False`, so `MAX_IDS_DEFAULT = 1` applies. Most recent of the top tier wins.

```python
EvidenceSelection(
    message_ids=("message_0100",),
    tier=EvidenceTier.RELATIONSHIP_AND_CONTENT,
    shortlist_considered=("message_0100", "message_0099", "message_0237",
                          "message_0151", ...),   # all 31 candidates, ranked
)
```

Integrity check: `message_0100.user_id == "u_011"` ✓, `created_at` strictly earlier ✓.

### Stage 8 — `scoring.confidence.score_confidence`

| term | value | why |
|---|---:|---|
| `W_BASE` | +0.55 | |
| trigger | +0.10 | applied rule's trigger includes perception claims → `W_TRIGGER_PERCEPTUAL` |
| evidence | +0.12 | tier `RELATIONSHIP_AND_CONTENT` |
| engagement | +0.08 | stance `DISMISSIVE` aligns with the chosen action `mute` |
| media | 0.00 | `media_unread` is `False` |
| ambiguity | −0.04 | 2 type candidates → `W_TYPE_AMBIGUITY × (2 − 1)` |
| contention | 0.00 | no superseded firing |
| **total** | **0.81** | within `[0.35, 0.95]`; rounded to 2dp |

### Stage 9 — `compose.reason.render_reason`

Takes the applied firing's justification. 106 characters, one sentence, third person, no identifiers — inside `[MIN_CHARS, MAX_CHARS]`, so it passes the register check unchanged.

> "A payment instruction arrived from a sender with no established authority for payments in this conversation."

### Stage 10 — `RoutingResult`

`route_one` wraps everything the stages produced:

```python
RoutingResult(
    output=OutputRow(
        message_id="msg_022",
        action=Action.MUTE,
        message_type=MessageType.SCAM,
        reason="A payment instruction arrived from a sender with no established "
               "authority for payments in this conversation.",
        confidence=0.81,
        evidence_message_ids=("message_0100",),
        override_codes=(OverrideCode.UNAUTHORIZED_PAYMENT_REQUEST,),
        firings=(...),
        threat_events=(),
    ),
    perception=...,            # the Stage 4 object, carried intact
    features=...,              # the Stage 5 object, carried intact
    evidence=...,              # the Stage 7 object, incl. shortlist_considered
    telemetry=CallTelemetry(
        cache_media=CacheOutcome.NOT_APPLICABLE,
        cache_perception=CacheOutcome.HIT,
        schema_retry_count=0,
        tokens_input=1180, tokens_output=96,
        wall_clock_ms=12.4,
    ),
)
```

### Stage 11 — emit

`emit.writer` projects `result.output` to the six contract columns, at the row position `msg_022` occupies in `messages.csv`:

```csv
msg_022,mute,scam,A payment instruction arrived from a sender with no established authority for payments in this conversation.,0.81,message_0100
```

`emit.record` projects the full `RoutingResult` into the `RowRecord` — `model_proposal` from `result.perception`, `sender_relationship_class` from `result.features`, `evidence_shortlist` from `result.evidence`, cache outcomes and per-row cost from `result.telemetry` — and appends it to `runs/<id>/rows.jsonl`. Nothing is counted here; `code/metrics/` derives the fire count for `UNAUTHORIZED_PAYMENT_REQUEST` post hoc.

---

## 9. Testing boundaries

Every module is exercised without a network call.

| module | test approach |
|---|---|
| `ingest.loader` | a fixture directory of small CSVs, including malformed rows that must raise |
| `ingest.context` | assert the assembled context for a fixture row; assert a broken join raises |
| `media.locate` | a path with `..` and a symlink out of root must raise `MediaPathError` |
| `media.decode` | fixture files whose extension contradicts their container |
| `perception.prompt` | assert untrusted content is fenced, that free-text context fields are absent, and that a fence token inside the input is neutralised |
| `perception.client` | the only module with a network path; tested against a recorded transport, never live |
| `perception.parse` | responses with extra fields, a confidence field, or an out-of-enum action must raise |
| `features.*` | pure functions; table-driven, including `UNDETERMINED` domain and zero-history senders |
| `policy.engine` | table-driven over constructed `(Perception, FeatureVector)` pairs; one case per rule, plus every conflict pair in [DECISION_ENGINE.md §4](DECISION_ENGINE.md#4-conflict-resolution) |
| `scoring.confidence` | assert each term's contribution in isolation and that clamping holds at both ends |
| `scoring.evidence` | assert tier ordering, recency tie-break, the strictly-older filter, and that a cross-user id raises |
| `compose.reason` | assert register bounds and that no identifier pattern appears in output |
| `emit.writer` | assert exactly six columns in contract order, one row per input, input order preserved |

Two whole-package tests guard the architecture:

- **Import-graph test** — asserts no cycles and that `policy/` imports neither `perception/` nor `ingest/`.
- **Literal-scan test** — the [G2](GENERALIZATION_POLICY.md#g2--literal) gate, automated: greps the package for corpus identifier patterns (`msg_\d+`, `message_\d+`, `u_\d+`, `group_\d+`, `business_\d+`, `img_\d+`, `vn_\d+`) and fails on any hit outside `tests/fixtures/`.
