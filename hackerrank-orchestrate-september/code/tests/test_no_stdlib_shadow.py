"""No package or module under bow/ may shadow a standard-library module.

Python ships a stdlib module named ``code``; a top-level package by that name
would shadow it. ``bow`` exists to avoid exactly that class of problem, and
this test keeps it avoided as subpackages are added.
"""

from __future__ import annotations

import sys

from _treewalk import PACKAGE_ROOT, SUBMISSION_ROOT, rel

STDLIB = set(sys.stdlib_module_names)


def test_package_name_does_not_shadow_stdlib():
    assert PACKAGE_ROOT.name not in STDLIB, (
        f"the importable package is named {PACKAGE_ROOT.name!r}, "
        "which shadows a standard-library module"
    )


def test_no_subpackage_shadows_stdlib():
    offenders = sorted(
        p.name
        for p in PACKAGE_ROOT.rglob("*")
        if p.is_dir() and p.name != "__pycache__" and p.name in STDLIB
    )
    assert not offenders, f"subpackage name(s) shadow stdlib modules: {offenders}"


def test_no_module_shadows_stdlib():
    offenders = sorted(
        rel(p)
        for p in PACKAGE_ROOT.rglob("*.py")
        if p.stem != "__init__" and p.stem in STDLIB
    )
    assert not offenders, f"module name(s) shadow stdlib modules: {offenders}"


def test_sibling_directories_are_not_packages():
    """config/, prompts/, scripts/, tests/, docs/, evaluation/ stay non-packages.

    With the submission root on sys.path, an ``__init__.py`` in any of them
    would make it importable; a top-level ``tests`` package in particular is a
    known pytest failure mode.
    """
    offenders = []
    for name in ("config", "prompts", "scripts", "tests", "docs", "evaluation"):
        candidate = SUBMISSION_ROOT / name / "__init__.py"
        if candidate.exists():
            offenders.append(rel(candidate))
    assert not offenders, (
        "these directories must not become packages: " + ", ".join(offenders)
    )


def test_stdlib_code_module_still_resolves():
    """Importing the stdlib ``code`` module must not pick up the submission dir."""
    import code as stdlib_code

    origin = getattr(stdlib_code, "__file__", "") or ""
    assert "site-packages" not in origin
    assert str(SUBMISSION_ROOT) not in origin, (
        f"`import code` resolved to {origin!r} inside the submission root"
    )
    assert hasattr(stdlib_code, "interact"), "resolved `code` is not the stdlib module"
