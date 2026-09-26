#!/usr/bin/env python3
"""Dump one document's raw transcription and everything derived from it.

Diagnostic only. It never writes the pipeline's amount cache, so running it
cannot change what a run ships; the image id is an argument, never a literal.

Prints the provider's blocks, the label/value pairs each pairing strategy
produced, the observation's labelled figures and line items, what the priority
list selects, and whether that selection reconciles — which is the chain you
need to see to tell a transcription failure from a pairing failure.

Usage:
    python scripts/dump_document_read.py --image <image_id> [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.perception.parsing import ParseError, reconciles, select_amount  # noqa: E402
from bow.perception.vision import build_observation  # noqa: E402
from bow.providers.sarvam import SarvamClient  # noqa: E402
from bow.settings import load_settings, load_thresholds  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    thresholds = load_thresholds().vision
    client = SarvamClient(load_settings())
    job = client.digitise(paths.image_path(args.image))
    client.close()
    payload = job.payload or {}
    print(f"job status: {job.status}")

    observation = build_observation(payload)

    print("=== blocks ===")
    for document in payload.get("documents") or []:
        for page in document.get("pages") or []:
            for block in sorted(page.get("blocks") or [],
                                key=lambda b: b.get("reading_order", 0)):
                text = (block.get("text") or "")
                print(f"-- reading_order={block.get('reading_order')} "
                      f"tag={block.get('tag')} coords={block.get('coordinates')}")
                print(text[:2000])

    print("\n=== labelled figures ===")
    for figure in observation.labelled_figures:
        print(f"  {figure.raw_value:>14}   <- {figure.label!r}")

    print("\n=== line items (what reconciliation sums) ===")
    total = Decimal()
    for item in observation.line_items:
        print(f"  {item.raw_amount:>14}   <- {item.description!r}")
    print(f"  line item count: {len(observation.line_items)}")

    print("\n=== selection ===")
    try:
        amount, label = select_amount(
            observation,
            label_priority=thresholds.label_priority,
            label_denylist=thresholds.label_denylist,
        )
        print(f"  selected {amount} on label {label!r}")
        ok, why = reconciles(
            amount, observation,
            tolerance=Decimal(thresholds.reconciliation_tolerance),
            from_line_item_sum=(label == "line_item_sum"),
        )
        print(f"  reconciles: {ok} ({why})")
    except ParseError as exc:
        print(f"  selection failed: {exc}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "payload": payload,
            "labelled_figures": [f.model_dump() for f in observation.labelled_figures],
            "line_items": [i.model_dump() for i in observation.line_items],
            "source_text": observation.source_text,
        }, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
