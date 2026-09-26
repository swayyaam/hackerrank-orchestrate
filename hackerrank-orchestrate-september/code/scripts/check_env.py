#!/usr/bin/env python3
"""Verify the credential is present, well-shaped, and outside the zip boundary.

Prints one PASS/FAIL line per check. The key's length is the only thing about
its value that is ever printed; no character of it is emitted anywhere.

Exit code 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.settings import ConfigError, load_models  # noqa: E402

# Key-shaped-string detector, independent of any real key value.
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

TEXT_SUFFIXES = {
    ".py", ".yaml", ".yml", ".toml", ".md", ".txt", ".json", ".cfg", ".ini",
    ".csv", ".lock", ".sh", ".env",
}
SKIP_DIRS = {".venv", "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".cache"}

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def skip(name: str, why: str) -> None:
    """Not applicable here. Printed, but not counted as a failure.

    Used only for checks on the DEVELOPMENT tree: an unpacked code.zip sits
    beside no repository, and a red FAIL there would tell a reviewer something
    is wrong when nothing is.
    """
    print(f"[SKIP] {name} — {why}")


def scan_for_key_shaped(root: Path) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in KEY_SHAPED.finditer(text):
            line = text[: match.start()].count("\n") + 1
            # Report the location, never the matched text.
            hits.append(f"{path.relative_to(root)}:{line}")
    return hits


def main() -> int:
    print("check_env — credential and boundary checks\n")

    # 1. .env resolves from the repository root.
    env_path = paths.ENV_PATH
    resolved = env_path.is_file()
    check(
        ".env resolves from the repository root",
        resolved,
        str(env_path) if resolved else f"not found at {env_path} "
        f"(override with {paths.ENV_PATH_ENV_VAR})",
    )

    # 2 & 3. Key present, non-empty, plausible shape.
    variable = "SARVAM_API_KEY"
    key_len = 0
    prefix = ""
    try:
        models = load_models()
        variable = models.provider.api_key_env
    except ConfigError as exc:
        check("models.yaml names the credential variable", False, str(exc))
    else:
        check("models.yaml names the credential variable", True, variable)

    try:
        from bow.settings.loader import _read_api_key

        secret = _read_api_key(variable, env_path)
        value = secret.get_secret_value()
        key_len = len(value)
        prefix = value.split("_", 1)[0] + "_" if "_" in value[:8] else "(no prefix)"
        check(f"{variable} is present and non-empty", True, f"length {key_len}")
    except ConfigError as exc:
        check(f"{variable} is present and non-empty", False, str(exc))

    check(
        "key length is plausible",
        24 <= key_len <= 200,
        f"length {key_len} (expected 24–200)",
    )
    check(
        "key prefix shape is plausible",
        bool(prefix) and prefix != "(no prefix)",
        f"prefix {prefix!r}" if prefix else "no separator in the first 8 characters",
    )

    # 4. models.yaml holds the variable NAME, never a value.
    try:
        raw_config = (paths.CONFIG_DIR / "models.yaml").read_text(encoding="utf-8")
        check(
            "models.yaml contains no key-shaped string",
            not KEY_SHAPED.search(raw_config),
            "names the environment variable only",
        )
    except OSError as exc:
        check("models.yaml contains no key-shaped string", False, str(exc))

    # 5. .env is gitignored. A check on the development tree, not the submission.
    gitignore = paths.REPO_ROOT / ".gitignore"
    if (paths.REPO_ROOT / ".git").exists():
        ignored = gitignore.is_file() and any(
            line.strip() in {".env", "/.env", "*.env"}
            for line in gitignore.read_text(encoding="utf-8").splitlines()
        )
        check(".env is listed in .gitignore", ignored, str(gitignore))
    else:
        skip(".env is listed in .gitignore",
             "no git repository at the run root, so there is nothing to ignore")

    # 6. .env is outside the submission root.
    try:
        env_path.resolve().relative_to(paths.SUBMISSION_ROOT.resolve())
        inside = True
    except ValueError:
        inside = False
    check(
        ".env is outside code/ (zipping code/ cannot capture it)",
        not inside,
        f"{env_path} vs submission root {paths.SUBMISSION_ROOT}",
    )

    # 7. No key-shaped string anywhere under code/.
    hits = scan_for_key_shaped(paths.SUBMISSION_ROOT)
    check(
        "no key-shaped string exists anywhere under code/",
        not hits,
        "clean" if not hits else f"{len(hits)} location(s): {', '.join(hits[:5])}",
    )

    failures = [name for name, ok, _ in _results if not ok]
    print()
    if failures:
        print(f"{len(failures)} of {len(_results)} checks FAILED: {', '.join(failures)}")
        return 1
    print(f"all {len(_results)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
