# IMPLEMENTATION_PLAN.md

Build sequence. The organising principle: **a complete, valid `output.csv` exists at the end of Phase 0, produced by rules alone with zero model calls.** Every phase after that improves an artifact that is already submittable, and every phase after Phase 2 iterates for free against cached perception.

Companion documents: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), [DECISION_ENGINE.md](DECISION_ENGINE.md), [CACHING.md](CACHING.md), [METRICS.md](METRICS.md), [EVALUATION_STRATEGY.md](EVALUATION_STRATEGY.md), [GENERALIZATION_POLICY.md](GENERALIZATION_POLICY.md).

---

## 1. Sequencing rationale

Three ordering constraints drive everything below.

**A submittable artifact comes first.** If the deadline arrives mid-Phase-4, there is still a valid 110-row `output.csv` on disk from the last completed phase. No phase is allowed to leave the repository in a state where no output can be produced.

**Measurement lands with the baseline, not after it.** The metrics engine is part of Phase 0. If it arrived in Phase 3, then Phases 1 and 2 would be unmeasured — their effect would have to be reconstructed after the fact, and any regression they introduced would be attributed to Phase 3. Building the measuring instrument alongside the thing it measures costs one phase of effort once, instead of a debugging session per phase forever.

**Caching lands before policy work.** Policy is the thing under iteration and it is where most of the effort goes. If perception is uncached during that work, every rule-table experiment costs a live pass over 110 messages plus 33 media files. Landing the cache first converts the entire back half of the build into free, offline, sub-second iterations. This is the single highest-leverage ordering decision in the plan.

### Phase map

| phase | title | network | leaves behind |
|---|---|---|---|
| **0** | Walking skeleton: rules-only baseline + metrics engine | **none** | first valid `output.csv`; every metric computable |
| **1** | Cache substrate + media pipeline | one paid warm pass over 33 files | media permanently cached; offline plumbing |
| **2** | Perception v1 + perception cache | one paid warm pass over 140 rows | full pipeline runs offline |
| **3** | Full policy rule table | **none** | all 15 rules; safety behaviour real |
| **4** | Evidence, confidence, reason | **none** | all six output columns earned rather than defaulted |
| **5** | Consistency hardening + threat verification | **none** | divergence has named causes; mitigations proven |
| **6** | Submission packaging | one final re-warm if prompts changed | `code.zip`, README, reproducible run |

Phases 3, 4, and 5 — the bulk of the work — require **zero** model calls.

---

## 2. Phase 0 — Walking skeleton and the metrics engine

**Goal.** A complete, valid `output.csv` for all 110 rows, produced by structural rules alone, plus a metrics engine that can grade it. No network access of any kind.

### Files touched

```
code/main.py                        THE canonical entry point (AGENTS.md §6.4): route | validate | bank
code/config.py                       PolicyConfig, ConfidenceConfig, EvidenceConfig, LimitsConfig, ReasonConfig
code/schemas.py                      all enums and models from SYSTEM_DESIGN.md §4–5
code/errors.py                       exception hierarchy
code/logging_setup.py                structured JSONL logger
code/ingest/loader.py                read and validate the 13 CSVs; build indexes
code/ingest/context.py               assemble MessageContext
code/features/{business,group,sender,engagement,content,assemble}.py
code/policy/rules.py                 structural rules only: IMPERSONATION, REPEAT_DISMISSAL,
                                    GROUP_MUTE_CAP, COLD_SENDER_CAP, PERCEPTION_ACCEPTED
code/policy/engine.py                full resolution algorithm (all five effects)
code/scoring/evidence.py             deterministic retrieval
code/scoring/confidence.py           full formula
code/compose/reason.py               clause grammar + register enforcement
code/emit/writer.py                  six-column projection
code/emit/record.py                  RowRecord + manifest serialisation
code/perception/null.py              StructuralPerception: the zero-network stand-in
code/metrics/{loader,label_free,labeled,consistency,diff,report}.py
tests/…                             unit tests per module + fixture run directory
```

### The zero-network stand-in

`code/perception/null.py` provides a `PerceptionClient`-shaped object that returns a `Perception` derived from structure alone:

- every content claim is `False` — no model, so nothing is claimed about content;
- `proposed_action = DIGEST` — the neutral default, since without reading the message we have no basis to interrupt or suppress;
- `type_candidates` from a **structural prior**: `business → business_update`, `group → personal`, `personal → personal`.

The structural prior is marked in code as a Phase-0 placeholder and is **deleted in Phase 2**. It exists so the baseline is non-degenerate: a run that emitted `unknown` for all 110 rows would give the distribution, entropy, and consistency metrics nothing to work on, and their first real exercise would be during a phase that also changed behaviour.

Because every claim is `False`, the perceptual rules cannot fire. Only the four structural rules do. That is the point: the baseline's ceiling is visible in the Phase 0 metrics, and every later phase's gain is measured against it.

### What becomes true

- `python main.py route` writes 110 rows to `dataset/output.csv` with the exact six columns in contract order, in `messages.csv` order, with no network access.
- The same command writes `runs/<run_id>/` containing `manifest.json`, `rows.jsonl`, `eval_rows.jsonl`, `eval_truth.csv` (the 30 sample labels copied at run time, so labeled metrics stay computable from the run directory alone), and a copy of `output.csv`.
- `python -m code.metrics report runs/<run_id>` produces every label-free, label-dependent, and consistency metric in [METRICS.md](METRICS.md).
- The submission is valid, if weak. If everything after this fails, this ships.

### Verification

1. **Contract** — `output.csv` has 110 rows; header is exactly the six columns in order; the `message_id` set equals `messages.csv`'s; row order matches.
2. **Offline** — the whole test suite and a full `route` run pass with network disabled at the process level. `test_no_network` asserts no networking import outside `code/perception/client.py`.
3. **Determinism** — `output.csv` is byte-identical across two consecutive runs, and the run directories are identical under `metrics.diff.assert_runs_identical(baseline, candidate)`, which compares every field **except** the declared volatile allowlist:

   ```python
   # code/metrics/diff.py
   VOLATILE_FIELDS: dict[str, frozenset[str]] = {
       "rows.jsonl":      frozenset({"wall_clock_ms"}),
       "eval_rows.jsonl": frozenset({"wall_clock_ms"}),
       "manifest.json":   frozenset({"run_id", "created_at_utc"}),
   }
   # events.jsonl is excluded from the comparison entirely: it is a log stream
   # whose timestamps and interleaving are inherently volatile.
   ```

   Any differing field outside this allowlist fails the gate. The allowlist is the record schema's authoritative list of run-varying fields — a new timing or timestamp field must be added here explicitly or the gate breaks loudly.
4. **Metrics self-check** — the engine runs against a checked-in fixture run directory and reproduces a stored expected report exactly. This is what proves the engine is post-hoc: it never executed the pipeline.
5. **Architecture** — import-graph test passes; `test_no_corpus_literals` passes; `test_no_magic_numbers` passes.

### Rollback

Not applicable — this is the first artifact. The completion gate instead: **if the metrics engine cannot reproduce its own report from the saved run directory, Phase 0 is not done.** A metrics engine that needs the pipeline in memory is inline instrumentation wearing a package's clothes, and it would fail its purpose in every later phase.

---

## 3. Phase 1 — Cache substrate and media pipeline

**Goal.** All 33 media files decoded and analysed exactly once, permanently. The cache module exists, is the sole owner of the cache directory, and offline-mode plumbing is in place.

### Files touched

```
code/cache/store.py                  the only module that touches .cache/ — see CACHING.md
code/media/locate.py                 path resolution confined to the dataset root
code/media/decode.py                 magic-byte container detection; canonical PCM normalisation
code/media/extract.py                image observation + audio transcription via isolated calls
code/perception/client.py            the single network boundary (shared by media and perception)
code/main.py                              add `warm-media`; add `--offline` flag
code/emit/record.py                  populate real cache outcomes in CallTelemetry (fields exist from Phase 0 as n/a)
code/metrics/label_free.py           add media coverage + media cache hit rate
tests/…                             extension-lies fixtures, path-escape fixtures, corrupt-entry fixtures
```

### What becomes true

- `python main.py warm-media` processes all 20 images and 13 audio files once and writes them to the media cache, keyed by **sha256 of the file bytes**.
- Container format is detected from magic bytes, so the 9 files whose `.mp3` extension lies ([DATASET_ANALYSIS §3](DATASET_ANALYSIS.md#3-media-presence)) decode correctly.
- `F.media_unread` is now real rather than always `False`, so the `W_MEDIA_UNREAD` confidence term becomes live.
- A second `warm-media` is a complete no-op: 33 hits, zero calls.
- A corrupt cache entry reads as a miss and logs; it never raises.

### Verification

1. `warm-media` twice; the second run reports 33/33 media cache hits and makes zero network calls.
2. All 13 audio files produce a non-empty transcript and a detected container; assert the 5 WAV and 4 AAC files are identified as such and not as MP3.
3. Truncate a cache file by hand; re-run; assert it is treated as a miss, logged once, and rewritten.
4. A `file_path` containing `..` and a symlink pointing outside the dataset both raise `MediaPathError`.
5. `test_cache_isolation` — no module other than `code/cache/store.py` references the cache directory.
6. Metrics diff Phase 0 → Phase 1: media coverage moves from 0 analysed to 33; confidence on media-bearing rows drops where media is genuinely unreadable.

### Status (implemented 2026-08-01)

Cache substrate, magic-byte decode, `warm-media`, and the offline preflight are
complete and verified. **The extraction half is gated on credentials**:
`ANTHROPIC_API_KEY` and `ROUTER_MODEL_ID` are unset, so `warm-media` caches every
container fact but performs no image description or transcription, and all 23
media-bearing rows carry `extraction_unavailable`. The paid pass is one command
once credentials exist:

```bash
cd code && python main.py --dataset ../dataset warm-media --allow-network
```

`AnthropicMediaClient.describe_image` / `.transcribe_audio` are the two bodies
still to fill; everything around them — key construction, envelope, atomicity,
idempotence, corrupt handling, offline preflight — is done and tested.

Measured on the corpus: **19 of 33 media files carry an extension that
misidentifies the container** (13 audio all named `.mp3`: 5 WAV, 4 AAC/M4A,
4 genuinely MP3; 20 images all named `.jpg`: 7 PNG, 2 WebP, 1 MP4 video). This
is larger than the 9-of-13 recorded in DATASET_ANALYSIS §3, which examined audio
only.

### Rollback

Media results are content-addressed and immutable, so there is nothing to invalidate. If Phase 1 makes metrics worse, `git revert` the phase commit and re-run offline; the cache directory can stay — Phase 0's code simply does not read it. **Cache contents are never deleted as part of a rollback**, because re-warming costs money and the entries are keyed by content, so a reverted code path cannot produce a stale hit.

---

## 4. Phase 2 — Perception v1 and the perception cache

**Goal.** One paid pass populates perception for all 110 target rows and all 30 labeled eval rows. From the end of this phase, the entire pipeline runs offline.

### Files touched

```
code/perception/prompt.py            fenced untrusted blocks; PROMPT_TEMPLATE_VERSION constant
code/perception/parse.py             strict validation into Perception
code/cache/store.py                  perception namespace + key construction
code/main.py                              add `warm-perception`; wire --offline preflight
code/policy/rules.py                 enable the perceptual rules (Tier 0 subset)
code/perception/null.py              DELETED — structural prior removed
code/metrics/label_free.py           add schema retry / parse failure / perception cache hit rate
```

### What becomes true

- `python main.py warm-perception` calls the model once per row across 140 rows (110 targets + 30 eval) and caches each result under a key derived from **the exact bytes sent plus the prompt template version plus the model id** ([CACHING.md §3](CACHING.md#3-perception-cache)).
- `python main.py route` executes the full pipeline with **zero network calls**, and if any required record is missing it prints the missing `message_id`s and exits non-zero rather than silently calling out.
- Changing a prompt misses the cache. Changing `PolicyConfig` does not — policy is deliberately excluded from the key.
- The structural prior is gone; `message_type` now comes from perception.

### Verification

1. `warm-perception`, then `route` twice: both offline runs make zero network calls, produce byte-identical `output.csv`, and are identical under `metrics.diff.assert_runs_identical` (the Phase 0 volatile allowlist).
2. Delete one perception cache entry; `route` names that exact `message_id` as missing and exits non-zero.
3. Change one character of the prompt template; assert 140/140 misses. Change `FORWARD_CHAIN_THRESHOLD`; assert 140/140 hits.
4. Feed the parser a response containing a `confidence` field; assert `PerceptionSchemaError`, not a silent trim.
5. Metrics diff Phase 1 → Phase 2: `message_type` entropy rises sharply off the structural prior; the override-direction breakdown becomes non-trivial.

### Rollback

This is the phase most likely to regress labeled accuracy, because it replaces a deterministic prior with a model judgement. The rollback is *not* to revert the phase — offline iteration depends on it. Instead:

- If `message_type` accuracy regresses by **≥ 2 rows** on the eval set ([METRICS.md](METRICS.md) noise floor), revise the prompt and re-warm. Prompt revision is the only paid iteration in the plan, so it is batched: collect several prompt criticisms, apply them together, re-warm once.
- If a **label-free** integrity metric regresses — hard parse failures > 0, ungrounded citations > 0 — that is a code defect, not a perception defect. Fix it offline; no re-warm needed.

---

## 5. Phase 3 — Full policy rule table

**Goal.** All 15 rules from [DECISION_ENGINE.md §2](DECISION_ENGINE.md#2-precedence-table) implemented, with the resolution algorithm exercised across every conflict case. **Zero model calls.**

### Files touched

```
code/policy/rules.py                 remaining 11 rules
code/policy/engine.py                guard suppression; cap-beats-floor resolution
code/features/assemble.py            sender_holds_payment_authority; relationship classes
code/metrics/consistency.py          composite-key grouping
tests/test_policy_table.py          one case per rule + all six conflict cases
```

### What becomes true

- Every rule fires at least once across the 110 rows, or its zero count is explained in the phase notes. A safety rule that never fires is either unnecessary or broken.
- The six conflict cases in [DECISION_ENGINE.md §4](DECISION_ENGINE.md#4-conflict-resolution) are covered by tests constructed from typed inputs, not from dataset rows.
- The consistency scan reports structurally similar rows that diverged, with the override codes on each side.

### Verification

1. Table-driven tests: one per rule, plus cases B and C (which differ in one perception claim and must produce opposite actions).
2. Every `RowRecord` has exactly one firing with status `APPLIED`.
3. Metrics: fire count per override code is non-zero or explained; rows with ≥2 competing overrides are reported and each is inspected by hand.
4. Consistency: every divergent group has a named override code explaining the divergence. **A group that diverges with no differing code is a bug**, and this phase does not complete while one exists.
5. Iteration cost check: a full offline re-run plus metrics diff completes in seconds, with zero calls.

### Rollback

Per-rule, not per-phase. Each rule lands as its own commit carrying its six [review-gate](GENERALIZATION_POLICY.md#3-the-review-question) answers. If the metrics diff shows a rule regressing labeled accuracy by ≥ 2 rows **and** the regression is not explained by its stated justification, revert that one commit. A rule that regresses ≤ 1 row is within noise and is kept or dropped on its reasoning, never on the metric — see the anti-overfitting protocol in [EVALUATION_STRATEGY.md](EVALUATION_STRATEGY.md).

---

## 6. Phase 4 — Evidence, confidence, reason

**Goal.** The three fields that Phase 0 defaulted are now earned. **Zero model calls.**

### Files touched

```
code/scoring/evidence.py             tier ranking, content overlap, pattern-aware id count
code/scoring/confidence.py           all seven terms live
code/compose/reason.py               full clause grammar
code/metrics/label_free.py           evidence coverage, ungrounded citations, reason health
```

### What becomes true

- Evidence is tiered and deterministic; ties break by `(tier, recency, message_id)` so two runs cannot differ.
- Ungrounded citations are **structurally impossible**: the selector chooses only from the shortlist it was given, and the shortlist goes into the `RowRecord`, so the metric verifies the invariant rather than estimating it.
- Confidence varies with evidence strength, engagement alignment, media readability, and type ambiguity, instead of sitting at the base value.
- Reasons pass the register check and name the decisive factor.

### Verification

1. `ungrounded_citation_count == 0` — a hard merge gate, not a target.
2. Evidence invariants: every cited id belongs to the receiving user and predates the target. Violations raise.
3. Confidence: unit tests assert each term's contribution in isolation and that clamping binds at both ends. Distribution is expected to be **wider** than the sample's [0.78, 0.91] — that divergence is the deliberate trade recorded in [PROBLEM_ANALYSIS D3](PROBLEM_ANALYSIS.md#d3--what-reasonable-confidence-calibration-means), not a regression.
4. Reason health: 100% within register bounds; share naming concrete evidence reported and inspected.

### Rollback

Confidence is the risky part, because calibration metrics are computed on 30 rows and are indicative only. **Do not tune weights against ECE or Brier at n=30.** If calibration looks poor, check first whether the confidence *ordering* is sensible — are overridden rows lower than accepted ones, are media-unread rows lowest — and only revise a weight if its stated justification is wrong. Revert to the previous weight set if a change cannot be justified in words.

---

## 7. Phase 5 — Consistency hardening and threat verification

**Goal.** Every divergence has a named cause; every mitigation in [THREAT_MODEL.md](THREAT_MODEL.md) is demonstrated. **Zero model calls**, except for the ablation that requires re-warming.

### Files touched

```
code/policy/rules.py                 refinements driven by the consistency scan
code/perception/prompt.py            only if a perception-owned error class is confirmed
tests/test_threat_model.py          one test per mitigation
tests/test_ablations.py             input-dependence checks
```

### What becomes true

- No consistency group diverges without a differing override code.
- Each mitigation has a test: fence-token neutralisation, free-text exclusion from the prompt, schema rejection of a `confidence` field, path-escape rejection, media-limit rejection, evidence-owner and temporal invariants.
- The input-dependence checks in [EVALUATION_STRATEGY.md](EVALUATION_STRATEGY.md) pass — degrading a signal moves the output in the expected direction, proving the system uses the inputs it claims to.

### Verification

1. Consistency scan: zero unexplained divergent groups.
2. Ablations, each offline except the last: zero out engagement history → `REPEAT_DISMISSAL` count falls to 0; set all `group_muted_by_user = 0` → `GROUP_MUTE_CAP` to 0; set all `verified = 1` → `IMPERSONATION` to 0; blank all `official_domain` → `IMPERSONATION` to 0; drop all media → media-unread share rises and confidence on those rows falls by `W_MEDIA_UNREAD`. **A null result on any ablation means the system is not using that input** and is a defect.
3. Threat tests all pass.

### Rollback

Per-change. A hardening change that reduces divergence but regresses labeled accuracy by ≥ 2 rows is reverted; consistency is a diagnostic, not an objective. Making the system uniformly wrong would improve consistency.

---

## 8. Phase 6 — Submission packaging

**Goal.** A reproducible submission.

### Files touched

```
README.md                           what the system does, then how to run it
pyproject.toml                      the single dependency manifest (see below)
main.py                             `validate` subcommand: contract checks only
make_zip.py                         archive build + content secret scan
docs/INTERVIEW_PREP.md              constants, decisions, measured facts, defects
```

**Two deviations from this plan as originally written, both resolved deliberately:**

*`requirements.txt` is not used; `pyproject.toml` is canonical.* Two dependency manifests are two sources of truth that drift, and `pyproject.toml` already has to exist — it carries the package layout (`code/` is a source root, not a package), the pytest configuration, and the ruff configuration. A `requirements.txt` would restate a subset of one of its fields and nothing else. Install with `pip install -e .`, or `pip install -e ".[dev]"` for the test suite.

*The contract check is a `validate` subcommand, not a `--verify` flag.* It takes a path argument, so it can check any candidate CSV — a run directory's output, a banked fallback, or the submitted file — rather than only whatever the last run happened to write.

### What becomes true

- `code.zip` contains the full runnable solution, prompts, config, docs, **and the warmed cache**, with no secrets and no `.venv`.
- `python main.py route` reproduces the submitted `output.csv` byte-for-byte from the shipped cache, **with zero network calls and no credentials**.
- The final live re-warm has been run **only if** prompts changed since the last one, and the resulting metrics diff has been reviewed.

### Verification

1. Clean-room test: extract `code.zip` to an empty directory, fresh venv, `pip install -e .`, `python main.py route` reproduces the submitted CSV exactly. Run from the **archive**, not a git clone — the archive is what is submitted, so it is the artifact whose reproducibility matters.
2. `python main.py validate`: 110 rows, exact columns and order, ids match, no empty field, confidence in [0, 1], every `evidence_message_ids` value either `none` or `;`-joined `message_NNNN` ids present in `message_history.csv`.
3. Secret scan across the zip — **contents of every member**, not the file list, with each pattern exercised against a planted example (`tests/test_packaging.py`). A scan that has never been shown to fail is not evidence.
4. Final metrics report archived alongside the submission.

### Rollback

The last green run directory is the fallback submission. Every phase's `runs/<run_id>/output.csv` is retained, so reverting to any prior phase's artifact is a file copy.

---

## 9. Cross-phase rules

**Every phase ends with a run directory.** A phase is not complete until `python -m code.metrics report runs/<id>` succeeds and its report is committed to `runs/`. That makes the metrics diff against the previous phase available immediately.

**Every phase after 0 is diffed against its predecessor.** The diff's headline is the churn-to-net-improvement ratio ([METRICS.md §7](METRICS.md#7-run-comparison)). High churn with near-zero net gain means the change reshuffled behaviour rather than improving it, and that is a reason to look harder regardless of what the accuracy number did.

**The rollback trigger is uniform:**

| signal | threshold | action |
|---|---|---|
| label-free integrity metric regresses | any non-zero increase in hard parse failures, ungrounded citations, evidence-invariant violations, or reason-register violations | **revert immediately** — these are correctness invariants, not tradeoffs |
| labeled accuracy regresses | **≥ 2 rows** (≥ 6.7pp at n=30) | revert unless the change's stated reasoning explains the regression |
| labeled accuracy regresses | ≤ 1 row | **not a signal.** Keep or drop on reasoning alone |
| consistency divergence increases | any unexplained divergent group | fix before the phase completes |
| churn/net ratio high | churn ≥ 10 rows with net ≤ 1 row | investigate before merging; probably a reshuffle |

**Paid work is batched.** Only Phases 1, 2, and possibly 6 make network calls. Prompt criticisms accumulate and are applied in one re-warm rather than one at a time — see [EVALUATION_STRATEGY.md](EVALUATION_STRATEGY.md).
