#!/usr/bin/env python3
"""Build `code.zip` at the run root, and refuse to build a bad one.

The archive is a deliverable, so producing it by hand is exactly the kind of
step that silently ships a `.venv`, a `__pycache__`, or a credential once and
nobody notices. This builds it deterministically and then re-opens it and checks
what actually went in — the check is on the bytes in the archive, not on the
intent of the exclusion list.

What is excluded, and why:
  runtime caches      .cache, .hypothesis, .pytest_cache, __pycache__ — generated
  environments        .venv, venv, node_modules — machine state
  the credential      .env — it lives outside code/ and must never appear
  editor debris       .DS_Store

Usage:
    python scripts/build_zip.py [--out PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402

#: Directory or file names that never enter the archive, at any depth.
EXCLUDED_NAMES = frozenset({
    ".cache", ".hypothesis", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "__pycache__", ".venv", "venv", "node_modules", ".env", ".DS_Store",
    ".git",
})

#: Same shape as the hygiene test's detector, kept here so the archive is
#: checked even when the test suite is not run.
KEY_SHAPED = re.compile(
    r"\b(?:sk|pk|api|key|tok|sec|bearer)[_\-]"
    r"(?=[A-Za-z0-9_\-]{16,}\b)(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{16,}\b"
    r"|\b(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9]{32,}\b",
)

TEXT_SUFFIXES = frozenset({
    ".py", ".yaml", ".yml", ".toml", ".md", ".txt", ".json", ".cfg", ".ini",
    ".csv", ".lock", ".sh",
})

#: Files that must be inside the archive for it to be a valid submission.
REQUIRED_IN_ARCHIVE = (
    "code/README.md",
    "code/main.py",
    "code/pyproject.toml",
    "code/evaluation/usage_report.md",
)


def selected_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(_SUBMISSION_ROOT.rglob("*")):
        if any(part in EXCLUDED_NAMES for part in path.relative_to(_SUBMISSION_ROOT).parts):
            continue
        if path.name in EXCLUDED_NAMES or not path.is_file():
            continue
        out.append(path)
    return out


def build(destination: Path, files: list[Path]) -> None:
    # Fixed compression and sorted order so two builds of the same tree agree.
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = Path(_SUBMISSION_ROOT.name) / path.relative_to(_SUBMISSION_ROOT)
            archive.write(path, arcname.as_posix())


def verify(destination: Path) -> list[str]:
    """Re-open the archive and check what is really in it."""
    problems: list[str] = []
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        for name in names:
            parts = Path(name).parts
            offending = set(parts) & EXCLUDED_NAMES
            if offending:
                problems.append(f"excluded entry present: {name}")
        for required in REQUIRED_IN_ARCHIVE:
            if required not in names:
                problems.append(f"missing required entry: {required}")
        for name in names:
            if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            body = archive.read(name).decode("utf-8", errors="ignore")
            if KEY_SHAPED.search(body):
                # Report the location only. Never echo the match.
                problems.append(f"key-shaped string inside the archive: {name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=paths.REPO_ROOT / "code.zip")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = selected_files()
    print(f"selected {len(files)} file(s) under {_SUBMISSION_ROOT}")
    if args.dry_run:
        for path in files:
            print("  ", path.relative_to(_SUBMISSION_ROOT))
        return 0

    build(args.out, files)
    problems = verify(args.out)
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size:,} bytes, {len(files)} entries)")
    if problems:
        args.out.unlink(missing_ok=True)
        print("ARCHIVE REJECTED AND DELETED:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("archive verified: no excluded entry, no key-shaped string, "
          "every required file present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
