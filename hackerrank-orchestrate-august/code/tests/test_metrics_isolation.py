"""Gate 1: the metrics engine is post-hoc.

It must reproduce its full report from a saved run directory in a process where
every pipeline module is unimportable. If it needed the pipeline in memory it
would be inline instrumentation wearing a package's clothes.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
FIXTURE = PKG / "tests" / "fixtures" / "run_fixture"

PIPELINE_MODULES = (
    "pipeline",
    "config",
    "schemas",
    "ingest.loader",
    "ingest.context",
    "policy.engine",
    "policy.rules",
    "features.assemble",
    "scoring.evidence",
    "scoring.confidence",
    "compose.reason",
    "emit.record",
    "emit.writer",
    "media.extract",
    "perception.cached",
    "perception.prompt",
    "perception.parse",
)

BLOCKER = """
import sys, json

BLOCKED = {blocked!r}
BLOCKED_ROOTS = {{name.split(".")[0] for name in BLOCKED}}

class Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED or any(name.startswith(b + ".") for b in BLOCKED):
            raise ImportError("pipeline module %r is unimportable in this process" % name)
        return None

sys.meta_path.insert(0, Blocker())

from metrics.loader import load_run
from metrics.report import build_report, render_console

run = load_run({run_dir!r})
report = build_report(run)
console = render_console(report)
print(json.dumps({{
    "label_free_rows": report["label_free"]["n_rows"],
    "labeled_rows": report["labeled"].get("n_rows", 0),
    "consistency_groups": report["consistency"].get("groups_total", 0),
    "console_lines": len(console.splitlines()),
    "loaded_pipeline_modules": sorted(
        m for m in sys.modules
        if m.split(".")[0] in BLOCKED_ROOTS and not m.startswith("metrics")
    ),
}}))
"""


def _latest_run() -> Path:
    runs = sorted((PKG.parent / "runs").glob("2026*"))
    if not runs:
        pytest.skip("no run directory available")
    return runs[-1]


@pytest.mark.parametrize("run_dir_factory", [lambda: FIXTURE, _latest_run], ids=["fixture", "real"])
def test_metrics_runs_with_pipeline_unimportable(run_dir_factory) -> None:
    run_dir = run_dir_factory()
    if not (run_dir / "manifest.json").exists():
        pytest.skip(f"{run_dir} is not a run directory")

    script = BLOCKER.format(blocked=set(PIPELINE_MODULES), run_dir=str(run_dir))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PKG,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"metrics failed with pipeline blocked:\n{proc.stderr}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["label_free_rows"] > 0
    assert payload["console_lines"] > 20
    assert payload["loaded_pipeline_modules"] == [], (
        f"metrics imported pipeline modules: {payload['loaded_pipeline_modules']}"
    )


def test_metrics_modules_do_not_import_pipeline() -> None:
    """Static check: no module under code/metrics/ names a pipeline module."""
    offenders: list[str] = []
    for path in (PKG / "metrics").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in PIPELINE_MODULES:
            if f"import {module}" in text or f"from {module}" in text:
                offenders.append(f"{path.name} -> {module}")
    assert not offenders, f"metrics imports the pipeline: {offenders}"


def test_pipeline_modules_do_not_import_metrics() -> None:
    """And the reverse: the pipeline never imports a metric.

    Parses the import statements rather than grepping the source, so a docstring
    that merely names the metrics CLI is not mistaken for a dependency.
    """
    import ast

    offenders: list[str] = []
    from test_architecture import _is_source

    for path in PKG.rglob("*.py"):
        parts = path.relative_to(PKG).parts
        # metrics/ is the thing itself; _is_source filters venvs, caches, tests
        if "metrics" in parts or not _is_source(path, PKG):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "metrics" or name.startswith("metrics."):
                    offenders.append(f"{path.name}:{node.lineno} -> {name}")
    assert not offenders, f"pipeline imports metrics: {offenders}"
