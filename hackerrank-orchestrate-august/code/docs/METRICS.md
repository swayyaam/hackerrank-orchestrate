# METRICS.md

The metrics engine: a package at `code/metrics/`, computed post hoc from a saved run directory.

Companion documents: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), [CACHING.md](CACHING.md), [EVALUATION_STRATEGY.md](EVALUATION_STRATEGY.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

---

## 1. Design constraints

**The pipeline writes records. The engine computes metrics. Neither does the other's job.**

| constraint | consequence |
|---|---|
| Metrics are computed post hoc from a saved run directory | No counter is incremented in the routing loop. No timing hook in `policy/` or `perception/`. No pipeline module imports anything from `code/metrics/`. |
| The engine runs standalone against any past run directory | `python -m code.metrics report runs/<id>` works on a run from three weeks ago with no pipeline execution. |
| A new metric applies retroactively | The per-row record must already contain the inputs. This is the entire reason for the split. |
| stdlib + pandas only | Confusion matrix, calibration bucketing, ECE, and Brier are implemented directly. No sklearn, no matplotlib, no seaborn. |
| The consistency scan groups by a composite key | No all-pairs comparison. 110 rows would be 5,995 pairs; grouping is a single `groupby`. |

**Enforced by** `tests/test_architecture.py::test_metrics_isolation`: no module under `code/` (excluding `code/metrics/`) may import `code.metrics`, and no module under `code/metrics/` may import the pipeline.

### Retroactivity and graceful degradation

The row record below is specified to be complete enough for every metric in this document. Where a future metric needs a field that older runs do not have, the engine reports that metric as `"unavailable"` with the missing field named — it never crashes, and it never guesses a default. A metric that silently substitutes zero for absent data produces a confident wrong number, which is worse than a gap.

```json
"reason_health": {
  "status": "unavailable",
  "missing_fields": ["reason_cites_evidence"],
  "first_available_in_schema_version": 2
}
```

---

## 2. Run directory layout

```
runs/<run_id>/
├── manifest.json          run identity, config hash, git commit, mode, counts
├── rows.jsonl             one RowRecord per TARGET message (110)
├── eval_rows.jsonl        one RowRecord per LABELED message (30)
├── eval_truth.csv         the 30 sample label rows, copied at run time — labeled
│                          metrics read THIS, never dataset/, so a past run stays
│                          gradable even if the dataset changes (retroactivity)
├── output.csv             the submission artifact this run produced
├── events.jsonl           structured log (THREAT_MODEL §7)
└── metrics/               written by the engine, never by the pipeline
    ├── report.json
    └── report.txt
```

`run_id` is `<UTC compact timestamp>-<git short sha>-<config hash prefix>`, e.g. `20260801T193044Z-b2c4b90-a17f3c`. Sortable, and it names the two things that determine behaviour.

### Two row files, because the labeled rows are not the target rows

`sample_messages.csv` uses `sample_msg_NNN` ids and `messages.csv` uses `msg_NNN` ids. **The overlap is zero** — the 30 labeled rows are 30 *different messages*, all dated 2026-07-31, not a labeled subset of the 110 ([DATASET_ANALYSIS §1.2](DATASET_ANALYSIS.md#12-sample_messagescsv--30-rows--16-cols)).

So a run routes both sets through the identical pipeline and writes them separately:

| file | rows | used by |
|---|---:|---|
| `rows.jsonl` | 110 | label-free metrics, consistency scan |
| `eval_rows.jsonl` | 30 | label-dependent metrics only |

`output.csv` is built from `rows.jsonl` alone. The eval rows never enter the submission.

---

## 3. The per-row record

Written by `code/emit/record.py`. One JSON object per line. This schema is the contract that makes retroactive metrics possible — every field exists because a metric in this document needs it.

```json
{
  "schema_version": 2,
  "message_id": "msg_022",

  "action": "mute",
  "message_type": "scam",
  "reason": "A payment instruction arrived from a sender with no established authority for payments in this conversation.",
  "confidence": 0.81,
  "evidence_cited": ["message_0100"],

  "model_proposal": {
    "action": "notify",
    "type_candidates": ["payment", "scam"]
  },
  "override_codes_fired": ["UNAUTHORIZED_PAYMENT_REQUEST"],
  "override_code_applied": "UNAUTHORIZED_PAYMENT_REQUEST",
  "firings": [
    {"code": "UNAUTHORIZED_PAYMENT_REQUEST", "effect": "hard_override",
     "status": "applied", "would_set_action": "mute"}
  ],
  "integrity_warnings": [],

  "evidence_shortlist": ["message_0100", "message_0099", "message_0237"],
  "evidence_tier": 3,

  "schema_retry_count": 0,
  "hard_parse_failure": false,

  "media_id": null,
  "media_kind": "",
  "media_outcome": "none",
  "media_failure_cause": null,

  "cache": {"media": "n/a", "perception": "hit"},
  "served_entirely_from_cache": true,

  "tokens": {"input": 1180, "output": 96},
  "wall_clock_ms": 12.4,

  "conversation_type": "group",
  "sender_relationship_class": "established_dismissive",
  "proposed_message_type": "payment",
  "integrity_state": "flagged",

  "reason_char_length": 106,
  "reason_cites_evidence": true,
  "reason_references_integrity": true
}
```

### Derived classification fields

Three fields are computed at write time specifically so the consistency scan and the reason-health metrics are retroactively derivable. They are recorded rather than recomputed, because recomputing them later would require the `FeatureVector`, which is not persisted.

**`sender_relationship_class`** — from `F.relationship_established` and `F.sender_stance`:

| value | condition |
|---|---|
| `unestablished` | `not relationship_established` |
| `established_receptive` | established and stance `RECEPTIVE` |
| `established_neutral` | established and stance `NEUTRAL` or `UNKNOWN` |
| `established_dismissive` | established and stance `DISMISSIVE` |

**`integrity_state`** — the row's risk posture:

| value | condition |
|---|---|
| `clean` | no integrity warning, no Tier 0/1 firing |
| `warned` | at least one integrity warning, no Tier 0/1 hard override |
| `flagged` | a Tier 0/1 hard override fired |

**`reason_cites_evidence`** / **`reason_references_integrity`** — booleans set by `compose.reason` from the applied `Rule`'s declared properties (`cites_history`, `is_integrity_rule`). Mechanical, not string matching: the composer knows which clause it emitted.

---

## 4. Manifest

```json
{
  "schema_version": 2,
  "run_id": "20260801T193044Z-b2c4b90-a17f3c",
  "created_at_utc": "2026-08-01T19:30:44.117Z",
  "git_commit": "b2c4b90…",
  "git_dirty": false,
  "config_hash": "a17f3c…",
  "config_snapshot": {
    "PolicyConfig": {"MIN_PATTERN_OBSERVATIONS": 2, "…": "…"},
    "ConfidenceConfig": {"W_BASE": 0.55, "…": "…"},
    "EvidenceConfig": {"…": "…"},
    "ReasonConfig": {"…": "…"}
  },
  "prompt_template_version": "3.1.0",
  "media_extraction_version": "1.2.0",
  "model_id": "…",
  "dataset_fingerprint": "sha256 over the 13 CSVs, sorted by filename",
  "mode": "offline",
  "rows_expected": 110,
  "rows_written": 110,
  "eval_rows_expected": 30,
  "eval_rows_written": 30,
  "failures": [
    {"message_id": "msg_0xx", "error_type": "PerceptionSchemaError"}
  ]
}
```

`failures` entries carry the `error_type` (the `RouterError` subclass name) because a hard-failed row produces **no** `RowRecord` — the manifest is the only place its cause can live, and `perception_health.hard_parse_failure_count` is derived from it (§6).

`config_snapshot` is stored in full, not just hashed, so a diff between two runs can say *which* constant changed rather than only that the hash differs.

---

## 5. Report schema

`metrics/report.json`. Three structurally separate top-level sections, plus provenance. **Label-free and label-dependent results never merge into a shared block** — mixing a 110-row measurement with a 30-row measurement in one table invites reading them with equal weight.

```json
{
  "schema_version": 2,
  "generated_at_utc": "…",
  "run_id": "…",
  "manifest": { "…echoed for standalone readability…" },

  "label_free": {
    "n_rows": 110,
    "action_distribution":       {"counts": {}, "shares": {}, "entropy_bits": 1.47, "entropy_normalised": 0.93},
    "message_type_distribution": {"counts": {}, "shares": {}, "entropy_bits": 2.61, "entropy_normalised": 0.75},
    "override_codes": {
      "fire_counts": {"CREDENTIAL_SOLICITATION": 7},
      "applied_counts": {"CREDENTIAL_SOLICITATION": 7},
      "rows_with_multiple_firings": 9,
      "never_fired": ["UNSAFE_INSTRUCTION"]
    },
    "proposal_override": {
      "rows_overridden": 41, "share_overridden": 0.373,
      "by_direction": {"escalated": 6, "demoted": 35, "type_only": 3, "unchanged": 69},
      "transition_matrix": {"notify": {"notify": 0, "digest": 0, "mute": 0}, "…": {}}
    },
    "evidence": {
      "rows_with_citation": 96, "share_with_citation": 0.873,
      "rows_with_none": 14, "share_none": 0.127,
      "mean_citations_per_row": 1.06,
      "citation_count_histogram": {"0": 14, "1": 88, "2": 8},
      "tier_counts": {"3": 51, "2": 33, "1": 12, "0": 14},
      "ungrounded_citation_count": 0,
      "ungrounded_rows": []
    },
    "integrity_warnings": {"counts_by_type": {}, "rows_with_any": 0},
    "perception_health": {"schema_retry_count": 0, "hard_parse_failure_count": 0,
                          "rows_with_retry": 0},
    "confidence": {
      "overall": {"min": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "max": 0.0,
                  "mean": 0.0, "std": 0.0},
      "by_action": {"notify": {}, "digest": {}, "mute": {}},
      "mean_on_overridden": 0.0,
      "mean_on_not_overridden": 0.0
    },
    "media": {
      "images_analysed": 15, "voice_notes_transcribed": 8,
      "failures_by_cause": {"decode_failed": 0, "limit_exceeded": 0,
                            "path_rejected": 0, "cache_missing": 0},
      "rows_media_unread": 0
    },
    "cache": {
      "media_hit_rate": 1.0, "media_outcomes": {"hit": 23, "miss": 0, "corrupt": 0, "n/a": 87},
      "perception_hit_rate": 1.0, "perception_outcomes": {"hit": 110, "miss": 0, "corrupt": 0},
      "rows_served_entirely_from_cache": 110
    },
    "budget": {
      "tokens": {"input_total": 0, "output_total": 0,
                 "input_mean_per_row": 0.0, "input_p95": 0.0,
                 "output_mean_per_row": 0.0, "output_p95": 0.0},
      "wall_clock_ms": {"total": 0.0, "mean_per_row": 0.0, "p95": 0.0}
    },
    "reason_health": {
      "char_length": {"min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0, "mean": 0.0},
      "rows_outside_register_bounds": 0,
      "share_citing_concrete_evidence": 0.0,
      "share_referencing_integrity_when_warned": 0.0,
      "rows_warned_without_integrity_reason": []
    }
  },

  "labeled": {
    "n_rows": 30,
    "noise_floor": {
      "one_row_pp": 3.33,
      "reportable_threshold_rows": 2,
      "reportable_threshold_pp": 6.67,
      "note": "n=30. A single row flip moves any rate by 3.33pp. Changes below 2 rows are not described as improvements."
    },
    "action": {
      "accuracy": 0.0, "correct": 0, "incorrect": 0,
      "per_class": {"notify": {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 9}},
      "macro_f1": 0.0,
      "confusion_matrix": {"rows_are_truth": true, "labels": ["notify","digest","mute"],
                           "matrix": [[0,0,0],[0,0,0],[0,0,0]]}
    },
    "message_type": {
      "accuracy": 0.0,
      "per_class": {}, "macro_f1": 0.0,
      "confusion_matrix": {"labels": [], "matrix": []}
    },
    "joint_accuracy": {"both_correct": 0, "accuracy": 0.0},
    "evidence_overlap": {
      "rows_compared": 28,
      "exact_set_match": 0, "any_overlap": 0,
      "mean_jaccard": 0.0, "mean_precision": 0.0, "mean_recall": 0.0,
      "both_none": 0, "predicted_none_truth_cited": 0, "predicted_cited_truth_none": 0
    },
    "calibration": {
      "indicative_only": true,
      "bucket_edges": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
      "buckets": [{"lower": 0.6, "upper": 0.8, "n": 11,
                   "mean_confidence": 0.0, "accuracy": 0.0, "gap": 0.0}],
      "expected_calibration_error": 0.0,
      "brier_score": 0.0,
      "brier_definition": "mean((confidence - is_action_correct)^2), one-vs-rest on the predicted action"
    }
  },

  "consistency": {
    "n_rows": 110,
    "grouping_key": ["conversation_type", "sender_relationship_class",
                     "proposed_message_type", "integrity_state"],
    "groups_total": 0,
    "groups_with_divergence": 0,
    "groups_diverging_without_code_difference": 0,
    "divergent_groups": [
      {
        "key": {"conversation_type": "group", "sender_relationship_class": "established_neutral",
                "proposed_message_type": "promotion", "integrity_state": "clean"},
        "size": 4,
        "actions": {"digest": 3, "mute": 1},
        "explained": true,
        "distinguishing_codes": ["REPEAT_DISMISSAL"],
        "members": [
          {"message_id": "…", "action": "digest", "override_codes_fired": [],
           "confidence": 0.0},
          {"message_id": "…", "action": "mute",
           "override_codes_fired": ["REPEAT_DISMISSAL"], "confidence": 0.0}
        ]
      }
    ]
  }
}
```

---

## 6. Metric definitions

Formulas that are not self-evident from the field names.

### Entropy

Shannon entropy in bits over the observed distribution, plus a normalised form:

```
H    = -Σ p_i · log2(p_i)          (p_i = 0 contributes 0)
H_n  = H / log2(k)                  k = number of allowed values (3 for action, 11 for type)
```

`H_n` is reported because raw entropy is not comparable between a 3-value and an 11-value field. `H_n` near 0 means the system is collapsing to one answer; near 1 means it is spreading uniformly, which for `action` would also be suspicious.

### Override direction

`escalated` / `demoted` compare `ACTION_RANK[final]` against `ACTION_RANK[proposal.action]`. `type_only` is a row where the action was unchanged but `message_type` differs from `proposal.type_candidates[0]`. The full 3×3 transition matrix is reported alongside, since the three summary buckets lose which transition happened.

### Ungrounded citations

```
ungrounded = { id for id in evidence_cited if id not in evidence_shortlist }
```

This should be **structurally zero** — the selector picks only from the shortlist it was handed. The metric verifies the invariant rather than estimating a rate. A non-zero value is a defect, not a score, and it is a hard merge gate in [EVALUATION_STRATEGY.md](EVALUATION_STRATEGY.md).

### Evidence overlap (labeled only)

Per row, against the ground-truth `evidence_message_ids`:

```
precision = |pred ∩ truth| / |pred|      (1.0 when both are empty)
recall    = |pred ∩ truth| / |truth|     (1.0 when both are empty)
jaccard   = |pred ∩ truth| / |pred ∪ truth|
```

`both_none` is tracked separately, because "correctly declined to cite" and "correctly cited" are different competencies, and averaging them hides which one is working.

### Calibration (labeled only)

Five equal-width buckets over `[0, 1]`. Five, not the conventional ten, because at n=30 ten buckets average three rows each and the per-bucket accuracy is meaningless. **Bucket `n` is always printed**, so an empty or near-empty bucket is visible rather than being averaged into a summary number.

```
ECE = Σ_b (n_b / N) · | accuracy_b − mean_confidence_b |
```

Brier is the one-vs-rest score on the predicted action, because the system emits a single confidence rather than a distribution over the three actions:

```
Brier = mean( (confidence_i − correct_i)² )       correct_i ∈ {0, 1}
```

The `brier_definition` string is written into the report so the choice is not silently assumed by a later reader.

#### A low ECE here is partly an artifact of a flat predictor

**Do not treat ECE as an optimisation target while the confidence predictor is this narrow.** As measured on the Phase 2 run: **27 of the 30 eval rows sit in a single bucket** (`[0.6, 0.8]`), the whole predictor spans 0.55–0.95 with a mean of 0.73 and a median of 0.71, and the **per-action means differ by 0.05** — `notify` 0.70, `digest` 0.70, `mute` 0.75.

A predictor that emits approximately one value scores well on ECE by construction. ECE asks "within each bucket, does mean confidence match accuracy?", and a single populated bucket reduces that to one comparison of two aggregates. There is very little for the reliability curve to be wrong about, so being right about it is weak evidence.

Two things follow, and both are constraints on how this number may be used:

* **The honest reading of a low ECE here is "confidence is not *mis*-calibrated", not "confidence is informative".** The second claim requires a predictor that separates rows first. A flat predictor can be perfectly calibrated and still carry no information — if every row is 0.71 and 71% of rows are correct, ECE is ~0 and the column has told the reader nothing about *which* rows to doubt.
* **Optimising ECE directly would push the wrong way.** The cheapest way to lower it is to flatten the predictor further toward the base rate, which is the opposite of what a confidence column is for.

The number to watch first is therefore **spread**, not error: per-action means, the standard deviation, and how many buckets are occupied at all. Those are printed alongside ECE for exactly this reason. ECE becomes a meaningful target once more than one bucket is populated; until then it is reported as context and explicitly not chased.

This is a property of the current predictor, not a permanent one — the confidence formula gains terms in a later phase, and this caveat should be re-checked, not assumed to still hold, whenever the spread changes.

#### Closing position: the predictor stays as it is, and why

The caveat above was written expecting the spread to widen later. It did not, and this records the decision not to widen it rather than leaving the expectation dangling.

**As shipped** (committed artifact, Phase 5): **ECE 0.119, Brier 0.139**, with **23 of the 30 eval rows in a single bucket** (`[0.6, 0.8]`). The predictor spans 0.51–0.95, mean 0.75, median 0.75; per-action means `mute` 0.81 > `notify` 0.71 > `digest` 0.69, and overridden rows 0.83 against accepted rows 0.70. Terms were added across Phases 3–5 and the ordering properties are now real — the separation that exists is between *overridden and accepted* rows, which is the distinction the column is most useful for — but 23 of 30 rows still land in one bucket, so the caveat above holds essentially unchanged.

Three things stated plainly:

* **ECE 0.119 with 23 of 30 rows in one bucket means the predictor is not mis-calibrated. It does not mean it is informative.** With a single bucket carrying 77% of the labeled rows, ECE is very nearly one comparison between two aggregates. Being right about it is weak evidence, exactly as the paragraph above says.
* **Brier 0.139 is the more honest number.** It scores every row individually against its own outcome and cannot be rescued by bucketing, so it responds to whether confidence tracks correctness *per row* rather than on average. Where the two disagree about how good the column is, believe Brier.
* **Widening the spread is the correct next step for a build with more time. It is not a defect being hidden.** A confidence column that separates rows is strictly more useful than one that does not, and this one separates less than it should.

**Why it was not done here.** [EVALUATION_STRATEGY §6a](EVALUATION_STRATEGY.md#6a-reproducibility-under-independent-re-derivation--measured) measured `confidence` as **the most volatile output column — 24 of 110 rows differ under independent re-derivation**, against 3 of 110 for `action`. It is volatile precisely because it is an unthresholded sum: every other column is a discrete choice that absorbs small perturbations, while any moved term moves the number. Adding terms to a scoring layer that reads the output of every rule increases that volatility, and it does so late in the build, for a gain that could not be measured — the labeled floor is two rows and no confidence change is worth two labeled rows, because confidence is not what the labeled action and type metrics score.

So the trade is: a column that is honestly narrow and stable, over a column that is wider, less reproducible, and unverifiable in the time remaining. **No terms added, no reweighting, no widening.** The next person to touch this should widen the spread first and re-measure §6a's volatility second, in that order.

### Perception health

`schema_retry_count` is summed over the row records. `hard_parse_failure_count` is **not** derivable from row records — a hard-failed row writes none — so it is the count of `manifest.failures` entries whose `error_type` is `PerceptionSchemaError`. Rows in `failures` for any reason are also excluded from every rate's denominator, and the report states the effective n when it differs from `rows_expected`.

### Noise floor

Printed with **every** label-dependent metric:

```
one_row_pp = 100 / n_labeled = 100 / 30 = 3.33
reportable_threshold_rows = 2
reportable_threshold_pp   = 6.67
```

**The engine does not describe a change smaller than two rows as an improvement.** In the diff section, any labeled delta with `|Δ rows| < 2` is emitted with `verdict: "WITHIN_NOISE"` and rendered as `within noise`, never as `improved`. This is a property of the renderer, not a convention for the reader to remember.

### Consistency grouping

Composite key: `(conversation_type, sender_relationship_class, proposed_message_type, integrity_state)`. A single pandas `groupby`; groups of size 1 are dropped; groups whose members share one action are dropped.

A divergent group is **explained** when the set of `override_codes_fired` differs between the members that got different actions. It is **unexplained** when structurally similar rows received different actions with identical codes — which means the divergence came from perception or from an unrecorded input, and that is what this scan exists to surface.

---

## 7. Run comparison

```
python -m code.metrics diff runs/<baseline> runs/<candidate>
```

Rows are joined on `message_id` across `rows.jsonl`, and on `message_id` across `eval_rows.jsonl` for the labeled section.

```json
"diff": {
  "baseline_run_id": "…", "candidate_run_id": "…",
  "config_delta": {"PolicyConfig.FORWARD_CHAIN_THRESHOLD": {"from": 5, "to": 4}},
  "prompt_template_version_changed": false,
  "model_id_changed": false,

  "gross_churn": {"rows_action_changed": 12, "share": 0.109,
                  "rows_type_changed": 5, "rows_confidence_changed": 61},

  "net_labeled": {"improved": 2, "regressed": 1, "unchanged": 27,
                  "net_rows": 1, "net_pp": 3.33,
                  "verdict": "WITHIN_NOISE"},

  "churn_to_net_ratio": {
    "gross_churn_rows": 12, "net_improvement_rows": 1, "ratio": 12.0,
    "interpretation": "12 rows changed action to gain a net 1 labeled row, which is within noise. This reshuffled behaviour rather than improving it."
  },

  "override_code_deltas": {"REPEAT_DISMISSAL": {"from": 8, "to": 14, "delta": 6}},

  "unexplained_action_changes": {
    "count": 3,
    "note": "Action changed with no change in override codes or integrity warnings. Indicates model nondeterminism or an unrecorded input, not a design change.",
    "message_ids": ["…", "…", "…"]
  },

  "changed_rows": [
    {"message_id": "msg_030",
     "action": {"from": "digest", "to": "mute"},
     "message_type": {"from": "promotion", "to": "promotion"},
     "confidence": {"from": 0.79, "to": 0.86},
     "override_codes_fired": {"from": [], "to": ["REPEAT_DISMISSAL"]},
     "integrity_warnings": {"from": [], "to": []},
     "explained": true}
  ]
}
```

**`churn_to_net_ratio` is stated plainly in prose**, not left as a number to interpret. High churn with near-zero net gain means the change reshuffled behaviour rather than improving it, and the report says exactly that.

**`unexplained_action_changes`** is the nondeterminism detector. A row whose action changed while every override code and integrity warning stayed identical did not change because of a design decision — it changed because perception returned something different. On an offline rerun against an unchanged cache this count must be **zero**; a non-zero value there means the pipeline has a nondeterminism bug.

---

## 8. Console format

`report.txt` and stdout. Readable without opening a file.

```
════════════════════════════════════════════════════════════════════════
 RUN  20260801T193044Z-b2c4b90-a17f3c        offline    110 rows + 30 eval
 cfg  a17f3c   prompt 3.1.0   media 1.2.0   model claude-…
════════════════════════════════════════════════════════════════════════

LABEL-FREE  (110 rows — full target set)
────────────────────────────────────────────────────────────────────────
 action        mute 47 (42.7%)  digest 41 (37.3%)  notify 22 (20.0%)
               entropy 1.51 bits / 1.58 max  (norm 0.96)
 type          scam 19  promotion 17  personal 15  business_update 13
               event 11  urgent 10  forward 9  greeting 8  payment 5
               spam 2  unknown 1        entropy 3.14 / 3.46  (norm 0.91)

 overrides     41 rows overridden (37.3%)   demoted 35  escalated 6
               CREDENTIAL_SOLICITATION      7
               IMPERSONATION                6
               REPEAT_DISMISSAL            14
               GROUP_MUTE_CAP               9
               …
               never fired: UNSAFE_INSTRUCTION
               9 rows had 2+ rules fire

 evidence      96/110 cited (87.3%)   14 none   mean 1.06/row
               tier3 51  tier2 33  tier1 12  tier0 14
               ungrounded citations: 0   ✓

 integrity     0 warnings      perception: 0 retries, 0 parse failures  ✓
 confidence    0.41 – 0.93   mean 0.74   median 0.75
               notify 0.83   digest 0.71   mute 0.73
               overridden 0.79  vs  accepted 0.70
 media         15 images, 8 voice   failures: 0                        ✓
 cache         media 23/23 (100%)   perception 110/110 (100%)
               110 rows served entirely from cache
 budget        0 tokens, 3.1s wall   (fully cached run)
 reason        58–118 chars, median 89   0 outside register            ✓
               cites concrete evidence 71%   names integrity when warned 100%

LABEL-DEPENDENT  (30 rows — INDICATIVE ONLY, 1 row = 3.33pp)
────────────────────────────────────────────────────────────────────────
 action        accuracy 24/30 = 80.0%      macro F1 0.78
                        pred→   notify  digest  mute
               truth notify         7       2      0
                     digest         1       9      1
                     mute           0       2      8
 type          accuracy 21/30 = 70.0%      macro F1 0.61
 joint         19/30 = 63.3%
 evidence      exact 14/28  any overlap 21/28  mean jaccard 0.58
               both-none 2   pred-none/truth-cited 1
 calibration   ECE 0.084   Brier 0.147     (indicative — see note)
               [0.6,0.8)  n=11  conf 0.72  acc 0.73  gap 0.01
               [0.8,1.0]  n=17  conf 0.85  acc 0.82  gap 0.03
               [0.4,0.6)  n= 2  conf 0.49  acc 0.50  gap 0.01
 ⚠ n=30. One row = 3.33pp. Differences under 2 rows are not improvements.

CONSISTENCY  (110 rows, no labels)
────────────────────────────────────────────────────────────────────────
 38 groups, 6 with divergent actions, 0 unexplained                    ✓

 group  conversation=group  sender=established_neutral
        proposed=promotion  integrity=clean          (4 rows)
        digest 3 / mute 1     distinguishing: REPEAT_DISMISSAL
          msg_0xx  digest  []                          conf 0.71
          msg_0yy  mute    [REPEAT_DISMISSAL]          conf 0.83
```

Diff console:

```
════════════════════════════════════════════════════════════════════════
 DIFF   baseline 20260801T1712…-a1  →  candidate 20260801T1930…-b2
 config PolicyConfig.FORWARD_CHAIN_THRESHOLD  5 → 4
 prompt unchanged (3.1.0)     model unchanged
════════════════════════════════════════════════════════════════════════

 GROSS CHURN      12 rows changed action (10.9%)
                   5 rows changed type
                  61 rows changed confidence

 NET (labeled)     improved 2   regressed 1   unchanged 27
                   net +1 row (+3.33pp)        WITHIN NOISE

 CHURN / NET       12 rows changed to gain a net 1 labeled row (ratio 12.0).
                   That net gain is within noise at n=30. This change
                   reshuffled behaviour rather than improving it.

 OVERRIDE DELTAS   REPEAT_DISMISSAL              8 → 14   (+6)
                   FREQUENTLY_FORWARDED_BROADCAST 3 →  7   (+4)

 ⚠ UNEXPLAINED     3 rows changed action with no override or warning change.
                   Indicates model nondeterminism, not a design change.
                     msg_0aa   msg_0bb   msg_0cc

 CHANGED ROWS (12)
   msg_030   digest → mute     promotion → promotion    0.79 → 0.86
             codes  [] → [REPEAT_DISMISSAL]                    explained
   msg_0aa   notify → digest   urgent → personal        0.85 → 0.72
             codes  [] → []                                  UNEXPLAINED
   …
```

Rendering rules: `✓` marks an invariant holding at zero; `⚠` marks something requiring attention; every labeled figure carries its row count; no labeled delta under two rows is ever rendered with the word "improved".

---

## 9. Package structure

```
code/metrics/
├── __init__.py
├── __main__.py        CLI: `report <run_dir>` | `diff <base> <cand>`
├── loader.py          Read a run directory into typed frames. The only I/O module.
├── label_free.py      All §5 label_free metrics from rows.jsonl.
├── labeled.py         All §5 labeled metrics from eval_rows.jsonl + sample_messages.csv.
├── consistency.py     Composite-key grouping and divergence detection.
├── diff.py            Two-run comparison.
├── report.py          Assemble report.json; render report.txt and stdout.
└── schemas.py         Report models. Independent of code/schemas.py.
```

`code/metrics/schemas.py` deliberately does **not** import the pipeline's schemas. If it did, a change to `Perception` could break the ability to read an old run — the opposite of retroactivity. The engine parses the JSON it finds and validates against its own models, tolerating unknown fields and reporting missing ones.

### Interfaces

```python
def load_run(run_dir: Path) -> RunData:
    """Read manifest, rows, eval rows, and eval_truth.csv — all from the run
    directory, never from dataset/. Raises RunDirectoryError on a missing manifest."""

def compute_label_free(rows: pd.DataFrame, manifest: Manifest) -> LabelFreeReport: ...
def compute_labeled(eval_rows: pd.DataFrame, truth: pd.DataFrame) -> LabeledReport: ...
def compute_consistency(rows: pd.DataFrame) -> ConsistencyReport: ...
def compute_diff(baseline: RunData, candidate: RunData) -> DiffReport: ...

def assert_runs_identical(baseline: RunData, candidate: RunData) -> None:
    """Field-level equality ignoring VOLATILE_FIELDS (per-row wall_clock_ms;
    manifest run_id and created_at_utc; events.jsonl excluded entirely).
    Raises RunsDifferError naming every differing field. The determinism gate
    (IMPLEMENTATION_PLAN Phase 0 verification 3)."""

def render_json(report: FullReport) -> str: ...
def render_console(report: FullReport, width: int = 72) -> str: ...
```

Every compute function is **pure**: a DataFrame in, a model out. No file access, no clock, no pipeline import.

### Testing

Each module is tested against a checked-in fixture run directory at `tests/fixtures/run_fixture/`, hand-authored with about a dozen rows chosen to exercise the edge cases: a row with `evidence_cited` empty, a row with two competing overrides, a row with a media failure, a row with a corrupt cache outcome, a divergent consistency group with a distinguishing code, and one without.

**No test executes the pipeline.** That is the property being tested — if the metrics engine needed a pipeline run to be exercised, it would not be post hoc.

A second fixture at `tests/fixtures/run_fixture_v1/` uses `schema_version: 1` and omits the reason-health fields, so the graceful-degradation path is covered: the engine must report `reason_health.status = "unavailable"` rather than crashing or emitting zeros.
