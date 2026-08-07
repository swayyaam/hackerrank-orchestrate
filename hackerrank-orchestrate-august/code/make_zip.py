#!/usr/bin/env python3
"""Build the submission archive: a zip containing this directory as `code/`.

    cd code && python make_zip.py [--out ../code.zip]

Excluded, always: virtualenvs, bytecode caches, test caches, run directories,
the dataset, and any real `.env`. The design documents under `docs/` ARE
included -- they live here rather than being copied in, so the archive cannot
ship a stale or missing copy.

`.cache/` is INCLUDED, deliberately. It is the model's recorded answers, and
without it the archive cannot be run at all by a reviewer without credentials:
`route` would have nothing to read and would need a full paid re-warm before
producing a single row. Shipping it is what makes the headline property true --
`python main.py route` reproduces the submitted CSV byte-for-byte, offline, with
zero network calls. It carries no credential: entries are keyed by a hash of the
inputs and hold only model output, and `verify()` below scans every archived
member's bytes to prove it.

Stdlib only, so it runs in a clean checkout before anything is installed.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ARCHIVE_ROOT = "code"

EXCLUDED_DIR_NAMES = frozenset(
    {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "runs",
        "dataset",
        ".git",
        ".idea",
        ".vscode",
        "build",
        "dist",
    }
)

EXCLUDED_FILE_NAMES = frozenset({".env", ".DS_Store", "Thumbs.db"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd", ".so", ".zip", ".log"})


def is_excluded(path: Path, root: Path) -> bool:
    """Whether a path is excluded from the archive."""
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
        return True
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    return path.suffix in EXCLUDED_SUFFIXES


def collect(root: Path) -> list[Path]:
    """Every file that belongs in the archive, sorted for a reproducible zip."""
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and not is_excluded(path, root)
    )


def build(root: Path, out_path: Path) -> tuple[int, int]:
    """Write the archive. Returns (file count, byte size)."""
    files = collect(root)
    if not files:
        raise SystemExit(f"error: nothing to archive under {root}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=str(Path(ARCHIVE_ROOT) / path.relative_to(root)))
    return len(files), out_path.stat().st_size


REQUIRED_MEMBERS = (
    f"{ARCHIVE_ROOT}/main.py",
    f"{ARCHIVE_ROOT}/pyproject.toml",
    f"{ARCHIVE_ROOT}/README.md",
    f"{ARCHIVE_ROOT}/config.py",
    f"{ARCHIVE_ROOT}/docs/SYSTEM_DESIGN.md",
    f"{ARCHIVE_ROOT}/docs/DECISION_ENGINE.md",
    f"{ARCHIVE_ROOT}/tests/test_architecture.py",
)


# --------------------------------------------------------------------------
# Secret scan
#
# Scans the BYTES OF EVERY ARCHIVED MEMBER, not the file list. Excluding `.env`
# by name proves only that one filename is absent; it says nothing about a key
# pasted into a doc, echoed into a cached model response, or committed to a
# config default. The check has to read what is actually being shipped, or it is
# the same class of defect as a metric that reads rule metadata instead of the
# sentence it claims to measure.
# --------------------------------------------------------------------------

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    # Provider-issued key shapes.
    ("anthropic api key", re.compile(rb"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai-style api key", re.compile(rb"\bsk-[A-Za-z0-9]{32,}")),
    ("sarvam subscription key", re.compile(rb"\bsk_[a-z0-9]{8,}_[A-Za-z0-9]{16,}")),
    ("aws access key id", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("bearer token", re.compile(rb"[Bb]earer\s+[A-Za-z0-9_\-\.]{24,}")),
    ("private key block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # A known credential variable assigned a non-empty value. `.env.example`
    # ships every one of these with an EMPTY value on purpose, so the pattern
    # requires at least one non-space character after the `=` and does not fire
    # on the template.
    (
        "populated credential assignment",
        re.compile(rb"(?:ANTHROPIC_API_KEY|SARVAM_API_KEY|OPENAI_API_KEY)\s*[=:]\s*\S"),
    ),
)

# Files whose *documented job* is to name credential variables. They are still
# scanned by every pattern above; the assignment pattern alone is waived,
# because ".env.example lists ANTHROPIC_API_KEY=" is the point of the file.
ASSIGNMENT_PATTERN_WAIVED = frozenset(
    {
        f"{ARCHIVE_ROOT}/.env.example",
        f"{ARCHIVE_ROOT}/README.md",
        f"{ARCHIVE_ROOT}/make_zip.py",
    }
)


def scan_for_secrets(out_path: Path) -> list[str]:
    """Read every member's bytes and report anything key-shaped. Empty is a pass."""
    findings: list[str] = []
    with zipfile.ZipFile(out_path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/"):
                continue
            data = archive.read(name)
            for label, pattern in SECRET_PATTERNS:
                if label == "populated credential assignment" and name in ASSIGNMENT_PATTERN_WAIVED:
                    continue
                match = pattern.search(data)
                if match is None:
                    continue
                # Report the location and the pattern, never the matched bytes.
                offset = match.start()
                findings.append(f"{label} in {name} at byte {offset}")
    return findings


def verify(out_path: Path) -> list[str]:
    """Check the archive contains what a reviewer needs and nothing excluded."""
    problems: list[str] = []
    with zipfile.ZipFile(out_path) as archive:
        names = set(archive.namelist())

    for member in REQUIRED_MEMBERS:
        if member not in names:
            problems.append(f"missing required member: {member}")

    if not any(name.startswith(f"{ARCHIVE_ROOT}/docs/") for name in names):
        problems.append("no design documents in the archive")

    # The offline-reproducibility claim in this module's docstring is only true
    # if the cache actually shipped. Assert it rather than trusting the walk.
    if not any(name.startswith(f"{ARCHIVE_ROOT}/.cache/perception/") for name in names):
        problems.append("no perception cache in the archive: `route` cannot run offline")
    if not any(name.startswith(f"{ARCHIVE_ROOT}/.cache/media/") for name in names):
        problems.append("no media cache in the archive: `route` cannot run offline")

    for name in sorted(names):
        parts = Path(name).parts
        if any(part in EXCLUDED_DIR_NAMES for part in parts):
            problems.append(f"excluded directory leaked into the archive: {name}")
        if Path(name).suffix in EXCLUDED_SUFFIXES:
            problems.append(f"excluded file type leaked into the archive: {name}")
        if Path(name).name in EXCLUDED_FILE_NAMES:
            problems.append(f"excluded file leaked into the archive: {name}")

    problems.extend(scan_for_secrets(out_path))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python make_zip.py")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("..") / "code.zip",
        help="output path for the archive (default: ../code.zip)",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    out_path = args.out if args.out.is_absolute() else (Path.cwd() / args.out).resolve()

    count, size = build(root, out_path)
    problems = verify(out_path)

    print(f"archive : {out_path}")
    print(f"files   : {count}")
    print(f"size    : {size / 1024:.1f} KiB")
    print(f"root    : {ARCHIVE_ROOT}/")
    print(
        "excluded: "
        + ", ".join(sorted({".venv", "__pycache__", ".pytest_cache", "runs", "dataset", ".env"}))
    )
    with zipfile.ZipFile(out_path) as archive:
        cached = sum(1 for n in archive.namelist() if f"{ARCHIVE_ROOT}/.cache/" in n)
    print(f"included: .cache/ ({cached} entries) — route runs offline with zero calls")

    if problems:
        print("\nVERIFICATION FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"\nverified: required members present, no excluded content leaked,\n"
        f"          secret scan clean across all {count} members "
        f"({len(SECRET_PATTERNS)} patterns)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
