"""Every threshold, cap, weight, limit, and budget ceiling in the router.

No behavioural literal appears anywhere else in ``code/``. Each constant carries
its provenance per GENERALIZATION_POLICY G4:

    (a) the output contract or the problem statement
    (b) a quantity with independent domain meaning
    (c) a value observed in dataset/  -- PROHIBITED

Config objects are frozen and are passed as parameters, never read from module
globals, so a test can construct an alternative without monkeypatching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Challenge timing
# --------------------------------------------------------------------------

CHALLENGE_END_UTC: datetime | None = None
"""SOURCE (a): none available.

Neither README.md nor problem_statement.md states a challenge end date, and the
platform supplied none. Explicitly ``None`` rather than a guess -- run.py reports
"not configured" instead of computing a remaining time from an invented value.
"""

RESULTS_ANNOUNCED_UTC: datetime | None = None
"""SOURCE (a): none available. No announcement time appears in the repository."""


# --------------------------------------------------------------------------
# Policy thresholds
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyConfig:
    MIN_PATTERN_OBSERVATIONS: int = 2
    """SOURCE (b): one instance is an event; two is the minimum that can
    constitute a pattern. REPEAT_DISMISSAL's justification says "repeatedly",
    and one occurrence cannot support that word."""

    DISMISSAL_MAJORITY: float = 0.5
    """SOURCE (b): a strict majority is the weakest claim that supports
    "usually". Any higher value would be a preference without an argument."""

    FORWARD_CHAIN_THRESHOLD: int = 5
    """SOURCE (b): WhatsApp's own product definition -- the platform labels a
    message "forwarded many times" once it has passed through five or more
    chats, and restricts onward forwarding at that point. A published constant
    about the platform being modelled."""

    MIN_STANCE_OBSERVATIONS: int = 2
    """SOURCE (b): a stance is a claim about a pattern of behaviour, so it needs
    the same minimum as any other pattern claim. Below this the stance is
    UNKNOWN rather than asserted from a single interaction."""


# --------------------------------------------------------------------------
# Confidence weights
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceConfig:
    W_BASE: float = 0.55
    """SOURCE (b): a three-way choice at chance is 0.33. A decision informed by
    content, sender relationship, and conversation type should beat chance, but
    one with nothing corroborating it is not settled. 0.55 is the midpoint of
    that description."""

    W_TRIGGER_STRUCTURAL: float = 0.20
    """SOURCE (b): the trigger is a fact recorded in the input, so the decision
    is as reliable as the input record."""

    W_TRIGGER_PERCEPTUAL: float = 0.10
    """SOURCE (b): a content judgement is reliable but not independently
    verifiable, so it earns half of a structural trigger."""

    W_EVIDENCE_RELATIONSHIP_AND_CONTENT: float = 0.12
    """SOURCE (b): the strongest corroboration available -- this user has met
    this situation from this sender before."""

    W_EVIDENCE_RELATIONSHIP: float = 0.07
    """SOURCE (b): same sender, different subject. Weaker than the above."""

    W_EVIDENCE_TOPICAL: float = 0.03
    """SOURCE (b): same subject, no relationship. The weakest corroboration."""

    W_ENGAGEMENT_ALIGNED: float = 0.08
    """SOURCE (b): agreement between the decision and the user's own past
    behaviour is two independent lines of reasoning converging. Applied
    symmetrically as a penalty on disagreement, since both are equally
    informative."""

    W_MEDIA_UNREAD: float = 0.15
    """SOURCE (b): the largest single term, because it is the only one standing
    for a decision made without the substance of the message."""

    W_TYPE_AMBIGUITY: float = 0.04
    """SOURCE (b): each additional candidate is one more reading the model could
    not rule out. Small, because type ambiguity often does not change the
    action at all."""

    W_CONTENTION: float = 0.03
    """SOURCE (b): a decision several rules disagreed about is less certain than
    one reached unopposed."""

    FLOOR: float = 0.35
    """SOURCE (b): slightly above the 0.33 chance rate for a three-way choice.
    Below this we would assert our own answer is worse than a guess, which is
    not a claim we can make while still emitting it."""

    CEILING: float = 0.95
    """SOURCE (b): no router is certain. Even a structural trigger rests on a
    snapshot that may be stale; 1.0 would be a false claim."""

    DECIMAL_PLACES: int = 2
    """SOURCE (a): the output contract asks for a number in [0, 1]; two places
    is the resolution at which a confidence is meaningful to a reader."""


# --------------------------------------------------------------------------
# Evidence selection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceConfig:
    MAX_IDS_DEFAULT: int = 1
    """SOURCE (b): an explanation cites the minimum evidence supporting the
    claim it makes. A claim about one prior interaction needs one instance."""

    MAX_IDS_PATTERN_RULE: int = 2
    """SOURCE (b): a claim of repetition needs at least two instances."""

    MIN_SHARED_CONTENT_TOKENS: int = 2
    """SOURCE (b): two messages are topically related when they share at least
    two content words.

    This replaces a Jaccard ratio threshold of 0.25 whose provenance was flagged
    UNRESOLVED at design time and never resolved. Measured against the corpus it
    sat at roughly the 92nd percentile of non-zero overlaps -- which is what a
    value fitted to this corpus looks like, and is exactly what the flag warned
    against.

    A count has an argument a ratio does not. One shared content word between two
    short messages is coincidence: any two sentences about a building share
    "gate". Two co-occurring content words is the smallest quantity that can
    indicate shared subject matter rather than shared vocabulary. The claim is
    about how language works, not about how this corpus is distributed, and it
    does not move if the corpus grows or the messages get longer -- whereas a
    Jaccard threshold does, because the denominator is the union of both
    vocabularies.

    Jaccard is still computed, and still ranks candidates. It is only no longer
    asked to answer a yes/no question it had no principled cut-point for."""

    MIN_TOKEN_LENGTH: int = 3
    """SOURCE (b): tokens shorter than three characters are overwhelmingly
    function words, which carry no topical signal in any language."""

    SEPARATOR: str = ";"
    """SOURCE (a): the output contract specifies semicolon-separated ids."""

    NONE_LITERAL: str = "none"
    """SOURCE (a): the output contract specifies the literal `none`."""


# --------------------------------------------------------------------------
# Reason register
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReasonConfig:
    MIN_CHARS: int = 55
    MAX_CHARS: int = 120
    """SOURCE (a), style only: problem_statement.md scopes sample_messages.csv to
    "the expected output format and style", which licenses matching the observed
    register. The bounds are a padded envelope around it, not a fitted value --
    no decision depends on where exactly they sit."""

    MAX_SENTENCES: int = 1
    """SOURCE (a): every sample reason is a single sentence; register is the one
    thing the statement explicitly licenses copying."""


# --------------------------------------------------------------------------
# Resource and budget ceilings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitsConfig:
    MAX_UNTRUSTED_CHARS: int = 4_000
    """SOURCE (b): a bound on how much attacker-controlled text enters one
    prompt. Well above any legitimate chat message, well below a size at which
    a wall of text could crowd out the instruction context."""

    MAX_MEDIA_BYTES: int = 25_000_000
    """SOURCE (b): WhatsApp's own per-file media ceiling for documents. A file
    larger than the platform permits did not arrive over the platform."""

    MAX_AUDIO_SECONDS: float = 300.0
    """SOURCE (b): five minutes. Longer than any voice note a person sends in
    a chat, short enough that one file cannot stall a run."""

    MAX_IMAGE_PIXELS: int = 40_000_000
    """SOURCE (b): 40 megapixels exceeds any consumer camera likely to have
    produced a shared photo, and bounds decompression cost."""

    MODEL_TIMEOUT_SECONDS: float = 60.0
    """SOURCE (b): a single perception call that has not returned in a minute
    has failed in practice, whatever the transport reports."""

    MODEL_MAX_ATTEMPTS: int = 3
    """SOURCE (b): one retry for a transient fault, one for a second transient
    fault, then stop. A third failure is not transient."""

    MAX_RUN_INPUT_TOKENS: int = 5_000_000
    MAX_RUN_OUTPUT_TOKENS: int = 1_000_000
    """SOURCE (b): a ceiling roughly an order of magnitude above one full live
    pass over 140 rows, so it stops a runaway loop without interrupting
    legitimate work. Not reached in Phase 0, which makes no calls."""

    MAX_RUN_WALL_CLOCK_SECONDS: float = 3_600.0
    """SOURCE (b): one hour. A live pass over 140 rows that has not finished in
    an hour is stuck, not slow."""


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheConfig:
    root: Path = Path(".cache")
    media_dir: Path = Path(".cache/media")
    perception_dir: Path = Path(".cache/perception")

    schema_version: int = 1
    """SOURCE (b): a one-line global invalidation lever. Bumping it changes the
    key rather than deleting anything."""

    shard_prefix_length: int = 2
    """SOURCE (b): two hex characters -> 256 shards, so no directory grows
    unbounded. Not tuned to the current corpus size."""

    json_indent: int = 2


# --------------------------------------------------------------------------
# Media extraction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionConfig:
    """Who extracts what, and under which settings.

    Every field here is part of the media cache key. A change to any of them
    produces new keys rather than a stale hit, which is the point: a transcript
    produced in one mode must never be served for a request in another.
    """

    VISION_PROVIDER: str = "anthropic"

    VISION_PROMPT_VERSION: str = "1.0.0"
    """SOURCE (b): bumped when the image prompt changes. Keyed, so a bump
    invalidates nothing -- it simply addresses a different entry."""

    VISION_MAX_OUTPUT_TOKENS: int = 1_024
    """SOURCE (b): a hard ceiling on how much text one image can produce.
    Generous for a paragraph plus a poster's worth of transcribed text, tight
    enough that an image crafted to elicit a wall of output cannot."""

    VISION_EFFORT: str = "low"
    """SOURCE (b): this call performs perception, not reasoning -- describe and
    transcribe. Depth buys nothing and costs latency on every image."""

    VOICE_PROVIDER: str = "sarvam"
    VOICE_MODEL_ID: str = "saaras:v3"

    VOICE_MODE: str = "translit"
    """SOURCE (a): the text channel is 100% Latin script -- messages.csv and
    message_history.csv contain zero non-ASCII bytes -- yet carry romanised
    Hindi on both sides of the safety boundary. `translit` is the only saaras
    mode whose output shares that register: Latin script, Indic words
    romanised, English kept English, digits as digits. `transcribe`,
    `verbatim` and `codemix` emit Devanagari; `translate` emits English."""

    VOICE_LANGUAGE_CODE: str | None = None
    """SOURCE (b): unset, so the model auto-detects and returns
    language_probability. Pinning a language suppresses that signal. Keyed as
    null so pinning one later produces new keys."""

    VOICE_MAX_SECONDS: float = 30.0
    """SOURCE (b): the documented ceiling of the Sarvam REST speech-to-text
    endpoint, which returns 422 above it. Checked before the call so a file we
    already know is too long spends no request."""

    RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0)
    """SOURCE (b): a fixed, un-jittered schedule for the two retryable provider
    faults (rate limit, service unavailable). Fixed rather than random so a
    retried run is as reproducible as an un-retried one."""


# --------------------------------------------------------------------------
# Perception
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PerceptionConfig:
    """Request shape for the routing call. Every field enters the cache key.

    Deliberately absent from this object: anything the policy layer reads.
    Policy iteration must not miss the cache, which is the entire reason the
    two are separated.
    """

    EFFORT: str = "medium"
    """SOURCE (b): the call reads one short message and reports what is in it.
    Higher effort buys deliberation this task does not have room for; lower
    risks under-reading a message whose safety claims are the whole point."""

    MAX_OUTPUT_TOKENS: int = 2_048
    """SOURCE (b): a fixed-shape object of fifteen fields plus one clause of
    media summary. Generous for that, and a hard ceiling on what a message
    engineered to elicit a long reply can extract."""

    THINKING: str = "adaptive"
    """SOURCE (b): the model decides per message how much deliberation a read
    needs. A fixed budget would over-spend on a greeting and under-spend on an
    ambiguous payment demand."""

    def model_params(self) -> dict[str, object]:
        """The request-shaping values, for the cache key. Sorted by the store."""
        return {
            "effort": self.EFFORT,
            "max_output_tokens": self.MAX_OUTPUT_TOKENS,
            "thinking": self.THINKING,
        }


# --------------------------------------------------------------------------
# Record / run
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordConfig:
    SCHEMA_VERSION: int = 2
    """SOURCE (b): the per-row record schema version. Version 1 predates the
    reason-health fields; the metrics engine degrades gracefully on it."""

    RUNS_DIR: Path = Path("runs")
    SUBMISSION_DIR: Path = Path("submission")

    RUN_ID_HASH_CHARS: int = 6
    """SOURCE (b): enough hex to make a config hash collision improbable within
    one project's run history, short enough to read in a directory listing."""

    LOG_DIGEST_CHARS: int = 16
    """SOURCE (b): the prefix of a sha256 used to correlate repeated untrusted
    payloads in the log without storing any of them."""

    GIT_TIMEOUT_SECONDS: float = 10.0
    """SOURCE (b): a `git rev-parse` that has not answered in ten seconds is
    not going to; the run proceeds with an empty commit field."""

    ERROR_LIST_TRUNCATION: int = 10
    """SOURCE (b): how many offending ids an error message lists before eliding.
    Enough to see a pattern, few enough to read."""

    WALL_CLOCK_DECIMALS: int = 3
    """SOURCE (b): milliseconds to three places -- microsecond resolution, which
    is finer than any real per-row variation."""


# --------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RouterConfig:
    """The aggregate handed to orchestration and sliced per module.

    Functions take the narrowest config they need; cross-cutting entry points
    (extract, route_one) take this.
    """

    dataset_root: Path = Path("dataset")
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    reason: ReasonConfig = field(default_factory=ReasonConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    record: RecordConfig = field(default_factory=RecordConfig)

    model_id: str = ""
    """The Anthropic model, read from the environment in ``load_model_id``.
    Governs image description and the routing call. It does **not** govern
    transcription -- that is ``extraction.VOICE_MODEL_ID`` -- so the two are
    keyed separately."""


class ConfigError(Exception):
    """A config value is outside its permitted range."""


def validate(cfg: RouterConfig) -> None:
    """Check every bounded constant at startup, before any message is routed.

    Raises:
        ConfigError: a value is outside its permitted range.
    """
    c = cfg.confidence
    weights = {
        "W_BASE": c.W_BASE,
        "W_TRIGGER_STRUCTURAL": c.W_TRIGGER_STRUCTURAL,
        "W_TRIGGER_PERCEPTUAL": c.W_TRIGGER_PERCEPTUAL,
        "W_EVIDENCE_RELATIONSHIP_AND_CONTENT": c.W_EVIDENCE_RELATIONSHIP_AND_CONTENT,
        "W_EVIDENCE_RELATIONSHIP": c.W_EVIDENCE_RELATIONSHIP,
        "W_EVIDENCE_TOPICAL": c.W_EVIDENCE_TOPICAL,
        "W_ENGAGEMENT_ALIGNED": c.W_ENGAGEMENT_ALIGNED,
        "W_MEDIA_UNREAD": c.W_MEDIA_UNREAD,
        "W_TYPE_AMBIGUITY": c.W_TYPE_AMBIGUITY,
        "W_CONTENTION": c.W_CONTENTION,
    }
    for name, value in weights.items():
        if not 0.0 <= value <= 1.0:
            raise ConfigError(f"{name}={value} is outside [0.0, 1.0]")
    if not 0.0 <= c.FLOOR < c.CEILING <= 1.0:
        raise ConfigError(
            f"confidence bounds invalid: FLOOR={c.FLOOR} CEILING={c.CEILING}; "
            "require 0.0 <= FLOOR < CEILING <= 1.0"
        )
    if c.W_TRIGGER_PERCEPTUAL > c.W_TRIGGER_STRUCTURAL:
        raise ConfigError(
            "W_TRIGGER_PERCEPTUAL must not exceed W_TRIGGER_STRUCTURAL: a content "
            "judgement cannot be more reliable than a recorded fact"
        )
    if not (
        c.W_EVIDENCE_TOPICAL
        <= c.W_EVIDENCE_RELATIONSHIP
        <= c.W_EVIDENCE_RELATIONSHIP_AND_CONTENT
    ):
        raise ConfigError("evidence weights must be non-decreasing in tier strength")

    p = cfg.policy
    if p.MIN_PATTERN_OBSERVATIONS < 2:
        raise ConfigError(
            f"MIN_PATTERN_OBSERVATIONS={p.MIN_PATTERN_OBSERVATIONS}; a pattern "
            "needs at least two observations"
        )
    if not 0.0 < p.DISMISSAL_MAJORITY < 1.0:
        raise ConfigError(f"DISMISSAL_MAJORITY={p.DISMISSAL_MAJORITY} is outside (0, 1)")
    if p.FORWARD_CHAIN_THRESHOLD < 1:
        raise ConfigError(f"FORWARD_CHAIN_THRESHOLD={p.FORWARD_CHAIN_THRESHOLD} must be >= 1")

    e = cfg.evidence
    if e.MAX_IDS_DEFAULT < 1 or e.MAX_IDS_PATTERN_RULE < e.MAX_IDS_DEFAULT:
        raise ConfigError(
            f"evidence id caps invalid: default={e.MAX_IDS_DEFAULT} "
            f"pattern={e.MAX_IDS_PATTERN_RULE}"
        )
    if e.MIN_SHARED_CONTENT_TOKENS < 1:
        raise ConfigError(
            f"MIN_SHARED_CONTENT_TOKENS={e.MIN_SHARED_CONTENT_TOKENS} would make every pair "
            "of messages topically related"
        )

    r = cfg.reason
    if r.MIN_CHARS >= r.MAX_CHARS:
        raise ConfigError(f"reason bounds invalid: {r.MIN_CHARS} >= {r.MAX_CHARS}")


DEFAULT_MODEL_ID = "claude-sonnet-5"
"""The model the shipped `.cache/perception/` entries were produced by.

**Not a secret.** It is a public model name, already written into every run
manifest and into `docs/` — recording it here publishes nothing that was not
already published, and it is kept separate from the three credential variables,
which have no defaults and never will.

It has to be recorded somewhere the archive carries, because the model id is part
of the perception cache key: a cached answer must be attributable to a model, or
a model swap would silently reuse the previous one's answers. That invariant is
right and is not being weakened here. But it means an unset `ROUTER_MODEL_ID`
produces a *different key for every row*, and a reviewer who follows the README
-- which correctly says no credentials are needed for an offline run -- would get
140 cache misses and a hard failure on the one property the archive most wants to
demonstrate. Phase 6's clean-room test is what surfaced that; before it, the
offline-reproducibility claim had never been executed anywhere the environment
was actually clean.

An explicit `ROUTER_MODEL_ID` still overrides this and still re-keys the cache,
so re-warming against a different model behaves exactly as before.
"""


def load_model_id() -> str:
    """The model identifier: the environment if set, else the recorded default.

    The only env read in `code/` outside `perception/client.py`.
    """
    return os.environ.get("ROUTER_MODEL_ID") or DEFAULT_MODEL_ID
