"""Architecture gates from ENGINEERING_CONVENTIONS section 7."""

from __future__ import annotations

import ast
import pathlib
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG  # paths are reported relative to the package root

CORPUS_IDENTIFIER = re.compile(
    r"\b(?:msg|sample_msg|message|u|group|business|img|vn)_\d+\b"
)
NETWORK_MODULES = {
    "requests", "httpx", "urllib", "urllib3", "socket", "aiohttp",
    "anthropic", "sarvamai", "openai",
}
"""Both provider SDKs are listed: the gate is "no network outside the client",
not "no Anthropic outside the client"."""
WALL_CLOCK_EXEMPT = {"logging_setup.py", "record.py", "report.py", "main.py"}
"""The stamping modules. logging_setup stamps log lines, emit/record stamps the
run id and manifest, metrics/report stamps generated_at_utc, and main stamps one
timestamp per warm pass so every entry it writes shares it. None is decision
logic -- media/extract.py takes its timestamp as a parameter for exactly this
reason."""
JUNK_DRAWERS = {"utils.py", "helpers.py", "common.py", "misc.py"}

NON_SOURCE_DIRS = frozenset(
    {".venv", "venv", "env", "__pycache__", ".pytest_cache", ".ruff_cache",
     ".mypy_cache", "build", "dist", "docs", "tests"}
)
"""Directories that are not first-party pipeline source.

`.venv` matters: the submission archive is installed with a virtualenv created
*inside* `code/`, so an unfiltered rglob would scan site-packages and report
third-party code as architecture violations.
"""


def _is_source(path: pathlib.Path, root: pathlib.Path) -> bool:
    return not any(part in NON_SOURCE_DIRS for part in path.relative_to(root).parts)


_LOCAL_ROOTS = frozenset(
    p.stem if p.is_file() else p.name
    for p in PKG.iterdir()
    if (p.is_file() and p.suffix == ".py")
    or (p.is_dir() and p.name not in NON_SOURCE_DIRS and not p.name.startswith("."))
)
"""Top-level module and package names inside the source root."""


def _modules() -> list[Path]:
    return sorted(
        p for p in PKG.rglob("*.py") if p.name != "__init__.py" and _is_source(p, PKG)
    )


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(PKG).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_corpus_literals() -> None:
    """G2: no identifier from dataset/ may appear in the package."""
    offenders: list[str] = []
    for path in _modules():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in CORPUS_IDENTIFIER.finditer(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {match.group(0)}")
    assert not offenders, "corpus identifiers in code/:\n" + "\n".join(offenders)


def test_no_network_outside_the_client() -> None:
    """Every module below perception/client.py must be testable offline."""
    offenders: list[str] = []
    for path in _modules():
        if path.name == "client.py":
            continue
        for imported in _imports(path):
            if imported.split(".")[0] in NETWORK_MODULES:
                offenders.append(f"{path.relative_to(REPO)} -> {imported}")
    assert not offenders, f"network imports outside the client: {offenders}"


def test_no_wall_clock_in_decision_logic() -> None:
    """Only the stamping modules may read the clock."""
    offenders: list[str] = []
    for path in _modules():
        if path.name in WALL_CLOCK_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in ("datetime.now(", "date.today(", "time.time("):
            if pattern in text:
                offenders.append(f"{path.relative_to(REPO)}: {pattern}")
    assert not offenders, f"wall-clock reads in decision logic: {offenders}"


def test_policy_imports_only_leaves() -> None:
    """policy/ takes typed values; it never fetches them."""
    forbidden = ("perception", "ingest", "media", "emit", "scoring")
    offenders: list[str] = []
    for path in (PKG / "policy").glob("*.py"):
        for imported in _imports(path):
            if imported.split(".")[0] in forbidden:
                offenders.append(f"{path.name} -> {imported}")
    assert not offenders, f"policy/ reaches outside its layer: {offenders}"


def test_no_import_cycles() -> None:
    graph: dict[str, set[str]] = {}
    for path in _modules():
        name = _module_name(path)
        graph[name] = {i for i in _imports(path) if i.split(".")[0] in _LOCAL_ROOTS}

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str, trail: list[str]) -> None:
        if node in done:
            return
        if node in visiting:
            raise AssertionError(f"import cycle: {' -> '.join([*trail, node])}")
        visiting.add(node)
        for target in sorted(graph.get(node, ())):
            if target in graph:
                walk(target, [*trail, node])
        visiting.discard(node)
        done.add(node)

    for module in sorted(graph):
        walk(module, [])


def test_no_junk_drawer_modules() -> None:
    offenders = [p.name for p in _modules() if p.name in JUNK_DRAWERS]
    assert not offenders, f"junk-drawer modules: {offenders}"


def test_no_bare_except() -> None:
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, f"bare except: {offenders}"


def test_no_threshold_literals_outside_config() -> None:
    """G3/G4: a threshold is a literal a value is COMPARED against.

    Scans comparison operands specifically, rather than every integer in the
    file: a slice bound, a rounding precision, or an enum ordinal does not
    change a routing decision, whereas `x > 5` does. This is the construct a
    corpus-fitted constant would take.

    Exempt: config.py (where thresholds belong), engagement.py (its ordinal
    score weights carry their rationale in place and are thresholded against
    each other, not against data), decode.py (magic-byte values and header
    offsets fixed by published container specifications -- JPEG, PNG, RIFF,
    MPEG -- which no corpus could have fitted), and metrics/ (reporting
    constants declared with their source at module scope).
    """
    allowed = {0, 1, -1, 0.0, 1.0}
    offenders: list[str] = []
    for path in _modules():
        if path.name in {"config.py", "engagement.py", "decode.py"} or "metrics" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in (node.left, *node.comparators):
                if (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, (int, float))
                    and not isinstance(operand.value, bool)
                    and operand.value not in allowed
                ):
                    offenders.append(f"{path.relative_to(REPO)}:{operand.lineno}: {operand.value}")
    assert not offenders, "threshold literals outside config.py:\n" + "\n".join(offenders)


def test_every_threat_event_is_an_enum_member() -> None:
    """No free-string event names."""
    from schemas import LogEvent, ThreatEvent

    known = {e.value for e in ThreatEvent} | {e.value for e in LogEvent}
    used: set[str] = set()
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        used.update(re.findall(r"(?:ThreatEvent|LogEvent)\.([A-Z_]+)", text))
    unknown = {name for name in used if name not in {e.name for e in ThreatEvent} | {e.name for e in LogEvent}}
    assert not unknown, f"event names not in an enum: {unknown}"
    assert known
