"""Shared helpers for the structural lint tests.

Not a package: pytest imports it via ``conftest``'s ``sys.path`` entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = SUBMISSION_ROOT / "bow"
REPO_ROOT = SUBMISSION_ROOT.parent

SKIP_DIR_NAMES = {
    ".venv", "venv", "__pycache__", ".git", ".pytest_cache",
    ".ruff_cache", ".cache", ".mypy_cache", "node_modules",
}


def _skipped(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def package_modules() -> list[Path]:
    """Every .py file inside the bow package."""
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if not _skipped(p))


def submission_python_files() -> list[Path]:
    """Every .py file anywhere under the submission root."""
    return sorted(p for p in SUBMISSION_ROOT.rglob("*.py") if not _skipped(p))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(SUBMISSION_ROOT))
    except ValueError:
        return str(path)


def imported_names(tree: ast.Module) -> list[tuple[str, int]]:
    """Every module name this file imports, with its line number.

    ``from X import a`` records both ``X`` and ``X.a``. Without the second
    form, ``from bow import paths`` looks like an import of ``bow`` and slips
    past a rule that forbids importing ``bow.paths``.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                continue
            if node.module:
                found.append((node.module, node.lineno))
                found.extend(
                    (f"{node.module}.{alias.name}", node.lineno) for alias in node.names
                )
    return found
