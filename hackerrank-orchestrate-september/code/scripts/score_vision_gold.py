#!/usr/bin/env python3
"""Score the vision path against the hand-derived gold set.

The gold set (`tests/fixtures/vision_gold.json`) records, per image, the figure
the document actually says was owed or paid, and the figure a naive extractor
that grabbed the largest or most total-looking number would have returned. The
second column is the point: a regression toward the naive answer is visible
rather than silent.

The runtime path cannot read this file — `tests/test_vision.py` asserts that no
module under `bow/` references it by name or by path. Only this scorer and the
tests do, and the scorer is the thing that must never be confused with the
pipeline. It calls the same `read_amount` the pipeline calls; it does not
reimplement it.

Reads are cached under `.cache/vision` by image id, so re-scoring after a run
costs nothing.

Usage:
    python scripts/score_vision_gold.py [--out PATH] [--no-cache]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.data.loaders import load_dataset  # noqa: E402
from bow.perception.resolve import resolve_blank_amounts  # noqa: E402
from bow.providers.sarvam import SarvamClient  # noqa: E402
from bow.settings import load_settings, load_thresholds  # noqa: E402

GOLD = _SUBMISSION_ROOT / "tests" / "fixtures" / "vision_gold.json"


def _same(a: str | Decimal | None, b: str) -> bool:
    """Numeric equality, so 4543 and 4543.00 are the same answer."""
    if a is None:
        return False
    try:
        return Decimal(str(a)) == Decimal(b)
    except InvalidOperation:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=paths.EVAL_DIR / "vision_run.json")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore the disk cache and pay for every read again")
    args = parser.parse_args()

    gold = json.loads(GOLD.read_text(encoding="utf-8"))["images"]
    settings = load_settings()
    thresholds = load_thresholds()
    dataset = load_dataset(paths.require_dataset_dir()).dataset

    client = SarvamClient(settings)
    started = time.monotonic()
    events, result = resolve_blank_amounts(
        dataset.events, dataset.images, client,
        image_path=paths.image_path,
        label_priority=thresholds.vision.label_priority,
        label_denylist=thresholds.vision.label_denylist,
        tolerance=Decimal(thresholds.vision.reconciliation_tolerance),
        cache_dir=None if args.no_cache else paths.CACHE_DIR,
    )
    wall = time.monotonic() - started

    by_event = {image.related_event_id: image for image in dataset.images}
    got: dict[str, str] = {}
    for event in events:
        image = by_event.get(event.event_id)
        if image is not None and image.image_id in gold:
            got[image.image_id] = None if event.amount is None else str(event.amount)

    records: dict[str, dict] = {}
    hits = {"sample": [0, 0], "eval": [0, 0]}
    naive_hits: list[str] = []
    misses: list[str] = []
    for image_id in sorted(gold):
        want = gold[image_id]
        mine = got.get(image_id)
        ok = _same(mine, want["correct"])
        naive = _same(mine, want["naive"])
        bucket = hits.setdefault(want["set"], [0, 0])
        bucket[1] += 1
        bucket[0] += int(ok)
        if not ok:
            misses.append(image_id)
        if naive:
            naive_hits.append(image_id)
        records[image_id] = {
            "got": mine, "want": want["correct"], "ok": ok,
            "path": result.paths.get(image_id),
            "set": want["set"], "naive_match": naive,
            "detail": result.detail.get(image_id),
        }
        print(f"{image_id} {want['set']:<7}{'OK ' if ok else 'MISS'} "
              f"got={str(mine):>12} want={want['correct']:>12} "
              f"path={result.paths.get(image_id, '-'):<22}"
              f"{'  NAIVE' if naive else ''}")

    total_ok = sum(v[0] for v in hits.values())
    total = sum(v[1] for v in hits.values())
    second = sum(1 for v in records.values() if v["path"] == "digitise_then_extract")
    print()
    print(f"GOLD {total_ok}/{total}  " + "  ".join(
        f"{name} {v[0]}/{v[1]}" for name, v in sorted(hits.items()) if v[1]
    ))
    print(f"second read used: {second}/{total}")
    print(f"unresolved: {len(result.unresolved)}")
    print(f"miss list: {misses or 'empty'}")
    print(f"matched the naive answer: {naive_hits or 'none'}")
    print(f"wall {wall:.0f}s  usage {json.dumps(client.usage.as_dict())}")
    client.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0 if total_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
