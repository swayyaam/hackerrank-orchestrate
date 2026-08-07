"""Metrics engine tested against the fixture run directory. No pipeline execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from metrics.consistency import compute_consistency
from metrics.diff import RunsDifferError, assert_runs_identical, compute_diff
from metrics.label_free import compute_label_free, entropy_bits
from metrics.labeled import compute_labeled, confusion_matrix, macro_f1, per_class
from metrics.loader import load_run
from metrics.report import build_report, render_console, render_diff_console

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "run_fixture"
FIXTURE_V1 = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "run_fixture_v1"


@pytest.fixture(scope="module")
def run():
    return load_run(FIXTURE)


# ---- loader -------------------------------------------------------------


def test_loader_reads_all_four_artifacts(run) -> None:
    assert len(run.rows) == 14
    assert len(run.eval_rows) == 6
    assert len(run.eval_truth) == 6
    assert run.run_id == "fixture-0000000-abcdef"


def test_loader_reads_truth_from_run_dir_not_dataset(run) -> None:
    """Retroactivity: labeled metrics must not depend on dataset/."""
    assert set(run.eval_truth["message_id"]) == {f"e0{i}" for i in range(1, 7)}


# ---- label-free ---------------------------------------------------------


def test_entropy_bounds() -> None:
    assert entropy_bits([1, 1]) == pytest.approx(1.0)
    assert entropy_bits([5, 0, 0]) == pytest.approx(0.0)
    assert entropy_bits([]) == 0.0


def test_label_free_counts(run) -> None:
    lf = compute_label_free(run.rows, run.manifest)
    assert lf["n_rows"] == 14
    assert lf["action_distribution"]["counts"]["mute"] == 3
    assert lf["override_codes"]["fire_counts"]["REPEAT_DISMISSAL"] == 3
    assert lf["override_codes"]["rows_with_multiple_firings"] == 1


def test_ungrounded_citations_detected() -> None:
    """The invariant is verified from the record, not trusted."""
    import pandas as pd

    rows = pd.DataFrame(
        [
            {
                "message_id": "x1",
                "action": "mute",
                "message_type": "scam",
                "confidence": 0.8,
                "evidence_cited": ["ghost"],
                "evidence_shortlist": ["real"],
                "evidence_tier": 2,
                "model_proposal": {"action": "mute", "type_candidates": ["scam"]},
                "override_codes_fired": ["PERCEPTION_ACCEPTED"],
                "override_code_applied": "PERCEPTION_ACCEPTED",
                "integrity_warnings": [],
                "schema_retry_count": 0,
                "media_kind": "",
                "media_outcome": "none",
                "media_failure_cause": None,
                "cache": {"media": "n/a", "perception": "hit"},
                "served_entirely_from_cache": True,
                "tokens": {"input": 0, "output": 0},
                "wall_clock_ms": 1.0,
                "proposed_message_type": "scam",
                "reason_char_length": 60,
                "reason_cites_evidence": False,
                "reason_references_integrity": False,
            }
        ]
    )
    lf = compute_label_free(rows, {"failures": []})
    assert lf["evidence"]["ungrounded_citation_count"] == 1
    assert lf["evidence"]["ungrounded_rows"] == ["x1"]


def test_hard_parse_failure_comes_from_manifest(run) -> None:
    """A hard-failed row writes no record, so the count must come from failures."""
    lf = compute_label_free(run.rows, run.manifest)
    assert lf["perception_health"]["hard_parse_failure_count"] == 1


def test_media_unread_is_narrow(run) -> None:
    """A captioned image that fails is media_failed but not media_unread."""
    lf = compute_label_free(run.rows, run.manifest)
    assert lf["media"]["rows_media_failed"] == 2
    assert lf["media"]["rows_media_unread"] == 1


def test_graceful_degradation_on_old_schema() -> None:
    """A v1 run reports reason_health unavailable rather than crashing or zeroing."""
    old = load_run(FIXTURE_V1)
    lf = compute_label_free(old.rows, old.manifest)
    assert lf["reason_health"]["status"] == "unavailable"
    assert "reason_char_length" in lf["reason_health"]["missing_fields"]
    assert lf["n_rows"] == 4


# ---- labeled ------------------------------------------------------------


def test_confusion_matrix_and_f1() -> None:
    labels = ["a", "b"]
    matrix = confusion_matrix(["a", "a", "b"], ["a", "b", "b"], labels)
    assert matrix == [[1, 1], [0, 1]]
    stats = per_class(matrix, labels)
    assert stats["a"]["recall"] == pytest.approx(0.5)
    assert stats["a"]["precision"] == pytest.approx(1.0)
    assert macro_f1(stats) == pytest.approx((0.6667 + 0.6667) / 2, abs=1e-3)


def test_labeled_accuracy_and_noise_floor(run) -> None:
    lb = compute_labeled(run.eval_rows, run.eval_truth)
    assert lb["n_rows"] == 6
    assert lb["action"]["correct"] == 3
    assert lb["noise_floor"]["one_row_pp"] == pytest.approx(16.67, abs=0.01)
    assert lb["noise_floor"]["reportable_threshold_rows"] == 2


def test_calibration_buckets_report_counts(run) -> None:
    lb = compute_labeled(run.eval_rows, run.eval_truth)
    cal = lb["calibration"]
    assert cal["indicative_only"] is True
    assert len(cal["buckets"]) == 5
    assert sum(b["n"] for b in cal["buckets"]) == 6
    assert 0.0 <= cal["expected_calibration_error"] <= 1.0
    assert 0.0 <= cal["brier_score"] <= 1.0


def test_evidence_overlap_separates_both_none(run) -> None:
    lb = compute_labeled(run.eval_rows, run.eval_truth)
    assert lb["evidence_overlap"]["rows_compared"] == 6


# ---- consistency --------------------------------------------------------


def test_consistency_finds_explained_and_unexplained(run) -> None:
    cs = compute_consistency(run.rows)
    assert cs["groups_with_divergence"] == 3
    assert cs["groups_diverging_without_code_difference"] == 1
    unexplained = [g for g in cs["divergent_groups"] if not g["explained"]]
    assert len(unexplained) == 1
    # t13/t14 share a key, share their override codes, AND share a proposal, so
    # nothing recorded on either row accounts for the divergence.
    assert {m["message_id"] for m in unexplained[0]["members"]} == {"t13", "t14"}


def test_a_differing_model_proposal_explains_a_divergence(run) -> None:
    """Two rows alike on every structural field can still be read differently.

    The proposal is recorded on the row, so it is a named cause. Calling it
    unexplained would mean perception is never permitted to distinguish two
    structurally similar messages, which is the reason perception exists.
    """
    cs = compute_consistency(run.rows)
    by_ids = {
        frozenset(m["message_id"] for m in g["members"]): g for g in cs["divergent_groups"]
    }
    group = by_ids[frozenset({"t10", "t11"})]
    assert group["explained"] is True
    assert group["distinguished_by_proposal"] is True
    assert "MODEL_PROPOSAL" in group["distinguishing_codes"]


def test_consistency_groups_by_composite_key_not_all_pairs(run) -> None:
    cs = compute_consistency(run.rows)
    assert cs["grouping_key"] == [
        "conversation_type",
        "sender_relationship_class",
        "proposed_message_type",
        "integrity_state",
    ]


# ---- diff ---------------------------------------------------------------


def test_diff_of_a_run_against_itself_is_empty(run) -> None:
    d = compute_diff(run, run)
    assert d["gross_churn"]["rows_action_changed"] == 0
    assert d["unexplained_action_changes"]["count"] == 0


def test_assert_runs_identical_ignores_volatile_fields(run) -> None:
    import copy

    other = copy.deepcopy(run)
    other.rows.loc[0, "wall_clock_ms"] = 999.0
    other.manifest["run_id"] = "different"
    other.manifest["created_at_utc"] = "2099-01-01T00:00:00Z"
    assert_runs_identical(run, other)


def test_assert_runs_identical_fails_outside_allowlist(run) -> None:
    import copy

    other = copy.deepcopy(run)
    other.rows.loc[0, "confidence"] = 0.01
    with pytest.raises(RunsDifferError, match="confidence"):
        assert_runs_identical(run, other)


def test_diff_flags_unexplained_change(run) -> None:
    import copy

    other = copy.deepcopy(run)
    other.rows.loc[0, "action"] = "mute"  # codes unchanged -> unexplained
    d = compute_diff(run, other)
    assert d["unexplained_action_changes"]["count"] == 1
    assert d["changed_rows"][0]["explained"] is False


# ---- report -------------------------------------------------------------


def test_report_has_three_separate_sections(run) -> None:
    report = build_report(run)
    assert set(report) >= {"label_free", "labeled", "consistency", "manifest", "run_id"}
    assert report["label_free"]["n_rows"] == 14
    assert report["labeled"]["n_rows"] == 6


def test_console_is_readable_without_opening_files(run) -> None:
    console = render_console(build_report(run))
    assert "LABEL-FREE" in console
    assert "LABEL-DEPENDENT" in console
    assert "CONSISTENCY" in console
    assert "INDICATIVE ONLY" in console
    assert "ungrounded citations: 0" in console


def test_diff_console_states_churn_ratio_in_prose(run) -> None:
    import copy

    other = copy.deepcopy(run)
    other.rows.loc[0, "action"] = "mute"
    text = render_diff_console(compute_diff(run, other))
    assert "CHURN / NET" in text
    assert "GROSS CHURN" in text
