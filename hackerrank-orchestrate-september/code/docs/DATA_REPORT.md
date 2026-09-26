# Buy or Wait? — Dataset Reconnaissance Report

> **Read the Corrections section at the end before relying on anything here.**
> This report is the reconnaissance as it was written, and it is deliberately
> not edited in place: several claims in the body were later found too strong or
> simply wrong, and each is corrected at the end with the evidence that changed
> it. The body is a record of what was believed at the time, not a statement of
> current fact.

Reconnaissance only. No application code was written. All figures below were produced by
throwaway scripts under `scratch/` reading `dataset/` read-only.

- Repo: `hackerrank-orchestrate-september26`
- Generated: 2026-09-12
- Source of truth for rules: `problem_statement.md`, `AGENTS.md` §6

---

## Table of contents

1. [CSV inventory](#1-csv-inventory)
2. [Join integrity](#2-join-integrity)
3. [Lifecycle chains](#3-lifecycle-chains)
4. [Blank amounts](#4-blank-amounts)
5. [Image recon](#5-image-recon)
6. [Untrusted content survey](#6-untrusted-content-survey)
7. [Currency and FX coverage](#7-currency-and-fx-coverage)
8. [Ground truth shape](#8-ground-truth-shape)
9. [Request and option shape](#9-request-and-option-shape)
10. [Open questions](#10-open-questions)

---

## 1. CSV inventory

### 1.1 Shape summary

| File | Rows (excl. header) |
|---|---|
| `exchange_rates.csv` | 134 |
| `financial_events.csv` | 25,342 |
| `financial_profiles.csv` | 275 |
| `images.csv` | 16 |
| `messages.csv` | 215 |
| `output.csv` | 250 (template, all prediction cells blank) |
| `request_payment_options.csv` | 790 |
| `requests.csv` | 250 |
| `sample_requests.csv` | 25 |

Media: `dataset/media/images/` contains exactly 16 PNGs, `image_01.png` … `image_16.png`.

### 1.2 `exchange_rates.csv` — 134 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `rate_date` | date | 0 | 39 |
| 2 | `from_currency` | str | 0 | 2 |
| 3 | `to_currency` | str | 0 | 5 |
| 4 | `rate` | float | 0 | 5 |

- `from_currency` (2): `USD` (88), `EUR` (46)
- `to_currency` (5): `INR` (33), `IDR` (30), `EUR` (25), `USD` (24), `ZAR` (22)
- `rate` (5): `83.33` (33), `15833.33` (30), `0.92` (25), `1.09` (24), `20` (22)

### 1.3 `financial_events.csv` — 25,342 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `event_id` | str | 0 | 25,342 |
| 2 | `user_id` | str | 0 | 275 |
| 3 | `event_type` | str | 0 | 8 |
| 4 | `description` | str | 0 | 164 |
| 5 | `category` | str | 0 | 22 |
| 6 | `direction` | str | 0 | 3 |
| 7 | `amount` | float | **16** | 17,785 |
| 8 | `currency` | str | 0 | 5 |
| 9 | `event_date` | date | 0 | 1,308 |
| 10 | `settlement_date` | date | **10** | 1,310 |
| 11 | `status` | str | 0 | 6 |
| 12 | `linked_event_id` | str | 25,284 | 58 |
| 13 | `flexibility` | str | 0 | 4 |
| 14 | `minimum_allowed_amount` | float | 22,435 | 301 |

**True enum inventories (verbatim, with counts) — fail-closed on anything else:**

`event_type` (8):
```
expense               20525
subscription           2488
income                 1696
debt_payment            567
investment_purchase      29
refund                   22
investment_valuation     10
investment_sale           5
```

`status` (6):
```
settled     25148
pending        71
scheduled      70
cancelled      22
failed         21
unrealized     10
```

`direction` (3):
```
debit      23609
credit      1723
non_cash      10
```

`flexibility` (4) — this is the flexibility/recurrence flag:
```
fixed                    21138
reducible                 2682
stoppable                 1297
reducible_or_stoppable     225
```

`currency` (5): `INR` 6457, `EUR` 5585, `IDR` 4992, `ZAR` 4489, `USD` 3819.

`category` (22, over the 12-value threshold but enumerated because it drives protect/reduce/stop):
`groceries` 5812, `transport` 5626, `dining` 3479, `salary` 1690, `utilities` 1452, `rent` 1355,
`cloud_storage` 833, `shopping` 813, `streaming` 683, `debt_repayment` 553, `entertainment` 521,
`insurance` 456, `music_subscription` 451, `healthcare` 356, `delivery_membership` 351,
`education` 306, `housing` 246, `gym` 170, `family_support` 125, `investment` 44,
`work_expense` 14, `windfall` 6.

**Invariants observed and worth asserting:**

- `minimum_allowed_amount` is populated **iff** `flexibility ∈ {reducible, reducible_or_stoppable}`
  (2,682 + 225 = 2,907 rows). It is never populated for `fixed` or `stoppable`.
- Every non-`fixed` event is `status=settled` and `direction=debit`, and is of type
  `expense` (2,505) or `subscription` (1,699). No pending/scheduled/credit row is ever flexible.
- `minimum_allowed_amount` is always **strictly less than** `amount` (0 violations);
  ratio range 0.357 – 0.694, median exactly 0.500.
- The 10 rows with a blank `settlement_date` are exactly the 10
  `investment_valuation` / `unrealized` / `non_cash` rows.
- `settlement_date` is never before `event_date`; 178 rows have `settlement_date > event_date`.
- There are **no** zero amounts and **no** negative amounts. Sign is carried by `direction`.

### 1.4 `financial_profiles.csv` — 275 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `user_id` | str | 0 | 275 |
| 2 | `home_currency` | str | 0 | 5 |
| 3 | `current_available_balance` | float | 0 | 275 |
| 4 | `minimum_balance_to_keep` | int | 0 | 192 |
| 5 | `financial_priorities` | str (pipe list) | 0 | 9 |
| 6 | `expense_categories_to_protect` | str (pipe list) | 0 | 8 |
| 7 | `expense_categories_user_is_willing_to_reduce` | str (pipe list) | **39** | 18 |
| 8 | `expense_categories_user_is_willing_to_stop` | str (pipe list) | **62** | 10 |
| 9 | `payment_methods_user_will_consider` | str (pipe list) | 0 | 7 |
| 10 | `max_installment_months` | int | **119** | 11 |

`home_currency` (5): `INR` 67, `EUR` 62, `IDR` 55, `ZAR` 51, `USD` 40.

**`payment_methods_user_will_consider` (7 combinations):**
```
full_payment                                60
partial_payment|installments                52
installments                                41
full_payment|partial_payment                40
full_payment|installments                   35
full_payment|partial_payment|installments   28
partial_payment                             19
```
Token totals: `full_payment` 163, `installments` 156, `partial_payment` 139.
Order within the field is always `full_payment | partial_payment | installments`.

**`max_installment_months` (11 values, 119 blank):** `2` 15, `3` 18, `4` 17, `5` 16, `6` 13,
`7` 14, `8` 10, `9` 9, `10` 12, `11` 16, `12` 16. Max is 12; minimum is 2.
It is blank **exactly** for the 119 users who do not list `installments` (0 contradictions
in either direction — verified).

`financial_priorities` (9 combinations, always 2 tokens):
```
emergency_savings|travel                46
retirement_investment|emergency_savings 42
education|debt_repayment                36
education|emergency_savings             34
emergency_savings|housing               29
healthcare|family_support               25
education|family_support                24
debt_repayment|emergency_savings        20
healthcare|retirement_investment        19
```

`expense_categories_to_protect` (8 combinations):
```
rent|groceries|transport                 63
rent|insurance|transport                 46
rent|utilities|groceries                 42
rent|education|groceries|debt_repayment  36
rent|healthcare|family_support|groceries 25
housing|utilities|education              24
rent|utilities|debt_repayment            20
housing|healthcare|utilities             19
```

`expense_categories_user_is_willing_to_stop` (10 combinations, 62 blank):
```
cloud_storage                           57
streaming|cloud_storage                 52
streaming                               32
music_subscription                      26
music_subscription|delivery_membership  24
delivery_membership                     10
gym|music_subscription                   4
gym|music_subscription|delivery_membership 4
gym|delivery_membership                  3
gym                                      1
```

`expense_categories_user_is_willing_to_reduce` has 18 combinations (over threshold);
token totals: `dining` 153, `shopping` 72, `streaming` 66, `entertainment` 44, `gym` 14.

**Profile ↔ event consistency (verified, 0 exceptions):** a `reducible` event's category is
always in the user's reduce list and never in the stop list; `stoppable` is always in the stop
list and never in reduce; `reducible_or_stoppable` is in both. No flexible event is ever in
`expense_categories_to_protect`. So `flexibility` and the profile lists are redundant encodings
of the same fact — a good cross-check assertion.

### 1.5 `images.csv` — 16 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `image_id` | str | 0 | 16 |
| 2 | `user_id` | str | 0 | 16 |
| 3 | `request_id` | str | 0 | 16 |
| 4 | `related_event_id` | str | 0 | 16 |

All four columns are fully populated with 16 distinct values. There is a perfect 1:1:1:1
mapping: each image belongs to one user, one request, one event.

### 1.6 `messages.csv` — 215 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `message_id` | str | 0 | 215 |
| 2 | `user_id` | str | 0 | 215 |
| 3 | `request_id` | str | **87** | 128 |
| 4 | `related_event_id` | str | **176** | 39 |
| 5 | `sent_at` | datetime (ISO-8601 Z) | 0 | 120 |
| 6 | `source_type` | str | 0 | 5 |
| 7 | `message_text` | str | 0 | 215 |

`source_type` (5): `employer` 126, `service_provider` 31, `financial_service` 23, `bank` 18, `merchant` 17.

Exactly **one message per user**; 215 of 275 users have a message, 60 have none.

### 1.7 `request_payment_options.csv` — 790 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `payment_option_id` | str | 0 | 790 |
| 2 | `request_id` | str | 0 | 275 |
| 3 | `payment_method` | str | 0 | 2 |
| 4 | `payment_amount` | float | 0 | 787 |
| 5 | `number_of_payments` | int | 0 | 9 |
| 6 | `first_payment_date` | date | 0 | 194 |
| 7 | `payment_frequency_days` | int | **275** | 3 |
| 8 | `financing_fee` | float | 0 | 515 |
| 9 | `total_payable_amount` | float | 0 | 787 |

`payment_method` (2): `installments` 515, `full_payment` 275.
`number_of_payments` (9): `1` 275, `24` 96, `15` 89, `21` 88, `18` 87, `3` 80, `6` 65, `2` 7, `4` 3.
`payment_frequency_days` (3 + blank): `28` 180, `30` 169, `31` 166, blank 275
(blank **iff** `payment_method=full_payment`, i.e. `number_of_payments=1`).

Arithmetic holds exactly (0 violations at ±0.02 tolerance):
`payment_amount × number_of_payments == total_payable_amount` and
`requested_amount + financing_fee == total_payable_amount`.
`financing_fee` is `0` for every `full_payment` option and non-zero for every `installments`
option — no exceptions.

### 1.8 `requests.csv` — 250 rows

| # | column | dtype | nulls | distinct |
|---|---|---|---|---|
| 1 | `request_id` | str | 0 | 250 |
| 2 | `user_id` | str | 0 | 250 |
| 3 | `request_date` | date | 0 | 61 |
| 4 | `request_type` | str | 0 | 9 |
| 5 | `requested_amount` | float | 0 | 248 |
| 6 | `desired_completion_date` | date | 0 | 170 |
| 7 | `allows_partial_payment` | bool (lowercase literal) | 0 | 2 |
| 8 | `request_text` | str | 0 | 250 |

`request_type` (9, near-uniform):
```
debt_repayment    28
education         28
family_transfer   28
housing           28
investment        28
purchase          28
travel            28
emergency_expense 27
other             27
```
`allows_partial_payment` (2): `false` 170, `true` 80. Serialised lowercase, **not** `True`/`False`.

`request_date` spans 2023-01-20 … 2026-09-04. `request_id` values are `request_26` … `request_275`
(`request_01`–`request_25` live in `sample_requests.csv`).

### 1.9 `sample_requests.csv` — 25 rows

Same 8 input columns as `requests.csv` plus the 7 output columns. `request_date` spans
2019-09-03 … 2026-07-07. Output-column enums:

`affordability_status` (4): `affordable_with_plan` 9, `not_affordable` 7, `affordable_later` 6, `affordable_now` 3.
`recommended_payment_method` (5): `not_recommended` 7, `full_payment` 6, `wait` 6, `installments` 5, `partial_payment` 1.
`spending_changes_needed` (4 distinct): `none` 22, plus three populated rows (listed in §8).
`earliest_date_for_full_payment` is empty in 7 rows.

### 1.10 `output.csv` — 250 rows

`request_id` populated with exactly the 250 `requests.csv` ids, in the same order
(`request_26` first). All seven prediction columns are empty in every row.

---

## 2. Join integrity

**Result: the dataset is referentially perfect. Zero orphans in every direction tested.**

### 2.1 `user_id`

| Relation | Left | Right | Left-only | Right-only |
|---|---|---|---|---|
| profiles ↔ events | 275 | 275 | 0 | 0 |
| profiles ↔ requests | 275 | 250 | **25** | 0 |
| profiles ↔ sample_requests | 275 | 25 | **250** | 0 |
| requests ↔ sample_requests | 250 | 25 | 250 | 25 |
| profiles ↔ messages | 275 | 215 | **60** | 0 |
| profiles ↔ images | 275 | 16 | **259** | 0 |

- The 25 profiles not in `requests.csv` are exactly the 25 sample users; the eval and sample
  user sets are **disjoint** and together cover all 275 profiles.
- Every user has exactly one request (eval or sample). No user appears twice.
- 60 users have no message; 259 have no image. These are absences by design, not orphans.

### 2.2 `request_id`

| Relation | Left | Right | Left-only | Right-only |
|---|---|---|---|---|
| requests ↔ output | 250 | 250 | 0 | 0 |
| (requests ∪ sample) ↔ payment_options | 275 | 275 | 0 | 0 |
| messages.request_id → (requests ∪ sample) | 128 | 275 | **0 orphans** | 147 never referenced |
| images.request_id → (requests ∪ sample) | 16 | 275 | **0 orphans** | 259 never referenced |

- Every one of the 275 requests has ≥ 2 payment options. **Zero requests with zero options.**
- 116 of the 250 eval requests carry ≥ 1 message; **134 carry none.**
- 11 of the 16 images attach to eval requests; 5 attach to sample requests.

### 2.3 Event references

| Reference | Populated | Orphans |
|---|---|---|
| `messages.related_event_id` → `events.event_id` | 39 | **0** |
| `images.related_event_id` → `events.event_id` | 16 | **0** |
| `events.linked_event_id` → `events.event_id` | 58 | **0** |

Rows never referenced: 25,284 events are never the target of a `linked_event_id`;
25,236 events are never referenced by any of message / image / link. That is expected —
the vast majority of events are plain history.

### 2.4 Denormalised-key consistency

`messages.user_id` and `images.user_id` always agree with the owning user of the referenced
event **and** of the referenced request. **0 mismatches** across both files. The denormalised
columns are safe to trust, but asserting them is cheap insurance.

---

## 3. Lifecycle chains (`linked_event_id`)

58 rows carry a `linked_event_id`, pointing at 58 **distinct** parents.

- **Chain-length distribution: `{2: 58}`.** Every chain is exactly two events long.
- **Cycles: none.**
- **Events with more than one inbound link: none** (every parent has exactly one child).
- **Chains mixing users: none. Chains mixing currencies: none.**
- The child's `event_date` is always ≥ the parent's.

Because every chain is length 2, the "longest chain" is any of the 58. Printed in full:

```
event_98  user_01 expense settled debit   583 ZAR 2024-01-25/2024-01-26 shopping "Card charge later reversed"
event_99  user_01 refund  settled credit  583 ZAR 2024-01-28/2024-01-29 shopping "Settled card charge reversal"  linked_event_id=event_98
```

### 3.1 The eight archetypes (this is the real payload of §3)

Each pair is one of eight fixed shapes. The cash-flow treatment differs per shape and the
`linked_event_id` alone does **not** tell you which — you must read `status` and `direction`.

| # | Parent | Child | n | Correct cash treatment |
|---|---|---|---|---|
| 1 | `expense` **cancelled** "Card authorization" | `expense` **settled** "Settled card purchase" | 8 | Count the child only. The cancelled parent is not cash. |
| 2 | `expense` settled "Card charge later reversed" | `refund` **settled** credit "Settled card charge reversal" | 7 | Both settled; they net to zero. |
| 3 | `expense` settled "Purchase awaiting refund" | `refund` **pending** credit "Pending merchant refund" | 8 | Count the debit. **Do not count the pending credit.** |
| 4 | `expense` settled "Reimbursable work expense" | `refund` **settled** credit "Employer expense reimbursement" | 8 | Both count. Not salary — do not fold into income recurrence. |
| 5 | `expense` settled "Original card charge" | `expense` **pending** debit "Possible duplicate card charge" | 7 | **Conflict — see Open Question 1.** |
| 6 | `debt_payment` **failed** debit | `debt_payment` **scheduled** debit "Scheduled bill payment retry" | 7 | Ignore the failed attempt; reserve the scheduled retry. |
| 7 | `investment_purchase` settled debit | `investment_sale` **settled** credit | 5 | Both are real cash. |
| 8 | `investment_purchase` settled debit | `investment_valuation` **unrealized** `non_cash` | 8 | Debit is cash; valuation is **not** cash, blank `settlement_date`. |

Note archetype 8 includes both gains (e.g. `event_1960`: 184.80 → 369.60 EUR) and losses
(e.g. `event_1856`: 676.80 → 270.72 USD). Treating the valuation as available cash is wrong
in both directions.

---

## 4. Blank amounts

**Exact count: 16** events have a blank `amount`. There are no zero amounts anywhere in the
file, so blank is unambiguously "missing", consistent with the spec's "do not treat a blank
amount as zero."

**Every one of the 16 has a matching `images.csv` row. There are zero unresolvable
blank-amount events.** Conversely, all 16 images point at a blank-amount event — there are no
images attached to events that already have an amount.

| event_id | user_id | event_type | event_date | settlement_date | currency | status | category | image | message |
|---|---|---|---|---|---|---|---|---|---|
| `event_253` | user_03 | income | 2019-08-31 | 2019-08-31 | IDR | settled | salary | `image_01` | — |
| `event_1442` | user_16 | expense | 2023-08-11 | 2023-08-16 | INR | scheduled | rent | `image_02` | — |
| `event_1545` | user_17 | expense | 2026-02-27 | 2026-02-27 | INR | settled | groceries | `image_03` | — |
| `event_1700` | user_19 | expense | 2024-09-03 | 2024-09-03 | INR | settled | groceries | `image_04` | — |
| `event_1786` | user_20 | expense | 2026-02-06 | 2026-02-09 | INR | **pending** | utilities | `image_05` | — |
| `event_3051` | user_33 | expense | 2026-01-06 | 2026-01-06 | INR | settled | groceries | `image_06` | — |
| `event_3231` | user_35 | expense | 2025-10-29 | 2025-10-29 | INR | settled | dining | `image_07` | — |
| `event_4535` | user_48 | expense | 2026-07-24 | 2026-07-24 | INR | settled | housing | `image_08` | `message_35` |
| `event_5170` | user_55 | expense | 2026-06-07 | 2026-06-07 | INR | settled | utilities | `image_09` | — |
| `event_6033` | user_64 | expense | 2024-06-03 | 2024-06-10 | INR | **pending** | groceries | `image_10` | — |
| `event_6859` | user_73 | expense | 2023-01-19 | 2023-01-23 | INR | **scheduled** | healthcare | `image_11` | — |
| `event_7307` | user_78 | expense | 2025-10-01 | 2025-10-01 | **USD** | settled | transport | `image_12` | — |
| `event_7941` | user_84 | expense | 2026-04-03 | 2026-04-03 | INR | settled | shopping | `image_13` | `message_64` |
| `event_9421` | user_101 | expense | 2025-11-02 | 2025-11-02 | INR | settled | healthcare | `image_14` | — |
| `event_9806` | user_105 | expense | 2026-06-07 | 2026-06-07 | INR | settled | transport | `image_15` | — |
| `event_10521` | user_113 | expense | 2026-09-03 | 2026-09-03 | INR | settled | transport | `image_16` | `message_86` |

**Flags:**

- **No blank-amount event lacks an image.** The fail-closed path is still worth building
  (an OCR failure is functionally the same as a missing image), but no row needs it today.
- Every blank event has `flexibility=fixed` and blank `minimum_allowed_amount`, so none of
  them is ever a `spending_changes_needed` target.
- 15 of 16 are `debit`; one (`event_253`) is an income `credit`.
- **`event_7307` is the only blank-amount event in a non-home currency** (USD for an INR user).
  It is also the only foreign-currency **debit** in the whole dataset. It needs OCR *and*
  FX conversion — double hazard, single point of failure.
- Three blank events are not `settled`: two `pending` (`event_1786`, `event_6033`) and two
  `scheduled` (`event_1442`, `event_6859`). Those four are forward-looking commitments whose
  amount comes only from an image, so an OCR miss silently under-reserves cash.

---

## 5. Image recon

### 5.1 Load-bearing vs contextual

**All 16 images are load-bearing. There are zero contextual images.**

Every `images.csv` row's `related_event_id` points at an event with a blank `amount`, and every
blank-amount event has exactly one image. The requested "random sample of 10 contextual images"
is therefore an empty set — there is nothing else to sample. All 16 were opened.

| Class | Count |
|---|---|
| Load-bearing (linked to a blank-amount event) | **16** |
| Contextual | **0** |

Linkage (all three link columns are populated for every image):

| image | user | request | event | in eval set? |
|---|---|---|---|---|
| `image_01` | user_03 | `request_03` | `event_253` | sample (ground truth available) |
| `image_02` | user_16 | `request_16` | `event_1442` | sample |
| `image_03` | user_17 | `request_17` | `event_1545` | sample |
| `image_04` | user_19 | `request_19` | `event_1700` | sample |
| `image_05` | user_20 | `request_20` | `event_1786` | sample |
| `image_06` | user_33 | `request_33` | `event_3051` | **eval** |
| `image_07` | user_35 | `request_35` | `event_3231` | **eval** |
| `image_08` | user_48 | `request_48` | `event_4535` | **eval** |
| `image_09` | user_55 | `request_55` | `event_5170` | **eval** |
| `image_10` | user_64 | `request_64` | `event_6033` | **eval** |
| `image_11` | user_73 | `request_73` | `event_6859` | **eval** |
| `image_12` | user_78 | `request_78` | `event_7307` | **eval** |
| `image_13` | user_84 | `request_84` | `event_7941` | **eval** |
| `image_14` | user_101 | `request_101` | `event_9421` | **eval** |
| `image_15` | user_105 | `request_105` | `event_9806` | **eval** |
| `image_16` | user_113 | `request_113` | `event_10521` | **eval** |

Five images sit on sample requests with published ground truth — a free validation harness for
the OCR layer (see §5.4).

### 5.2 Taxonomy table

| image | document kind | currency shown | single unambiguous total? | competing figures present |
|---|---|---|---|---|
| `image_01` | Indonesian corporate pay slip (Aug-2019) | `IDR` written out | No | Salary 4,500,000 · Subtotal Earnings 4,780,800 · Total Earnings 4,780,800 · Total Deductions 415,800 · **Net Pay 4,365,000** |
| `image_02` | Handwritten-template rent receipt (India) | "Rupees" / implied ₹ | No | Rent & Maintenance 1,80,000 · Water 5,000 · Rental Tax 5,000 · Electrical 10,000 · Total to be Received 2,00,000 · Amount Received 1,00,000 · **Balance Due 1,00,000** |
| `image_03` | Thermal grocery "Bill of Supply" | none (bare numbers) | Yes | 11 line items (max 13,920.00) · **Net Amount 41272.0** · **Cash Paid 41272.00** |
| `image_04` | Grocery-delivery app order screen | `₹` | **No — total is cropped off** | 13 line items · **Item Bill ₹2854.00** · "Delivery f…" row cut mid-glyph |
| `image_05` | Airtel postpaid telecom bill | `₹` (in "amount(₹)") | No | Previous balance 3,543.54 · Payments −3,543.54 · **Amount due till 06-Feb-2026 = 704.05** · Amount due **after** 06-Feb-2026 = 822.05 · Rentals 580.65 · Usage 16.00 · Taxes 107.40 |
| `image_06` | GST tax invoice (Blink Commerce/Grofers) | `INR` in column headers | Yes | Per-line totals (max 565.00) · CGST 47.49 · SGST 47.49 · **Total 1995.00** (words confirm) |
| `image_07` | Restaurant thermal tax invoice, "PAID" stamp | `RS` | **Near-miss** | SubTotal 8122.00 · SGST 203.05 · CGST 203.05 · Total 8528.10 · **Grand Total (RS) 8528** |
| `image_08` | Society maintenance receipt (ICICI/paytm) | `₹` | Yes | 3 line items (13,880 / 1,050 / 409) · **Total Amount Received ₹15,339.00** (words confirm) · Convenience Fee Rs 0.00 |
| `image_09` | Same template as `image_08`, water bill | `₹` | Yes | Single line 723.00 · **Total Amount Received ₹723.00** (words confirm) |
| `image_10` | Two-page GST grocery invoice, scanned | `₹` | Yes, but on page 2 | 22 line items (max 9,600.00) · Sub Total 72,045.00 · CGST/SGST 1,513.13 ×2 · CGST20/SGST20 2,304.00 ×2 · **Total ₹79,679.26** · **Balance Due ₹79,679.26** |
| `image_11` | Hospital provisional bill | none (bare numbers) | Yes | Total Bill 3650.00 · Amount Payable 3650.00 · **Amount Paid 0.00** · **Balance 3650.00** · detailed-breakup subtotals 250/1400/1000/500 |
| `image_12` | Taxi thermal receipt | `$` | Yes | Ride 28,50 · Airport Surcharge 5,00 · Subtotal 33,50 · Tax 0,00 · **Total $33,50** · Cash Paid 40,00 · Change 6,50 |
| `image_13` | E-commerce order summary | `₹` | Yes | 2 items (699 / 1,599) · Item Total ₹2,298 · Delivery Free · **Total paid ₹2,298** |
| `image_14` | **Handwritten** pharmacy cash bill | none (Rs./Ps. columns) | Yes | 6 handwritten lines 1500/724/796/550/303/670 · **TOTAL 4543** (Rs) / 00 (Ps) |
| `image_15` | IndiGo airline GST invoice | `INR` (stated) | No | Taxable 9,124.00 · NonTaxable 388.00 · Total 9,512.00 · row Total(Incl Taxes) 9,580.00 / 388.00 · **Grand Total (Incl Taxes) 9,968.00** |
| `image_16` | EV-charging session invoice | `₹` implied (Rupees in words) | No | AMOUNT 333.24 · Session Fee "-" · Idle Fee "-" · CGST 9% 29.99 · SGST 9% 29.99 · **Total 393.22** (words confirm) |

### 5.3 Quality problems — call-outs

- **Cropped:** `image_04` (bottom cut mid-row; the grand-total row does not exist in the file),
  `image_02` (right edge cut: `2,00,000`, "Total Amount to be Receiv|", "1 Lakh" all truncated),
  `image_14` (left edge cut: "ices Charged Include all Taxes", "ds once sold…"),
  `image_11` (detailed breakup runs off the bottom).
- **Handwritten:** `image_14` entirely. The `670` line is written over a struck-through figure.
- **Photographed / skewed / low-res:** `image_03` (curled thermal paper, faint ink, a blue
  "28 FEB 2026" inward stamp overprinting the header), `image_07` (angled thermal photo, purple
  "PAID" stamp over the header), `image_10` (two scanned pages composited with a black band,
  warped, low contrast), `image_14` (photo of paper, off-axis).
- **Non-English script:** `image_01` is an Indonesian pay slip (Bahasa Indonesia labels around
  English-ish column names; amount in English words at the bottom). No other image is non-Latin.
- **Non-English number formatting:**
  - `image_12` uses **comma as the decimal separator** (`$33,50`, `12,3 mi`). A comma-stripping
    parser reads 3350 instead of 33.50 — a 100× error.
  - `image_02` uses **Indian lakh grouping** (`1,00,000.00`, `1,80,000.00`). A parser assuming
    3-digit groups can mis-segment.
  - `image_16` and `image_03` use `DD/MM/YYYY` (`03/09/2026` = 3 September 2026,
    `27/02/2026` = 27 February). A US-format reader lands on the wrong month.
- **Split currency columns:** `image_14` has separate `Rs.` and `Ps.` columns — reading the row
  left-to-right yields `454300` instead of `4543.00`.
- **Wrapped numbers inside table cells:** `image_15` renders `9,124.0` / `0` and `9,512.0` / `0`
  across two lines within one cell.

### 5.4 Per-image: the correct number, and what a naive extractor gets wrong

This is the section to build the OCR prompt and the verifier against.

| image | **correct value** | independent cross-check | what a naive extractor grabs instead |
|---|---|---|---|
| `image_01` | **4,365,000 IDR** (Net Pay) | "Transferred to: Bank Central Asia … IDR 4,365,000" and the words line | `4,500,000` — the line literally labelled **"Salary"**, which also matches the event's `category=salary`. Or `4,780,800` (Total Earnings), the largest number on the page. The event description says "August 2019 **net** salary". |
| `image_02` | **100,000 INR** (Balance Due) | 2,00,000 total − 1,00,000 received = 1,00,000 | `200000` — "Total Amount to be Received", the biggest and most total-looking figure. Or `180000` (Rent & Maintenance). Event description is "Outstanding rent **balance**". **Ground-truth-confirmed: see below.** |
| `image_03` | **41,272.00 INR** | "Net Amount 41272.0" = "Cash Paid 41272.00" | Largest line item `13920.00`; or trusting the 1-decimal "41272.0". |
| `image_04` | **2,854.00 INR** (Item Bill) | The 13 line items sum to exactly 2854.00 | Trying to read a grand total that is **not in the image**, then either failing closed or hallucinating a delivery fee. Or grabbing `531.0` (largest item). |
| `image_05` | **704.05 INR** | "This Month's Charges → Total ₹704.05" = "Amount due till 06-Feb-2026"; 580.65+16.00+107.40 = 704.05 | `3,543.54` (Previous balance — largest) or `822.05` (the post-due-date figure). See Open Question 6: the request is dated 2026-02-07, one day *after* the 06-Feb cut-off. |
| `image_06` | **1,995.00 INR** | Words: "One Thousand And Nine Hundred And Ninety-Five Rupees And Zero Paisa Only" | The `Total` row's tax cells `47.49`; or the largest line `565.00`; or summing the "Taxable Value" column (which excludes GST and the delivery sub-rows). |
| `image_07` | **8,528 INR** (Grand Total) | SubTotal 8122.00 + 203.05 + 203.05 = 8528.10, rounded to 8528 | `8528.10` (the line labelled "Total:") or `8122.00` (SubTotal). The bill itself prints both — see Open Question 5. |
| `image_08` | **15,339.00 INR** | 13,880 + 1,050 + 409 = 15,339; words confirm; `message_35` says "the receipt has the final INR amount" | `13,880.00` (largest line). Also a date trap: the Due Date column says 30-08-2026 but the event and the Charge Date are 24-07-2026 — `message_35` explicitly flags 30-08 as "the **original** due date". |
| `image_09` | **723.00 INR** | Words confirm | Nothing much — this one is clean. |
| `image_10` | **79,679.26 INR** | Words: "Seventy-Nine Thousand Six Hundred Seventy-Nine and Twenty-Six Paise"; Total = Balance Due | `72,045.00` (Sub Total — pre-GST); or reading page 1 only and missing item 22 plus the whole summary block; or the largest line `9,600.00`. |
| `image_11` | **3,650.00 INR** (Balance) | Total Bill = Amount Payable = Balance = 3650.00, Amount Paid = 0.00 | **`0.00`** — any extractor keyed on "amount paid" returns zero, which the spec explicitly forbids for a blank amount. Or summing the DETAILED BREAKUP subtotals (250+1400+1000+500 = **3150**, wrong, because the breakup is cut off at the bottom). |
| `image_12` | **33.50 USD** → **2,791.555 INR** at the 2025-10-01 `USD→INR` rate of 83.33 | Subtotal 33,50 = Total 33,50; Cash 40,00 − Change 6,50 = 33,50 | **`3350`** from comma-as-thousands. Or `40.00` ("Cash Paid"). Or forgetting FX entirely and booking 33.50 against an INR balance. |
| `image_13` | **2,298 INR** (Total paid) | Item Total = Total paid, 699 + 1,599 = 2,298; `message_64` confirms "paid in INR on 3 April 2026" | `1,599` (largest item). |
| `image_14` | **4,543.00 INR** | 1500+724+796+550+303+670 = 4543, matching the handwritten TOTAL | Misreading handwriting: `724`→`729`/`124`, `796`→`746`, and the TOTAL's leading `4` reads convincingly as `9` (→ 9543). Or reading the Rs./Ps. columns together as `454300`. Or taking the struck-over `670` at face value without the sum check. |
| `image_15` | **9,968.00 INR** (Total Incl Taxes) | 9,124 + 388 + 228 + 228 = 9,968 | **`9,512.00`** — the Grand Total row's cell under the column literally headed `Total`. The correct column is the far-right `Total(Incl Taxes)`. Or the row-level `9,580.00`. |
| `image_16` | **393.22 INR** | 333.24 + 29.99 + 29.99 = 393.22; words confirm | **`333.24`** — the only value in the column headed `AMOUNT`; the 393.22 sits outside the table under a bold "Total". Also the "-" placeholders for Session/Idle Fee can be parsed as 0 or as a minus sign. |

**Recommended extraction discipline, derived from the above:** prefer the figure that (a) is
labelled `Balance Due` / `Amount Due` / `Net Pay` / `Grand Total` / `Total paid`, in that
priority over `Total`, `Subtotal`, `Amount Paid`, or any line item; (b) reconciles arithmetically
against the visible components; and (c) matches the amount-in-words block when one exists.
Eleven of the sixteen images carry at least one independent cross-check, so a
verify-then-accept extractor is realistic here.

### 5.5 Same-vendor-type layout disagreements

- **`image_08` vs `image_09`** — **agree.** Identical "Receipt / Charge Date / Invoice No. /
  Due Date / Charged Amount(₹) / Amount (₹)" template ending in "Total Amount Received" plus an
  "In Words" line. A single template rule handles both.
- **`image_04` vs `image_13`** — **disagree, and this is the dangerous pair.** Both are
  e-commerce/delivery order summaries. `image_13` ends with an explicit bold **"Total paid"**;
  `image_04` has **no total row at all** — it is cropped after "Item Bill", and its next row
  ("Delivery f…") is cut mid-glyph. An extractor tuned to "find the row labelled Total paid"
  returns nothing on `image_04`. The correct fallback there is `Item Bill` (2854.00), validated
  by summing the line items.
- **`image_03` vs `image_07`** — both are Indian thermal-paper tax invoices, but `image_03`
  terminates at `Net Amount` / `Cash Paid` with no tax lines, while `image_07` adds
  SubTotal → SGST → CGST → Total → **Grand Total (rounded)**. Same physical format, different
  terminal label and a rounding step in one but not the other.
- **`image_06` vs `image_10` vs `image_15`** — all Indian GST invoices, three different total
  labels: `Total` (last column of a totals row), `Total` + `Balance Due` (a summary block), and
  `Grand Total` split across **five** numeric columns of which only the last is correct.
  There is no single "GST invoice" rule.

---

## 6. Untrusted content survey

### 6.1 Direct prompt-injection / system-directed instructions

**Count: 0.** A regex sweep of all 215 `message_text` values for system-directed imperatives
(`ignore`, `disregard`, `override`, `you must`, `treat this as`, `set … to`, `system`,
`assistant`, `agent`, `classify`, `output`, `regardless`, `bypass`, `priority override`, …)
returned **zero** matches. The same sweep over all 250 `request_text` values also returned
**zero** matches. None of the sixteen images contains text addressed to a machine.

Message bodies are uniformly third-party notification prose (121 distinct opening phrases across
215 messages, e.g. "A quick update from the payroll team at …", "Here's the latest account
information from …"), each ending with a reference code (`EMP-00nn`, `BAN-00nn`, `FIN-00nn`,
`MER-00nn`, `SER-00nn`).

### 6.2 Social-engineering / advance-fee content (the real trap)

**Count: 2** messages read as manipulation aimed at the *user* rather than at the system. They
carry no `request_id` and no `related_event_id`, and there is no corresponding financial event —
acting on them means inventing either income or an expense. Quoted at ≤ 15 words each,
**not acted upon**:

| source_id | user | source_type | quote (≤15 words) |
|---|---|---|---|
| `message_67` | user_88 | `financial_service` | "Pay the release charge today to receive the funds immediately." |
| `message_142` | user_179 | `financial_service` | "Bayar biaya pencairan hari ini agar dana segera diterima." |

Both also contain a "Congratulations! You've been selected for a cash prize" / "Selamat! Anda
terpilih untuk menerima hadiah uang tunai" framing. The spec's instruction not to count
"lottery proceeds" until they settle is aimed squarely at these.

A third, `message_134` (user_170), is the safe counterpart — it states a prize claim is
"verified and still being paid out … has not yet reached your account", i.e. explicitly
*not* cash. There are 6 `windfall`-category events and 6 `Prize proceeds` income rows in
`financial_events.csv`; several messages (e.g. `message_17`, `message_28`, `message_88`,
`message_99`, `message_110`) confirm prize proceeds *have* settled. So the dataset deliberately
mixes settled prizes (countable) with unsettled/scam prizes (not countable).

### 6.3 Messages that contradict or extend the ledger

Not injections, but they change the answer and must be read as data:

- **`message_86`** (user_113) supplies a future salary that has **no event row**:
  "Your employer has confirmed a USD 1296 salary credit for 15 September 2026." user_113 has no
  `scheduled` "Next confirmed salary" event; the USD 1296 figure is corroborated by five settled
  `International employer payroll` rows on the 15th of Apr–Aug 2026.
- **`message_35`** (user_48) distinguishes the receipt's payment date (24 July 2026) from
  "the original due date" printed on it (30-08-2026).
- Archetype-3 refund messages (8 of them, e.g. `message_14`, `message_25`, `message_39`,
  `message_59`, `message_152`, `message_172`, `message_215`) all say a refund "has been initiated
  but has not reached your account yet" — reinforcing *do not* count pending credits.
- Archetype-5 duplicate-charge messages (6, e.g. `message_106`, `message_121`, `message_157`,
  `message_164`, `message_183`, `message_197`) say the extra charge "is still being investigated"
  and "a reversal has not been posted" — see Open Question 1.
- Archetype-8 valuation messages (e.g. `message_15`, `message_52`, `message_205`) say
  "No units have been sold and no cash proceeds have been generated."
- Three bank messages (`message_13`, `message_23`, `message_41`, `message_135`) explain that a
  matching debit/credit pair is "a transfer between your two accounts" — self-transfers, net zero.

### 6.4 Length distribution and linkage

`message_text` length (characters): n=215, min **127**, p25 230, **median 254**, p75 276,
p90 299, max **330**, mean 251.0.

Histogram (100-char bins): `100–199` → 14, `200–299` → 182, `300–399` → 19.

| linkage | count |
|---|---|
| has `related_event_id` (describes one supplied event row) | **39** |
| no `related_event_id` (general user context) | **176** |
| has `request_id` | 128 |
| no `request_id` | 87 |
| has both `request_id` and `related_event_id` | 28 |
| has `request_id` only | 100 |
| has `related_event_id` only | 11 |
| has neither (pure user-level context) | 76 |

`source_type` × has-event: `merchant` 8/17 linked, `financial_service` 17/23, `bank` 10/18,
`employer` 3/126, `service_provider` 1/31. So employer messages are overwhelmingly
*general* payroll context, while merchant/bank/financial_service messages usually annotate a
specific event.

Language: 45 messages are in Bahasa Indonesia, 170 in English. 60 contain non-ASCII characters
(typographic quotes `'` and Indonesian text). No non-Latin script. `sent_at` is `09:30:00Z` for
214 of 215 messages; `message_86` alone is `01:00:00Z`.

---

## 7. Currency and FX coverage

### 7.1 Distributions

Profiles (`home_currency`, n=275): `INR` 67, `EUR` 62, `IDR` 55, `ZAR` 51, `USD` 40.
Events (`currency`, n=25,342): `INR` 6,457, `EUR` 5,585, `IDR` 4,992, `ZAR` 4,489, `USD` 3,819.

### 7.2 Conversions actually required

A conversion is required only when an event's `currency` differs from its owner's
`home_currency`.

**Required conversions: 140 of 25,342 events (0.55%), across 27 users.**

| event currency → home currency | count |
|---|---|
| `USD` → `INR` | 54 |
| `USD` → `IDR` | 28 |
| `USD` → `EUR` | 22 |
| `EUR` → `ZAR` | 20 |
| `EUR` → `USD` | 16 |

By type: 139 `income`, 1 `expense`. By direction: 139 `credit`, 1 `debit`.
By status: 132 `settled`, 8 `scheduled`.

The 139 credits are all recurring foreign payroll (`International employer payroll`, or a
`Next confirmed salary` row) and every one of them settles on the **15th of the month**.
The single debit is **`event_7307`** — the blank-amount USD taxi fare for INR-home `user_78`,
settling 2025-10-01.

### 7.3 Rate lookup: exact pair, exact settlement date

**Misses: 0.** Every one of the 140 required conversions has an exact
`(from_currency, to_currency, settlement_date)` row in `exchange_rates.csv`.

`exchange_rates.csv` contains 39 distinct `rate_date` values: the **15th of every month**
from 2023-10-15 to 2026-11-15, **plus exactly one exception — `2025-10-01`** carrying
`USD,INR,83.33`. That row exists solely to serve `event_7307`. Its presence is strong
confirmation that `event_7307`'s image amount is meant to be converted on its settlement date.

### 7.4 Pairs present, and directionality

| pair | dates | rate (constant across all dates) | date range |
|---|---|---|---|
| `USD` → `INR` | 33 | `83.33` | 2024-01-15 … 2026-11-15 |
| `USD` → `IDR` | 30 | `15833.33` | 2023-10-15 … 2026-06-15 |
| `USD` → `EUR` | 25 | `0.92` | 2023-10-15 … 2026-03-15 |
| `EUR` → `USD` | 24 | `1.09` | 2024-04-15 … 2026-09-15 |
| `EUR` → `ZAR` | 22 | `20` | 2023-10-15 … 2026-01-15 |

**Every rate is constant over time within a pair** — there are only 5 distinct rate values in
the whole file. Date matching is therefore a correctness formality, not a numeric one, but it
should still be enforced strictly so an unexpected pair/date fails loudly.

**Directionality — yes, the file is asymmetric:**

- `USD↔EUR` is the **only** bidirectional pair (`USD→EUR` 0.92 and `EUR→USD` 1.09 both present;
  note 1/0.92 = 1.0870 ≠ 1.09, so the two directions are not exact reciprocals).
- **`EUR→ZAR` is present (20) but `ZAR→EUR` is absent.**
- **`USD→IDR` is present but `IDR→USD` is absent.**
- **`USD→INR` is present but `INR→USD` is absent.**

Of the 20 possible ordered pairs among the five currencies, **15 are entirely absent**:
`EUR→IDR`, `EUR→INR`, `IDR→EUR`, `IDR→INR`, `IDR→USD`, `IDR→ZAR`, `INR→EUR`, `INR→IDR`,
`INR→USD`, `INR→ZAR`, `USD→ZAR`, `ZAR→EUR`, `ZAR→IDR`, `ZAR→INR`, `ZAR→USD`.

None of the 15 missing pairs is ever needed by the data, so **do not** implement reciprocal
inversion or triangulation — treat a missing pair as a hard error instead.

---

## 8. Ground truth shape (`sample_requests.csv`, n=25)

### 8.1 Class distributions

`affordability_status`:

| value | n |
|---|---|
| `affordable_with_plan` | 9 |
| `not_affordable` | 7 |
| `affordable_later` | 6 |
| `affordable_now` | 3 |

`recommended_payment_method`:

| value | n |
|---|---|
| `not_recommended` | 7 |
| `full_payment` | 6 |
| `wait` | 6 |
| `installments` | 5 |
| `partial_payment` | 1 |

**Cross-tab — this is nearly a function, and it is the single most useful structural fact here:**

| status | method | n |
|---|---|---|
| `affordable_now` | `full_payment` | 3 |
| `affordable_with_plan` | `installments` | 5 |
| `affordable_with_plan` | `full_payment` | 3 |
| `affordable_with_plan` | `partial_payment` | 1 |
| `affordable_later` | `wait` | 6 |
| `not_affordable` | `not_recommended` | 7 |

`affordable_now` ⇒ `full_payment`; `affordable_later` ⇔ `wait`; `not_affordable` ⇔
`not_recommended`. Only `affordable_with_plan` fans out — and all three of its `full_payment`
rows (`request_06`, `request_11`, `request_21`) are exactly the rows where
`spending_changes_needed ≠ none`.

### 8.2 `payment_plan` payment counts

| payments in plan | n |
|---|---|
| 0 (`none`) | 7 |
| 1 | 12 |
| 2 | 1 |
| 3 | 5 |

`payment_plan = none` occurs in exactly the 7 `not_recommended` rows, and only there.
Every `wait` row carries a **single future payment** (not `none`) whose date equals
`earliest_date_for_full_payment`. Every installment plan has 3 payments here and matches a
supplied option's `payment_amount`, `number_of_payments`, `first_payment_date` and
`payment_frequency_days` exactly (verified by reconstructing the dates from the frequency).

### 8.3 `spending_changes_needed`

Not `none` in **3 of 25** rows. Change counts: 1 change ×2, 2 changes ×1. Never 3.

| request | value |
|---|---|
| `request_06` | `stop:event_476` |
| `request_11` | `reduce_to:event_989:665950` |
| `request_21` | `stop:event_1815\|reduce_to:event_1816:23.50` |

Three facts confirmed against `financial_events.csv`:

1. The `reduce_to` amount is **exactly the event's `minimum_allowed_amount`**
   (`event_989` → 665950; `event_1816` → 23.5). Not a computed shortfall.
2. The referenced `event_id` is the **most recent occurrence** of that recurring series
   (`event_476` is the last of 5 monthly "Family streaming plan" rows; `event_1815` the last
   of 5; `event_1816` the last of 5; `event_989` the last of 2).
3. The target's `flexibility` matches the verb: `stop:` on `stoppable`, `reduce_to:` on
   `reducible` or `reducible_or_stoppable`. `event_1816` is `reducible_or_stoppable` and was
   *reduced*, not stopped.

10 of the 275 users have **zero** flexible series, so `spending_changes_needed` must be `none`
for them by construction. Flexible series per user ranges 0–12 (mode 2).

### 8.4 `earliest_date_for_full_payment`

Empty in **7 of 25** — exactly the 7 `not_affordable` rows. Non-empty in all 18 others.

Every non-empty value that is not `request_date` **falls on the 15th of a month**
(2019-11-15, 2024-06-15, 2025-04-15, 2024-05-15, 2026-09-15, 2025-07-15, 2026-01-15,
2025-09-15, 2024-10-23 ←, 2026-03-15, 2025-01-15, 2024-09-15, 2026-04-15). The one exception is
`request_07`'s 2024-10-23. The pattern reflects salary settlement dates.

For the three `affordable_with_plan` + `full_payment` rows, `earliest_date_for_full_payment` is
**after** `desired_completion_date` (by 1 day in two cases) — consistent with the spec line that
it measures capacity *without* optional spending changes.

### 8.5 Amount formatting — three different rules in the same row

This matters for scoring and is easy to get wrong.

**`amount_safe_to_pay` — minimal representation, trailing zeros stripped, no separators, no symbol.**
Observed decimal places: 0 dp ×14, 1 dp ×3, 2 dp ×8.
Verbatim values:
```
25256  17229139.2  873000  8401800  737  603.3  87170.56  284.57  166.61  12700
12510645  65164  433.4  597.74  83.05  122500  243849.58  462  28820  5400
1543.35  475.46  9152  13420  1425000
```
Note `603.3`, `433.4`, `17229139.2` — **one** decimal place. This field is *not* padded to 2 dp.
No thousands separators, no currency symbol, never `0` in the samples (min is 83.05).

**`payment_plan` amounts — integers bare, non-integers padded to exactly 2 dp.**
Observed decimal places: 0 dp ×11, 2 dp ×18, **never 1 dp**. Verbatim tokens:
```
25256  15952906.67 (×3)  5491000  12693000  620.40  68432 (×3)  996.60  166.61
13110000  22590.19 (×3)  941.60  122500  95194.67 (×3)  3246.10  28820  10840
1574.40  253.59 (×3)  38016
```
`620.40`, `996.60`, `941.60`, `3246.10`, `1574.40` are **zero-padded** — compare with
`amount_safe_to_pay`'s `603.3`. The same numeric value would be written differently in the two
columns. Format: `YYYY-MM-DD:AMOUNT`, entries joined by `|`, no spaces anywhere.

**`spending_changes_needed` amounts — same rule as `payment_plan`.**
`reduce_to:event_989:665950` (integer, bare) vs `reduce_to:event_1816:23.50` (padded 2 dp).

**`decision_explanation` amounts — `<CUR> <#,##0[.00]>`,** i.e. currency code, space,
comma-grouped thousands, 2 dp only when non-integer:
`ZAR 25,256` · `IDR 15,952,906.67` · `EUR 620.40` · `INR 122,400` · `EUR 5,414.20` ·
`USD 23.50` · `IDR 34,140,600` · `INR 92,800`.
Dates inside explanations use `D Month YYYY` with **no leading zero**:
"15 November 2019", "8 August 2025", "12 January 2026", "1 March 2026".
Explanations are 1–2 sentences and follow four templates, keyed to the method:
- `full_payment` (no changes): "Pay CUR X today. This leaves at least CUR MIN available over the next 90 days."
- `full_payment` (with changes): "Stop/Reduce …, then pay CUR X today. This leaves at least CUR MIN available."
- `installments`: "Use N installments of CUR X, starting D Month YYYY. This leaves at least CUR MIN available."
- `wait`: "Pay CUR X in full on D Month YYYY. Paying earlier would take the balance below the CUR MIN minimum."
- `partial_payment`: "Pay CUR A today and the remaining CUR B on D Month YYYY. This completes the full request and keeps the CUR MIN minimum protected."
- `not_recommended`, two variants: "Do not make this payment by D Month YYYY. None of the available options keeps the CUR MIN minimum protected." and "Do not proceed with the CUR X request. Although CUR Y is available today, the full amount cannot be completed safely within 90 days."

`requested_amount` in the input columns has 0 dp ×17, 1 dp ×7, 2 dp ×1 — also minimal
representation, matching `amount_safe_to_pay`'s convention rather than `payment_plan`'s.

### 8.6 Sample rows checked against the spec's stated invariants

**Result: zero violations. No sample row contradicts the specification.**

Every one of the following was machine-checked across all 25 rows:

- `0 ≤ amount_safe_to_pay ≤ requested_amount` — holds in all 25.
- `affordable_now ⇒ earliest_date_for_full_payment == request_date` — holds (3/3).
- `affordable_now ⇒ full_payment ∈ payment_methods_user_will_consider` — holds.
- `affordable_now ⇒ amount_safe_to_pay == requested_amount` — holds.
- `recommended_payment_method ∈ {full_payment, partial_payment, installments}` ⇒ that method is
  in the user's `payment_methods_user_will_consider` — holds in all 12 such rows.
- `wait ⇒ full_payment ∈ payment_methods_user_will_consider` — holds (6/6).
- `partial_payment` ⇒ status is `affordable_with_plan`, `allows_partial_payment=true`,
  `0 < safe < requested`, exactly 2 payments, payments sum to `requested_amount`, first on
  `request_date`, second on `earliest_date_for_full_payment`, and
  `earliest ≤ desired_completion_date` — all hold for `request_19`.
- `not_recommended ⇔ payment_plan == none` — holds in both directions (7/7).
- `installments` ⇒ the plan matches a supplied option **and** the dates are exactly
  `first_payment_date + k × payment_frequency_days` **and**
  `number_of_payments ≤ max_installment_months` — holds for all 5.
- `payment_plan` is chronological, never starts before `request_date`, never ends after
  `desired_completion_date`, never ends beyond `request_date + 90 days` — holds for all 18 plans.
- `earliest_date_for_full_payment`, when present, is within `[request_date, request_date+90d]` — holds.
- `spending_changes_needed` has ≤ 3 entries and never stops and reduces the same `event_id` — holds.

One derived rule the samples add that the spec does not state explicitly:
**`affordable_with_plan` + `full_payment` occurs only when `spending_changes_needed ≠ none`**
(3 for 3). Without a spending change, a full payment that is safe today is `affordable_now`.

---

## 9. Request and option shape

### 9.1 Options per request

| options | eval (n=250) | sample (n=25) |
|---|---|---|
| 2 | 58 | 7 |
| 3 | 165 | 15 |
| 4 | 27 | 3 |

**Requests with zero options: 0** (in both sets). Every request has exactly **one**
`full_payment` option and 1–3 `installments` options. No request lacks a full-payment option
and none lacks an installment option.

Eval set: 719 options total — 250 `full_payment`, 469 `installments`.

### 9.2 Fees

**Nonzero `financing_fee`: 469 of 719 (65.2%)** in the eval set; 46 of 71 in the sample set.
The split is exactly by method: **every** `installments` option has a nonzero fee and **every**
`full_payment` option has `financing_fee = 0`. There are no zero-fee installment plans and no
fee-bearing full payments.

### 9.3 First-payment offsets (days after `request_date`)

Eval set, all options:

| offset (days) | count |
|---|---|
| 0 | 347 |
| 1 | 2 |
| 3 | 142 |
| 5 | 2 |
| 7 | 95 |
| 14 | 131 |

**No negative offsets** — no option ever starts before `request_date`.

Split by method:
- `full_payment`: offset is **always 0** (250/250). The full-payment option is always "today".
- `installments`: `0` ×97, `1` ×2, `3` ×142, `5` ×2, `7` ×95, `14` ×131.

### 9.4 `payment_frequency_days` and `number_of_payments`

`payment_frequency_days` (eval): `28` ×165, `30` ×151, `31` ×153, blank ×250 (all full payments).

`number_of_payments` (eval): `1` ×250, `2` ×6, `3` ×74, `4` ×3, `6` ×60, `15` ×81, `18` ×79,
`21` ×80, `24` ×86.

Joint `(number_of_payments, payment_frequency_days)`:
```
(1, -)  250     (2, 28)   5     (2, 31)   1     (3, 28)  27     (3, 30)  16
(3, 31)  31     (4, 30)   3     (6, 28)  24     (6, 30)  19     (6, 31)  17
(15, 30) 81     (18, 31) 79     (21, 28) 80     (24, 28) 29     (24, 30) 32     (24, 31) 25
```

The population is bimodal: **short plans (2/3/4/6 payments, 143 options)** and **long plans
(15/18/21/24 payments, 326 options)**. Since `max_installment_months` maxes out at 12, every
long plan is rejected for every user. Only the short plans can ever be chosen.

### 9.5 Plans that overrun their own deadline

- **394 of 719 (54.8%)** eval options have a last payment **after** `desired_completion_date`.
- **389 of 719 (54.1%)** have a last payment **after** `request_date + 90 days`.

So over half the supplied options are structurally ineligible before any balance arithmetic runs.

### 9.6 The eligibility funnel — a top-level finding

Applying only the three non-financial filters — (a) method ∈ user's
`payment_methods_user_will_consider`, (b) `number_of_payments ≤ max_installment_months`,
(c) last payment ≤ `desired_completion_date`:

| eligible supplied options | eval requests | sample requests |
|---|---|---|
| 0 | **28** | 4 |
| 1 | **222** | 21 |
| 2+ | **0** | **0** |

**No request anywhere in the dataset has more than one eligible supplied payment option.**
The ranking tie-breakers in the spec (minimize total paid, start earlier, fewer payments,
lowest `payment_option_id`) therefore never have to discriminate *between supplied options* —
they only ever discriminate between the single eligible supplied option, a synthesised
`partial_payment` plan, and `wait`.

The sample set confirms the selected option is always that single eligible one, with one
instructive exception: **`request_19`** had `payment_option_53` (installments, n=2) eligible,
yet ground truth chose `partial_payment`. Total payable was 41,246.40 for the installment plan
versus 39,660 for the partial plan — ranking rule 3 ("minimize the total amount paid") decided it.

**The 28 zero-eligible-option eval requests** (`not_recommended` unless partial payment rescues
them). 12 of the 28 both allow partial payment *and* have a user who accepts `partial_payment`;
the other **16 have no fallback at all**:

| request | user | payment_methods_user_will_consider | max_inst | allows_partial | installment option sizes |
|---|---|---|---|---|---|
| `request_54` | user_54 | partial_payment\|installments | 11 | false | 18, 24 |
| `request_63` | user_63 | partial_payment | — | true | 18 |
| `request_65` | user_65 | partial_payment | — | true | 15 |
| `request_76` | user_76 | partial_payment | — | false | 21, 24 |
| `request_79` | user_79 | partial_payment | — | true | 21 |
| `request_80` | user_80 | installments | 3 | false | 15, 24 |
| `request_83` | user_83 | partial_payment\|installments | 10 | false | 15 |
| `request_85` | user_85 | partial_payment\|installments | 2 | false | 21 |
| `request_95` | user_95 | partial_payment | — | false | 15 |
| `request_116` | user_116 | partial_payment\|installments | 12 | false | 15, 24 |
| `request_129` | user_129 | installments | 10 | false | 18 |
| `request_133` | user_133 | partial_payment | — | false | 21 |
| `request_140` | user_140 | installments | 3 | false | 15, 24 |
| `request_141` | user_141 | partial_payment | — | true | 18 |
| `request_168` | user_168 | partial_payment | — | true | 3, 4 |
| `request_171` | user_171 | partial_payment\|installments | 4 | false | 18 |
| `request_184` | user_184 | installments | 2 | false | 21, 24 |
| `request_209` | user_209 | partial_payment | — | false | 15 |
| `request_220` | user_220 | partial_payment\|installments | 6 | true | 21, 24 |
| `request_227` | user_227 | partial_payment | — | false | 15 |
| `request_236` | user_236 | partial_payment | — | true | 15, 24 |
| `request_240` | user_240 | partial_payment | — | true | 18, 24 |
| `request_248` | user_248 | partial_payment | — | true | 15, 24 |
| `request_250` | user_250 | partial_payment | — | true | 21, 24 |
| `request_251` | user_251 | partial_payment\|installments | 11 | false | 15 |
| `request_260` | user_260 | partial_payment | — | true | 15, 24 |
| `request_263` | user_263 | partial_payment | — | true | 15 |
| `request_270` | user_270 | installments | 7 | false | 18, 24 |

Note `request_168`: options of size 3 and 4 exist, but user_168 does not accept `installments`
at all (methods = `partial_payment` only, `max_installment_months` blank).

### 9.7 `desired_completion_date`

| metric | eval | sample |
|---|---|---|
| min (`dcd − request_date`) | **6 days** | 11 days |
| max | **86 days** | 76 days |
| `dcd < request_date` | **0** | 0 |
| `dcd > request_date + 90 days` | **0** | 0 |

Histogram of `dcd − request_date`, 15-day bins (eval): `0–14` 23, `15–29` 30, `30–44` 25,
`45–59` 30, `60–74` 109, `75–89` 33.

So `desired_completion_date` is **always** inside the 90-day forecast window and always at least
6 days out. No inverted or out-of-horizon deadlines exist; assert it anyway, but no repair path
is needed.

### 9.8 Two other shape facts worth knowing

- **`current_available_balance` is a given, not derivable.** Summing all settled cash events up
  to `request_date` (with FX applied) differs from the stated balance by large, arbitrary,
  sign-varying amounts (e.g. user_01 +160,144.93 ZAR; user_03 −3,674,472.58 IDR). The balance is
  the starting point at `request_date`; past settled events are history for recurrence detection
  only, and must not be re-applied to the balance.
- **Only 47 of 275 users have an explicit `scheduled` "Next confirmed salary" event.** For the
  other 228, future income must be inferred from recurrence in `settled` history (and, for
  user_113, from `message_86`). Where the scheduled row does exist, it lands 8–20 days after
  `request_date`. The most recent event for a user is typically 1 day before `request_date`
  (129 of 275 users), ranging −20 to +6 days.

---

## 10. Open questions

Places where the spec is ambiguous against what the data actually shows. **Not resolved here.**

1. **Pending "Possible duplicate card charge": reserve it or ignore it?**
   Archetype 5 (7 pairs, e.g. `event_12708`→`event_12709`, `event_18268`→`event_18269`) is a
   settled "Original card charge" followed by a **pending** debit of the identical amount
   described as "Possible duplicate card charge". The spec says both *"Reserve pending debits"*
   and *"Ignore … duplicate records"*. Those directly conflict on these 7 rows.
   The linked messages say the extra charge "is still being investigated" and "a reversal has not
   been posted to the account yet."
   **Best reading:** reserve it — the money has genuinely left/is leaving, no reversal exists,
   and conflict-resolution rule 4 says prefer "the financially safer interpretation."
   **Alternative:** ignore it as a duplicate record, which the 90-Day Safety Check names
   explicitly alongside failed and cancelled transactions.

2. **`max_installment_months`: compare against `number_of_payments`, or against elapsed months?**
   The field is named in *months* but plans are defined by a payment **count** and a
   `payment_frequency_days` of 28, 30 or 31. A 6-payment / 31-day plan spans 186 days ≈ 6.1
   months — accepted under a count rule, rejected under a span rule.
   Every sample row is consistent with **both** readings, so the samples do not discriminate.
   Exactly **two** eval options disagree between the two rules:
   `payment_option_646` (`request_224`, n=3, freq 28, user max 2) and
   `payment_option_679` (`request_235`, n=6, freq 30, user max 5).
   **Best reading:** count rule (`number_of_payments ≤ max_installment_months`) — it is the
   simpler mapping and both disputed options fail it, which is also the safer outcome.
   **Alternative:** span rule (`freq × (n−1) / 30.44 ≤ max`), which would admit both.

3. **Does `affordable_later` or `affordable_with_plan` apply when `wait` completes by the deadline?**
   Every `wait` sample (6/6, e.g. `request_03`, `request_08`, `request_13`) has
   `earliest_date_for_full_payment ≤ desired_completion_date`, so the request *is* completed by
   its deadline via a plan — yet the label is `affordable_later`, not `affordable_with_plan`.
   The spec defines `affordable_with_plan` as "the full requested amount can be completed safely
   using a partial-payment schedule, installments, or permitted spending changes," which
   arguably covers a single deferred full payment.
   **Best reading:** a single deferred full payment is always `affordable_later` + `wait`;
   `affordable_with_plan` is reserved for the three named mechanisms.
   **Alternative:** classify a deadline-meeting deferred payment as `affordable_with_plan`,
   which the samples contradict — so this reading is almost certainly wrong, but the spec text
   alone does not exclude it.

4. **What happens when `wait` is the right answer but the user does not accept `full_payment`?**
   The spec says `wait` is eligible only when "full payment becomes safe later **and** the user
   accepts `full_payment`." For the 112 users whose methods exclude `full_payment`, a request
   that only becomes affordable later has no `wait` route. The samples contain no such case.
   **Best reading:** fall through to `not_recommended` with `payment_plan = none`, while still
   reporting a non-empty `earliest_date_for_full_payment` (which the spec says measures capacity
   "independently of the user's payment-method preferences").
   **Alternative:** leave `earliest_date_for_full_payment` empty too, on the grounds that no
   full payment will ever be made. The two readings produce different values in a scored column.

5. **`image_07`: `8528` (Grand Total, rounded) or `8528.10` (Total, exact)?**
   The receipt prints both, on adjacent lines, with "Grand Total (RS): 8528" in bold beneath
   "Total: 8528.10". `request_35` is in the eval set, so no ground truth exists.
   **Best reading:** `8528` — it is the final, bolded, actually-paid figure on a "PAID"-stamped
   receipt, and Indian retail rounds to the rupee at the till.
   **Alternative:** `8528.10`, the arithmetically exact tax-inclusive total.

6. **`image_05`: which of the two due amounts applies?**
   The bill shows "Amount due till 06-Feb-2026 = 704.05" and "Amount due after 06-Feb-2026 =
   822.05". The event (`event_1786`) is dated 2026-02-06 but is **`pending` with a settlement
   date of 2026-02-09**, and `request_date` is 2026-02-07 — both after the cut-off.
   **Best reading:** 704.05, matching the event's own `event_date` of 2026-02-06 and the
   independently-reconcilable "This Month's Charges" total (580.65 + 16.00 + 107.40).
   **Alternative:** 822.05, since the bill will actually settle on 2026-02-09, past the cut-off.
   `request_20` is a sample row but is `not_affordable` under either figure, so ground truth
   does not discriminate.

7. **Can a message create future income that has no event row?**
   `message_86` states "Your employer has confirmed a USD 1296 salary credit for 15 September
   2026" for user_113, who has **no** `scheduled` salary event. The spec says "Do not invent
   unsupported future income" but also "Use messages and images to clarify, amend, cancel,
   delay, or confirm financial information."
   **Best reading:** count it — the message is the support, it is corroborated by five settled
   USD 1296 payroll rows on the 15th of Apr–Aug 2026, and it converts at the 2026-09-15
   `USD→INR` rate. `request_113` is in the eval set, so this is a live scoring decision.
   **Alternative:** rely only on recurrence inferred from the settled history and treat the
   message as confirmation rather than as the source. (Both give the same number here; they
   diverge if the message and the history ever disagree.)

8. **Are past `settled` events already reflected in `current_available_balance`?**
   §9.8 shows the balance is not reconstructible from the ledger, which strongly implies the
   balance is a given as-of `request_date`. But 17 users have their last event **on**
   `request_date` and 6 have events up to 6 days *after* it.
   **Best reading:** the balance is as-of `request_date` and already includes every event whose
   `settlement_date ≤ request_date`; only later-settling events (pending, scheduled, and the
   forward forecast) are applied on top.
   **Alternative:** the balance is as-of the last settled event and same-day events must be
   applied — which would double-count for those 17 users.

9. **Is a `pending` credit ever counted once its settlement date passes inside the forecast?**
   Archetype-3 pending merchant refunds (8 rows) carry a `settlement_date` 10 days after the
   event date, often *before* `request_date + 90`. The spec says "Ignore pending credits" without
   a time qualifier, and the linked messages say the refund "has not reached your account yet."
   **Best reading:** never count a pending credit, regardless of its stated settlement date.
   **Alternative:** count it from its `settlement_date` onward, treating "pending" as a
   statement about today rather than about the whole horizon.

10. **Does `amount_safe_to_pay` account for the request's own future obligations?**
    It is defined as "the most the user can pay today … without breaking the 90-day safety
    check," and the samples report a non-zero value even for `not_recommended` rows (min 83.05,
    never 0). It is unclear whether the 90-day check behind this number assumes no further
    payments toward the request, or assumes the rest of the request is still coming.
    **Best reading:** a standalone one-off payment today with no further request-related
    outflows — a pure "spare cash above the floor across 90 days" figure.
    **Alternative:** the first leg of the recommended plan, which would make it
    plan-dependent and would contradict the spec's "before optional spending changes" framing.

11. **Which occurrence of a recurring series should `spending_changes_needed` reference?**
    All four sample targets reference the **most recent past occurrence** (§8.3), but the spec
    only says "flexible expenses that must be stopped or reduced." Series length varies 1–7
    occurrences and the last occurrence is 1–178 days before `request_date`.
    **Best reading:** always the latest occurrence by `event_date` within that
    (description, category) series for that user.
    **Alternative:** the first *projected future* occurrence — which has no `event_id` at all,
    so it cannot be expressed in the required `stop:<event_id>` syntax. This effectively
    confirms the best reading, but the spec never states it.

12. **`image_07` and `image_03` aside — is `Net Amount` or `Cash Paid` authoritative when they
    differ in precision?** `image_03` prints "Net Amount: 41272.0" and "Cash Paid: 41272.00".
    They are the same number to two decimals, so nothing turns on it here, but the general rule
    (prefer the amount tendered, or the computed net?) is unstated and would matter if a future
    receipt showed cash paid exceeding the net, as `image_12` does (Cash Paid $40,00 vs Total
    $33,50 with $6,50 change).
    **Best reading:** the invoice total (`Net Amount` / `Total`), never the tendered cash.
    **Alternative:** the tendered amount, which would be wrong on `image_12` by 19%.

13. **The two prize/advance-fee messages (`message_67`, `message_142`) — model anything at all?**
    They imply both an incoming prize and an outgoing "release charge", and neither has any
    corresponding event row.
    **Best reading:** model neither. No income (unsettled, and the spec names lottery proceeds
    explicitly) and no expense (no event supports it; "do not invent unsupported … expenses").
    **Alternative:** model the release charge as a pending obligation on the safety-conservative
    principle — which would be inventing an expense from untrusted content, so almost certainly
    wrong, but it is the direction a naively "safe" agent drifts.

---

*End of report.*

---

## Corrections

Appended 2026-09-12, after the data layer was built and the report's claims were
checked against code rather than against a one-off script. **The body above is
left exactly as written.** It is a record of how the reconnaissance was done,
and visibly correcting it is worth more than silently rewriting it.

### C1 — Three archetype counts in section 3 are wrong

The treatments in section 3's archetype table are all correct, and the total of
58 is correct. Three of the eight per-archetype counts are not:

| archetype | section 3 says | actual |
|---|---|---|
| 4 — `Reimbursable work expense` → `Employer expense reimbursement` | 8 | **7** |
| 5 — `Original card charge` → `Possible duplicate card charge` | 7 | **6** |
| 8 — `Investment contribution` → `Current portfolio valuation` | 8 | **10** |

Nothing depends on these counts. `tests/test_archetypes.py` asserts the
*treatments*, which were right, and it passes against all 58 pairs.

### C2 — The flexibility claim is one-directional, not bidirectional

Section 1.4 states that `flexibility` and the profile pipe-lists "are redundant
encodings of the same fact". That is too strong. The verification behind it
looked only at non-`fixed` events, where the count of disagreements is indeed
zero.

Checking **all** events finds **28** `fixed` events whose category *is* in the
owner's reduce list — 27 `shopping` and 1 `dining`, across 23 `expense` and 5
`refund` rows and 19 users.

That is correct data, not a conflict. `flexibility` is a property of one event;
the profile list is a willingness about a category. A category a user would
reduce in general still contains one-off purchases that are individually fixed.
The true statement is one-directional:

> A flexible event's category is always in the matching profile list. The
> converse does not hold.

Only the forward direction is load-bearing — it is what stops a spending change
exceeding what the user authorised. The two directions are now separate checks:

- `flexible_event_outside_profile_lists` — load-bearing, currently **0**
- `fixed_event_in_changeable_category` — informational, currently **28**

### C3 — There are no whitespace anomalies in this dataset

A later prompt attributed to this report a finding that it "flags whitespace-only
differences". It does not, and the claim is false for this dataset: **zero**
cells across all seven CSVs carry leading or trailing whitespace, and there are
**zero** full-tuple duplicate groups (comparing every field but `event_id`, with
whitespace normalised).

Duplicate removal is implemented with whitespace-normalised comparison anyway,
because it is the safer rule, but on this dataset it is a no-op. It is now a
named decision, `Q17_duplicate_removal`, because on a dataset where it *does*
fire it is the one normalization rule that can delete real recurring spending
and silently change a series' cadence.

### C4 — One predicted currency conversion is deferred, not performed

Section 7 reports 140 conversions required. The pipeline performs **139** and
defers **1**: `event_7307` has a blank amount, so there is nothing to convert
until its document is read. The deferral is explicit in
`ConversionResult.deferred` and in the run report, not silent. 139 + 1 = 140.

This is what fixes the pipeline order: FX cannot run before the vision step.
