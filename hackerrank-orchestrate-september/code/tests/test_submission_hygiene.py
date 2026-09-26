"""What may and may not exist inside the submission, and outside it.

code/ is zipped and graded. Two properties matter:

1. Nothing secret, machine-specific, or environmental ships inside it.
2. Nothing authored for this submission lives outside it, so the zip is
   complete on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _treewalk import REPO_ROOT, SUBMISSION_ROOT, rel

# Independent of any real key value.
KEY_SHAPED = re.compile(
    # A secret-ish prefix, a separator, then a long high-entropy run that
    # contains at least one digit. The digit requirement is what separates a
    # credential from an ordinary hyphenated identifier such as an HTTP header
    # name, which has the same prefix-and-length shape but no digits.
    r"\b(?:sk|pk|api|key|tok|sec|bearer)[_\-]"
    r"(?=[A-Za-z0-9_\-]{16,}\b)(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{16,}\b"
    # Or an unprefixed run that is long AND mixes case AND contains a digit.
    # Lowercase-and-digit hex (content hashes in a lockfile) is excluded by the
    # uppercase requirement.
    r"|\b(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9]{32,}\b",
)

FORBIDDEN_NAMES = {".env", ".venv", "venv", "__pycache__", ".pytest_cache",
                   ".mypy_cache", ".ruff_cache", "node_modules"}

#: Runtime caches live under the submission root but must not be shipped. They
#: are permitted to exist and excluded from the archive.
EXCLUDED_FROM_ARCHIVE = {".cache"}

TEXT_SUFFIXES = {
    ".py", ".yaml", ".yml", ".toml", ".md", ".txt", ".json", ".cfg", ".ini",
    ".csv", ".lock", ".sh",
}

SKIP_SCAN_DIRS = {".git", ".venv", "__pycache__", ".cache", ".pytest_cache"}

#: Shipped with the starter repository. Not ours, never edited.
PROVIDED_AT_REPO_ROOT = frozenset({
    "dataset", "problem_statement.md", "README.md", "AGENTS.md", "CLAUDE.md",
})

#: The submission itself, zipped as ``code.zip``.
SUBMISSION_AT_REPO_ROOT = frozenset({"code"})

#: The complete allowlist of authored artifacts permitted outside ``code/``.
#: Each earns its place by an external rule, not by convenience:
#:   output.csv  the README places the generated predictions at the run root
#:   code.zip    the archive of code/ is a submission deliverable
#:   log.txt     AGENTS.md §2 fixes it beside the top-level AGENTS.md
#: Nothing is added here because a file happened to land at the root.
AUTHORED_ALLOWLIST_AT_REPO_ROOT = frozenset({
    "output.csv", "code.zip", "log.txt",
})

#: Not authored for the submission and not shipped: the credential deliberately
#: outside the zip boundary, throwaway analysis, and local tooling state.
NON_SUBMISSION_AT_REPO_ROOT = frozenset({
    ".env", ".gitignore", "scratch", ".venv", ".git", ".DS_Store",
})

ALLOWED_AT_REPO_ROOT = (
    PROVIDED_AT_REPO_ROOT
    | SUBMISSION_AT_REPO_ROOT
    | AUTHORED_ALLOWLIST_AT_REPO_ROOT
    | NON_SUBMISSION_AT_REPO_ROOT
)

#: Exact header the organisers' template carries, and therefore the exact
#: header the graded file must carry, in this order.
def _in_development_tree() -> bool:
    """Is this the submission sitting inside its own repository?

    An unpacked `code.zip` sits beside no `dataset/`, no `.gitignore` and no
    generated `output.csv`. The checks below that assert about the surrounding
    repository are real checks HERE and meaningless THERE, so they skip rather
    than fail: a reviewer following the README must not be told something is
    broken when nothing is. In this repository the layout exists, so they run.
    """
    return (REPO_ROOT / "dataset").is_dir() and (REPO_ROOT / ".git").exists()


OUTPUT_COLUMNS = (
    "request_id,amount_safe_to_pay,affordability_status,"
    "recommended_payment_method,payment_plan,earliest_date_for_full_payment,"
    "spending_changes_needed,decision_explanation"
)


def test_no_forbidden_directory_or_file_under_submission():
    offenders = sorted(
        rel(p) for p in SUBMISSION_ROOT.rglob("*") if p.name in FORBIDDEN_NAMES
    )
    assert not offenders, (
        "these must never exist under code/ — it is what gets zipped:\n"
        + "\n".join(offenders)
        + "\n\nRun uv with UV_PROJECT_ENVIRONMENT pointing outside code/, and "
        "pytest with bytecode writing disabled (tests/conftest.py does this)."
    )


def test_no_key_shaped_string_under_submission():
    offenders = []
    for path in sorted(SUBMISSION_ROOT.rglob("*")):
        if any(part in SKIP_SCAN_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in KEY_SHAPED.finditer(text):
            line = text[: match.start()].count("\n") + 1
            # Report the location only. Never echo the match.
            offenders.append(f"{rel(path)}:{line}")
    assert not offenders, (
        "key-shaped string(s) found under code/:\n" + "\n".join(offenders)
    )


def test_lockfile_contains_no_external_or_machine_paths():
    """The lockfile must resolve inside the submission, not from this machine."""
    lockfile = SUBMISSION_ROOT / "uv.lock"
    if not lockfile.exists():
        return
    text = lockfile.read_text(encoding="utf-8", errors="ignore")
    offenders = []
    for pattern, why in (
        (re.compile(r'(?:path|directory)\s*=\s*"(/[^"]+)"'), "absolute path"),
        (re.compile(r'(?:path|directory)\s*=\s*"((?:\.\./)+[^"]*)"'), "escapes code/"),
        (re.compile(r'"([A-Za-z]:\\\\[^"]+)"'), "Windows absolute path"),
        (re.compile(r"/(?:Users|home)/[^\"\s]+"), "machine home directory"),
    ):
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"uv.lock:{line} {why}: {match.group(0)[:80]}")
    assert not offenders, "lockfile references paths outside code/:\n" + "\n".join(offenders)


def test_env_is_outside_the_submission_and_gitignored():
    if not _in_development_tree():
        pytest.skip("no repository around code/; this asserts about the "
                    "development tree, not the submission")
    env = REPO_ROOT / ".env"
    if env.exists():
        assert env.parent == REPO_ROOT, ".env must sit at the repository root"
        assert not (SUBMISSION_ROOT / ".env").exists(), ".env must not be inside code/"
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), ".gitignore is missing"
    entries = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}
    assert entries & {".env", "/.env", "*.env"}, ".env is not listed in .gitignore"


def test_nothing_authored_lives_outside_the_submission():
    if not _in_development_tree():
        pytest.skip("no repository around code/; this asserts about the "
                    "development tree, not the submission")
    offenders = sorted(
        p.name for p in REPO_ROOT.iterdir() if p.name not in ALLOWED_AT_REPO_ROOT
    )
    assert not offenders, (
        "everything authored for this submission belongs inside code/; "
        f"found at the repository root: {offenders}"
    )


def test_docs_moved_into_the_submission():
    if not _in_development_tree():
        pytest.skip("no repository around code/; this asserts about the "
                    "development tree, not the submission")
    assert (SUBMISSION_ROOT / "docs" / "DATA_REPORT.md").is_file(), (
        "docs/DATA_REPORT.md must live inside code/"
    )
    assert not (REPO_ROOT / "docs").exists(), (
        "the repository-root docs/ directory must not still exist"
    )


def test_required_submission_artifacts_are_present():
    required = [
        "pyproject.toml", "main.py", "README.md",
        "config/models.yaml", "config/decisions.yaml",
        "bow/paths.py", "bow/settings/loader.py",
        "bow/pipeline.py", "bow/engine/plans.py", "bow/engine/ranker.py",
        "bow/engine/policy.py", "bow/engine/changes.py",
        "bow/format/amounts.py", "bow/explain/template.py", "bow/validate/gate.py",
        "evaluation/run_manifest.json",
        "scripts/check_env.py", "scripts/probe_sarvam.py",
        "scripts/build_zip.py", "config/thresholds.yaml", "config/pricing.yaml",
        "bow/usage.py",
        "evaluation/usage_report.md", "evaluation/harness.py",
        "evaluation/ablations.py", "docs/INDEX.md",
        "scripts/score_vision_gold.py", "scripts/probe_vision_stability.py",
        "docs/ARCHITECTURE.md", "docs/DATA_REPORT.md",
        "docs/VARIABLE_SPEND_EXPERIMENT.md",
        "docs/PROVIDER_CAPABILITIES.md", "docs/LIMITATIONS.md",
        "docs/CAPACITY_FORENSICS.md",
    ]
    missing = [name for name in required if not (SUBMISSION_ROOT / name).exists()]
    assert not missing, f"missing submission artifact(s): {missing}"


def test_the_graded_output_lives_at_the_run_root_and_not_inside_the_submission():
    """One graded output.csv, at the run root. No stale copy inside the zip.

    The README is explicit: the blank ``dataset/output.csv`` is a reference
    template and the generated file must be the root-level ``output.csv``.
    A second copy under ``code/`` would ship in the zip and could disagree with
    the graded one, so its absence is asserted rather than tolerated.
    """
    if not _in_development_tree():
        pytest.skip("no repository around code/; this asserts about the "
                    "development tree, not the submission")
    graded = REPO_ROOT / "output.csv"
    assert graded.is_file(), (
        "the graded predictions must be written to <repo_root>/output.csv"
    )
    assert not (SUBMISSION_ROOT / "output.csv").exists(), (
        "code/output.csv must not exist: the graded file is at the run root, "
        "and a second copy inside the zip could disagree with it"
    )
    lines = graded.read_text(encoding="utf-8").splitlines()
    assert lines[0] == OUTPUT_COLUMNS, "output.csv header is not the template header"
    assert len(lines) > 1, "output.csv has a header but no rows"


def test_the_provided_template_is_never_written_to():
    """``dataset/`` is read-only. The template must still be blank."""
    if not _in_development_tree():
        pytest.skip("no repository around code/; this asserts about the "
                    "development tree, not the submission")
    template = REPO_ROOT / "dataset" / "output.csv"
    lines = template.read_text(encoding="utf-8").splitlines()
    assert lines[0] == OUTPUT_COLUMNS
    filled = [
        line for line in lines[1:]
        if line.strip() and line.split(",", 1)[1].strip(",").strip()
    ]
    assert not filled, (
        f"dataset/output.csv has been written into ({len(filled)} non-blank "
        "row(s)); it is the provided template and must stay blank"
    )


def test_output_path_resolves_outside_the_submission_root():
    """The constant, not just the file, points at the run root."""
    import sys

    if str(SUBMISSION_ROOT) not in sys.path:
        sys.path.insert(0, str(SUBMISSION_ROOT))
    from bow import paths

    assert paths.OUTPUT_PATH == REPO_ROOT / "output.csv"
    assert SUBMISSION_ROOT not in paths.OUTPUT_PATH.parents, (
        "OUTPUT_PATH must resolve outside code/"
    )
    assert paths.DATASET_DIR not in paths.OUTPUT_PATH.parents, (
        "OUTPUT_PATH must never resolve inside dataset/"
    )


def test_the_authored_allowlist_outside_the_submission_is_exactly_three_files():
    """Pin the allowlist so widening it is a deliberate, reviewed edit."""
    assert AUTHORED_ALLOWLIST_AT_REPO_ROOT == {"output.csv", "code.zip", "log.txt"}
    assert not (AUTHORED_ALLOWLIST_AT_REPO_ROOT & PROVIDED_AT_REPO_ROOT)
    assert not (AUTHORED_ALLOWLIST_AT_REPO_ROOT & NON_SUBMISSION_AT_REPO_ROOT)


def test_the_built_archive_carries_the_required_files_and_nothing_excluded():
    """If code.zip has been built, check the bytes that are actually in it.

    The exclusion list is intent; the archive is fact. This asserts on the
    archive, so a builder bug cannot ship a cache, an environment or a
    credential just because the list looked right.
    """
    import zipfile

    archive_path = REPO_ROOT / "code.zip"
    if not archive_path.is_file():
        pytest.skip("code.zip has not been built yet")

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "code/evaluation/usage_report.md" in names, (
            "AGENTS.md 6.5 requires evaluation/usage_report.md inside code.zip"
        )
        assert "code/README.md" in names and "code/main.py" in names

        banned = FORBIDDEN_NAMES | EXCLUDED_FROM_ARCHIVE | {".hypothesis", ".git"}
        offenders = [n for n in names if set(Path(n).parts) & banned]
        assert not offenders, f"excluded entries inside code.zip: {offenders[:10]}"

        leaked = []
        for name in names:
            if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            body = archive.read(name).decode("utf-8", errors="ignore")
            if KEY_SHAPED.search(body):
                leaked.append(name)  # location only, never the match
        assert not leaked, f"key-shaped string inside code.zip: {leaked}"

        assert not any(n.startswith("output.csv") or n == "code/output.csv"
                       for n in names), "the graded output.csv must not be in the zip"
