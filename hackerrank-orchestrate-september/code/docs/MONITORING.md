# Monitoring — what I would watch in production

Outcome-level. Every threshold below is anchored to a number this system
actually produced, in `evaluation/run_manifest.json` or `NUMBERS.md`, so the
alert has a baseline rather than a guess. Where I have no baseline, I say so.

**Page** means wake someone. **Review** means it goes into a weekly digest.
The split is: *page when money could move wrongly now*, *review when quality is
drifting*.

---

## 1. Per-class precision drift on `affordability_status`

| | |
|---|---|
| **signal** | precision per class over a rolling 500 labelled outcomes, compared to the launch baseline |
| **baseline** | 19/25 overall; the standing confusion is `affordable_with_plan → affordable_now` (3 of 25) |
| **threshold** | any single class drops more than 10 points from baseline, or `not_affordable` recall drops at all |
| **action** | freeze the config, diff the flag set against the last known-good run, replay the last 500 requests through the previous config |
| **page or review** | **page** on `not_affordable` recall; review otherwise |

`not_affordable` recall is the asymmetric one. Calling a safe purchase unsafe
annoys a user. Calling an unsafe purchase safe takes their rent money.

## 2. `amount_safe_to_pay` error distribution shift

| | |
|---|---|
| **signal** | median and p90 relative error on the **implied trough** — `amount_safe_to_pay + minimum_balance_to_keep` — not on the raw amount |
| **baseline** | median 10.0% (deterministic), 10.4% (with perception) |
| **threshold** | median above 15%, or p90 moving more than 1.5× in a week |
| **action** | split the error by whether the trough sits before or after the next income event; that split is what diagnosed L4 |
| **page or review** | review |

Watching the raw amount instead would be a mistake: it is the trough minus a
constant and then clamped, so its error is dominated by the clamp and by the
size of `minimum_balance_to_keep`. That is the 44.8% figure, and it moves for
reasons that are not model quality.

## 3. Unsafe recommendations — the one that matters

| | |
|---|---|
| **signal** | replay each recommendation against the *realised* ledger 90 days later; count plans whose realised balance went below `minimum_balance_to_keep` on any day |
| **baseline** | **none — this cannot be measured on a static dataset.** Today's proxy is `forecast_goes_negative_in_horizon`, firing on **20 of 250** |
| **threshold** | any realised breach caused by a plan we recommended |
| **action** | pull the request, re-run the trace, find which flow the forecast missed; if it is a class rather than a one-off, roll back |
| **page or review** | **page, every single one** |

This is the only metric that measures the actual promise: *the balance never
falls below the minimum*. Everything else is a proxy for it. I would build this
first in production and I could not build it here, because there is no realised
future to compare against.

## 4. Vision reconciliation failure and substitution rate

| | |
|---|---|
| **signal** | three rates per day: second reads as a share of documents, substitutions as a share, unresolved as a share |
| **baseline** | second read **12/16**, substitution **1/16**, unresolved **0/16** |
| **threshold** | substitution above 20% of documents, or unresolved above 0 |
| **action** | substitution rising means label matching is degrading and arithmetic is carrying it; unresolved above zero means blank amounts are reaching the engine |
| **page or review** | page on unresolved > 0; review on the other two |

The second-read rate at 75% is already high and is the largest available cost
saving in that path. It is a cost signal, not a correctness one.

## 5. Message extraction failure rate

| | |
|---|---|
| **signal** | failures as a share of messages, and deltas surviving the guard |
| **baseline** | **3, 4 and 13 failures across three identical runs**; deltas kept 12, 12 and 5 |
| **threshold** | above 15 failures per 200 messages on two consecutive runs |
| **action** | check `finish_reason` — every failure measured so far was a length truncation returning HTTP 200 with empty content, not a language or schema failure |
| **page or review** | review |

The baseline is a range, not a point, and the threshold has to respect that.
A single run at 13 is inside observed variance. Two consecutive is not.

## 6. Unresolved-flag rate by category

| | |
|---|---|
| **signal** | `unresolved_total` split by reason: `extraction_failed`, `missing_amount`, `unresolvable_fx` |
| **baseline** | **0 of 250**, and `conversions_deferred: 0` |
| **threshold** | any non-zero, since the baseline is clean |
| **action** | fail-closed is working; the question is whether the upstream data changed shape |
| **page or review** | review, unless it exceeds 1% of requests, then page |

## 7. Injection and untrusted-content drop rate, against a planted canary set

| | |
|---|---|
| **signal** | a fixed canary set of adversarial messages injected into each run; the share the guard drops must be 100% |
| **baseline** | 167 deltas dropped as *asserts a fact no event row supports*; both advance-fee fixtures produce no delta (`tests/test_untrusted.py`) |
| **threshold** | **any canary surviving the guard** |
| **action** | stop the run; a surviving canary means a message reached the ledger with a financial fact behind it |
| **page or review** | **page** |

The canary set does not exist yet — today the equivalent is two fixtures in the
test suite. In production I would plant ~50 and run them every cycle, because
this is the one failure mode where the system is being actively attacked rather
than merely wrong.

## 8. `variable_spend_dominates_opening_balance`

| | |
|---|---|
| **signal** | count of requests where projected variable spending over the horizon exceeds a configured fraction of the opening balance |
| **baseline** | **56 of 250 (22%)** |
| **threshold** | above 35% of requests |
| **action** | it fires when the trailing rate is extrapolating a burst into a permanent rate; a rise means the rule needs the damping I refused to add, and the evidence would finally justify it |
| **page or review** | review |

At 22% this is already a loud number, and it is the counter I would point at
first if someone asked what is most likely wrong.

## 9. Cost and latency per request

| | |
|---|---|
| **signal** | ₹ per request and p95 wall time, per role |
| **baseline** | **₹0.473/request**, 408 calls for 250 requests, 1,433 s total; `message_extract` is 70% of spend at ~7,700 completion tokens per call |
| **threshold** | cost per request above ₹0.75, or p95 latency above 2× baseline |
| **action** | check completion-token distribution first — cost here is output tokens, not call volume |
| **page or review** | page on a 3× cost spike (runaway retry); review otherwise |

---

## Which of these would have caught the `image_14` pairing bug

**Caught it: #4, the substitution rate — but only after the fix existed.** Before
the fix, `image_14` reconciled on 0 of 5 reads. A reconciliation-failure rate
broken out per document would have shown one document failing every single time
while fourteen others never failed. That is a standing signal, and I was not
looking at it: I was looking at the gold score, which was 15/16 and looked like
one bad document rather than one *systematically* bad document.

**Would also have caught it: a per-document stability check.** Not on the list
above because it costs N× on every document. As a sampled canary — five reads of
one rotating document per day — it is affordable, and it is what
`scripts/probe_vision_stability.py` does.

**Would NOT have caught it:**

- **#2, error distribution.** `event_9421` is a settled past expense, and `Q8`
  takes the opening balance as already inclusive of it. The wrong amount moved
  **no output row**. An outcome metric cannot see a defect that changes no
  outcome.
- **#6, unresolved rate.** The read never failed. It returned a confident,
  well-formed, wrong number. Fail-closed does not catch fail-plausible.
- **#3, unsafe recommendations.** Same reason as #2 — no row moved.
- **#1, precision drift.** One document out of sixteen, on one row out of 250.

That is the honest lesson: **the bug was invisible to every outcome-level metric,
and visible in a process-level one** — the reconciliation-failure rate per
document. It is on this list because of that, not despite it. The gold score
that did eventually surface it only worked because
`scripts/score_vision_gold.py` was rewritten to score *the cache the pipeline
actually uses*; before that it scored its own separate reads and reported 16/16
while the pipeline shipped 670.
