# Walkthrough — one request, end to end, with the real values

Every number here comes from a run against the shipped tree. Regenerate the
whole trace with:

```bash
python evaluation/harness.py            # the scorecard
python -m pytest tests/test_endtoend_groundtruth.py -s   # the per-row report
```

The per-hop values below were produced by tracing `request_84` through the same
functions the pipeline calls, in the same order. **The row this trace produces is
byte-identical to the row in the shipped `output.csv`.**

## Why this request

`request_84` exercises more of the system than any other single row: five
foreign-currency rows needing FX, a blank amount backed by an image, a message
that names that same event, 31 detected series of which 10 project, 14 series
contributing variable spending, and 11 spending-change candidates.

**What it misses: more than one eligible plan.** Only one plan survives safety
here, so the ranking tie-breakers never bind. That case is traced separately at
the end using `request_56`, which has three safe plans.

---

## 0. The request and the profile

```text
request_id               request_84          user_id                  user_84
request_date             2026-04-04          request_type             education
requested_amount         95400               desired_completion_date  2026-06-15
allows_partial_payment   False
request_text             "Enrolment for the course comes to INR 95,400. Would paying
                          the course fee now leave enough…"

home_currency                                 INR
current_available_balance                     153945
minimum_balance_to_keep                       91300
expense_categories_to_protect                 rent, insurance, transport
expense_categories_user_is_willing_to_reduce  dining, shopping
expense_categories_user_is_willing_to_stop    streaming, cloud_storage
payment_methods_user_will_consider            full_payment, partial_payment
max_installment_months                        blank  -> installments not eligible
```

Two things already constrain the answer: the user will not consider
installments, and the headroom above the floor is `153,945 − 91,300 = 52,645`
against a request of `95,400`.

## 1. Load — `bow/data/loaders.py`

123 event rows for `user_84`: 122 `settled`, 1 `scheduled`; 118 debit, 5 credit;
118 INR, 5 USD. One row carries a **blank amount**: `event_7941`. No
`linked_event_id` on any of them, so there is no lifecycle chain for this user.

## 2. The six rules — `bow/data/normalize.py::cash_treatment`

The whole of cash treatment is six branches on typed fields. Nothing reads a
description. For this user:

| outcome | rows | example |
|---|---:|---|
| `SETTLED` (keep) | 122 | `event_7820` settled/credit 1296 USD |
| `SCHEDULED_COMMITMENT` (keep) | 1 | `event_7942` scheduled/debit 6080 INR |

Nothing dropped — **123 kept of 123**. Across the whole dataset the same six
rules give 25,148 settled + 70 scheduled + 63 pending-debit-reserved kept, and
43 cancelled-or-failed + 8 pending-credit + 10 unrealized-or-non-cash dropped:
**25,281 kept, 61 dropped of 25,342**, which is exactly what
`evaluation/run_manifest.json` records for the shipped run.

## 3. Lifecycle collapse — `bow/data/normalize.py::collapse_lifecycles`

**Not exercised here** — `user_84` has no linked rows. Dataset-wide there are 58
linked rows in 7 distinct parent→child shapes:

```text
 14  expense/settled/debit        -> refund/settled/credit
 10  investment_purchase/settled  -> investment_valuation/unrealized/non_cash
  8  expense/cancelled/debit      -> expense/settled/debit
  8  expense/settled/debit        -> refund/pending/credit
  7  debt_payment/failed/debit    -> debt_payment/scheduled/debit
  6  expense/settled/debit        -> expense/pending/debit
  5  investment_purchase/settled  -> investment_sale/settled/credit
```

Every one of these is decided by the six rules above applied to each row on its
own. The link never decides cash treatment — that is why six rules replace a
seven-case table.

## 4. FX — `bow/data/normalize.py::convert_currency`

Five USD salary rows, each converted on its **settlement date**, in the stated
direction, with no inversion and no triangulation:

| event | printed | cash date | rate keyed on | rate | converted |
|---|---|---|---|---|---|
| `event_7820` | 1296 USD | 2025-11-15 | 2025-11-15 | 83.33 | 107,995.68 INR |
| `event_7828` | 1296 USD | 2025-12-15 | 2025-12-15 | 83.33 | 107,995.68 INR |
| `event_7836` | 1296 USD | 2026-01-15 | 2026-01-15 | 83.33 | 107,995.68 INR |
| `event_7844` | 1296 USD | 2026-02-15 | 2026-02-15 | 83.33 | 107,995.68 INR |
| `event_7852` | 1296 USD | 2026-03-15 | 2026-03-15 | 83.33 | 107,995.68 INR |

`Q16_cash_date_basis` picks the settlement date, and the same flag keys the rate
lookup — the coupling is deliberate and recorded in the register, because letting
the two diverge would convert an amount on one date and bank it on another.

## 5. Perception — `bow/perception/`

`event_7941` (expense / shopping / settled / debit / 2026-04-03) has a blank
amount. `images.csv` links it to `image_13`, and `messages.csv` links
`message_64` to the same event:

> "BuyBox confirmed that the tote bag order was paid in INR on 3 April 2026. The
> receipt has the final amount. Order ref MER-0064."

The vision read returns **2,298** on the label `Total paid`
(`evaluation/vision_run.json`), which is the gold answer for that image.

**Neither changed this row.** The deterministic trace — no vision, no messages —
reproduces the shipped row byte for byte. `event_7941` is settled and dated
2026-04-03, one day *before* the request, and `Q8_balance_as_of_semantics` takes
`current_available_balance` as already inclusive of everything settled on or
before `request_date`. Resolving a settled past amount corrects the ledger and
moves no forecast. That is worth saying plainly rather than implying the image
drove the answer.

## 6. Recurrence — `bow/data/recurrence.py::project`

**31 series detected, 10 projected.** Projected:

| cadence | n | period | anchor | last | age | amount | series |
|---|---:|---:|---:|---|---:|---:|---|
| monthly_anchored | 6 | 31 | 1 | 2026-04-01 | 3 | 26,200 | rent |
| monthly_anchored | 5 | 30 | 15 | 2026-03-15 | 20 | 107,995.68 | salary (credit) |
| monthly_anchored | 5 | 30 | 11 | 2026-03-11 | 24 | 5,861.73 | household shopping |
| monthly_anchored | 5 | 30 | 5 | 2026-03-05 | 30 | 4,737.81 | utilities |
| monthly_anchored | 5 | 30 | 6 | 2026-03-06 | 29 | 4,790 | vehicle insurance |
| monthly_anchored | 5 | 30 | 13 | 2026-03-13 | 22 | 3,831.07 | games |
| monthly_anchored | 5 | 30 | 8 | 2026-03-08 | 27 | 2,390 | streaming |
| monthly_anchored | 5 | 30 | 11 | 2026-03-11 | 24 | 405 | cloud storage |
| fixed_interval | 2 | 50 | — | 2026-03-27 | 8 | 3,421.26 | supermarket basket |
| fixed_interval | 2 | 120 | — | 2026-02-15 | 48 | 4,440.76 | fresh food |

Not projected, with the reason each was refused:

- **`irregular_gaps` (13 series)** — gaps do not cluster, so no cadence is claimed.
- **`single_occurrence` (3 series)** — one data point is not a pattern (`Q18` keeps the floor at two).
- **`stale` (5 series)** — last occurrence beyond the staleness multiple of the detected period. Example: *Family dinner*, period 21 days, last seen 2026-01-23, **age 71 days** — 3.4 periods, past the configured 1.5.

## 7. Variable spending — `bow/engine/forecast.py::variable_spend_flows`

`Q26` is on, so a series with no cadence still contributes at a trailing-window
rate. **14 series qualify**, each with at least the configured minimum
occurrences inside the 90-day window:

```text
218.55/day  household groceries    (4 occurrences in window, 19,669.21 total)
109.37/day  bakery and snacks      (3, 9,843.42)
 91.17/day  neighbourhood restaurant (2, 8,205.39)
 88.21/day  quick-service meal     (2, 7,939.25)
 73.69/day  weekend food delivery  (2, 6,632.02)
 51.23/day  local taxi             (2, 4,610.39)
 47.08/day  fuel refill            (2, 4,237.11)
 …
```

The rate is `window_total / window_days` with **no free coefficient** — that
refusal is why L4 records the forecast as still too optimistic rather than
tuned into agreement. The combined rate lands at **964.947…/day**, applied as
one flow per day for 90 days.

## 8. The forecast — `bow/engine/forecast.py::build_forecast`

114 flows in `[2026-04-04, 2026-07-03]`: 24 projected recurrences, 1 given
commitment (`event_7942`, the scheduled 6,080 insurance debit on 2026-04-11),
89 daily variable-spend flows. `CashFlow.signed` negates a debit; the amount
itself is always a magnitude.

```text
2026-04-09   137,202.45
2026-04-10   136,237.51
2026-04-11   129,192.56      <- the 6,080 scheduled commitment lands
2026-04-12   128,227.61
2026-04-13   127,262.67
2026-04-14   126,297.72      <- TROUGH
2026-04-15   233,328.45      <- salary, 107,995.68
```

**Trough = 126,297.7188… on 2026-04-14**, the day before payday. That shape —
the low point immediately before the next inflow — is what a correct forecast
looks like.

## 9. `amount_safe_to_pay` — `bow/engine/capacity.py`

```text
clamp(trough − minimum, 0, requested)
clamp(126,297.7188… − 91,300 = 34,997.7188…, 0, 95,400) = 34,997.7188…
```

Quantized once at the output boundary: **34,997.72**.

`amount_safe_by_search` re-simulates the whole curve under a binary search and
returns **34,997.71**. The two agree to the search's resolution: it steps down to
one minor unit (0.01) and returns the largest value it proved safe, so it lands
one cent low by construction. The closed form is exact and is what ships; the
search exists only to falsify it, and does so over 500 generated cases in
`tests/test_capacity.py`.

## 10. `earliest_date_for_full_payment`

**2026-06-15** — the first date on which paying the full 95,400 leaves the whole
remaining horizon above 91,300. It is on the desired completion date, which is
what makes `wait` a complete answer rather than a deferral.

## 11. Plans and ranking — `bow/engine/plans.py`, `bow/engine/ranker.py`

Three options are supplied:

```text
payment_option_234  full_payment  95,400 x1   first 2026-04-04   fee 0       total 95,400
payment_option_235  installments   6,042 x18  first 2026-04-18   fee 13,356  total 108,756
payment_option_236  installments   4,849.50 x24 first 2026-04-07 fee 20,988  total 116,388
```

Both installment options are **rejected before safety is even considered**:
`max_installment_months` is blank, so the user does not accept the method. Full
payment today is rejected by the forecast — 95,400 today would put the trough at
30,897.72, below the 91,300 floor. One candidate survives:

```text
wait   sort_key = (deadline_miss=False, changes=False, total=95400,
                   first=2026-06-15, n=1, id_rank=1)
```

With one candidate no tie-breaker binds. **See §14 for a case where they do.**

## 12. Spending changes — `bow/engine/changes.py`

The search **did not run**, and that is ranking rule 2 working: a plan that needs
no change outranks one that does, so changes are only searched when nothing is
safe unmodified. Had it run, 11 candidates were available:

```text
reduce_to:event_7940:1730     3,957.84 -> 1,730   dining / bakery and snacks
reduce_to:event_7858:2260     5,861.73 -> 2,260   shopping / household shopping
stop:event_7857                  2,390 -> 0       streaming / family streaming
stop:event_7856                    405 -> 0       cloud storage
… 7 more, all dining
```

Every one is in `dining`, `shopping`, `streaming` or `cloud_storage` — the
categories this profile permits. Nothing in `rent`, `insurance` or `transport`
appears, because `ChangePermission` vetoes protected categories outright, and
each `reduce_to` amount is the event's own `minimum_allowed_amount`, never a
computed shortfall.

## 13. Status, output row, ledger

`bow/engine/policy.py::decide` derives the status from the chosen plan:
`wait` means full payment becomes safe later, so the status is
**`affordable_later`** — not `affordable_with_plan`, which §6.2 reserves for a
partial schedule, installments, or permitted spending changes.

```text
request_id                      request_84
amount_safe_to_pay              34997.72
affordability_status            affordable_later
recommended_payment_method      wait
payment_plan                    2026-06-15:95400
earliest_date_for_full_payment  2026-06-15
spending_changes_needed         none
decision_explanation            Pay INR 95,400 in full on 15 June 2026. Paying earlier
                                would take the balance below the INR 91,300 minimum.
```

Identical to the shipped row. The explanation is rendered by
`bow/explain/template.py` from the frozen `Decision` — it can restate 95,400 and
91,300 because both are already in the decision, and it has no path to a number
that is not.

**The evidence ledger** (`bow/contracts/evidence.py::EvidenceLedger`) is built
per request and frozen before the engine sees it. For this request the shipped
run's perception entries are the vision read of `image_13` — `effect=extract`,
provenance `{event_id, label, read_path, currency}` — plus any surviving message
delta. What the ledger stops is specific: `bow/engine/forecast.py::_amendment_flows`
will turn an entry into a signed cash flow **only** when `effect is AMEND` and
the provenance carries both a cash date and a direction. Vision writes neither,
so a document can supply a magnitude and can never assert which way money moved.

---

## 14. The case `request_84` misses: more than one safe plan

`request_56`, three safe candidates, real `sort_key` tuples:

```text
partial_payment  opt=None                (False, False,  881.56, 2025-02-03, 2, id_rank=1)
installments     opt=payment_option_155  (False, False,  916.84, 2025-02-03, 2, id_rank=0)
installments     opt=payment_option_156  (True,  False,  934.47, 2025-02-06, 3, id_rank=0)
CHOSEN: partial_payment        decided by key 3: minimize the total amount paid
```

Key 1 eliminates `payment_option_156` — its third payment falls after the
deadline. Keys 1 and 2 tie between the remaining two, and **key 3 decides**: the
installment option carries a 35.28 financing fee, so it costs 916.84 against
881.56 for paying part now and the rest on the earliest safe date.

Across the whole evaluation set `multiple_eligible_supplied_options` fired **3
times**: once decided by key 1, twice by key 3. **Keys 4, 5 and 6 never bound** —
no two supplied options within any request share a total payable, a first
payment date and a payment count, so the tie-breakers below key 3 are unexercised
on this data. They are implemented anyway; `LIMITATIONS.md` L7 records the
ordering bug that was found in key 6 precisely because nothing exercised it.
