"""Gate 3: the submission contract, and loader/writer round-trips."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from config import RouterConfig
from emit.writer import CONTRACT_COLUMNS, render_evidence, validate_submission
from errors import DatasetError
from ingest.loader import INPUT_CSVS, load_dataset
from schemas import Action, MessageType

PKG = Path(__file__).resolve().parents[1]
DATASET = Path(os.environ.get("ROUTER_DATASET", PKG.parent / "dataset"))

# The submission zip deliberately excludes the dataset, so these tests skip
# rather than fail when it is absent. Set ROUTER_DATASET to point elsewhere.
pytestmark = pytest.mark.skipif(
    not (DATASET / "messages.csv").exists(),
    reason=f"dataset not present at {DATASET}; set ROUTER_DATASET to run contract tests",
)
OUTPUT = PKG.parent / "dataset" / "output.csv"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(DATASET)


def test_loader_reads_targets_and_eval_on_equal_footing(dataset) -> None:
    assert len(dataset.targets) == 110
    assert len(dataset.eval_messages) == 30
    assert len(dataset.eval_truth) == 30
    assert not set(m.message_id for m in dataset.targets) & set(
        m.message_id for m in dataset.eval_messages
    )


def test_eval_rows_project_onto_incoming_message(dataset) -> None:
    """U2: the 11 input columns become IncomingMessage; the 5 label columns do not."""
    sample = dataset.eval_messages[0]
    assert sample.message_id.startswith("sample_msg_")
    assert not hasattr(sample, "action")


def test_fingerprint_excludes_the_written_output(dataset) -> None:
    """The bug the determinism gate caught: output.csv must not be an input."""
    assert "output.csv" not in INPUT_CSVS
    assert len(INPUT_CSVS) == 12


def test_fingerprint_is_stable_across_loads(dataset) -> None:
    assert load_dataset(DATASET).fingerprint == dataset.fingerprint


def test_history_is_sorted_and_owned(dataset) -> None:
    for user_id, records in dataset.history_by_user.items():
        assert all(r.user_id == user_id for r in records)
        assert list(records) == sorted(records, key=lambda r: (r.created_at, r.message_id))


def test_loader_rejects_a_broken_foreign_key(tmp_path) -> None:
    for name in INPUT_CSVS:
        (tmp_path / name).write_bytes((DATASET / name).read_bytes())
    rows = list(csv.DictReader((tmp_path / "messages.csv").open(encoding="utf-8")))
    rows[0]["user_id"] = "u_does_not_exist"
    with (tmp_path / "messages.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(DatasetError, match="not in users.csv"):
        load_dataset(tmp_path)


def test_render_evidence_uses_none_literal_and_no_spaces() -> None:
    cfg = RouterConfig().evidence
    assert render_evidence([], cfg) == "none"
    assert render_evidence(["a", "b"], cfg) == "a;b"


@pytest.mark.skipif(not OUTPUT.exists(), reason="no output.csv; run `python run.py route` first")
def test_output_csv_passes_the_submission_validator(dataset) -> None:
    history_ids = {r.message_id for records in dataset.history_by_user.values() for r in records}
    problems = validate_submission(OUTPUT, dataset.target_order, history_ids, RouterConfig().evidence)
    assert not problems, "submission invalid:\n" + "\n".join(problems)


@pytest.mark.skipif(not OUTPUT.exists(), reason="no output.csv")
def test_output_csv_shape_explicitly() -> None:
    with OUTPUT.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    assert tuple(header) == CONTRACT_COLUMNS
    assert len(header) == 6
    assert len(rows) == 110
    ids = [r[0] for r in rows]
    assert len(set(ids)) == 110
    allowed_actions = {a.value for a in Action}
    allowed_types = {t.value for t in MessageType}
    for row in rows:
        assert row[1] in allowed_actions
        assert row[2] in allowed_types
        assert 0.0 <= float(row[4]) <= 1.0


@pytest.mark.skipif(not OUTPUT.exists(), reason="no output.csv")
def test_output_row_order_matches_messages_csv(dataset) -> None:
    with OUTPUT.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        next(reader)
        ids = [r[0] for r in reader]
    assert ids == list(dataset.target_order)


def test_validator_catches_a_bad_action(tmp_path, dataset) -> None:
    bad = tmp_path / "bad.csv"
    with bad.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(CONTRACT_COLUMNS)
        for message_id in dataset.target_order:
            writer.writerow([message_id, "shout", "personal", "reason text", "0.5", "none"])
    problems = validate_submission(bad, dataset.target_order, set(), RouterConfig().evidence)
    assert any("action='shout'" in p for p in problems)


def test_validator_catches_an_unresolvable_evidence_id(tmp_path, dataset) -> None:
    bad = tmp_path / "bad.csv"
    with bad.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(CONTRACT_COLUMNS)
        for message_id in dataset.target_order:
            writer.writerow([message_id, "digest", "personal", "reason text", "0.5", "message_9999"])
    problems = validate_submission(bad, dataset.target_order, {"message_0001"}, RouterConfig().evidence)
    assert any("not in message_history" in p for p in problems)


def test_the_submission_path_is_not_relative_to_the_working_directory() -> None:
    """`--out` must default to <dataset>/output.csv, never to a cwd-relative path.

    A cwd-relative default silently creates a second `dataset/` directory under
    whatever directory the command was run from, writes the submission artifact
    into it, and leaves the real one stale — with no error at any point.
    """
    import main

    args = main.argparse.Namespace(dataset=Path("/tmp/some-dataset"), runs_dir=None, out=None)
    cfg = main._config(args)
    resolved = args.out if args.out is not None else cfg.dataset_root / "output.csv"
    assert resolved == Path("/tmp/some-dataset/output.csv")


def test_route_declares_no_default_output_path() -> None:
    """The default must be None so the dataset-relative fallback is what applies."""
    import main

    args = main.build_parser().parse_args(["route"])
    assert args.out is None
