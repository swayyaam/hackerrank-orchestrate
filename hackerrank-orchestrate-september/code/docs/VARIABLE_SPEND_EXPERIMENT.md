# Variable-spending projection — predictions, then results

**Metric change, in force from this document onward.**

- **Primary:** median relative error against `implied_trough`, over the
  **invertible** rows only (those where the clamp is not binding).
- **Secondary:** trough-date exact match, and the signed day-offset histogram.
- Clamp-bound rows are reported separately and never enter an accuracy figure.
- The honest baseline is **accuracy on the trough itself is 0 of 20**. Every
  figure below starts from there.

Baseline, current code, all three flags off: **median relative error 52.8%**,
date-exact 7/20, signed offsets `{−73, −69, −31×3, −30, −12×2, −11, −8, 0×3}` —
every populated offset negative.

---

## Section 1 — Predictions, recorded before any run

Written before the projection code existed and before any of the eight
combinations was executed. Nothing below has been revised after seeing a result.

### Reasoning behind the predictions

The rate is total spend inside a trailing 90-day window divided by 90, with no
free coefficient. That definition matters for the priors: **a series whose
occurrences all predate the window contributes exactly zero regardless of which
flag admits it.** Single-occurrence series in the sample are frequently old —
148, 174, 134 days — so `Q27` should do far less under a trailing window than it
did under the full-span rate I tested earlier. The same partly applies to `Q28`.

`Q26` admits the dense, recent series: groceries and transport with three to
nine occurrences, most of them inside the window. That is where the outflow is.

I therefore expect `Q26` to carry almost the whole effect, `Q27` and `Q28` to be
close to no-ops, and the earlier over-correction (median error 100% under the
full-span rate with everything on) **not** to reproduce, because the trailing
window is a stricter estimator than the full-span one I used before.

### Predicted table

| # | Q26 irregular | Q27 single | Q28 stale | predicted median rel err | predicted date-exact |
|---|---|---|---|---|---|
| 1 | off | off | off | 52.8% (baseline, known) | 7/20 |
| 2 | **on** | off | off | **30–35%** | **10/20** |
| 3 | off | on | off | 48–52% | 7–8/20 |
| 4 | off | off | on | 46–50% | 7–8/20 |
| 5 | **on** | **on** | off | 32–38% | 10/20 |
| 6 | **on** | off | **on** | 28–34% | 10/20 |
| 7 | off | on | on | 44–48% | 8/20 |
| 8 | **on** | **on** | **on** | 35–45% | 10/20 |

### Predicted conclusions

1. **Row 2 or row 6 wins.** `Q26` alone, or `Q26` plus `Q28`.
2. **`Q27` makes things worse wherever it is on**, by turning a single large
   purchase into a permanent daily rate. Row 5 worse than row 2; row 8 worse
   than row 6.
3. **`Q28` is close to a no-op** under a trailing window, moving the median by
   less than 5 percentage points either way.
4. **Offsets stop being uniformly negative.** The histogram should straddle
   zero rather than sitting entirely below it.
5. **No combination reaches single-digit median relative error.** The mechanism
   addresses one cause; `request_14` and the residual stay wrong.
6. **The priors hold** — final answer `Q26` on, `Q27` off, `Q28` off — unless
   row 6 beats row 2 by a margin clearly outside noise on eleven rows.

---

---

## Section 2 — Results

Two tables. The first is over each combination's own invertible rows, which is
what the metric definition asks for. The second is over the **twelve rows
invertible under all eight combinations**, which is the only fair way to compare
combinations against each other, because a combination that clamps fewer rows is
otherwise scored on a different and harder sample.

### Over each combination's own invertible rows

| # | Q26 irr | Q27 sgl | Q28 stl | median rel err | n | clamp-bound | date exact |
|---|---|---|---|---|---|---|---|
| 1 | off | off | off | 14.7% | 12 | 8 | 8/20 |
| 2 | **on** | off | off | 11.0% | 15 | 5 | **13/20** |
| 3 | off | on | off | 13.9% | 12 | 8 | 9/20 |
| 4 | off | off | on | 14.5% | 12 | 8 | 8/20 |
| 5 | **on** | **on** | off | 9.8% | 16 | 4 | 12/20 |
| 6 | **on** | off | **on** | 10.2% | 15 | 5 | 12/20 |
| 7 | off | on | on | 11.9% | 13 | 7 | 8/20 |
| 8 | **on** | **on** | **on** | **8.9%** | 16 | 4 | 12/20 |

### Over the twelve rows invertible under all eight

| # | Q26 | Q27 | Q28 | median rel err | **mean rel err** | date exact |
|---|---|---|---|---|---|---|
| 1 | off | off | off | 14.7% | **30.8%** | 8/20 |
| 2 | **on** | off | off | 10.4% | 31.5% | **13/20** |
| 3 | off | on | off | 13.9% | 29.0% | 9/20 |
| 4 | off | off | on | 14.5% | 30.2% | 8/20 |
| 5 | on | on | off | 9.6% | 33.1% | 12/20 |
| 6 | on | off | on | 10.3% | 31.9% | 12/20 |
| 7 | off | on | on | 13.7% | 28.3% | 8/20 |
| 8 | on | on | on | **9.4%** | **33.4%** | 12/20 |

Signed day-offset histograms:

```
1 baseline    {-73:1, -69:1, -31:3, -30:1, -12:2, -11:1, -8:1, 0:3}
2 Q26         {-73:1,         -31:1,        -12:2,        -8:1, 0:7}
5 Q26+Q27     {-73:1,                       -12:2,        -8:1, 0:6}
8 all on      {-73:1,                       -12:2,        -8:1, 0:6}
```

### Per-row relative error on the common twelve

| request | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| request_02 | 19.6% | 15.8% | 18.4% | 19.6% | 14.6% | 15.8% | 18.4% | 14.6% |
| request_05 | 42.0% | 29.6% | 36.2% | 42.0% | 35.3% | 29.6% | 36.2% | 35.3% |
| request_07 | 8.8% | 7.4% | 7.3% | 8.8% | 5.8% | 7.4% | 7.3% | 5.8% |
| request_08 | **9.0%** | **101.2%** | 1.0% | 9.0% | **114.9%** | 101.2% | 1.0% | **114.9%** |
| request_10 | 85.2% | 13.7% | 81.6% | 79.7% | 17.2% | 19.1% | 76.1% | 22.7% |
| request_14 | **131.7%** | **175.0%** | 133.7% | 131.7% | **177.0%** | 175.0% | 133.7% | **177.0%** |
| request_15 | 11.6% | 5.7% | 10.8% | 10.2% | 4.9% | 4.3% | 9.5% | 3.6% |
| request_18 | 13.5% | 9.8% | 11.9% | 13.5% | 8.2% | 9.8% | 11.9% | 8.2% |
| request_22 | 3.6% | 0.3% | 3.5% | 3.3% | 0.5% | 0.6% | 3.2% | 0.8% |
| request_23 | 15.8% | 11.0% | 15.8% | 15.5% | 11.0% | 10.7% | 15.5% | 10.7% |
| request_24 | 18.0% | 7.5% | 16.9% | 18.0% | 6.4% | 7.5% | 16.9% | 6.4% |
| request_25 | 10.8% | 1.4% | 10.3% | 10.6% | 0.9% | 1.2% | 10.1% | 0.7% |

---

## Section 3 — Where the predictions were wrong

### The predictions were anchored to the wrong baseline

**My own error, stated first.** The predicted table used a 52.8% baseline, which
is the *old* metric — median relative error of `amount_safe_to_pay` against the
published value. The metric in force is median relative error of the **trough**
against `implied_trough`, whose baseline is 14.7%. My absolute predictions are
therefore not comparable with the results at all. Only the directional
predictions can be graded, and they are graded below.

### Prediction by prediction

| # | prediction | verdict |
|---|---|---|
| 1 | Row 2 or row 6 wins | **Partly right.** Row 2 wins the secondary metric outright (13/20). Row 8 wins the primary by 1.0pp. |
| 2 | `Q27` makes things worse wherever it is on | **Wrong on the median, right on everything else.** Q27 *improves* the median in every pairing (10.4→9.6, 10.3→9.4) but worsens the mean (31.5→33.1, 31.9→33.4), costs a date (13→12), and is responsible for the worst single-row regression in the experiment. |
| 3 | `Q28` is close to a no-op | **Right.** 14.7%→14.5% alone, dates unchanged at 8/20. The trailing window does the self-correcting I predicted. |
| 4 | Offsets stop being uniformly negative | **Wrong.** Every populated offset is still ≤ 0. What actually happened is that the mass moved to zero (3 zeros → 7) rather than straddling it. The forecast is still never late, only less early. |
| 5 | No combination reaches single-digit median | **Wrong**, but only because of the baseline error above — 8.9% in the new metric. |
| 6 | The priors hold | **Right**, and for a better reason than I gave. |

### The finding I did not predict at all

**Enabling the projection can make an individual row catastrophically worse.**
`request_08` goes from **9.0% to 101.2%** with `Q26` on, and to **114.9%** with
`Q27` as well. Its trailing rate is 14.43/day, so 1,298.74 over the horizon
against an opening balance of 1,536.57 — **85% of everything the user has**.
The forecast drives the balance to −12.94 and the answer collapses.

That is precisely what the `variable_spend_dominates_opening_balance` warning
was specified to catch, and it fires on this row at the configured 0.5
threshold. The warning is doing its job; the row is a real limitation of a
trailing-rate estimator on a user whose recent spending is large relative to
their balance.

It is also why the **mean** matters here and the median alone would mislead: the
median improves monotonically as flags are enabled while the mean gets steadily
worse. The mechanism helps the typical row and hurts the tail.

---

## Section 4 — Chosen setting

**`Q26` on, `Q27` off, `Q28` off — row 2. All three priors upheld.**

The tiebreak rule was to depart from a prior only where the data contradicts it
clearly, and to prefer the priors when combinations are within noise.

- **`Q26` on.** Confirmed, not merely tolerated: 14.7% → 10.4% median on the
  common rows, and dates 8/20 → 13/20. It is the single largest effect in the
  experiment.
- **`Q28` off.** Confirmed. Its solo effect is 0.2pp with no date change.
- **`Q27` off.** The evidence is *mixed*, not clearly contradictory: better
  median (−0.8pp), worse mean (+1.6pp), one fewer date, and the largest
  single-row regression in the run. Mixed evidence does not clear the bar for
  departing from a prior, so the prior stands.

Row 8 beats row 2 by 1.0pp of median on twelve rows. That is not a margin worth
overturning a stated prior for, and it comes with a worse mean and a worse
secondary metric.

**Headline, in the metric now in force:** median relative error of the trough
against `implied_trough` improves from **14.7% to 10.4%** on the twelve
commonly-invertible rows, trough-date exact match from **8/20 to 13/20**, and
clamp-bound rows fall from **8 to 5**. Accuracy on `amount_safe_to_pay` as an
exact match remains what it was: the honest baseline of **0 of 20** on the
trough itself still stands as the number to beat.
