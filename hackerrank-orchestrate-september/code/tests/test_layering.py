"""Dependency rules between packages.

The engine consuming a frozen ledger and nothing else is what makes the whole
system testable with zero API calls. These tests hold that line.
"""

from __future__ import annotations

import pytest

from _treewalk import PACKAGE_ROOT, imported_names, package_modules, parse, rel

PACKAGE = "bow"

# subpackage -> the internal subpackages it is allowed to import.
ALLOWED: dict[str, set[str]] = {
    "contracts": set(),
    "engine": {"contracts"},
    "format": {"contracts"},
    # explain/ renders prose containing numbers, so it needs the number
    # formatters. format/ is itself pure and contracts-only, so this does not
    # widen what explain/ can reach.
    "explain": {"contracts", "providers", "format"},
}

# Packages that must stay free of I/O, clocks, randomness, and the network:
# same input, same output, always.
PURE = {"engine", "format"}
FORBIDDEN_IN_PURE = {
    "os", "io", "sys", "pathlib", "open", "socket", "time", "datetime.datetime",
    "random", "secrets", "requests", "httpx", "urllib", "urllib.request",
    "subprocess", "threading", "asyncio", "sqlite3", "pickle", "shutil", "uuid",
}
# `datetime` is allowed — dates are the domain — but reading the clock is not.
# Matched against the dotted call expression's suffix, so `datetime.now()`,
# `datetime.datetime.now()` and `dt.date.today()` are all caught.
FORBIDDEN_CLOCK_CALLS = (
    "datetime.now", "datetime.today", "datetime.utcnow", "datetime.now_utc",
    "date.today", "time.time", "time.monotonic", "time.perf_counter",
    "random.random", "random.choice", "random.shuffle", "random.randint",
    "uuid.uuid1", "uuid.uuid4", "os.urandom", "secrets.token_hex",
    "Path.read_text", "Path.write_text", "Path.open",
)


def subpackage_of(path):
    parts = path.relative_to(PACKAGE_ROOT).parts
    return parts[0] if len(parts) > 1 else ""


def internal_subpackage(module: str) -> str | None:
    if module == PACKAGE or module.startswith(f"{PACKAGE}."):
        tail = module[len(PACKAGE) :].lstrip(".")
        return tail.split(".", 1)[0] if tail else ""
    return None


@pytest.mark.parametrize("path", package_modules(), ids=rel)
def test_subpackage_import_rules(path):
    owner = subpackage_of(path)
    if owner not in ALLOWED:
        return
    # Deliberately NOT including "paths": contracts/ imports nothing internal,
    # and engine/ and format/ are pure, so none of them has any business
    # resolving a filesystem path.
    permitted = ALLOWED[owner] | {owner, ""}
    violations = []
    for module, lineno in imported_names(parse(path)):
        target = internal_subpackage(module)
        if target is None:
            continue
        if target not in permitted:
            violations.append(f"{rel(path)}:{lineno} imports {PACKAGE}.{target}")
    assert not violations, (
        f"bow/{owner}/ may import only {sorted(permitted - {''})}; found:\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize("path", package_modules(), ids=rel)
def test_pure_packages_do_no_io(path):
    if subpackage_of(path) not in PURE:
        return
    tree = parse(path)
    violations = []
    for module, lineno in imported_names(tree):
        root = module.split(".", 1)[0]
        if root in FORBIDDEN_IN_PURE or module in FORBIDDEN_IN_PURE:
            violations.append(f"{rel(path)}:{lineno} imports {module!r}")
    import ast as _ast

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        if isinstance(func, _ast.Name) and func.id in {"open", "input", "print"}:
            violations.append(f"{rel(path)}:{node.lineno} calls {func.id}()")
            continue
        if not isinstance(func, _ast.Attribute):
            continue
        # Unparse the whole dotted chain so `datetime.datetime.now` is seen.
        try:
            dotted = _ast.unparse(func)
        except Exception:  # pragma: no cover - defensive
            continue
        for forbidden in FORBIDDEN_CLOCK_CALLS:
            if dotted == forbidden or dotted.endswith("." + forbidden):
                violations.append(f"{rel(path)}:{node.lineno} calls {dotted}()")
                break
    assert not violations, (
        "bow/engine/ and bow/format/ must be deterministic: no I/O, no clock "
        "reads, no randomness, no network. Found:\n" + "\n".join(violations)
    )


def test_only_providers_makes_network_calls():
    """Network client libraries belong to bow/providers/ alone."""
    network = {"httpx", "requests", "urllib", "aiohttp", "socket", "http"}
    violations = []
    for path in package_modules():
        if subpackage_of(path) == "providers":
            continue
        for module, lineno in imported_names(parse(path)):
            if module.split(".", 1)[0] in network:
                violations.append(f"{rel(path)}:{lineno} imports {module!r}")
    assert not violations, (
        "only bow/providers/ may make network calls; found:\n" + "\n".join(violations)
    )


def test_layering_rules_cover_the_tree():
    """Guard against a subpackage being added without a rule for it."""
    present = {p.name for p in PACKAGE_ROOT.iterdir() if p.is_dir() and p.name != "__pycache__"}
    known = {
        "contracts", "data", "engine", "format", "providers",
        "perception", "agent", "explain", "validate", "settings",
    }
    assert present <= known, (
        f"new subpackage(s) {sorted(present - known)} have no layering rule. "
        "Add them to ALLOWED/PURE or to this list deliberately."
    )
