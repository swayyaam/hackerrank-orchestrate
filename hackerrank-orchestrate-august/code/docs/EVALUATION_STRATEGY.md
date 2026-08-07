# EVALUATION_STRATEGY.md

How the metrics engine is used as a loop: what runs after each change, what gates a merge, what is diagnostic only.

Companion documents: [METRICS.md](METRICS.md), [CACHING.md](CACHING.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [GENERALIZATION_POLICY.md](GENERALIZATION_POLICY.md).

---

## 1. The primary loop is free

**The iteration loop is a cached rerun plus a metrics diff, with zero model calls.**

```
edit rules / thresholds / evidence / confidence / reason
    ↓
python main.py route          # 0 network calls, seconds
    ↓
python -m code.metrics diff runs/<prev> runs/<new>
    ↓
read the diff, decide, repeat
```

After [Phase 2](IMPLEMENTATION_PLAN.md#4-phase-2--perception-v1-and-the-perception-cache), every rule change, threshold change, evidence-ranking change, confidence-weight change, and reason-wording change is exercised this way. None of them touches the network, because none of them changes what was sent to the model — and the perception cache key deliberately excludes policy config ([CACHING.md §3](CACHING.md#3-perception-cache)).

**Live reruns are batched and infrequent.** A live pass costs 140 model calls and only becomes necessary when the prompt template, the extraction prompt, the model id, or the `Perception` schema changes. Those changes are collected into a batch and applied together, so a build has roughly three paid passes total: one media warm, one perception warm, and at most one or two prompt revisions.

`route` defaults to `--offline`. A live pass requires an explicit `--allow-network`, so the expensive thing is the one you have to ask for.

---

## 2. Two error classes

Every wrong output belongs to one of two classes, and the class determines what it costs to fix.

### Class A — owned by code and rules

The model saw the message correctly and reported it correctly; the decision layer did the wrong thing with that report. Symptoms: the wrong rule fired, a threshold is wrong, precedence resolved badly, evidence was ranked poorly, confidence weighted the wrong term, the reason named the wrong factor.

**Diagnosis.** Read the row's `RowRecord`. `model_proposal` shows what perception said; `firings` shows every rule that fired and its status; `override_code_applied` shows which one won. If `model_proposal` is right and the final action is wrong, it is Class A.

**Cost to fix and re-grade: zero.** Edit, `route`, diff. Seconds, no calls.

### Class B — owned by model perception

The model misread the message: it denied `requests_authentication_secret` on a message that plainly requests an OTP, claimed a deadline that is not there, missed a router directive, or returned a type candidate list with the right answer absent.

**Diagnosis.** The same `RowRecord`. If `model_proposal` and the content claims disagree with the message content itself, it is Class B. The claim fields are what make this diagnosable — a single opaque `proposed_action` would leave "the model was wrong" as the only available description.

**Cost to fix: a paid rerun.** Fixing it means revising the prompt, which changes the cache key, which means re-warming 140 rows.

### Why this split governs the workflow

Class A is the large majority of what a build actually gets wrong, and it is free to iterate. So the discipline is: **exhaust Class A before touching a prompt.** Every Class B fix is queued rather than applied immediately. When the queue is worth a pass — several confirmed misreads that a single prompt revision addresses — one batch revision goes out, one re-warm runs, and the diff is reviewed against the batch as a whole.

A useful consequence: because a Class B queue accumulates over a phase, prompt revisions get written against a body of evidence rather than against the last row that annoyed someone.

---

## 3. What gates a merge

Three tiers. The distinction is what a violation *means*, not how large it is.

### Tier 1 — Hard gates. A violation blocks the merge.

These are correctness invariants. They are not tradeoffs, they have no acceptable non-zero value, and no accuracy gain justifies breaking one.

| gate | required value |
|---|---|
| `output.csv` contract | 110 rows, six columns in order, id set equals `messages.csv`, input order preserved |
| `label_free.evidence.ungrounded_citation_count` | **0** |
| `label_free.perception_health.hard_parse_failure_count` | **0** |
| `label_free.reason_health.rows_outside_register_bounds` | **0** |
| evidence owner / temporal invariants | **0** violations (these raise; a run that completed has none) |
| `manifest.failures` | empty |
| `consistency.groups_diverging_without_code_difference` | must not increase |
| `diff.unexplained_action_changes` on an offline rerun with unchanged cache | **0** — non-zero means a determinism bug |
| architecture tests | `test_import_graph`, `test_no_corpus_literals`, `test_no_network`, `test_metrics_isolation`, `test_cache_isolation` all pass |

### Tier 2 — Review gates. A violation requires a written justification.

| gate | trigger |
|---|---|
| labeled action accuracy | regression **≥ 2 rows** (≥ 6.7pp) |
| labeled type accuracy | regression **≥ 2 rows** |
| churn-to-net ratio | ≥ 10 rows churned for ≤ 1 net row gained |
| a safety rule's fire count drops to 0 | any Tier 0/1 rule that stops firing entirely |
| action distribution entropy collapse | normalised entropy falls below 0.5 (the system is collapsing onto one answer) |

A Tier 2 trigger does not automatically block. It requires an explanation in the commit that is consistent with the change's stated reasoning. "The metric went down but the rule is right because X" is an acceptable resolution; "the metric went down" with no account is not.

### Tier 3 — Diagnostic only. Never gates anything.

- Token counts, wall clock, cache hit rate — cost and plumbing, not quality.
- Per-class precision/recall/F1 where support < 5. At n=30, six of the eleven types have one or two labeled examples; a per-class F1 on two rows is noise wearing a decimal point.
- ECE and Brier. Reported, never optimised. See §5.
- Confidence distribution shape. Expected to differ from the sample's [0.78, 0.91] band — that is the deliberate trade in [PROBLEM_ANALYSIS D3](PROBLEM_ANALYSIS.md#d3--what-reasonable-confidence-calibration-means), not a regression.
- `share_citing_concrete_evidence`. Bounded by the corpus, not by the composer — see below.

### The two reason metrics, and why only one of them is allowed to be a target

The report prints two numbers about the reason sentence. They are not two readings of the same thing, and confusing them produces exactly the wrong work.

| metric | claim | status |
|---|---|---|
| `names a decisive factor` | the sentence names *something* the decision turned on — the receiver's history, an integrity concern, or a structural fact | **hard gate: must be 1.0** |
| `share_citing_concrete_evidence` | the sentence specifically describes this receiver's prior behaviour toward this sender | diagnostic only |

The first must be 1.0 because a reason that names nothing is not an explanation, whatever else is true of it. Anything below 1.0 is a defect with a named cause, not a score to nudge — `test_every_emittable_clause_is_classified` keeps the set of things that count as *naming something* closed, so a new generic phrasing cannot quietly appear and pass.

The second is **capped by how many rows were actually decided on history**, and that cap is well under 100%. On the current corpus 36 rows are determined by a safety rule or carry an integrity warning; on those the decisive factor is the safety concern, and the one sentence available spends itself naming that. Three more have no relationship evidence to describe (topical-tier evidence, or nothing readable). Making those rows cite history would mean printing a claim the row does not support in order to move a number — the failure mode §4 exists to prevent. Treat this metric as a check that the composer is *using* the history it has, never as a target with a threshold.

---

## 4. Anti-overfitting protocol

**The 30 labeled rows are an eval set. They are never a tuning target.**

This is the operational form of [GENERALIZATION_POLICY](GENERALIZATION_POLICY.md), and it is stated as rules because the temptation is strong and arrives late in a build when time is short.

### The rules

1. **No threshold may be adjusted to fix a specific sample row.** If `MIN_PATTERN_OBSERVATIONS` is 2 because two instances is the minimum that constitutes a pattern, it does not become 3 because that flips `sample_msg_045`. Changing a constant requires a new *reason*, in words, that would hold if the corpus were different — the [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance) source-(b) test.

2. **A change is justified by its reasoning and then checked against metrics — never selected by metric search.** The order is: state why the rule should exist, write it, then look at what happened. Sweeping a threshold across values and keeping the best is prohibited, even when each individual value is defensible, because the selection is what fits the sample rather than the reasoning.

3. **No per-row inspection drives a code change without a general finding.** Reading a wrong row to understand *why* is essential. Turning "this row is wrong" into a fix requires first stating the class of message that is being mishandled. If the class has one member and that member is a sample row, it is not a class.

4. **No sample-row id appears anywhere in `code/`.** Enforced by `test_no_corpus_literals`.

5. **The labeled set is read after the change, not before.** Deciding what to build by looking at which sample rows are wrong is metric search with extra steps.

### The tell

If a change's justification cannot be written without referring to the labeled outcome — if the honest sentence is "because it improves sample accuracy" — the change is overfitting regardless of how principled the code looks. That sentence is the tell, and it is grounds to drop the change.

---

## 5. When label-free and label-dependent disagree

They will. When they do, **the label-free set is more trustworthy here**, for four reasons that are specific to this dataset rather than general methodological preference:

1. **Sample size.** 110 rows versus 30. One labeled row moves any labeled rate by 3.33pp; the ordinary noise on a labeled metric is larger than most real effects.

2. **Distribution mismatch.** The 30 labeled rows are not a sample of the 110 targets — the id overlap is **zero** ([DATASET_ANALYSIS §1.2](DATASET_ANALYSIS.md#12-sample_messagescsv--30-rows--16-cols)). They are 30 separate messages, all timestamped 2026-07-31, hand-authored to *illustrate* the label definitions. The targets are spread evenly across 14 days and were generated to be routed. Labeled accuracy measures agreement with an illustrative set, not performance on the target distribution.

3. **Class sparsity.** Six of the eleven `message_type` values have one or two labeled examples, and `payment` has none. A "regression" in a class with one example is one row.

4. **What each actually measures.** Label-free metrics are largely *assertions we have made about our own system*: ungrounded citations must be zero, reasons must be in register, divergence must have a named cause, an offline rerun must be deterministic. These are not matters of opinion or of sampling — they are true or false. Labeled accuracy is an estimate with a wide interval.

### Measured asymmetries between the eval set and the target set

Established by the Phase 0 preflight (2026-08-01) over all 140 rows. The two sets are routable on equal footing — every FK resolves in both, no row in either has zero temporally-eligible evidence, all 31 ground-truth citations resolve with correct owner and strict temporal ordering, and conversation/media shape is close (57/27/15% vs 57/27/17%). These are the differences that remain:

| # | property | targets (110) | labeled (30) | consequence |
|---|---|---|---|---|
| **A1** | business rows that are unverified **and** domain-mismatched | **7 of 30** | **0 of 8** | **`IMPERSONATION` cannot fire on any labeled row.** Its labeled accuracy contribution is structurally zero, not small. Only 1 labeled business row is even unverified, and its domain matches. |
| A2 | `forwarded_count >= FORWARD_CHAIN_THRESHOLD` | 17 (15%) | 2 (7%) | `FREQUENTLY_FORWARDED_BROADCAST` is exercised by 2 labeled rows. Labeled has 4 distinct forward counts against 10 in targets. |
| A3 | temporal spread | 14 days, 06:00–22:00, 8 rows in-DND | **1 day** (2026-07-31), 08:00–17:00, **0** in-DND | No labeled row can exercise any time-of-day path. Independently corroborates [D7](PROBLEM_ANALYSIS.md#d7--whether-dnd-windows-change-the-action). |
| A4 | receiver-muted group rows | 14 of 63 (22%) | 2 of 17 (12%) | `GROUP_MUTE_CAP` is exercised by 2 labeled rows. |
| A5 | receivers not in the other set | — | u_024, u_025, u_026 | 3 labeled users never appear as targets; 18 of 21 do. |
| A6 | business rows with no `user_business_history` row | 11 of 30 (37%) | 2 of 8 (25%) | Cold-business handling is under-sampled in the eval set. |

**They point one way.** Every asymmetry under-represents the adversarial or structural condition, never over-represents it. The eval set is a set of *illustrative* messages; the target set contains the impersonation accounts, the forward chains, the muted groups, and the night-time arrivals. So labeled accuracy systematically under-measures exactly the rules the policy layer exists for, and a safety rule can be entirely correct while contributing nothing to it.

Two operational consequences, both binding:

1. **A safety rule's labeled accuracy is not evidence about that rule.** `IMPERSONATION` scoring zero labeled rows is its expected state, not a defect, and it must never be tuned against the eval set. Its correctness is established by its trigger's reasoning and by the [ablations](#6-input-dependence-checks) (A3, A4), not by these 30 rows.
2. **A labeled-accuracy regression on a rule that A1–A6 shows is under-sampled carries less weight than the same regression elsewhere**, and the review of a Tier 2 trigger must say which case applies.

### The resolution procedure

| situation | reading |
|---|---|
| label-free clean, labeled down ≤ 1 row | **Noise.** Keep the change on its reasoning. |
| label-free clean, labeled down ≥ 2 rows | **Investigate the rows.** If the change's stated reasoning explains why those rows moved, keep it and record the explanation. If it does not, the reasoning is probably wrong — revert. |
| label-free regressed, labeled up | **Revert.** A Tier 1 invariant broke. An accuracy gain bought with an ungrounded citation or a determinism bug is not a gain, and it will not survive on a different corpus. |
| both regressed | Revert. |
| both improved | Merge, and check the churn-to-net ratio anyway — a large churn with a small net gain means something else moved too. |

**The asymmetry is deliberate.** Label-free regressions block regardless of labeled movement; labeled regressions require investigation but not automatic reversion. That is the correct weighting when one measurement is an invariant on 110 rows and the other is an estimate on 30.

---

## 6. Input-dependence checks

Metrics tell you what the system *produced*. They do not tell you whether it produced it *for the stated reasons*. A system can score adequately while ignoring most of its inputs — and this dataset is small enough that a system keyed on conversation type alone would not look obviously broken.

So: **degrade or remove a signal and verify the output changes in the expected direction.** A null result is a defect, not a pass.

Each check runs against a mutated copy of the dataset. All but the last are offline, because none of them changes the prompt.

| # | ablation | expected direction | a null result means |
|---|---|---|---|
| A1 | Set every `message_events` row to opened + replied | `REPEAT_DISMISSAL` fire count → **0**; mute share falls; `sender_stance` becomes `RECEPTIVE` everywhere | engagement history is not reaching the policy layer |
| A2 | Set every `group_members.group_muted_by_user` to 0 | `GROUP_MUTE_CAP` → **0**; notify share rises | the mute flag is not read, and the statement's muted-group requirement is untested |
| A3 | Set every `business_accounts.verified` to 1 | `IMPERSONATION` → **0** | verification is not gating the impersonation rule |
| A4 | Blank every `official_domain` | `IMPERSONATION` → **0** (all `domain_state` become `UNDETERMINED`) | the three-state domain logic is collapsing blank to `MISMATCH` — the exact bug the enum exists to prevent |
| A5 | Set every `allows_promotions` to 1 and clear `promotions_opted_out_at` | `PROMOTION_OPT_OUT` → **0** | consent fields are not read |
| A6 | Set every `forwarded_count` to 0 | `FREQUENTLY_FORWARDED_BROADCAST` → **0** | the forward chain is not read |
| A7 | Remove all media artifacts from the cache and run offline | media-unread share → 100% of the 23 media rows; mean confidence on those rows falls by ≈ `W_MEDIA_UNREAD` | media is not affecting confidence, and the 8 text-free voice rows are being routed on nothing without saying so |
| A8 | Permute `sender_user_id` across rows (fixed seed) | action distribution shifts materially; many `sender_relationship_class` values change | the system is not using sender relationship at all — it is routing on content and conversation type alone |
| A9 | **(paid)** Replace every `message_text` with one neutral sentence, re-warm | every perceptual and mixed rule → **0** (rule 8's `solicits_purchase` conjunct fails on neutral text); only the four structural-trigger rules fire; output collapses toward the structural baseline | perception claims are not reaching the policy layer |

A7 and A9 are the two that most often surface real bugs, because both test that an *absence* is handled honestly rather than being silently treated as a benign default.

These run as `tests/test_ablations.py` and are part of [Phase 5](IMPLEMENTATION_PLAN.md#7-phase-5--consistency-hardening-and-threat-verification). A1–A8 run in CI on every commit that touches `features/` or `policy/`; A9 runs once per prompt revision, alongside the re-warm it needs anyway.

### Reading an ablation

The check is directional, not quantitative. It asserts a fire count reaches zero, or that a share moves the right way — never that it moves by a specific amount, which would be a source-(c) constant. An ablation that produces the expected direction confirms the wiring; it does not confirm the rule is correct, which is what the reasoning and the review gate are for.

---

## 6a. Reproducibility under independent re-derivation — measured

The perception cache makes a routing run exactly reproducible, but it does so by *fixing* the model's answers. It says nothing about how much those answers would move if they were obtained again. That was measured once, deliberately, by building a **second cache from cold** against the live APIs — 30 media extractions and 127 routing calls, in an isolated cache directory so the existing one was untouched — and diffing the resulting run against the cached run it should reproduce.

### The result

| | |
|---|---|
| `action` rows differing | **3 / 110 — 97.3% stable** |
| `message_type` rows differing | 9 / 110 |
| `evidence_message_ids` rows differing | 3 / 110 |
| `reason` rows differing | 14 / 110 |
| `confidence` rows differing | **24 / 110 — the most volatile column** |
| rows identical across **all six** columns | **81 / 110** |
| labeled action accuracy | 25/30 → 24/30 (**−1 row**) |

Every structural invariant survived: **all 15 rules in the table at the time still fired** (`RISK_TYPE_CAP` was added afterwards, so the table is now 16), **all 8 ablations still held**, `ungrounded_citation_count` stayed 0, and unexplained consistency divergences stayed 0. Two consecutive routes over the freshly-built cache were byte-identical to each other, so the determinism guarantee holds on a cold cache exactly as on a warm one.

### Why the sensitivity is ordered the way it is

The ordering — `action` most stable, `confidence` least — is a property of the architecture rather than an accident, and it is the clearest evidence available that the perception/policy split does what it claims.

**`action` is stable because a deterministic rule table owns it.** Of the rules, the ones that fire most read structural facts — `IMPERSONATION`, `REPEAT_DISMISSAL`, `GROUP_MUTE_CAP`, `COLD_SENDER_CAP` — which are identical between the two runs by construction. A perception claim has to flip *and* be the determining input before an action moves.

**`confidence` is least stable because it aggregates seven terms with no thresholding.** Every other column is a discrete choice that absorbs small perturbations: a claim can flip without changing which rule wins. Confidence has no such absorption — it is a sum, so *any* moved term moves the number, and the type-ambiguity term alone changes the value whenever the model lists one candidate instead of two. Its **ordering properties were unchanged** (`mute` 0.81 > `notify` 0.71 > `digest` 0.69; overridden 0.84 > accepted 0.70), which is what the column is actually for.

`message_type` sits between them because it is a discrete choice that the rule table only sometimes owns — Tier 0 rules set it, and on the ~75% of rows where no rule does, it is the model's ranking passed through unchanged.

### The consequence for every labeled metric

**Re-derivation alone moves the labeled set by ±1 row. The noise floor on labeled metrics is therefore two rows *measured*, not assumed.**

[METRICS.md](METRICS.md) already declines to call a sub-two-row change an improvement, on the arithmetic grounds that one row of 30 is 3.33pp. That was a statement about resolution. This is a stronger and more specific statement about *reproducibility*: a one-row difference between two runs is inside the range the system produces from identical code and identical inputs, so it carries no information about which run is better.

Two rules follow, and they bind:

* **A change worth ≤ 1 labeled row is not evidence of anything.** It must be justified by its reasoning and by label-free metrics, or not adopted.
* **Never swap one artifact for another on a ≤ 1 row difference.** That is not selection, it is re-rolling: repeat it enough times and a better number arrives regardless of whether anything improved. The cold live run scored −1 against the committed artifact and the committed artifact was kept for exactly this reason.

The measurement should be repeated if the model, the prompt template, or the effort setting changes, since all three could alter the variance. It is not repeated per phase — it costs a full paid re-warm.

---

## 7. The loop in practice

**After every change** — `route`, then `metrics diff` against the previous run. Read the diff top to bottom: Tier 1 gates first, then churn-to-net, then override deltas, then the changed-row list. Seconds, no calls.

**Before every commit** — full test suite including architecture and ablation tests. The six [review-question](GENERALIZATION_POLICY.md#3-the-review-question) answers go in the commit message for any change to a rule, threshold, or weight.

**At the end of every phase** — a run directory and its report are committed, so the next phase has a baseline to diff against ([IMPLEMENTATION_PLAN §9](IMPLEMENTATION_PLAN.md#9-cross-phase-rules)).

**Before a paid rerun** — review the accumulated Class B queue and confirm each entry is genuinely a perception error rather than a Class A error misfiled. A prompt revision applied to fix a rule bug costs money and fixes nothing.

**Before submission** — clean-clone reproduction from the shipped cache, `python main.py validate`, and the final report archived with the submission.
