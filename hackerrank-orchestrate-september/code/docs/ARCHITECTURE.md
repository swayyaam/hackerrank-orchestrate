# Architecture — Buy or Wait?

This document states the design, the alternatives that were rejected and why,
the rules that keep the system general, and the boundaries that tests enforce.

---

## Core design decision

**A deterministic financial solver with a narrow LLM perception layer. Not an
LLM reasoning system.**

The model does exactly four things:

1. extract amounts from images for blank-amount events
2. interpret message text into typed deltas
3. one bounded action-selection step when evidence is ambiguous
4. write the final explanation from an already-frozen evidence ledger

Code does everything else: normalization, FX, lifecycle collapse, recurrence
inference, forecasting, capacity, plan generation, ranking, status derivation,
formatting, validation.

**The model may never produce a number that appears in the output CSV**, with a
single exception: an amount read off an image, which is then validated and
normalized by code.

---

## Alternatives explicitly rejected

**1. End-to-end LLM decisioning.**
The spec defines a six-key lexicographic ranking and exact structural rules for
partial payment and installments. A model approximates these; code satisfies
them. Also unbounded cost per request.

**2. A fully general ReAct agent over the dataset.**
No bounded step count means no cost ceiling and no reproducibility, and the
decision surface is fully specified so there is nothing to explore.

The original design answered that with *exactly one bounded choice point* — a
`resolver` role that would pick an action from a closed set when evidence was
ambiguous. **It was never built, and it has been removed rather than left
looking live:** the `bow/agent/` package (an empty `__init__.py`), the
`resolver` role in `config/models.yaml`, and the `explain` role that would have
had a model write `decision_explanation`. This is a deliberate scope decision
taken under time pressure, not a discovery that they were unnecessary.

What ships instead is a fully deterministic pipeline with a narrow perception
layer. The model does two jobs: transcribe a document, and turn a message into a
typed delta. Every selection after that is code. In particular **the vision
escalation is code-driven, not model-driven** — `bow/perception/vision.py`
escalates to the second read when the arithmetic does not close, and
`reconciling_substitute` resolves a disagreement by checking the line-item sum
against a figure the document prints, not by asking a model which to believe.
`decision_explanation` is rendered by `bow/explain/template.py` from the frozen
decision.

**The tradeoff, named.** A bounded resolver would have helped where the typed
fields genuinely underdetermine the answer — `LIMITATIONS.md` L1 (income
continuation), L2 and L3 (two flags the sample set cannot discriminate). Those
are currently settled by a configured default rather than by reasoning over the
specific case, and the default is sometimes wrong. What we get in exchange is a
system whose decisions are reproducible, auditable line by line, and testable
with zero API calls — and the model-written explanation path, which would have
been the most likely source of an ungrounded number in the output, does not
exist to go wrong.

**3. RAG with embedding retrieval.**
Join keys are explicit and the recon confirmed zero orphans in every direction.
Embeddings would inject recall error into a problem that currently has none.

**4. Letting the explanation writer see raw source data.**
In the previous edition citations drifted from decisions. Preventing that
structurally is a primary design goal.

---

## Single vendor: Sarvam

Every model call goes to Sarvam, authenticated with one `SARVAM_API_KEY`. No
other provider, no other key. `openai` is a dependency only as an
OpenAI-compatible client pointed at Sarvam's `/v2` base URL; it is never
pointed at OpenAI. `tests/test_no_sdk_leak.py` fails the build on an
`anthropic` import anywhere.

Two Sarvam behaviours are pinned at every call site because both fail silently:

- **Reasoning is on by default** and reasoning tokens count toward completion
  tokens. A `max_tokens` that is too small is consumed entirely by reasoning and
  returns HTTP 200 with empty content. Probe 6 measured the reliable floor at
  1024 on `/v1` and 512 on `/v2`; every role sits above it with headroom.
- **The `max_tokens` ceiling is plan-dependent.** It is read from
  `config/models.yaml`, never assumed. Probe 7 measured 32768 accepted and
  131072 rejected on this account.

Probe 5 additionally measured that **temperature 0 alone is not byte-reproducible
on this provider, but temperature 0 with a fixed `seed` is.** Every
deterministic role therefore pins a seed.

Document AI is asynchronous: create a job, poll `get_status` until a terminal
state, then fetch results. Terminal states are `completed`,
`partially_completed`, `failed`, `rejected`. **`partially_completed` is a
flagged partial read, never a success.** The documented rate limit is 10
requests per minute on all plans, so the perception layer uses a real token
bucket, not a sleep.

---

## Language routing, not language hardcoding

Sarvam's own models cover Indian languages plus English — 23 for Sarvam Vision
and Sarvam Translate, 11 for Sarvam-105B and Mayura. Content outside that set
must not be silently fed to a model that was not trained for it. Sarvam also
serves GLM, Gemma and DeepSeek on the OpenAI-compatible `/v2/chat/completions`
endpoint, with the same key and credits, explicitly not tuned for Indian
languages. These are the out-of-set route.

**The routing rule derives from detection, never from an id:**

- For **message text**, call language detection first. If the detected language
  is inside the configured in-set list, route to the in-set text model. If
  outside, route to the configured out-of-set text model on `/v2`.
- For **documents**, attempt the in-set vision route with the configured default
  language. If the two reads fail reconciliation, or the job returns
  `partially_completed`, escalate to the configured out-of-set vision model.

The in-set language list and the out-of-set model choices live in
`config/models.yaml`. No message id, image id, or language literal appears
anywhere in source; `tests/test_no_hardcoding.py` enforces that.

---

## Why `bow/` exists rather than making `code/` the import root

With `code/` on `sys.path`, every sibling directory becomes an implicit
namespace package under PEP 420, and a top-level `tests` package is a known
pytest failure mode. Separately, Python ships a stdlib module named `code`, and
a package by that name shadows it. One package directory removes both problems.

`bow/` is the only importable package. `config/`, `prompts/`, `scripts/`,
`tests/`, `docs/` and `evaluation/` are sibling directories inside `code/` and
must never become packages — none of them contains an `__init__.py`, and
`tests/test_no_stdlib_shadow.py` fails the build if one appears.

---

## Generalization mandate

This must work on a dataset nobody here has seen. The recon describes one
sample of a distribution, not the distribution. **Encode rules, never
observations.**

Hard prohibitions, enforced by `tests/test_no_hardcoding.py`, which walks the
AST of every module under `bow/`:

- No string literal matching an `event_id`, `image_id`, `request_id`,
  `user_id`, `message_id`, or `payment_option_id` pattern.
- No branch on a `description` or `message_text` substring. Behaviour derives
  from typed fields only: `event_type`, `status`, `direction`, `flexibility`,
  `category`, `currency`, dates.
- No numeric literal that is a dataset amount, rate, or threshold. Money and
  rates come from the data. Tunable thresholds come from config.
- No literal count of rows, users, images, or categories.
- No language code, model id, or endpoint literal. All from config.
- No vendor name, document type, or layout description in any file under
  `prompts/`.
- No assumption that a set observed in the recon is closed, except the enums
  the spec itself defines.

### Enum policy

Every **spec-defined** enum is typed as an enum and fails closed on an unknown
value: `affordability_status`, `recommended_payment_method`, `request_type`,
`event_type`, `status`, `direction`, `flexibility`, `payment_method`. These are
contract.

Every **dataset-derived** vocabulary is a plain string with an unknown-value
counter surfaced in the run report: `category`, `description`, `source_type`.
These are samples, not contract.

---

## How `sample_requests.csv` was used, and why that is within the contract

AGENTS.md §6.1 draws the line: the sample set exists *"to understand the
expected format and decision style, not as labels for evaluation requests."*
It was used in exactly three ways, all on the understanding side of that line.
**First, for format** — the published rows fixed the amount rendering, the
`YYYY-MM-DD:amount` plan syntax, the `stop:`/`reduce_to:` change syntax, and the
convention that an empty `earliest_date_for_full_payment` is empty rather than
absent. **Second, as a discriminating test between two readings of an ambiguous
rule** — every entry in `config/decisions.yaml` records a
`sample_discriminates` field, and where it is true the published answers were
used to decide which reading the organisers meant (Q23, for instance, was
settled by a user whose only income is irregular and whose published answer is
`not_affordable`). That is reading the specification through worked examples,
which is what a worked example is for. **Third, as the measurement set** for
every accuracy figure quoted in this repository, which is why those figures are
reported as a 25-row sample and never as an estimate of evaluation-set score.

What it was *not* used for: nothing keys on a `request_id`, no published amount,
date, status or plan appears anywhere in `code/`, and no threshold was fitted to
minimise error against the 25 rows. The seven answer columns are parsed by the
loader — refusing to read a column the organisers supply would be a silent
truncation — and then go no further. `tests/test_sample_set_isolation.py`
enforces that in three ways: the labelled row type is confined to its contract
and its loader; no module that decides, projects, renders or validates may even
name the sample set; and, decisively, the whole sample run is repeated with
every published answer stripped from the input, asserting the predictions come
out byte-identical. `tests/test_no_hardcoding.py` separately forbids dataset
identifier literals and dataset-derived amounts anywhere under `bow/`.

The one place to be precise about is the decision register. Choosing
`never_project` for Q23 because the sample set shows it *is* using the sample
set — to resolve an ambiguity in the rules, which the contract permits, rather
than to fit a value to a row, which it does not. The distinction is recorded per
entry in `decisions.yaml`, and any entry resolved this way says so in its
rationale rather than presenting the choice as derived from first principles.

---

## Module dependency graph

```text
                       contracts          (pydantic models; imports nothing internal)
                           ^
        +------------+-----+-----+---------------+
        |            |           |               |
     engine       format      explain         validate
   (pure: no    (pure: no     |  ^               ^
    I/O, clock,  I/O, clock,  |  |               |
    randomness,  randomness,  |  |               |
    network)     network)     |  |               |
                              v  |               |
                          providers  <-----------+---- perception
                        (the ONLY package         (vision, messages,
                         permitted to make         untrusted guard,
                         a network call; owns      routing)
                         the rate limiter, job        ^
                         poller, retries, and         |
                         the usage meter)          data
                                                (loaders, normalization
                              settings           incl. lifecycle collapse and
                        (typed config, role       FX, recurrence)
                         resolution, limiter)
                              paths              usage
                        (CWD-independent     (renders evaluation/
                         path resolution)     usage_report.md from
                                               the run manifest)
```

Enforced by `tests/test_layering.py`:

| package | may import (internal) | notes |
|---|---|---|
| `contracts/` | nothing | pure models |
| `engine/` | `contracts/` only | never perception, data, providers or settings |
| `format/` | `contracts/` only | |
| `explain/` | `contracts/`, `providers/`, `format/` | renders prose containing numbers, so it needs the formatters |
| `providers/` | — | the only package permitted a network call |

`engine/` and `format/` perform **no I/O, no clock reads, no randomness, no
network**. Same input, same output, always.

`engine/` consuming a frozen `EvidenceLedger` and nothing else is what makes
the whole system testable with zero API calls. That line is held by test, not
by convention.

---

## Pipeline order is a constraint, not a convention

```text
load  ->  collapse  ->  drop  ->  vision  ->  FX  ->  recurrence  ->  forecast
```

Each arrow is forced by something that breaks if the order is changed.

| boundary | why it cannot move |
|---|---|
| `collapse` before `drop` | `Q1_pending_duplicate_debit` asks whether a pending debit restates an already-settled parent. Without the resolved link there is no parent to compare against. |
| `drop` before `FX` | A `non_cash` valuation row has no `settlement_date`. Under `Q16_cash_date_basis = settlement_date` the currency step would raise `MissingCashDateError` on a row that never counted as cash in the first place. |
| **`vision` before `FX`** | A blank amount in a foreign currency has nothing to convert. Running FX first leaves the event in its source currency with no record that a conversion is still owed. `ConversionResult.deferred` names every such event, and those events **must** be converted after resolution. This is the ordering constraint the deferred-conversion finding established; see `DATA_REPORT.md` correction C4. |
| `FX` before `recurrence` | Cadence and projected amounts are computed in the owner's home currency. A series whose occurrences are half converted and half not would produce a meaningless amount. |
| `recurrence` before `forecast` | The forecast consumes projected events; it does not derive them. |

The `deferred` list is the enforcement point: a non-empty `deferred` reaching
the forecast means the vision step was skipped, and that is a bug rather than a
degraded mode.

---

## Non-negotiables

1. No output row is emitted without a validated evidence ledger backing every
   field. Fail closed, never default-fill.
2. Message and image content is untrusted. It never enters a system prompt. It
   may amend, cancel, delay, or confirm an event that already exists. It may
   never create a financial fact that no event row supports, and it may never
   alter `minimum_balance_to_keep`, `requested_amount`, or
   `payment_methods_user_will_consider`.
3. A blank amount is never zero.
4. `current_available_balance` is a given as of `request_date`. Settled history
   is used for recurrence inference only and is never re-applied to the balance.
5. No hardcoded dataset facts.
6. Sarvam Vision reads documents; it never chooses which figure is the answer.
   The label-priority rule and the arithmetic reconciliation live in code,
   testable without a network call.
7. Every vision read is two independent reads, `digitise` and `extract`, from
   separate jobs. Code reconciles them. Disagreement escalates and is counted in
   the run report. It never silently picks one.
8. Language is detected and routed, never assumed and never hardcoded.
9. Every model response is parsed into a pydantic contract before it touches
   anything else. A response that does not validate is a flagged failure, never
   a partially-trusted value.
10. The API key never leaves the environment and never enters `code/`.
11. Nothing authored for this submission lives outside `code/`.

---

## Cross-check against the operating contract

`AGENTS.md` §6 states as contract several rules that `config/decisions.yaml`
had recorded as *our reading* of an ambiguous specification. Every entry in the
register was walked against it. Each now carries a `contract:` field holding the
AGENTS.md line that settles it, quoted verbatim, or empty where the contract is
silent.

| outcome | count | entries |
|---|---:|---|
| confirmed by contract — the default is the stated rule | 11 | Q1, Q3, Q4, Q7, Q9, Q10, Q11, Q13, Q16, Q23, Q26 |
| constrained, not resolved — the principle is contract, the number or the verb is still ours | 6 | Q18, Q19, Q21, Q27, Q28, Q29 |
| contract silent — still our reading, and still a scoring risk | 11 | Q2, Q5, Q6, Q8, Q12, Q14, Q15, Q17, Q20, Q22, Q24 |
| **contradicted by the contract** | **0** | — |

(28 entries; Q25 was never opened — it was ruled out before implementation.)

Nothing in AGENTS.md contradicts a recorded default. Three things moved, all of
them cases where the code and the register had drifted from each other or from
the contract, and none of them changed a single output row:

- **`Q7_message_sourced_future_income` flipped to `require_event_row`.** The
  register recorded a permissive default that was never implemented;
  `bow/perception/untrusted.py` has always dropped a delta that names no target
  event row. The flag now says what the code does.
- **Spending-change permission is enforced rather than asserted.** §6.2 is
  stricter than `problem_statement.md` here — *non-protected, flexible, and in a
  category the user permits*, where the problem statement says only *flexible*.
  See `LIMITATIONS.md` L8.
- **Rule 6 orders option ids numerically.** See `LIMITATIONS.md` L7.

Two clauses are worth stating precisely because a careless reading flips them:

**"Do not count pending credits … until they settle"** (§6.3). *Until they
settle* is a change of status in the data, not a date passing. A pending credit
carrying a future settlement date has still not settled, so it is never counted.
§6.1's *"treat settled, pending, scheduled, and unrealized according to their
cash state"* says the same thing from the other side.

**The ranking order** (§6.3) — completes by deadline, avoids spending changes,
minimizes total cost, starts earlier, fewer payments — matches
`bow/engine/ranker.py`'s `sort_key` element for element. §6.3 stops at five.
`problem_statement.md` lists a sixth, *lowest `payment_option_id`*, and that is
kept: §0 and §1 of AGENTS.md say `problem_statement.md` governs task semantics,
so the sixth key is a rule the contract does not repeat rather than a rule the
contract removed. The discrepancy is recorded here so nobody later reads the
extra key as invented.

---

## Known assumptions

Every row is an assumption the implementation will rest on. `decisions.yaml`
carries the fifteen specification ambiguities separately; this table is for
assumptions about the *environment and data shape* rather than about the rules.

Recon observations are listed here as **warning-level checks that report a
count, never raise**. The general case is built for each one; if a future
dataset breaks it, the run report shows a loud number rather than a crash or
silently wrong output.

| assumption | alternative not chosen | where enforced | how I would detect it is wrong |
|---|---|---|---|
| **FALSIFIED, AND THE GENERAL CASE HELD.** The recon saw no request with more than one eligible supplied option; the evaluation set has three. AGENTS.md §6.1 states plainly that *"A request has two to four options"*, so the observation was never the rule | The observation was never coded to — the full six-key ranking was implemented across a candidate set from the start | `bow/engine/ranker.py`; `multiple_eligible_supplied_options` in the run report | It fired: 0 on both reconnaissance sets, **3** on the evaluation set. All three resolved above the tie-breakers — one on rule 1 (the alternative missed the deadline) and two on rule 3 (a financing fee made the installment plan cost more than paying partially). Coding to the observation would have made three rows silently wrong with no signal |
| Every blank-amount event has exactly one image | Support zero images (fail closed) and many images (reconcile across them) | `bow/perception/vision.py`; warning counters | Report rows `blank_amount_without_image` and `blank_amount_multi_image` are non-zero |
| FX rates are constant per currency pair over time | Look rates up per exact pair and date, with no interpolation or inversion | `bow/data/normalize.py` (`convert_currency`); warning counter | Report row `fx_pairs_with_varying_rate` is non-zero |
| Lifecycle chains are always length two | Walk chains of arbitrary depth, detect cycles, detect multiple inbound links | `bow/data/normalize.py` (`collapse_lifecycles`); warning counters | Report rows `chain_length_max > 2`, `chain_cycles`, `chain_multi_inbound` are non-zero |
| All installment plans are three payments long | Generate a plan from any supplied option's count and frequency | `bow/engine/plans.py`; warning counter | Report row `installment_plan_length_distribution` shows values other than 3 |
| `flexibility` and the profile pipe-lists are redundant **in one direction only**: a flexible event's category is always in the matching profile list, but the converse does not hold | Treat the redundancy as bidirectional and require exact agreement | `bow/data/normalize.py` — `flexible_event_outside_profile_lists` (load-bearing) and `fixed_event_in_changeable_category` (informational) | The load-bearing counter is non-zero, meaning a spending change could exceed what the profile authorises. See `DATA_REPORT.md` correction C2 |
| A missing FX pair is a hard error, never inverted or triangulated | Derive the reciprocal rate, or route through a third currency | `bow/data/normalize.py` (`convert_currency`) | A required conversion raises rather than silently using a derived rate |
| **REVERSED.** `dataset/` is read-only and predictions are written to `<repo_root>/output.csv`, beside `code/` and `dataset/` rather than inside either | Originally `code/output.csv`, on the reading that everything authored belongs inside the submission root. That was wrong: the root `README.md` states that the blank `dataset/output.csv` is a reference template and *the final generated file must be the root-level `output.csv`* | `bow/paths.py` (`OUTPUT_PATH`, now derived from `REPO_ROOT`); `tests/test_submission_hygiene.py` asserts the root file exists, that no copy remains under `code/`, and that `dataset/output.csv` is still blank | The graded file is absent from the run root, or a second `output.csv` reappears inside `code/` and the two disagree |
| `.env` at the repository root, outside `code/` | Ship a `.env` inside the submission | `scripts/check_env.py`, `tests/test_submission_hygiene.py` | `check_env.py` fails the "`.env` is outside `code/`" check |
| The virtualenv and bytecode caches live outside `code/` | Let `uv` and CPython write into the submission root | `UV_PROJECT_ENVIRONMENT`, `tests/conftest.py`, `tests/test_submission_hygiene.py` | The hygiene test lists a forbidden directory under `code/` |
| A fixed `seed` at temperature 0 gives byte-reproducible completions | Accept non-reproducible free text and cache by content hash instead | `config/models.yaml` per-role `seed`; probe 5 | Re-running probe 5 reports the seeded column as not byte-identical |
| The configured `max_tokens` ceiling matches the account plan | Assume a fixed ceiling | `config/models.yaml` `provider.max_tokens_ceiling`; probe 7 | Probe 7's measured ceiling differs from the configured value and says so |
| Every role's `max_tokens` sits above the measured reasoning floor | Use a single global default | `config/models.yaml`; probe 6 | A role returns HTTP 200 with empty content; probe 6's floor rises above a configured value |

---

*Companion documents: `docs/DATA_REPORT.md` (dataset reconnaissance),
`docs/PROVIDER_CAPABILITIES.md` (live provider probe results),
`config/decisions.yaml` (the fifteen specification ambiguities).*
