"""CLI: report a run directory, or diff two of them.

Runs standalone against any past run directory. Imports nothing from the
pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from metrics.diff import RunsDifferError, assert_runs_identical, compute_diff
from metrics.loader import RunDirectoryError, load_run
from metrics.report import build_report, render_console, render_diff_console, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m metrics")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="compute the full report for one run directory")
    p_report.add_argument("run_dir", type=Path)
    p_report.add_argument("--json", action="store_true", help="print report.json instead of console")

    p_diff = sub.add_parser("diff", help="compare two run directories")
    p_diff.add_argument("baseline", type=Path)
    p_diff.add_argument("candidate", type=Path)
    p_diff.add_argument("--json", action="store_true")

    p_same = sub.add_parser("assert-identical", help="determinism gate")
    p_same.add_argument("baseline", type=Path)
    p_same.add_argument("candidate", type=Path)

    args = parser.parse_args(argv)

    try:
        if args.command == "report":
            run = load_run(args.run_dir)
            report = build_report(run)
            write_report(run, report)
            print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_console(report))
            return 0

        if args.command == "diff":
            baseline, candidate = load_run(args.baseline), load_run(args.candidate)
            result = compute_diff(baseline, candidate)
            print(json.dumps(result, indent=2, sort_keys=True) if args.json else render_diff_console(result))
            return 0

        baseline, candidate = load_run(args.baseline), load_run(args.candidate)
        try:
            assert_runs_identical(baseline, candidate)
        except RunsDifferError as exc:
            print(f"NOT IDENTICAL\n{exc}", file=sys.stderr)
            return 1
        print("IDENTICAL outside the volatile-field allowlist")
        return 0
    except RunDirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
