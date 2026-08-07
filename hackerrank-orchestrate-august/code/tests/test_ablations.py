"""Phase 5: input-dependence checks.

Every rule claims to read something. These tests degrade that something and
assert the output moves in the stated direction. **A null result is a defect,
not a pass** — if zeroing the engagement history leaves `REPEAT_DISMISSAL`
firing, the rule is not reading what its `reads` set says it reads, and the
whole explanation the system offers for that row is fiction.

Run against the real dataset when one is present, and skipped cleanly when it is
not — the submission archive ships without it. The ablations mutate an in-memory
copy of the loaded dataset; nothing on disk is touched.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from config import RouterConfig
from schemas import DomainState, EngagementStance, MediaKind, OverrideCode

PKG = Path(__file__).resolve().parents[1]
DATASET = Path(os.environ.get("ROUTER_DATASET", PKG.parent / "dataset"))

pytestmark = pytest.mark.skipif(
    not (DATASET / "messages.csv").exists(),
    reason=f"no dataset at {DATASET}; ablations need the real corpus",
)


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cfg() -> RouterConfig:
    import config as config_module
    from main import load_dotenv

    load_dotenv(PKG / ".env")
    return RouterConfig(dataset_root=DATASET, model_id=config_module.load_model_id())


@pytest.fixture(scope="module")
def contexts(cfg: RouterConfig):
    """Every target row's context, built once. Perception comes from the cache."""
    from ingest.context import build_context
    from ingest.loader import load_dataset
    from media import extract as media_extract
    from perception.cached import CachedPerceptionClient
    from perception.prompt import new_nonce

    dataset = load_dataset(cfg.dataset_root)
    client = CachedPerceptionClient(cfg, nonce=new_nonce())
    built = []
    for message in dataset.targets:
        paths = dict(
            dataset.images if message.media_kind is MediaKind.IMAGE else dataset.voice_notes
        )
        media, _outcome = media_extract.extract(message, cfg, media_paths=paths)
        context = build_context(dataset, message.message_id, media)
        try:
            perception, _telemetry = client.perceive(context)
        except Exception as exc:  # noqa: BLE001 - reported as a skip, not a failure
            pytest.skip(f"perception cache not warmed: {exc}")
        built.append((context, perception))
    return built


def _run(contexts, cfg: RouterConfig, mutate=None) -> Counter:
    """Route every row through features+policy, optionally mutating the context.

    Counts **firings**, not applied codes. A rule can fire on many rows and never
    be the determining one -- GROUP_MUTE_CAP does exactly that -- and an ablation
    asks whether the rule still *triggers*, which is the question its `reads` set
    makes a claim about. Counting applied codes would report zero for a rule that
    is working perfectly, and the guard test below is what caught that.

    Deliberately does not touch the model: an ablation that needed a re-warm
    could not be run offline, and every input under test here is structural.
    """
    from features.assemble import build_features
    from policy.engine import decide
    from schemas import FiringStatus

    dormant = {FiringStatus.DID_NOT_FIRE, FiringStatus.NOT_EVALUATED}
    counts: Counter = Counter()
    for context, perception in contexts:
        if mutate is not None:
            context = mutate(context)
        features = build_features(context, cfg.policy)
        for firing in decide(perception, features, cfg.policy).firings:
            if firing.status not in dormant:
                counts[firing.code] += 1
    return counts


def _confidences(contexts, cfg: RouterConfig, mutate=None) -> dict[str, float]:
    from features.assemble import build_features
    from policy.engine import decide
    from scoring.confidence import score_confidence
    from scoring.evidence import select_evidence

    out = {}
    for context, perception in contexts:
        original = context
        if mutate is not None:
            context = mutate(context)
        features = build_features(context, cfg.policy)
        decision = decide(perception, features, cfg.policy)
        evidence = select_evidence(context, decision, cfg.evidence)
        out[original.message.message_id] = score_confidence(
            decision, features, perception, evidence, cfg.confidence
        )
    return out


# --------------------------------------------------------------------------
# Ablations, per IMPLEMENTATION_PLAN Phase 5 verification 2
# --------------------------------------------------------------------------


def test_baseline_fires_every_rule_under_test(contexts, cfg: RouterConfig) -> None:
    """Guard for the ablations below: a rule that never fires cannot be shown to
    stop firing, so a null result would be meaningless rather than reassuring."""
    counts = _run(contexts, cfg)
    for code in (OverrideCode.REPEAT_DISMISSAL, OverrideCode.GROUP_MUTE_CAP,
                 OverrideCode.IMPERSONATION):
        assert counts[code] > 0, f"{code.value} never fires at baseline"


def test_zeroing_engagement_history_stops_repeat_dismissal(contexts, cfg: RouterConfig) -> None:
    """REPEAT_DISMISSAL claims to read the receiver's own interaction history."""
    def drop_history(context):
        return context.model_copy(update={"history": ()})

    before = _run(contexts, cfg)[OverrideCode.REPEAT_DISMISSAL]
    after = _run(contexts, cfg, drop_history)[OverrideCode.REPEAT_DISMISSAL]
    assert before > 0
    assert after == 0, f"still fires on {after} rows with no history to read"


def test_unmuting_every_conversation_stops_the_group_mute_cap(contexts, cfg: RouterConfig) -> None:
    def unmute(context):
        membership = context.receiver_membership
        if membership is None:
            return context
        return context.model_copy(
            update={"receiver_membership": membership.model_copy(
                update={"group_muted_by_user": False})}
        )

    before = _run(contexts, cfg)[OverrideCode.GROUP_MUTE_CAP]
    after = _run(contexts, cfg, unmute)[OverrideCode.GROUP_MUTE_CAP]
    assert before > 0
    assert after == 0


def test_verifying_every_business_stops_impersonation(contexts, cfg: RouterConfig) -> None:
    """IMPERSONATION requires an *unverified* sender; verifying all must disarm it."""
    def verify(context):
        if context.business is None:
            return context
        return context.model_copy(
            update={"business": context.business.model_copy(update={"verified": True})}
        )

    before = _run(contexts, cfg)[OverrideCode.IMPERSONATION]
    after = _run(contexts, cfg, verify)[OverrideCode.IMPERSONATION]
    assert before > 0
    assert after == 0


def test_blanking_official_domain_stops_impersonation(contexts, cfg: RouterConfig) -> None:
    """A blank domain is UNDETERMINED, not MISMATCH.

    Absence of evidence is not evidence of impersonation — the three-valued
    DomainState exists for exactly this, and this ablation is what proves the
    third value is load-bearing rather than decorative.
    """
    def blank_domain(context):
        if context.business is None:
            return context
        return context.model_copy(
            update={"business": context.business.model_copy(
                update={"official_domain": None, "brand_name": context.business.display_name})}
        )

    before = _run(contexts, cfg)[OverrideCode.IMPERSONATION]
    after = _run(contexts, cfg, blank_domain)[OverrideCode.IMPERSONATION]
    assert before > 0
    assert after == 0


def test_dropping_all_media_raises_the_unread_share_and_lowers_confidence(
    contexts, cfg: RouterConfig
) -> None:
    """The system should say out loud that it decided without the content."""
    from features.assemble import build_features
    from schemas import MediaArtifact

    def break_media(context):
        media = context.media
        if media is None:
            return context
        return context.model_copy(
            update={"media": MediaArtifact(
                media_id=media.media_id, kind=media.kind, container_format=media.container_format,
                duration_seconds=media.duration_seconds, readable=False, content=None,
                failure_reason="ablation",
            )}
        )

    unread_before = sum(
        build_features(c, cfg.policy).media_unread for c, _p in contexts
    )
    unread_after = sum(
        build_features(break_media(c), cfg.policy).media_unread for c, _p in contexts
    )
    assert unread_after > unread_before, "breaking every attachment changed no row"

    before = _confidences(contexts, cfg)
    after = _confidences(contexts, cfg, break_media)
    newly_unread = [
        mid for (c, _p) in contexts
        if not build_features(c, cfg.policy).media_unread
        and build_features(break_media(c), cfg.policy).media_unread
        for mid in [c.message.message_id]
    ]
    assert newly_unread, "no row became unread"
    # Confidence must fall on every row that lost its only content, by at least
    # the declared weight -- other terms may move it further, never back up.
    for mid in newly_unread:
        assert after[mid] <= before[mid] - cfg.confidence.W_MEDIA_UNREAD + 1e-9, mid


def test_making_every_sender_dismissive_moves_actions_toward_mute(
    contexts, cfg: RouterConfig
) -> None:
    """The engagement signal must be able to change outcomes, not just confidence."""
    from features.assemble import build_features
    from policy.engine import decide
    from schemas import Action, InteractionEvent

    def dismiss_all(context):
        history = tuple(
            record.model_copy(update={"event": InteractionEvent(
                user_id=record.event.user_id, message_id=record.event.message_id,
                opened=False, replied=False, reaction_time_minutes=None,
                dismissed=True, muted_after=True, reported=False,
            )})
            for record in context.history
        )
        return context.model_copy(update={"history": history})

    def mutes(mutate):
        return sum(
            decide(p, build_features(mutate(c) if mutate else c, cfg.policy), cfg.policy).action
            is Action.MUTE
            for c, p in contexts
        )

    assert mutes(dismiss_all) > mutes(None)


def test_an_ablation_that_should_change_nothing_changes_nothing(
    contexts, cfg: RouterConfig
) -> None:
    """The control. Without it, a harness bug that perturbs everything would make
    every ablation above 'pass' for the wrong reason.

    `group_name` is free text that THREAT_MODEL T5 closes off by architecture, so
    rewriting it must be invisible to every rule and every score.
    """
    def rename_group(context):
        if context.group is None:
            return context
        return context.model_copy(
            update={"group": context.group.model_copy(
                update={"group_name": "ZZZ totally different name"})}
        )

    assert _run(contexts, cfg, rename_group) == _run(contexts, cfg)
    assert _confidences(contexts, cfg, rename_group) == _confidences(contexts, cfg)
