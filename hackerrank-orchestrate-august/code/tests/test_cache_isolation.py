"""Cache reads and writes are confined to one module (CACHING.md §6)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from test_architecture import _is_source

PKG = Path(__file__).resolve().parents[1]
OWNER = PKG / "cache" / "store.py"

CACHE_DIR_LITERAL = re.compile(r'["\']\.?cache/')
CACHE_ATTRIBUTES = ("media_dir", "perception_dir")
"""What "touching the cache" means precisely: naming a cache *directory*.

Passing ``cfg.cache`` to ``store.read`` is not touching it -- that is the
documented way to use the store. A substring match on ".cache" would flag
``telemetry.cache_media`` and make the test meaningless."""


def _source_modules() -> list[Path]:
    return sorted(
        p
        for p in PKG.rglob("*.py")
        if p.name != "__init__.py" and _is_source(p, PKG) and p != OWNER
    )


def test_only_the_store_touches_the_cache_directory() -> None:
    """No module other than cache/store.py names a cache directory."""
    offenders: list[str] = []
    for path in _source_modules():
        if path.name == "config.py":
            continue  # declares the paths; never opens them
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if CACHE_DIR_LITERAL.search(line):
                offenders.append(f"{path.relative_to(PKG)}:{lineno}: cache directory literal")
            for attribute in CACHE_ATTRIBUTES:
                if f".{attribute}" in line:
                    offenders.append(f"{path.relative_to(PKG)}:{lineno}: .{attribute}")
    assert not offenders, "cache paths referenced outside cache/store.py:\n" + "\n".join(offenders)


def test_no_module_opens_a_file_under_the_cache_root() -> None:
    """Only the store performs file I/O against the cache."""
    offenders: list[str] = []
    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name not in {"open", "write_text", "write_bytes", "unlink", "rmtree"}:
                continue
            segment = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            if CACHE_DIR_LITERAL.search(segment) or any(
                f".{a}" in segment for a in CACHE_ATTRIBUTES
            ):
                offenders.append(f"{path.relative_to(PKG)}:{node.lineno}")
    assert not offenders, f"cache I/O outside the store: {offenders}"


def test_the_store_is_the_only_module_importing_itself_for_io() -> None:
    """Sanity: the owner exists and exposes the documented surface."""
    import cache.store as owner

    for symbol in ("read", "write", "media_key", "perception_key", "missing_keys", "MEDIA", "PERCEPTION"):
        assert hasattr(owner, symbol), f"cache.store is missing {symbol}"
