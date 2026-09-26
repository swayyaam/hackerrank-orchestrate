# Capacity forensics — why `amount_safe_to_pay` is wrong

Analysis only. Nothing in `engine/`, `data/` or `config/` was changed to produce
it, and no fix is implemented here.

**Question.** The capacity layer gets 3 of 20 resolvable sample requests exactly
right on `amount_safe_to_pay` and 7 of 20 on `earliest_date_for_full_payment`,
with a median relative error of 52.8%. What is causing that?

**Answer, up front.** One cause, not two. Essential variable spending is never
forecast, so the balance curve never erodes between income events. The trough
therefore lands in the first week instead of just before the next inflow, and
every downstream number is too optimistic. The two rows that looked like
separate outliers both collapse into the same cause once a second, independent
defect is accounted for.

---

## 1. Invert the ground truth

For each resolvable request, `implied_trough = actual_amount_safe_to_pay +
minimum_balance_to_keep`. Where our prediction equals `requested_amount` the
clamp is binding and the comparison gives a lower bound only; those rows are
marked and excluded from the level analysis, but kept for the date analysis.

| request | cur | our trough | day | implied trough | signed diff | ratio | clamp | error |
|---|---|---|---|---|---|---|---|---|
| request_05 | ZAR | 43,468.94 | 7 | 13,837.00 | 29,631.94 | 3.14 | ● | both |
| request_09 | EUR | 2,119.70 | 8 | 766.61 | 1,353.09 | 2.77 | ● | exact |
| request_10 | INR | 440,874.57 | 87 | 238,100.00 | 202,774.57 | 1.85 | | level |
| request_12 | ZAR | 189,967.97 | 6 | 108,364.00 | 81,603.97 | 1.75 | ● | exact |
| request_13 | EUR | 2,648.13 | 7 | 1,733.40 | 914.73 | 1.53 | ● | both |
| request_06 | EUR | 1,800.66 | 10 | 1,403.30 | 397.36 | 1.28 | ● | both |
| request_23 | ZAR | 46,147.32 | 7 | 36,152.00 | 9,995.32 | 1.28 | | both |
| request_04 | IDR | 48,498,531.40 | 7 | 39,088,400.00 | 9,410,131.40 | 1.24 | ● | both |
| request_02 | IDR | 56,824,741.10 | 8 | 46,387,539.20 | 10,437,201.90 | 1.23 | | both |
| request_24 | INR | 76,014.50 | 7 | 64,420.00 | 11,594.50 | 1.18 | | both |
| request_01 | ZAR | 49,313.68 | 71 | 43,256.00 | 6,057.68 | 1.14 | ● | exact |
| request_18 | EUR | 2,114.13 | 4 | 1,862.00 | 252.13 | 1.14 | | both |
| request_15 | EUR | 1,431.64 | 7 | 1,283.05 | 148.59 | 1.12 | | level |
| request_25 | IDR | 27,492,807.63 | 8 | 24,804,100.00 | 2,688,707.63 | 1.11 | | level |
| request_21 | USD | 3,673.97 | 9 | 3,343.35 | 330.62 | 1.10 | ● | both |
| request_08 | EUR | 1,182.61 | 64 | 1,084.57 | 98.04 | 1.09 | | both |
| request_07 | INR | 196,080.99 | 8 | 180,170.56 | 15,910.43 | 1.09 | | both |
| request_11 | IDR | 50,360,977.90 | 11 | 46,651,245.00 | 3,709,732.90 | 1.08 | ● | both |
| request_22 | EUR | 1,031.40 | 9 | 975.46 | 55.94 | 1.06 | | both |
| request_14 | EUR | −887.28 | 83 | 2,797.74 | −3,685.02 | −0.32 | | level |

Nine rows are clamp-bound. **Eleven are invertible for level analysis.**

### Constant ratio, constant offset, or scatter?

**A roughly constant ratio, once two known-cause rows are set aside.**

Excluding `request_10` (1.85) and `request_14` (−0.32), the nine remaining
invertible ratios are 1.06, 1.09, 1.09, 1.11, 1.12, 1.14, 1.18, 1.23, 1.28 —
mean 1.144, standard deviation 0.072, **coefficient of variation 0.063**. That
is tight.

The signed differences span 55.94 EUR to 10,437,201.90 IDR, tracking currency
magnitude, so the relationship is multiplicative rather than additive. No
additive normalisation comes close: `diff / opening_balance` has CV 12.4,
`diff / minimum_balance` 6.9, `diff / requested_amount` 3.3, `diff / total
projected outflow` 4.3.

**Ten of eleven invertible rows are too high.** The system is systematically
optimistic, not scattered.

Also note the trough day: **fifteen of twenty trough within eleven days** of the
request. That is the signature of a curve that goes down a little, then gets an
income event, then never comes down again.

---

## 2. The two populations

### `request_10` belongs with the cluster

Zero-income gig worker, trough at day 87 — a monotonic decline across the whole
horizon, which is the one shape where ninety days of variable spending genuinely
could close the gap. Checked arithmetically:

```
required extra outflow           202,774.57 INR  (= 2,253.05/day over 90 days)
unprojected variable spend rate    8,027.28/day  x 90d = 722,455.60
coverage                                                        356%
```

**Confirmed.** Twenty-two of its thirty series project nothing, including all
four income series (irregular platform payouts, correctly not projected under
`Q23`) and eighteen variable debit series. The available rate is more than three
times what is needed, so the mechanism explains this row comfortably — the
calibration, not the mechanism, is what is open.

### `request_05` is the true outlier, and its cause is `Q22`, not spending

```
                    projected income      trough         safe    error
Q22 ON                    44,220.00   43,468.94    15,488.00   14,751.00
Q22 OFF                        0.00   19,651.38     6,551.38    5,814.38
ground truth                      —   13,837.00       737.00
```

`Q22_series_continuation` pairs the stale `Payroll credit` series with
`Final employer payroll` and projects three further salaries of 14,740. The
gap to explain is 29,631.94 — **smaller than the 44,220 of income invented**.

With `Q22` off the error falls 61% and the trough moves from day 7 to day 88,
which is the cluster's shape. The residual 5,814.38 is then covered 281% by the
same unprojected variable spend.

**So there are not two populations. There is one cause plus one independent
defect**, recorded as `L1` in `LIMITATIONS.md`.

---

## 3. `request_23`, day by day

```
user_23 ZAR   opening 51,957.90   minimum 27,000   requested 38,016
ground truth  safe 9,152   earliest 2025-07-15   status affordable_later
implied trough 36,152.00      our trough 46,147.32 on 2025-05-14 (day 7)

  2025-05-07   51,957.90
  2025-05-08   49,277.75   debit    2,680.15  projected   Electricity bill
  2025-05-11   47,724.55   debit    1,553.20  given       Pending pharmacy card charge
  2025-05-14   46,147.32   debit    1,281.33  projected   Personal shopping
                           debit      295.90  projected   Cloud storage plan
```

Four flows in seven days, 5,810.58 of outflow. The gap is **9,995.32**, or
1,427.90 per day.

### Does seven days of unprojected spending cover it?

```
  [irregular      ] n=3  span  77d  rate  62.69/d   7d =   438.86  groceries/Bulk pantry shop
  [irregular      ] n=5  span 105d  rate  93.73/d   7d =   656.12  groceries/Grocery delivery
  [irregular      ] n=4  span 154d  rate  42.09/d   7d =   294.62  groceries/Household groceries
  [irregular      ] n=5  span 140d  rate  62.47/d   7d =   437.32  groceries/Neighbourhood grocer
  [irregular      ] n=4  span 105d  rate  63.53/d   7d =   444.72  groceries/Supermarket basket
  [fixed_interval ] n=2  span  42d  rate  88.93/d   7d =   622.52  groceries/Weekly produce market
  [irregular      ] n=4  span 154d  rate  24.98/d   7d =   174.88  transport/Metro and bus fares
  [irregular      ] n=3  span 126d  rate  18.41/d   7d =   128.88  transport/Parking and tolls
  3 single-occurrence series                        rate  n/a

  TOTAL over 7 days      3,197.92
  GAP                    9,995.32
  SHORTFALL              6,797.40          coverage 32%
```

**Seven days of variable spending covers only 32% of the gap. That refutes the
first-week reading of the hypothesis outright.**

### But the trough is not on day 7

The full curve shows why:

```
  2025-05-14    46,147.32   <- our trough
  2025-05-15    87,637.12   [salary +45,760]
  2025-06-14    58,788.86
  2025-06-15   100,278.66   [salary]
  2025-07-14    72,057.51
  2025-07-15   113,547.31   [salary]
```

Our curve **ratchets upward**: each salary more than replaces what was spent
since the last one, so the lowest point can only ever be before the first
salary. The ground truth's `earliest_date_for_full_payment` is **2025-07-15** —
the third salary — which is only possible if the true curve is still near its
floor in mid-July.

Measured against the day before that salary:

```
  our balance on 2025-07-14              72,057.51
  implied trough                         36,152.00
  required extra outflow                 35,905.51   (= 520.37/day over 69 days)
  unprojected variable rate 456.85/day x 69d = 31,522.31     coverage 88%
```

**Applied across the horizon rather than across the first week, variable
spending covers 88% of the gap and puts the trough on exactly the right day.**

Confirmed by substitution: adding the unprojected rate to `request_23` moves the
trough from 46,147.32 on 2025-05-14 to **40,992.05 on 2025-07-14** — the correct
date — and the safe amount from 19,147.32 to 13,992.05 against an actual of
9,152.00.

---

## 4. Structural hypotheses

### (a) The request's own remaining obligation — **cannot discriminate**

Under `Q10`'s alternative reading, `amount_safe_to_pay` is the first leg of a
plan and the remaining `requested − X` is a future outflow by
`desired_completion_date`. Recomputing all twenty rows under that reading gives
**identical numbers on nineteen of twenty**, and makes the twentieth
(`request_10`) worse: exact 3/20 either way, median relative error 52.8% either
way.

The reason is algebraic, and it is worth stating because it makes the decision
permanently untestable on data of this shape. Paying `X` now and `requested − X`
on `D2` gives a curve of `base(d) − X` before `D2` and `base(d) − requested`
after it. `X` cancels out of the second interval entirely, so the binding
constraint on `X` is the trough over `[request_date, D2)`. In nineteen of twenty
rows the trough already falls before `D2`, so that window contains the same
minimum and the answer does not move.

A variant paying the remainder at our own `earliest_date` instead was also
identical. **`Q10`'s alternative is not refuted — it is unfalsifiable here.**

### (b) Protected-category spending — **cannot discriminate**

`expense_categories_to_protect` names categories a plan must keep covering, and
those categories are already being projected wherever a cadence exists: 26,221.80
ZAR of protected outflow in `request_01`, 17,431,699.88 IDR in `request_02`,
40,702,978.04 IDR in `request_04`. The protected total is not a constant multiple
of the gap and does not track it — `request_06` has 869.87 of protected outflow
against a gap of 397.36, while `request_05` has 20,280.12 against 29,631.94.

There is no evidence of a separate reservation on top of projecting the series
themselves. There is also nothing that rules one out, because the same rows are
explained by cause (4e′) below.

### (c) The minimum balance is a floor on a different quantity — **UNTESTED (the check was circular)**

On the rows where our trough is closest to correct, the *form* of the
calculation reproduces ground truth exactly:

```
  request_22  our trough 1,031.40  − min 500.00  = 531.40   (actual safe 475.46)
  request_07  our trough 196,080.99 − min 93,000 = 103,080.99 (actual 87,170.56)
```

**Correction.** What was written here originally claimed this hypothesis was
refuted. It was not tested at all. `implied_trough` is *defined* as
`actual_safe + minimum_balance`, so showing that
`actual_safe = implied_trough − minimum_balance` recovers the definition and
establishes nothing. The check was circular and the "refuted" verdict is
withdrawn.

The hypothesis is very likely correct anyway, on the specification's own
wording — it says a plan is safe only while the balance stays at or above
`minimum_balance_to_keep`, which is exactly the quantity the closed form treats
as the floor. But that is an argument from the spec text, not a measurement, and
it is carried here as **untested** rather than as evidence.

The rest of the section's conclusion is unaffected: whatever the floor is a
floor on, every combination measured moves the *trough level*, and that is where
the error responds.

### (d) Given commitments dropped by dedup — **refuted**

Every scheduled row and reserved pending debit whose cash date falls inside each
horizon is present in the resulting forecast. Across the twenty requests: 11
given commitments expected, **11 applied, 0 missing**, with 18 inferred
occurrences suppressed against them. **No dedup bug.**

### (e) Fixed commitments misclassified as irregular — **refuted**

This was the first hypothesis to check and it is wrong. Counting debit series in
`rent`, `housing`, `insurance`, `debt_repayment`, `utilities` and `education`
that were classified but not projected:

**0 of 20 requests have a single unprojected fixed-commitment series.**

Rent, utilities, insurance and debt repayments are all clean monthly-anchored
series and all of them project. `request_23`'s rent appears on 2025-06-04,
2025-07-04 and 2025-08-04; the May payment fell on 2025-05-04, before the
request date, and is correctly outside the horizon. A missed rent payment is
indeed the right size and shape to move a day-7 trough — there simply are none.

### (e′) The cause that survives: variable spending across the horizon

Not one of the listed hypotheses, but what the evidence points at. Stated for
completeness because it is the verdict below.

---

## 5. Verdict

### Ranked causes

| rank | cause | evidence | rows explained |
|---|---|---|---|
| 1 | **Essential variable spending is never forecast, so the curve never erodes between income events and the trough lands in the first week instead of before the next inflow** | ratio cluster CV 0.063 across nine rows; 15/20 trough within 11 days; `request_23` covered 88% at horizon scale and only 32% at first-week scale; `request_10` covered 356%; substitution puts `request_23`'s trough on exactly the right day | **18 of 20** |
| 2 | **`Q22` invents income for a terminated employee** | `request_05` alone: 44,220 of invented income against a 29,631.94 gap; turning `Q22` off cuts the error 61% and moves the trough to the cluster's shape. **`Q22` has since been turned off by default.** | **1 of 20** (`request_05`) |
| 3 | **A missing inflow, not a spurious outflow** | `request_14`, now diagnosed: the user's salary is not projected at all, because the pre-leave series is stale and the post-leave series has one occurrence. See `LIMITATIONS.md` L7. | **1 of 20** (`request_14`) |

Causes (b), (c), (d), (e) are refuted or non-discriminating and explain nothing.

### One proposed fix

**Forecast non-projectable debit series as a continuous trailing-rate outflow
across the whole horizon.**

Not a regrouping — regrouping five grocery series under one category key creates
no outflow, which is why the earlier category-merge idea was held and why it
should stay held. The change is that series which currently contribute *nothing*
contribute a conservative rate.

Scope: debit series that were classified and found not projectable — irregular,
single-occurrence, and stale — in categories the profile does not mark
adjustable. Rate from a trailing window, not the full series span, and only from
series whose last occurrence is recent enough to still be running.

### Predicted effect, stated before building

- **Trough dates become right.** This is the strongest prediction. The naive
  version already moves `earliest_date_for_full_payment` from 7/20 to **9/20**
  exact, and collapses the signed day-offset histogram from
  `{−73, −69, −31×3, −30, −12×2, −11, −8, 0×3}` — every populated offset
  negative — to `{−12, 0×4, +23, +30}`.
- **Levels improve but the naive rate over-corrects.** The full-span rate over
  all non-projectable series takes the median relative error from 52.8% to
  **100%**, because several rows are pushed to zero. The mechanism is right and
  the magnitude is wrong.
- **With a damped rate I expect** exact matches to stay low (the three current
  exact matches are clamp artifacts and some will break), median relative error
  to land in the **25–35%** band, and date-exact to reach **10–12 of 20**.

The substance of the work is the rate estimator, not the mechanism. That should
be its own decision entry with its own thresholds, and it should be measured
against these same twenty rows before and after.

**One caveat on the measurement itself.** Nine of twenty rows are clamp-bound,
so `amount_safe_to_pay` exact-match count is a poor headline: all three current
exact matches are rows where the prediction hit `requested_amount` and the cap
happened to be correct. Accuracy on the trough itself is currently **0 of 20**.
Median relative error on the eleven invertible rows is the honest metric and the
one to track.
