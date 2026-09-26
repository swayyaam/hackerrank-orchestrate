#!/usr/bin/env python3
"""How stable is a document read? Measure it, do not assume it.

`docs/LIMITATIONS.md` L9 records one image whose transcription varies between
calls. One unstable document is a provider limitation; several would be an
architecture problem, and the two call for different answers. This tells them
apart by reading every image N times with a fresh cache key per read.

Nothing here compares against the gold set. The question is whether the reads
agree WITH EACH OTHER — agreement is measurable without knowing the right
answer, and keeping the gold set out of it is what stops this becoming a search
for the run that scores best.

The Document AI endpoints take no seed, so there is no sampling parameter to
pin: `config/models.yaml` records `seed: null` for both vision roles because the
API has no such field, not because it was left unset.

Each read is written to `.cache/vision_stability/trial_<n>/<image_id>.json`, so
an interrupted probe resumes instead of paying twice.

Usage:
    python scripts/probe_vision_stability.py [--trials 5] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.data.loaders import load_dataset  # noqa: E402
from bow.perception.vision import read_amount  # noqa: E402
from bow.providers.sarvam import SarvamClient  # noqa: E402
from bow.settings import load_settings, load_thresholds  # noqa: E402


def _key(value) -> str | None:
    """Compare figures numerically: 4543 and 4543.00 are the same answer."""
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).normalize())
    except InvalidOperation:
        return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--out", type=Path,
                        default=paths.EVAL_DIR / "vision_stability.json")
    args = parser.parse_args()

    thresholds = load_thresholds().vision
    settings = load_settings()
    dataset = load_dataset(paths.require_dataset_dir()).dataset
    images = sorted({i.image_id for i in dataset.images})
    root = paths.CACHE_DIR / "vision_stability"

    client = SarvamClient(settings)
    started = time.monotonic()
    for trial in range(1, args.trials + 1):
        for image_id in images:
            destination = root / f"trial_{trial}" / f"{image_id}.json"
            if destination.is_file():
                continue
            outcome = read_amount(
                image_id, paths.image_path(image_id), client,
                label_priority=thresholds.label_priority,
                label_denylist=thresholds.label_denylist,
                tolerance=Decimal(thresholds.reconciliation_tolerance),
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps({
                "amount": None if outcome.amount is None else str(outcome.amount),
                "label": outcome.label,
                "path": outcome.path.value,
                "reconciled": outcome.reconciled,
                "detail": outcome.detail,
            }, indent=2), encoding="utf-8")
            print(f"  trial {trial} {image_id}: {outcome.amount} "
                  f"({'reconciled' if outcome.reconciled else 'not reconciled'})",
                  flush=True)
    wall = time.monotonic() - started

    report: dict[str, dict] = {}
    unstable: list[str] = []
    for image_id in images:
        reads = []
        for trial in range(1, args.trials + 1):
            path = root / f"trial_{trial}" / f"{image_id}.json"
            reads.append(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)
        values = [None if r is None else r["amount"] for r in reads]
        keys = [_key(v) for v in values]
        counts = Counter(k for k in keys if k is not None)
        modal, modal_n = (counts.most_common(1)[0] if counts else (None, 0))
        stable = len(set(keys)) == 1
        if not stable:
            unstable.append(image_id)
        report[image_id] = {
            "values": values,
            "agree": stable,
            "modal_value": modal,
            "modal_count": modal_n,
            "distinct": len(set(keys)),
            "reconciled_per_read": [None if r is None else r["reconciled"] for r in reads],
            "any_read_reconciled": any(r and r["reconciled"] for r in reads),
        }

    width = max(len(v or "") for r in report.values() for v in r["values"]) + 2
    print()
    print(f"{'image':<10}{'agree':<8}" + "".join(
        f"{'read '+str(i):>{width}}" for i in range(1, args.trials + 1)
    ) + f"{'modal':>{width}}{'  rec':>7}")
    for image_id, row in report.items():
        cells = "".join(f"{str(v):>{width}}" for v in row["values"])
        rec = sum(1 for x in row["reconciled_per_read"] if x)
        print(f"{image_id:<10}{('yes' if row['agree'] else 'NO'):<8}{cells}"
              f"{str(row['modal_value']):>{width}}{rec:>4}/{args.trials}")

    print()
    print(f"images read           : {len(images)} x {args.trials} trials")
    print(f"stable (all agree)    : {len(images) - len(unstable)}/{len(images)}")
    print(f"UNSTABLE              : {len(unstable)} -> {unstable or 'none'}")
    print(f"wall {wall:.0f}s  usage {json.dumps(client.usage.as_dict())}")
    client.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "trials": args.trials,
        "unstable": unstable,
        "images": report,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
