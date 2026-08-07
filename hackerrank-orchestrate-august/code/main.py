#!/usr/bin/env python3
"""THE canonical entry point for the Message Notification Router.

``AGENTS.md`` section 6.4 names ``code/main.py`` as the conventional Python
entry point, so this is it. There is no second entry point: the former root
``run.py`` was folded into this file.

Both invocations work, from the repository root::

    python main.py route            # package form
    python code/main.py route            # direct form (bootstrapped below)

Commands::

    route              route every message; zero network calls by default
    warm-media         populate the media cache once, permanently
    warm-perception    populate the perception cache once, for all 140 rows
    validate <csv>     check a written output.csv against the submission contract
    bank <run_dir>     copy a run's output.csv to submission/ as a banked fallback

Metrics are a separate package and a separate command, because they are
computed post hoc and never by the pipeline::

    cd code && python -m metrics report <run_dir>
    cd code && python -m metrics diff <baseline_run_dir> <candidate_run_dir>
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

# `code/` is the source root: put it on sys.path so the imports below resolve
# whether this file is run as `python main.py` from inside code/, or as
# `python code/main.py` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_module
from config import RouterConfig
from emit.writer import validate_submission
from errors import BudgetExhaustedError, PerceptionTransportError, RouterError
from cache import store
from ingest.context import build_context
from ingest.loader import load_dataset
from media import extract as media_extract
from schemas import CacheOutcome, MediaKind
from logging_setup import build_logger
from perception.cached import CachedPerceptionClient
from perception.client import build_media_client, build_perception_transport
from perception.prompt import new_nonce
from pipeline import run as run_pipeline

EXIT_OK = 0
EXIT_ROWS_FAILED = 1
EXIT_PREFLIGHT = 2
EXIT_ABORT = 3
EXIT_BUDGET = 4


def _now_utc() -> str:
    """One timestamp per invocation. main.py is the only stamping entry point,
    so every cache entry a single pass writes carries the same stamp and the
    modules below never read the clock."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _challenge_line() -> str:
    if config_module.CHALLENGE_END_UTC is None:
        return "Challenge end time: not configured"
    return f"Challenge end time: {config_module.CHALLENGE_END_UTC.isoformat()}"


def load_dotenv(path: Path) -> list[str]:
    """Populate ``os.environ`` from a ``KEY=VALUE`` file. Returns the names set.

    Only the entry point reads this file, so the package itself stays free of
    implicit configuration -- the two modules that read the environment still
    read only the environment. A real export always wins over the file, an
    absent file is not an error, and **no value is ever printed or returned**:
    the caller learns which names were set, never what they were set to.
    """
    if not path.exists():
        return []
    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if not name or not value or os.environ.get(name):
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


def _config(args: argparse.Namespace) -> RouterConfig:
    """Build the config from the global flags. One place, so every command agrees."""
    record = config_module.RecordConfig()
    if args.runs_dir is not None:
        record = replace(record, RUNS_DIR=args.runs_dir)
    return RouterConfig(
        dataset_root=args.dataset,
        model_id=config_module.load_model_id(),
        record=record,
    )


def cmd_route(args: argparse.Namespace) -> int:
    cfg = _config(args)
    try:
        config_module.validate(cfg)
    except config_module.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_ABORT

    try:
        dataset = load_dataset(cfg.dataset_root)
    except RouterError as exc:
        print(f"abort: {exc}", file=sys.stderr)
        return EXIT_ABORT

    if not args.allow_network:
        missing = _media_preflight(dataset, cfg)
        if missing:
            print(f"PREFLIGHT: {len(missing)} media file(s) are not in the cache.", file=sys.stderr)
            for media_id, reason in missing[: cfg.record.ERROR_LIST_TRUNCATION]:
                print(f"  {media_id}: {reason}", file=sys.stderr)
            print(
                "\nRows carrying these will route with media_failed and be reported as such.\n"
                "Run `python main.py warm-media` to populate. No network call was made.",
                file=sys.stderr,
            )

    run_dir_parent = cfg.record.RUNS_DIR
    run_dir_parent.mkdir(parents=True, exist_ok=True)
    log = build_logger(None if args.quiet_log else run_dir_parent / "events.jsonl")

    transport = None
    if args.allow_network:
        try:
            transport = build_perception_transport(cfg.model_id, cfg.limits, cfg.perception)
        except PerceptionTransportError as exc:
            print(f"cannot build the perception transport: {exc}", file=sys.stderr)
            return EXIT_ABORT

    client = CachedPerceptionClient(
        cfg,
        nonce=new_nonce(),
        transport=transport,
        log=log,
        created_at_utc=_now_utc(),
    )

    if transport is None:
        missing = _perception_preflight(dataset, cfg, client, log)
        if missing:
            print(
                f"PREFLIGHT: {len(missing)} of {len(dataset.targets) + len(dataset.eval_messages)} "
                "row(s) have no cached perception.",
                file=sys.stderr,
            )
            for message_id in missing[: cfg.record.ERROR_LIST_TRUNCATION]:
                print(f"  {message_id}", file=sys.stderr)
            if len(missing) > cfg.record.ERROR_LIST_TRUNCATION:
                print(f"  ... and {len(missing) - cfg.record.ERROR_LIST_TRUNCATION} more", file=sys.stderr)
            print(
                "\nRun `python main.py warm-perception --allow-network` to populate.\n"
                "No network call was made and no row was routed.",
                file=sys.stderr,
            )
            return EXIT_PREFLIGHT

    mode = "live" if transport is not None else "offline"
    # Default the artifact into the dataset directory rather than into the
    # working directory: `route --dataset ../dataset` must write the CSV beside
    # the inputs it read, not create a second `dataset/` under code/.
    out_path = args.out if args.out is not None else cfg.dataset_root / "output.csv"
    result = run_pipeline(dataset, out_path, client, cfg, log, mode=mode)

    # Move the shared event log into the run directory it belongs to.
    if not args.quiet_log:
        source = run_dir_parent / "events.jsonl"
        if source.exists():
            shutil.move(str(source), str(result.run_dir / "events.jsonl"))

    print(_challenge_line())
    print(
        f"run_id={result.run_id}  rows={result.rows_written}/{result.rows_expected}  "
        f"eval={result.eval_rows_written}/{result.eval_rows_expected}  "
        f"failures={len(result.failures)}"
    )
    print(f"run directory: {result.run_dir}")
    if result.failures:
        for failure in result.failures[:10]:
            print(f"  FAILED {failure.message_id}: {failure.error_type}", file=sys.stderr)
        return EXIT_ROWS_FAILED
    if result.rows_written != result.rows_expected:
        return EXIT_ROWS_FAILED
    return EXIT_OK


def _media_preflight(dataset, cfg: RouterConfig) -> list[tuple[str, str]]:
    """Media ids referenced by a routed row whose cache entry is absent.

    Reports rather than calls out: a "free" iteration that quietly costs money
    is worse than one that says what is missing.
    """
    referenced: dict[str, MediaKind] = {}
    for message in (*dataset.targets, *dataset.eval_messages):
        if message.media_id is not None and message.media_kind is not MediaKind.NONE:
            referenced[message.media_id] = message.media_kind

    missing: list[tuple[str, str]] = []
    for media_id, kind in sorted(referenced.items()):
        table = dataset.images if kind is MediaKind.IMAGE else dataset.voice_notes
        relative = table.get(media_id)
        if relative is None:
            missing.append((media_id, "no file path"))
            continue
        try:
            path = (cfg.dataset_root / relative).resolve()
            key = store.media_key(
                store.content_sha256(path),
                media_extract.MEDIA_EXTRACTION_VERSION,
                media_extract.extractor_material(kind, cfg),
                cfg.cache,
            )
        except OSError:
            missing.append((media_id, "file unreadable"))
            continue
        if not store.read(store.MEDIA, key, cfg.cache).hit:
            missing.append((media_id, "no cache entry"))
    return missing


def _perception_preflight(
    dataset, cfg: RouterConfig, client: CachedPerceptionClient, log
) -> list[str]:
    """message_ids with no cached perception, in dataset order.

    Builds each context exactly as routing would -- extraction included -- so
    the key checked here is the key routing will look up, not an approximation
    of it. Offline and free; it is the whole run minus the model call.
    """
    missing: list[str] = []
    for message in (*dataset.targets, *dataset.eval_messages):
        media, _outcome = media_extract.extract(
            message, cfg, media_paths=_media_paths_for(dataset, message), log=None
        )
        context = build_context(dataset, message.message_id, media)
        if not client.is_cached(context):
            missing.append(message.message_id)
    return missing


def _media_paths_for(dataset, message) -> dict[str, str]:
    return dict(dataset.images if message.media_kind is MediaKind.IMAGE else dataset.voice_notes)


def cmd_warm_perception(args: argparse.Namespace) -> int:
    """Populate the perception cache once. The only command that writes it.

    Idempotent: a second invocation makes zero calls and reports every row as a
    hit. Keyed on the exact bytes that would be sent, so a prompt edit misses
    every row and a PolicyConfig edit misses none.
    """
    cfg = _config(args)
    try:
        dataset = load_dataset(cfg.dataset_root)
    except RouterError as exc:
        print(f"abort: {exc}", file=sys.stderr)
        return EXIT_ABORT

    log = build_logger(None if args.quiet_log else cfg.record.RUNS_DIR / "events.jsonl")

    transport = None
    note = ""
    if args.allow_network:
        try:
            transport = build_perception_transport(cfg.model_id, cfg.limits, cfg.perception)
        except PerceptionTransportError as exc:
            print(f"cannot build the perception transport: {exc}", file=sys.stderr)
            return EXIT_ABORT
    else:
        note = "  (no --allow-network: reporting coverage only; no call was made)"

    client = CachedPerceptionClient(
        cfg, nonce=new_nonce(), transport=transport, log=log, created_at_utc=_now_utc()
    )

    rows = [*dataset.targets, *dataset.eval_messages]
    hits = written = 0
    failures: list[tuple[str, str]] = []
    for message in rows:
        media, _outcome = media_extract.extract(
            message, cfg, media_paths=_media_paths_for(dataset, message), log=log
        )
        context = build_context(dataset, message.message_id, media)
        if client.is_cached(context):
            hits += 1
            continue
        if transport is None:
            failures.append((message.message_id, "not cached"))
            continue
        try:
            client.perceive(context)
            written += 1
        except BudgetExhaustedError as exc:
            print(f"BUDGET: {exc}", file=sys.stderr)
            return EXIT_BUDGET
        except RouterError as exc:
            failures.append((message.message_id, type(exc).__name__))

    print(f"perception cache: {len(rows)} rows")
    print(f"  {hits} already cached   {written} written   {len(failures)} failed{note}")
    print(f"  calls made: {client.calls_made}   tokens in/out: "
          f"{client.tokens_input_total}/{client.tokens_output_total}")
    if failures:
        for message_id, reason in failures[: cfg.record.ERROR_LIST_TRUNCATION]:
            print(f"  {message_id}: {reason}", file=sys.stderr)
        if len(failures) > cfg.record.ERROR_LIST_TRUNCATION:
            print(f"  ... and {len(failures) - cfg.record.ERROR_LIST_TRUNCATION} more", file=sys.stderr)
        return EXIT_ROWS_FAILED
    return EXIT_OK


def cmd_warm_media(args: argparse.Namespace) -> int:
    """Populate the media cache once. The only command that writes media entries.

    Idempotent: a second invocation makes zero calls and reports every file as a
    hit. Content-addressed, so the same image behind two ids is analysed once.
    """
    cfg = _config(args)
    dataset = load_dataset(cfg.dataset_root)
    log = build_logger(None if args.quiet_log else cfg.record.RUNS_DIR / "events.jsonl")

    client = None
    credential_note = ""
    if args.allow_network:
        try:
            client = build_media_client(cfg.model_id, cfg.limits, cfg.extraction)
        except PerceptionTransportError as exc:
            print(f"cannot build the extraction client: {exc}", file=sys.stderr)
            return EXIT_ABORT
    else:
        credential_note = (
            "  (no --allow-network: decoding and caching container facts only; "
            "no extraction call was made)"
        )

    targets: list[tuple[str, str, MediaKind]] = [
        *((mid, path, MediaKind.IMAGE) for mid, path in sorted(dataset.images.items())),
        *((mid, path, MediaKind.VOICE) for mid, path in sorted(dataset.voice_notes.items())),
    ]

    now_utc = _now_utc()
    hits = written = 0
    failures: list[tuple[str, str]] = []
    containers: dict[str, int] = {}
    for media_id, relative, kind in targets:
        try:
            outcome, failure = media_extract.warm_one(media_id, relative, kind, cfg, client, now_utc, log)
        except RouterError as exc:
            failures.append((media_id, type(exc).__name__))
            continue
        if outcome is CacheOutcome.HIT:
            hits += 1
        else:
            written += 1
        if failure:
            failures.append((media_id, failure))

    for _mid, relative, _kind in targets:
        head = (cfg.dataset_root / relative).read_bytes()[:32]
        from media.decode import sniff_container

        container = sniff_container(head)
        containers[container] = containers.get(container, 0) + 1

    print(f"media cache: {store.stats(store.MEDIA, cfg.cache)['entries']} entries")
    print(f"  {len(targets)} files   {hits} already cached   {written} written{credential_note}")
    print("  containers detected from magic bytes: " + ", ".join(
        f"{name}={count}" for name, count in sorted(containers.items())
    ))
    if failures:
        by_reason: dict[str, int] = {}
        for _mid, reason in failures:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        print("  incomplete: " + ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = _config(args)
    dataset = load_dataset(cfg.dataset_root)
    history_ids = {r.message_id for records in dataset.history_by_user.values() for r in records}
    problems = validate_submission(args.csv, dataset.target_order, history_ids, cfg.evidence)
    if problems:
        print(f"SUBMISSION INVALID — {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_ROWS_FAILED
    print(
        f"SUBMISSION VALID  {args.csv}\n"
        f"  {len(dataset.target_order)} rows, six columns in contract order,\n"
        f"  every action and message_type allowed, confidence numeric in [0,1],\n"
        f"  every evidence id resolves in message_history."
    )
    return EXIT_OK


def cmd_bank(args: argparse.Namespace) -> int:
    cfg = _config(args)
    dest_dir = cfg.record.SUBMISSION_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "baseline_output.csv"
    if dest.exists() and not args.force:
        print(f"{dest} already exists and is never overwritten by a later phase.", file=sys.stderr)
        return EXIT_ROWS_FAILED
    shutil.copy2(args.run_dir / "output.csv", dest)
    report = args.run_dir / "metrics" / "report.json"
    if report.exists():
        shutil.copy2(report, dest_dir / "baseline_metrics_report.json")
    console = args.run_dir / "metrics" / "report.txt"
    if console.exists():
        shutil.copy2(console, dest_dir / "baseline_metrics_report.txt")
    print(f"banked {dest} (+ metrics report) from {args.run_dir}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separated from execution so a test can inspect a default
    without running a command."""
    parser = argparse.ArgumentParser(prog="python main.py")
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="where run directories are written (default: ./runs, relative to the "
        "working directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_route = sub.add_parser("route", help="route every message (offline by default: reads both caches, calls nothing)")
    p_route.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the submission CSV (default: <dataset>/output.csv, so it "
        "follows --dataset instead of the working directory)",
    )
    p_route.add_argument("--quiet-log", action="store_true")
    p_route.add_argument(
        "--allow-network",
        action="store_true",
        help="permit live calls; without it the run is offline and never calls out",
    )
    p_route.set_defaults(func=cmd_route)

    p_warm = sub.add_parser("warm-media", help="populate the media cache once (permanent)")
    p_warm.add_argument("--quiet-log", action="store_true")
    p_warm.add_argument(
        "--allow-network",
        action="store_true",
        help="permit extraction calls; without it only container facts are cached",
    )
    p_warm.set_defaults(func=cmd_warm_media)

    p_warm_perception = sub.add_parser(
        "warm-perception", help="populate the perception cache once (140 rows)"
    )
    p_warm_perception.add_argument("--quiet-log", action="store_true")
    p_warm_perception.add_argument(
        "--allow-network",
        action="store_true",
        help="permit routing calls; without it the command only reports coverage",
    )
    p_warm_perception.set_defaults(func=cmd_warm_perception)

    p_validate = sub.add_parser("validate", help="check an output.csv against the contract")
    p_validate.add_argument("csv", type=Path, nargs="?", default=Path("dataset/output.csv"))
    p_validate.set_defaults(func=cmd_validate)

    p_bank = sub.add_parser("bank", help="copy a run's output.csv to submission/ as a fallback")
    p_bank.add_argument("run_dir", type=Path)
    p_bank.add_argument("--force", action="store_true")
    p_bank.set_defaults(func=cmd_bank)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(Path(__file__).resolve().parent / ".env")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
