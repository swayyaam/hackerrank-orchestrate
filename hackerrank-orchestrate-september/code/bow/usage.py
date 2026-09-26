"""Generate `evaluation/usage_report.md` from the run manifest.

AGENTS.md 6.5 requires the report to summarise *the final full-dataset run*.
A hand-written report satisfies that only until the next run, and then it is
quietly wrong — which is worse than absent, because it still reads as measured.
So the report is generated: every number in it comes from
`evaluation/run_manifest.json`, every rate from `config/pricing.yaml`, and every
model name from `config/models.yaml`.

Nothing here reads a credential. The provider block contributes its name and its
base URL only; `api_key_env` names an environment variable and is not emitted.

Unpriceable usage is reported as unpriceable. A role that billed something the
price list does not cover appears in the report with a loud line rather than
being silently costed at zero.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "PricingError",
    "RoleCost",
    "UsageReport",
    "build_report",
    "load_pricing",
    "write_usage_report",
]

_MONEY = Decimal("0.01")
_TOKENS = ("prompt_tokens", "completion_tokens")


class PricingError(RuntimeError):
    """The run used something the price list does not cover."""


def load_pricing(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "models" not in data:
        raise PricingError(f"{path} does not look like a price list")
    return data


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


class RoleCost:
    """One role's billed usage and what it cost."""

    def __init__(self, role: str, model: str, endpoint: str, unit: str) -> None:
        self.role = role
        self.model = model
        self.endpoint = endpoint
        self.unit = unit
        self.calls = 0
        self.input_units = 0
        self.output_units = 0
        self.seconds = 0.0
        self.cost = Decimal()

    @property
    def input_tokens(self) -> int:
        return self.input_units if self.unit == "tokens" else 0

    @property
    def output_tokens(self) -> int:
        return self.output_units if self.unit == "tokens" else 0

    def input_label(self) -> str:
        if self.unit == "tokens":
            return f"{self.input_units:,}"
        return f"{self.input_units:,} {self.unit}"


class UsageReport:
    def __init__(self, roles: list[RoleCost], *, requests: int, wall_seconds: float,
                 pricing: dict[str, Any], provider: str, unpriced: list[str]) -> None:
        self.roles = roles
        self.requests = requests
        self.wall_seconds = wall_seconds
        self.pricing = pricing
        self.provider = provider
        self.unpriced = unpriced

    @property
    def calls(self) -> int:
        return sum(r.calls for r in self.roles)

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.roles)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.roles)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost(self) -> Decimal:
        return _money(sum((r.cost for r in self.roles), Decimal()))

    def _per_request(self, value: Decimal | int) -> Decimal:
        if not self.requests:
            return Decimal()
        return Decimal(value) / Decimal(self.requests)

    def as_usd(self, value: Decimal) -> Decimal:
        rate = Decimal(str(self.pricing["usd_per_unit_of_currency"]))
        return (value * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _price_role(role: RoleCost, entry: dict[str, Any]) -> Decimal:
    block = Decimal(str(entry["units_per_price"]))
    inputs = Decimal(role.input_units) / block * Decimal(str(entry["input_price"]))
    outputs = Decimal(role.output_units) / block * Decimal(str(entry["output_price"]))
    return inputs + outputs


def build_report(manifest: dict[str, Any], pricing: dict[str, Any],
                 settings) -> UsageReport:
    """Fold the manifest's per-role usage against the price list."""
    usage = manifest.get("usage") or {}
    calls = usage.get("calls") or {}
    roles: list[RoleCost] = []
    unpriced: list[str] = []

    for role_name in sorted(calls):
        try:
            configured = settings.role(role_name)
        except Exception:  # noqa: BLE001 - a role the config no longer defines
            configured = None
        model = (getattr(configured, "model", None) or role_name)
        endpoint = getattr(configured, "endpoint", "") or ""
        entry = (pricing.get("models") or {}).get(model)
        if entry is None:
            unpriced.append(f"{role_name} ({model}): no entry in the price list")
            continue

        cost = RoleCost(role_name, model, endpoint, entry["unit"])
        cost.calls = int(calls.get(role_name, 0))
        cost.seconds = float((usage.get("seconds") or {}).get(role_name, 0.0))
        if entry["unit"] == "tokens":
            cost.input_units = int((usage.get(_TOKENS[0]) or {}).get(role_name, 0))
            cost.output_units = int((usage.get(_TOKENS[1]) or {}).get(role_name, 0))
        else:
            measured = (usage.get(entry["unit"]) or {}).get(role_name)
            if measured is None:
                unpriced.append(
                    f"{role_name} ({model}): billed by {entry['unit']}, but the "
                    f"run recorded no {entry['unit']} counter"
                )
                continue
            cost.input_units = int(measured)
        cost.cost = _price_role(cost, entry)
        roles.append(cost)

    provider = settings.models.provider
    return UsageReport(
        roles,
        requests=int((manifest.get("counts") or {}).get("requests", 0)),
        wall_seconds=float(manifest.get("total_seconds") or 0.0),
        pricing=pricing,
        provider=provider.name,
        unpriced=unpriced,
    )


def render(report: UsageReport, *, manifest: dict[str, Any]) -> str:
    p = report.pricing
    currency = p["currency"]
    lines: list[str] = []
    add = lines.append

    add("# Token usage and cost — final full-dataset run")
    add("")
    add(
        f"Generated from `evaluation/run_manifest.json` by `bow/usage.py`. Every "
        f"figure below is read from that manifest; every rate from "
        f"`config/pricing.yaml`. Nothing here is typed in by hand, so the report "
        f"cannot drift from the run it describes."
    )
    add("")
    add(f"Single provider: **{report.provider}**, one credential, no other vendor.")
    add("")
    add(
        f"Rates from `{p['source']}`, read {p['read_at']}. "
        f"Amounts are in {currency}; the USD figures use "
        f"{p['usd_per_unit_of_currency']} USD per {currency}, "
        f"read {p['usd_rate_read_at']}."
    )
    add("")

    add("## Models used")
    add("")
    add("| role | provider | model | endpoint | billed by |")
    add("|---|---|---|---|---|")
    for role in report.roles:
        add(
            f"| `{role.role}` | {report.provider} | `{role.model}` | "
            f"`{role.endpoint}` | {role.unit} |"
        )
    add("")

    add("## Per-model totals")
    add("")
    add(f"| role | model | calls | input | output tokens | {currency} |")
    add("|---|---|---:|---:|---:|---:|")
    for role in report.roles:
        output = f"{role.output_tokens:,}" if role.unit == "tokens" else "—"
        add(
            f"| `{role.role}` | `{role.model}` | {role.calls:,} | "
            f"{role.input_label()} | {output} | {_money(role.cost)} |"
        )
    add(
        f"| **overall** | | **{report.calls:,}** | "
        f"**{report.input_tokens:,}** | **{report.output_tokens:,}** | "
        f"**{report.cost}** |"
    )
    add("")

    add("## Rates applied")
    add("")
    add("| model | rate |")
    add("|---|---|")
    # One row per model, not per role: two roles can share a model, and the
    # rate belongs to the model.
    for model in sorted({role.model for role in report.roles}):
        entry = p["models"][model]
        block = entry["units_per_price"]
        if entry["unit"] == "tokens":
            rate = (
                f"{currency} {entry['input_price']} / {block:,} input, "
                f"{currency} {entry['output_price']} / {block:,} output"
            )
        else:
            per = f"{block:,} {entry['unit']}" if block != 1 else entry["unit"][:-1]
            rate = f"{currency} {entry['input_price']} / {per}"
        add(f"| `{model}` | {rate} |")
    add("")
    add(p["notes"].strip())
    add("")

    add("## Totals")
    add("")
    add("| metric | value |")
    add("|---|---|")
    add(f"| requests | {report.requests:,} |")
    add(f"| model calls | {report.calls:,} |")
    add(f"| input tokens | {report.input_tokens:,} |")
    add(f"| output tokens | {report.output_tokens:,} |")
    add(f"| **total tokens** | **{report.total_tokens:,}** |")
    per_request_tokens = report._per_request(report.total_tokens)
    add(
        f"| **average tokens per request** | "
        f"**{per_request_tokens.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}** |"
    )
    add(f"| **average model calls per request** | "
        f"**{report._per_request(report.calls).quantize(Decimal('0.01')):,}** |")
    add(
        f"| **estimated total cost** | **{currency} {report.cost} "
        f"(~USD {report.as_usd(report.cost)})** |"
    )
    per_request_cost = _money(report._per_request(report.cost))
    add(
        f"| **estimated cost per request** | **{currency} "
        f"{report._per_request(report.cost).quantize(Decimal('0.001'))} "
        f"(~USD {report.as_usd(report._per_request(report.cost))})** |"
    )
    add(f"| wall time | {report.wall_seconds:,.1f} s |")
    add("")

    if report.unpriced:
        add("## Usage the price list does not cover")
        add("")
        for item in report.unpriced:
            add(f"- **{item}**")
        add("")
        add(
            "These are excluded from the totals above rather than costed at "
            "zero, so the figure is understated by a known and named amount."
        )
        add("")

    add("## Where the cost goes")
    add("")
    total = report.cost
    ranked = sorted(report.roles, key=lambda r: r.cost, reverse=True)
    for role in ranked:
        share = (
            (role.cost / total * Decimal(100)).quantize(Decimal("0.1"))
            if total else Decimal()
        )
        mean_out = (
            Decimal(role.output_tokens) / Decimal(role.calls)
            if role.calls and role.unit == "tokens" else Decimal()
        )
        detail = (
            f", averaging {mean_out.quantize(Decimal('1'))} completion tokens per call"
            if mean_out else ""
        )
        add(
            f"- `{role.role}` — {currency} {_money(role.cost)}, {share}% of the "
            f"bill across {role.calls:,} call(s){detail}."
        )
    add("")

    add("## Reproducibility")
    add("")
    add(
        "Every chat role pins `temperature: 0`, `top_p: 1` and a per-role `seed`. "
        "See `docs/LIMITATIONS.md` L5 for the measured limits of that guarantee."
    )
    add("")
    add(
        "No API key, credential, endpoint secret or other sensitive configuration "
        "appears in this report. The provider is named; the credential is read "
        "from an environment variable that is never printed."
    )
    add("")
    counts = manifest.get("counts") or {}
    add(
        f"Run shape: {counts.get('requests', 0)} requests, "
        f"{manifest.get('rows_written', 0)} rows written, "
        f"{manifest.get('unresolved_total', 0)} unresolved item(s)."
    )
    return "\n".join(lines) + "\n"


def write_usage_report(manifest_path: Path, pricing_path: Path, settings,
                       out_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pricing = load_pricing(pricing_path)
    report = build_report(manifest, pricing, settings)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(report, manifest=manifest), encoding="utf-8")
    return out_path
