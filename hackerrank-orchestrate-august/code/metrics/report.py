"""Assemble report.json; render report.txt and stdout."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from metrics.consistency import compute_consistency
from metrics.label_free import compute_label_free
from metrics.labeled import compute_labeled
from metrics.loader import ACTIONS, RunData

WIDTH = 72
RULE = "=" * WIDTH
THIN = "-" * WIDTH


def build_report(run: RunData) -> dict[str, Any]:
    """Assemble the full report. Pure apart from the generation timestamp."""
    return {
        "schema_version": 2,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_id": run.run_id,
        "manifest": {
            k: v for k, v in run.manifest.items() if k not in ("config_snapshot",)
        },
        "label_free": compute_label_free(run.rows, run.manifest),
        "labeled": compute_labeled(run.eval_rows, run.eval_truth),
        "consistency": compute_consistency(run.rows),
    }


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def render_console(report: dict[str, Any]) -> str:
    """Readable without opening a file."""
    lines: list[str] = []
    m = report["manifest"]
    lf = report["label_free"]
    lb = report["labeled"]
    cs = report["consistency"]

    lines.append(RULE)
    lines.append(
        f" RUN  {report['run_id']}"
        f"   {m.get('mode', '?')}   {m.get('rows_written', 0)} rows + "
        f"{m.get('eval_rows_written', 0)} eval"
    )
    lines.append(
        f" cfg  {str(m.get('config_hash', ''))[:6]}"
        f"   prompt {m.get('prompt_template_version') or 'n/a'}"
        f"   media {m.get('media_extraction_version') or 'n/a'}"
        f"   model {m.get('model_id') or 'none (rules-only)'}"
    )
    lines.append(RULE)

    # ---- label-free ----
    lines.append("")
    lines.append(f"LABEL-FREE  ({lf['n_rows']} rows — full target set)")
    lines.append(THIN)
    ad = lf["action_distribution"]
    lines.append(
        " action        "
        + "  ".join(
            f"{a} {ad['counts'][a]} ({_pct(ad['shares'][a])})" for a in ACTIONS if ad["counts"][a]
        )
    )
    lines.append(
        f"               entropy {ad['entropy_bits']:.2f} bits / 1.58 max  "
        f"(norm {ad['entropy_normalised']:.2f})"
    )
    td = lf["message_type_distribution"]
    present = [(t, c) for t, c in td["counts"].items() if c]
    present.sort(key=lambda kv: (-kv[1], kv[0]))
    for chunk in range(0, len(present), 4):
        prefix = " type          " if chunk == 0 else "               "
        lines.append(prefix + "  ".join(f"{t} {c}" for t, c in present[chunk : chunk + 4]))
    lines.append(
        f"               entropy {td['entropy_bits']:.2f} / 3.46  (norm {td['entropy_normalised']:.2f})"
    )

    oc = lf["override_codes"]
    po = lf["proposal_override"]
    lines.append("")
    lines.append(
        f" overrides     {po['rows_overridden']} rows overridden ({_pct(po['share_overridden'])})"
        f"   demoted {po['by_direction']['demoted']}  escalated {po['by_direction']['escalated']}"
        f"  type-only {po['by_direction']['type_only']}"
    )
    for code, count in oc["fire_counts"].items():
        if count:
            lines.append(f"               {code:<32}{count:>4}")
    never = oc["never_fired"]
    if never:
        lines.append(f"               never fired ({len(never)}): {', '.join(never[:4])}")
        for chunk in range(4, len(never), 4):
            lines.append("                             " + ", ".join(never[chunk : chunk + 4]))
    lines.append(f"               {oc['rows_with_multiple_firings']} rows had 2+ rules fire")

    ev = lf["evidence"]
    lines.append("")
    lines.append(
        f" evidence      {ev['rows_with_citation']}/{lf['n_rows']} cited "
        f"({_pct(ev['share_with_citation'])})   {ev['rows_with_none']} none   "
        f"mean {ev['mean_citations_per_row']:.2f}/row"
    )
    tc = ev["tier_counts"]
    lines.append(
        f"               tier3 {tc['3']}  tier2 {tc['2']}  tier1 {tc['1']}  tier0 {tc['0']}"
    )
    mark = "OK" if ev["ungrounded_citation_count"] == 0 else "!!"
    lines.append(f"               ungrounded citations: {ev['ungrounded_citation_count']}   [{mark}]")

    ph = lf["perception_health"]
    iw = lf["integrity_warnings"]
    rh = lf["reason_health"]
    lines.append("")
    # The health marker asks "is anything unaccounted for?", not "did anything
    # happen?". Warnings are observations the system is supposed to make, so a
    # non-zero count is expected output, not a fault -- flagging it forever
    # would train the reader to ignore the marker. What is a fault is a warned
    # row whose reason never mentions it, or a response that could not be parsed.
    unexplained = rh.get("rows_warned_without_integrity_reason") or []
    healthy = not unexplained and ph["hard_parse_failure_count"] == 0
    lines.append(
        f" integrity     {iw['rows_with_any']} warnings, {len(unexplained)} unexplained"
        f"      perception: {ph['schema_retry_count']} retries, "
        f"{ph['hard_parse_failure_count']} parse failures   [{'OK' if healthy else '!!'}]"
    )

    cf = lf["confidence"]
    o = cf["overall"]
    lines.append(
        f" confidence    {o['min']:.2f} – {o['max']:.2f}   mean {o['mean']:.2f}   "
        f"median {o['median']:.2f}"
    )
    lines.append(
        "               "
        + "   ".join(
            f"{a} {cf['by_action'][a]['mean']:.2f}"
            for a in ACTIONS
            if lf["action_distribution"]["counts"][a]
        )
    )
    ov, nv = cf["mean_on_overridden"], cf["mean_on_not_overridden"]
    lines.append(
        f"               overridden {ov if ov is None else f'{ov:.2f}'}  vs  "
        f"accepted {nv if nv is None else f'{nv:.2f}'}"
    )

    md = lf["media"]
    lines.append(
        f" media         {md['images_analysed']} images, {md['voice_notes_transcribed']} voice "
        f"analysed of {md['rows_with_media']} media rows"
    )
    if md["failures_by_cause"]:
        lines.append(
            "               failures: "
            + ", ".join(f"{k}={v}" for k, v in sorted(md["failures_by_cause"].items()))
        )
    lines.append(
        f"               media_failed {md['rows_media_failed']}   media_unread {md['rows_media_unread']}"
    )

    ca = lf["cache"]
    lines.append(
        f" cache         media {ca['media_outcomes']['hit']}/"
        f"{sum(v for k, v in ca['media_outcomes'].items() if k != 'n/a')}"
        f"   perception {ca['perception_outcomes']['hit']}/"
        f"{sum(v for k, v in ca['perception_outcomes'].items() if k != 'n/a')}"
        f"   fully-cached rows {ca['rows_served_entirely_from_cache']}"
    )
    bd = lf["budget"]
    lines.append(
        f" budget        {bd['tokens']['input_total']} in / {bd['tokens']['output_total']} out tokens, "
        f"{bd['wall_clock_ms']['total'] / 1000:.1f}s wall"
    )

    if rh.get("status") == "unavailable":
        lines.append(f" reason        unavailable: {rh['missing_fields']}")
    else:
        cl = rh["char_length"]
        lines.append(
            f" reason        {cl['min']:.0f}–{cl['max']:.0f} chars, median {cl['median']:.0f}   "
            f"{rh['rows_outside_register_bounds']} outside register   [OK]"
        )
        share = rh["share_referencing_integrity_when_warned"]
        lines.append(
            f"               cites concrete evidence {_pct(rh['share_citing_concrete_evidence'])}"
            f"   names integrity when warned "
            f"{'n/a' if share is None else _pct(share)}"
        )
        named = rh.get("share_naming_a_decisive_factor")
        if named is not None:
            lines.append(
                f"               names a decisive factor {_pct(named)}"
                f"{'' if named >= 1.0 else '   [DEFECT: a reason naming nothing is not an explanation]'}"
            )

    # ---- labeled ----
    lines.append("")
    if lb.get("n_rows"):
        nf = lb["noise_floor"]
        lines.append(
            f"LABEL-DEPENDENT  ({lb['n_rows']} rows — INDICATIVE ONLY, "
            f"1 row = {nf['one_row_pp']}pp)"
        )
        lines.append(THIN)
        a = lb["action"]
        lines.append(
            f" action        accuracy {a['correct']}/{lb['n_rows']} = "
            f"{_pct(a['accuracy'])}      macro F1 {a['macro_f1']:.2f}"
        )
        labels = a["confusion_matrix"]["labels"]
        lines.append("                        pred→  " + "  ".join(f"{l:>7}" for l in labels))
        for i, label in enumerate(labels):
            prefix = "               truth " if i == 0 else "                     "
            lines.append(
                prefix + f"{label:<7}" + "  ".join(f"{v:>7}" for v in a["confusion_matrix"]["matrix"][i])
            )
        t = lb["message_type"]
        lines.append(
            f" type          accuracy {t['correct']}/{lb['n_rows']} = "
            f"{_pct(t['accuracy'])}      macro F1 {t['macro_f1']:.2f}"
        )
        j = lb["joint_accuracy"]
        lines.append(f" joint         {j['both_correct']}/{lb['n_rows']} = {_pct(j['accuracy'])}")
        eo = lb["evidence_overlap"]
        lines.append(
            f" evidence      exact {eo['exact_set_match']}/{eo['rows_compared']}  "
            f"any overlap {eo['any_overlap']}/{eo['rows_compared']}  "
            f"mean jaccard {eo['mean_jaccard']:.2f}"
        )
        lines.append(
            f"               both-none {eo['both_none']}   "
            f"pred-none/truth-cited {eo['predicted_none_truth_cited']}   "
            f"pred-cited/truth-none {eo['predicted_cited_truth_none']}"
        )
        cal = lb["calibration"]
        lines.append(
            f" calibration   ECE {cal['expected_calibration_error']:.3f}   "
            f"Brier {cal['brier_score']:.3f}     (indicative — see note)"
        )
        for b in cal["buckets"]:
            if b["n"]:
                lines.append(
                    f"               [{b['lower']:.1f},{b['upper']:.1f}]  n={b['n']:>2}  "
                    f"conf {b['mean_confidence']:.2f}  acc {b['accuracy']:.2f}  gap {b['gap']:.2f}"
                )
        lines.append(f" [!] {nf['note']}")
    else:
        lines.append("LABEL-DEPENDENT  (unavailable — no labeled rows in this run)")
        lines.append(THIN)

    # ---- consistency ----
    lines.append("")
    lines.append(f"CONSISTENCY  ({cs.get('n_rows', 0)} rows, no labels)")
    lines.append(THIN)
    if cs.get("status"):
        lines.append(f" {cs['status']}: {cs.get('missing_fields', '')}")
    else:
        mark = "OK" if cs["groups_diverging_without_code_difference"] == 0 else "!!"
        lines.append(
            f" {cs['groups_total']} groups, {cs['groups_with_divergence']} with divergent actions, "
            f"{cs['groups_diverging_without_code_difference']} unexplained   [{mark}]"
        )
        for group in cs["divergent_groups"][:6]:
            key = group["key"]
            lines.append("")
            lines.append(
                f" group  conversation={key['conversation_type']}  "
                f"sender={key['sender_relationship_class']}"
            )
            lines.append(
                f"        proposed={key['proposed_message_type']}  "
                f"integrity={key['integrity_state']}          ({group['size']} rows)"
            )
            lines.append(
                "        "
                + " / ".join(f"{a} {c}" for a, c in group["actions"].items())
                + f"     distinguishing: {', '.join(group['distinguishing_codes']) or 'NONE'}"
            )
            for member in group["members"][:4]:
                lines.append(
                    f"          {member['message_id']:<10} {member['action']:<7} "
                    f"{str(member['override_codes_fired']):<34} conf {member['confidence']:.2f}"
                )
    lines.append("")
    return "\n".join(lines)


def render_diff_console(diff: dict[str, Any]) -> str:
    lines = [RULE]
    lines.append(f" DIFF   baseline {diff['baseline_run_id']}  ->  candidate {diff['candidate_run_id']}")
    delta = diff["config_delta"]
    lines.append(
        " config "
        + ("unchanged" if not delta else "; ".join(f"{k} {v['from']} -> {v['to']}" for k, v in delta.items()))
    )
    if diff["dataset_fingerprint_changed"]:
        lines.append(" [!] DATASET CHANGED — row deltas are not attributable to the code change")
    lines.append(RULE)
    gc = diff["gross_churn"]
    lines.append("")
    lines.append(
        f" GROSS CHURN      {gc['rows_action_changed']} rows changed action ({_pct(gc['share'])})"
    )
    lines.append(f"                  {gc['rows_type_changed']} rows changed type")
    lines.append(f"                  {gc['rows_confidence_changed']} rows changed confidence")
    nl = diff["net_labeled"]
    lines.append("")
    lines.append(
        f" NET (labeled)    improved {nl['improved']}   regressed {nl['regressed']}   "
        f"unchanged {nl['unchanged']}"
    )
    lines.append(f"                  net {nl['net_rows']:+d} row(s) ({nl['net_pp']:+.2f}pp)   {nl['verdict']}")
    lines.append("")
    lines.append(" CHURN / NET      " + diff["churn_to_net_ratio"]["interpretation"])
    if diff["override_code_deltas"]:
        lines.append("")
        lines.append(" OVERRIDE DELTAS")
        for code, d in diff["override_code_deltas"].items():
            lines.append(f"                  {code:<34}{d['from']:>4} -> {d['to']:<4} ({d['delta']:+d})")
    ua = diff["unexplained_action_changes"]
    if ua["count"]:
        lines.append("")
        lines.append(f" [!] UNEXPLAINED  {ua['count']} rows changed action with no override or warning change.")
        lines.append(f"                  {', '.join(ua['message_ids'][:8])}")
    if diff["changed_rows"]:
        lines.append("")
        lines.append(f" CHANGED ROWS ({len(diff['changed_rows'])})")
        for row in diff["changed_rows"][:20]:
            lines.append(
                f"   {row['message_id']:<10} {row['action']['from']} -> {row['action']['to']:<8} "
                f"{row['message_type']['from']} -> {row['message_type']['to']:<16} "
                f"{row['confidence']['from']:.2f} -> {row['confidence']['to']:.2f}"
                f"   {'explained' if row['explained'] else 'UNEXPLAINED'}"
            )
    lines.append("")
    return "\n".join(lines)


def write_report(run: RunData, report: dict[str, Any]) -> Path:
    out_dir = run.run_dir / "metrics"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    console = render_console(report)
    (out_dir / "report.txt").write_text(console + "\n", encoding="utf-8")
    return out_dir
