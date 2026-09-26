#!/usr/bin/env python3
"""Buy or Wait? — command-line entry point.

Running this file directly puts the submission root (``code/``) on
``sys.path`` so that ``import bow`` resolves without installing the package,
from any working directory.

This is a shell. It resolves configuration and exits. No pipeline logic lives
here or anywhere else yet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Prepend the submission root, not the repository root: putting the repository
# root on sys.path would make `code/` an implicit namespace package that
# shadows the standard library's `code` module.
_SUBMISSION_ROOT = Path(__file__).resolve().parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.settings import ConfigError, load_settings  # noqa: E402

PROG = "bow"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Buy or Wait? — deterministic financial solver with a narrow "
            "LLM perception layer."
        ),
        epilog=(
            "Configuration lives in config/models.yaml and "
            "config/decisions.yaml. The API credential is read from the .env "
            f"file outside the submission root; override its location with "
            f"{paths.ENV_PATH_ENV_VAR}, and the dataset location with "
            f"{paths.DATASET_DIR_ENV_VAR}."
        ),
    )
    parser.add_argument(
        "--config-check",
        action="store_true",
        help="resolve configuration, print a summary, and exit",
    )
    parser.add_argument(
        "--show-paths",
        action="store_true",
        help="print every resolved path and exit",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="run over sample_requests.csv instead of requests.csv",
    )
    parser.add_argument(
        "--no-perception",
        action="store_true",
        help="run the deterministic path only; no vision, no messages, no network",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where to write the predictions (default: output.csv at the run root)",
    )
    return parser


def _print_paths() -> None:
    for name in paths.__all__:
        value = getattr(paths, name)
        if isinstance(value, Path) or isinstance(value, str):
            print(f"{name:<20} {value}")


def _print_config_summary() -> None:
    settings = load_settings()
    provider = settings.models.provider
    print(f"provider            {provider.name}")
    print(f"base_url            {provider.base_url}")
    print(f"plan                {provider.plan}")
    print(f"max_tokens_ceiling  {provider.max_tokens_ceiling}")
    print(f"credential          {provider.api_key_env}=<redacted, "
          f"length {settings.api_key_length}>")
    print(f"roles               {len(settings.models.roles)}")
    for name in sorted(settings.models.roles):
        role = settings.models.roles[name]
        print(f"  {name:<22} {role.kind:<7} {role.model or '-':<14} {role.endpoint}")
    print(f"decisions           {len(settings.decisions.decisions)}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.show_paths:
            _print_paths()
            return 0
        if args.config_check:
            _print_config_summary()
            return 0
    except (ConfigError, paths.PathResolutionError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    return _run(args)


def _run(args: argparse.Namespace) -> int:
    """Load, normalize, project, forecast, decide, format, validate, write."""
    from bow.pipeline import run_pipeline, write_manifest, write_output
    from bow.validate import run_gates

    targets = "sample_requests" if args.sample else "requests"
    destination = args.output or (
        paths.OUTPUT_SAMPLE_PATH if args.sample else paths.OUTPUT_PATH
    )

    try:
        result, requests = run_pipeline(
            paths.require_dataset_dir(), targets=targets,
            perception=not args.no_perception,
        )
    except (ConfigError, paths.PathResolutionError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    write_output(result.rows, destination)
    # The full-dataset manifest is a deliverable: AGENTS.md 6.5 requires the
    # cost report to describe the run that produced output.csv. A sample run
    # must therefore never write over it.
    manifest_path = paths.EVAL_DIR / (
        "run_manifest_sample.json" if args.sample else "run_manifest.json"
    )
    write_manifest(result, manifest_path)

    # AGENTS.md 6.5: the cost report must describe the run that produced
    # output.csv, so it is regenerated from the manifest that run just wrote.
    # Only for a full-dataset run: a sample run must not overwrite the
    # deliverable with a 25-row figure.
    if not args.sample:
        from bow.settings import load_settings
        from bow.usage import write_usage_report

        report_path = write_usage_report(
            manifest_path,
            paths.CONFIG_DIR / "pricing.yaml",
            load_settings(),
            paths.EVAL_DIR / "usage_report.md",
        )
        print(f"{PROG}: usage report at {report_path}")

    expected = tuple(requests)
    failures = run_gates(destination, expected, requests)

    for stage in result.stages:
        print(f"{PROG}: {stage.name:<12} {stage.seconds:>7.2f}s")
    print(f"{PROG}: wrote {len(result.rows)} rows to {destination}")
    print(f"{PROG}: manifest at {paths.EVAL_DIR / 'run_manifest.json'}")
    for name, count in sorted(result.counts.items()):
        print(f"{PROG}:   {name}: {count}")
    if result.unresolved:
        print(f"{PROG}: {len(result.unresolved)} unresolved item(s); "
              f"rows still written and flagged in the manifest")

    if failures:
        print(f"{PROG}: OUTPUT GATE FAILED ({len(failures)}):", file=sys.stderr)
        for failure in failures[:20]:
            print(f"{PROG}:   {failure}", file=sys.stderr)
        return 1
    print(f"{PROG}: output gate passed (schema + invariants, checked on the written file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
