# Known limitations

Every entry below is either **open** — it costs accuracy and is not fixed —
or **resolved**, kept as the record of a defect that shipped or nearly did.
Detail for each is in the section of the same name.

| id | statement | status | where it bites |
|---|---|---|---|
| [L1](#l1) | Income continuation is undecidable from the typed fields | **open** | earliest_date and amount on users whose income series ends |
| [L2](#l2) | Q24_horizon_end_basis is unresolvable from the sample set | **open** | a free choice; affects which payment dates are judged safe |
| [L3](#l3) | Q8_balance_as_of_semantics is unresolvable from the sample set | **open** | a free choice; shifts every opening balance |
| [L4](#l4) | Variable spending is forecast, but not conservatively enough | **open** | amount_safe_to_pay over-predicted; the unsafe direction |
| [L5](#l5) | Reproducibility via seed is weaker than first reported | **open** | two identical runs differ in message deltas and one document read |
| [L6](#l6) | Two roles were configured below their observed completion length | resolved | was: empty HTTP 200 responses read as no result |
| [L7](#l7) | Rule 6 ordered option ids as strings, not numbers | resolved | would bite a tie between a 2-digit and a 3-digit option id |
| [L8](#l8) | Spending-change permission was asserted, not enforced | resolved | would propose a change the profile forbids, on data where the two disagree |
| [L9](#l9) | image_14 was a pairing failure: split columns, undescribed row, merged footer | resolved | was: one blank-amount event read as 670 instead of 4,543 |
| [L10](#l10) | A minus glyph on a document could have flipped a cash flow | resolved | would invert one expense into income |
| [L11](#l11) | Some flag combinations make the engine refuse to produce a row | **open** | not reachable in the shipped configuration |

---
## L1 — Income continuation is undecidable from the available fields

Two sample rows fail in opposite directions for one reason.

```
request_05   Payroll credit (monthly, stale)  ->  Final employer payroll (n=1)
             the employment ENDED
             continuing the series invents 3 x 14,740 = 44,220 ZAR of income

request_14   Payroll before leave (monthly, stale) -> Payroll after returning
             from leave (n=1)
             the employment CONTINUED
             not continuing the series misses 3 x 2,717 = 8,151 EUR of income
```

**These two situations are identical in every typed field available.** Both are
a stale income series in a category, followed by a single fresh occurrence in
the same category, same direction, same event type. The amounts are even
plausible in both directions: a final settlement can match a normal salary, and
a return-from-leave payment can differ from one.

The only thing that separates them is the description — `Final employer payroll`
against `Payroll after returning from leave` — and reading the description is
exactly what the generalization mandate forbids, because a rule keyed on those
strings memorises this dataset and fails on the next one.

So the capability is not missing by oversight. **It is undecidable from the
fields the problem supplies.** Any rule built on typed fields alone must choose
which error to make:

| rule | `request_05` | `request_14` |
|---|---|---|
| continue the series (`Q22` on) | invents 44,220 of income | correct, if the gap threshold allowed it |
| do not continue (`Q22` off, current) | correct | misses 8,151 of income |

`Q22` is **off**. Inventing income for someone who has lost their job is the
worse error: it produces a confident "yes, you can afford this" for a user with
no income at all, where the opposite error produces an over-cautious "no".
Between two wrong answers, the cautious one is the one to keep.

Note that `request_14` would not be rescued by turning `Q22` back on in any
case: its handover gap is 91 days against a cutoff of 2.0 × 31 = 62 days.

**What would make it decidable.** A source outside the typed fields — a message
confirming future income is the obvious one, and `message_86` in the dataset
does exactly that for a different user. That belongs to the perception layer,
not to recurrence. Nothing here should be patched to guess at it.

---

## L2 — `Q24_horizon_end_basis` is unresolvable from the sample set

Whether the safety window ends at a point fixed by the request, or slides
forward with the payment date under evaluation, **makes no difference to any of
the twenty resolvable sample requests**. The accuracy grid is byte-identical
across both settings.

The reason is structural: in nineteen of twenty rows the balance trough falls
before every candidate payment date, so shrinking or extending the far end of
the window cannot change which point is lowest.

The default (`fixed_from_request_date`) is therefore a **free choice**, made on
the reading that the spec anchors its ninety-day check to the request rather
than to the payment. Nothing in the data supports or contradicts it. A dataset
with a trough late in the horizon, or a candidate payment date near the horizon
end, would discriminate; this one does not.

---

## L3 — `Q8_balance_as_of_semantics` is unresolvable from the sample set

Whether an event whose cash date falls exactly on `request_date` is already
inside `current_available_balance` or still to be applied **also makes no
difference to any of the twenty rows**. Same byte-identical grid.

The reconnaissance found seventeen users with an event dated on `request_date`,
so the case exists in the wider dataset — but none of those users is in the
resolvable sample set, and in the sample rows the boundary event never touches
the trough.

The default (`inclusive_through_request_date`) is a **free choice**, made on the
reading that a balance which cannot be reconstructed from history must be a
given as of the request date. It is asserted, not measured.

---

## L4 — Essential variable spending is forecast, but not conservatively enough

**Updated.** This entry originally said variable spending was not forecast at
all. `Q26_project_irregular_series` is now on, so it is: a debit series whose
gaps do not cluster contributes at a trailing-window rate, guarded by a minimum
number of occurrences inside that window. `VARIABLE_SPEND_EXPERIMENT.md` records
the eight-combination run that settled it, with the predictions written down
before it was run.

What is still not forecast is a series with one observed occurrence
(`Q27_project_single_occurrence_series`) or one past its staleness cutoff
(`Q28_project_stale_series`). Across the 275 users that is 1,530 single-
occurrence and part of 1,540 fixed-interval debit series, against 2,489
irregular ones that now do contribute — of 7,273 debit series in total. Both
flags were measured off and both priors held; one occurrence is not a pattern,
and re-admitting a stale series through a different door would undo a decision
taken deliberately.

The residual is still in the optimistic direction, and that is the direction
that matters. AGENTS.md §6.3 says *"Forecast essential variable spending
conservatively."* Conservative means not understating spending, and we
understate it: the balance trough still lands earlier and shallower than the
published answers imply, and `amount_safe_to_pay` is over-predicted more often
than under. So this clause of the contract is satisfied in kind and not in
degree, which is the honest way to put it.

See `CAPACITY_FORENSICS.md` for the evidence behind the diagnosis.

---

## L5 — Reproducibility via `seed` is weaker than first reported

An early probe found three completions byte-identical at temperature 0 with a
pinned seed, and that was reported as reproducibility.

A later measurement on a different prompt, with the same seed pinned, produced
completion counts of 882, 1536 and 1536 across three runs. The two values at
1536 are the configured `max_tokens` ceiling, which means those responses were
truncated rather than finished.

So the earlier claim holds only for short prompts that complete well inside
their budget. Where a response approaches its ceiling, output is not stable, and
the instability is a symptom of truncation rather than of sampling. Any
reproducibility claim in the submission must be scoped to that.

**Widened by later measurement.** Two full runs of the identical tree, config
and seed produced different perception results: message deltas surviving the
guard 10 and 12, message extraction failures 1 and 4, and a document read that
returned two different amounts for the same image (see L9). `temperature: 0`,
`top_p: 1` and a pinned `seed` reduce variance; they do not eliminate it on this
provider, and nothing in this submission should be read as claiming a
byte-reproducible run.

---

## L6 — RESOLVED: two roles were configured below their observed completion length

`explain` is configured at `max_tokens: 1536` and was measured hitting exactly
1536 on two of three identical calls. `resolver` at `max_tokens: 3072` with
`reasoning_effort: medium` returned **no visible content at all** on a
representative prompt.

---

## L7 — Rule 6 was ordering option ids as strings, and could not have been caught by this dataset

Ranking rule 6 breaks a remaining tie on the *lowest* `payment_option_id`. The
implementation compared ids as plain strings. The supplied ids are zero-padded
to two digits up to ninety-nine and then run to three, so a string comparison
puts the three-digit id first — the opposite of lowest. One request in the
dataset carries ids that straddle that boundary.

Fixed: `bow/engine/ranker.py` now compares digit runs numerically.

**It changed no output, and could not have.** Rule 6 binds only when two
candidates tie on all five keys above it, and no two supplied options within any
request share a total payable amount, first payment date and payment count — 0
such pairs across the whole dataset. The defect was real and latent, and it is
recorded here because a dataset that happened not to expose a bug is not
evidence the bug was absent.

---

## L8 — Spending-change permission was asserted, not enforced

AGENTS.md §6.2: *"Only non-protected, flexible events in a category the user
permits may be changed."* Until this was cross-checked, `bow/engine/changes.py`
filtered on the event's `flexibility` alone. The profile's protected list and
its willing-to-reduce / willing-to-stop lists were checked only as warning
counts in `bow/data/normalize.py`, both of which read zero on this dataset.

Fixed: permission is now a required argument to `candidate_changes`, with no
default, and a `reducible_or_stoppable` event falls back to whichever verb the
profile actually permits rather than emitting the Q29 preference regardless.

**It changed no output on this dataset** — verified by running the full
deterministic pipeline with the filter enforced and with it bypassed and
comparing a digest of all 250 rows, which matched. That is a property of this
data, not of the old code: on a dataset where a flexible event sits in a
protected category, the old code would have proposed a change the user had
refused.

---

## L9 — RESOLVED: image_14 was a pairing failure, not a transcription failure

**Superseded diagnosis.** This entry previously concluded that `image_14` was an
unstable transcription and left it at that. The instability is real — the
stability probe below stands — but it was not the cause. The extractor's own
detail string said so all along: *selected on label '…Signature TOTAL 4543'*.
It had read 4,543 out of the payload and then returned a different number. Label
found, wrong value taken.

**What the document actually contains.** One diagnostic read (never written to
the pipeline cache) showed three things at once:

```html
<tr><td>2</td><td>Samhan</td><td></td><td></td><td>1500</td><td>00</td></tr>
...
<tr><td></td><td></td><td></td><td></td><td>670</td><td>00</td></tr>
<tfoot><tr><td colspan="4">ices Charged Include all Taxes E &amp; O.E Signature TOTAL 4543 00</td></tr></tfoot>
```

1. Rupees and paise are **separate columns**, so every figure arrives as two cells.
2. The last row carries a figure and **no description at all**.
3. The total sits in a `colspan="4"` footer cell **merged with the signature
   scrawl**, so its label and its value are one string.

The line parser takes the last number on a line, which in that merged footer is
the paise cell `00`, not `4543` — the digitise read therefore selected **0**, and
670 only ever arrived from the escalated second read. Nothing here is specific to
this vendor or this layout: split currency columns, undescribed rows and merged
footer cells are ordinary receipt structure.

**Why reconciliation never closed.** The line-item sum was 3,873: the five
described rows summed, and the sixth undescribed row's 670 was dropped because a
figure was only emitted when its row had already set a label. The arithmetic that
would have caught the bad selection could not close, on every read.

**Two general fixes, and one general mistake made on the way.**

- A numeric cell in a row with **no descriptive cell anywhere** is a line item,
  emitted with an empty label so it reaches the sum but can never be selected as
  the answer. The first attempt emitted *every* numeric cell, which swept in the
  quantity column and made the sum 4,552 instead of 4,543 — caught by replaying
  the captured payload offline, not by a lucky read. A number standing *before*
  its row's description is a quantity; a number in a row that describes nothing
  is all that row has.
- **The substitution rule**, stated in one sentence before it was written and
  before any image was re-scored: *when the figure chosen by label priority does
  not reconcile, and the line-item sum equals a number printed somewhere in the
  document, take that sum as the amount and record the substitution in
  provenance.* The second clause is what stops it inventing a total — a sum the
  document never states is arithmetic we did, not a figure it asserts.

**Verified offline, not by re-reading until it passed.** The rule was replayed
against the captured payload that produced the failure: line-item sum 4,543,
which the document prints, substituted for the figure at the garbled TOTAL
label. Deterministic, no API call. The read that then filled the cache resolved
it the same way from `digitise` alone, with `substituted_from` recorded, and the
second read is no longer needed for that document (12 of 16 now, down from 13).

**Effect.** Gold set **15/16 → 16/16**, miss list empty. The other fifteen are
frozen by their caches and cannot have moved; the rule can only fire where
reconciliation fails, which on this set is `image_06` and `image_14`. `image_06`
is correct and stable but reconciles 0/5, so it is the one document where the new
rule *could* fire and has not been exercised — its cache was deliberately not
cleared.

**That was the architect's decision, and the reasoning is worth keeping.**
`image_06` is already correct and already stable at 1995.00 across five reads.
Clearing its cache to see what the substitution rule does would put a known-good
value back into a lottery for no gain: if the rule agrees, nothing is learned
that reasoning does not already give; if a bad draw lands, a correct row is lost
to satisfy curiosity. The rule can only fire where reconciliation fails, and the
cost of being wrong there is bounded by exactly one document. So it stays as it
is, and the residual risk is named rather than bought off.

**It changed no output row.** `event_9421` is a `settled` expense dated
2025-11-02 against a request dated 2025-11-03, and `Q8_balance_as_of_semantics`
takes `current_available_balance` as already inclusive of everything settled on
or before the request date. Correcting a settled past amount therefore moves no
forecast. `request_101` is byte-identical before and after: `143500`,
`affordable_now`, `full_payment`.

### The stability measurement, which still stands

`scripts/probe_vision_stability.py` read all sixteen images five times each with
a fresh cache key per read (`config/models.yaml` records `seed: null` for both
vision roles because the Document AI endpoints have no seed field). 145 pages,
48 minutes, ₹72.50. Full results in `evaluation/vision_stability.json`:

```text
stable (all five reads identical) : 15/16
unstable                          : 1  -> image_14
image_14 reads                    : 4543, 670.00, 4543.00, 4543, 670   (modal 4543, 3/5)
```

The instability was a symptom: the document's structure varies between reads, and
the pairing bug turned some of those structures into a wrong figure. Note also
that `image_06` reconciles on 0 of 5 reads and is nonetheless perfectly stable —
failing to reconcile is not the same as being unstable.

**Best-of-N was considered and deliberately not adopted.**
`config/thresholds.yaml` carries `reads_per_amount` and
`read_disagreement_rule`, both written **before** the stability probe was run so
the rule could not be chosen after seeing which answer it picks. The condition
recorded there was: adopt only if instability affects more than one image. It
affects one. `reads_per_amount` stays at 1, and
`tests/test_vision.py::test_the_best_of_n_config_matches_what_the_code_does`
fails if anyone raises it while no code implements it.

**The cache entry was cleared and re-read three times across this work**, each
time under a rule fixed in advance. The first two returned 670 and were shipped
as they came, because re-reading until the answer is welcome is choosing a result
rather than measuring one. The third was after the fix and resolved at the
digitise stage by substitution, which is a different thing from a better draw and
is distinguishable in the record: `path: digitise`, `substituted_from` populated,
no second read.

---

## L10 — RESOLVED: a minus glyph on a document could have flipped a cash flow

Found while verifying that document wording cannot influence direction. It
cannot — but a document's *sign* could have.

`parse_amount` deliberately keeps `-` among the characters it does not strip,
because a line item may legitimately be negative: a discount subtracts, and the
line-item sum has to be able to come out lower because of it. The same function
also parsed the **selected total**, so a figure printed as `-15,339.00` would
have become `Decimal("-15339.00")` and been written straight onto the event's
`amount`. `CashFlow.signed` negates a DEBIT, so that expense would have *added*
15,339 to the forecast balance instead of removing it — a two-way error of twice
the amount, with no warning, because nothing downstream checked the assumption
that `amount` is unsigned. `model_copy` does not re-validate, and `Money` carries
no non-negativity constraint, so there was no second line of defence.

Fixed in two places:

- `select_amount` returns `abs(...)` for the label path, with the reason in its
  docstring: a minus on a receipt is the document asserting a direction, and
  direction comes from the event's typed field. The line-item fallback is
  **not** forced positive — a set of lines summing below zero has not produced a
  usable total.
- `resolve_blank_amounts` refuses any negative resolution outright and raises an
  unresolved item, whatever produced it — the fallback sum, or a cache entry
  written by an older build.

**No effect on this dataset.** All sixteen resolved amounts were already
positive, `abs()` is a no-op on every one, and the guard never fires. The gold
set is unchanged at 15/16 with the same single miss, and `output.csv` is
byte-identical (sha256 `66c5c9bc…`). Recorded because a latent sign inversion is
worth naming even when the data never exercised it.

---

## L11 — Some flag combinations make the engine refuse to produce a row

Found by `evaluation/ablations.py`, which re-runs the eight variable-spend
combinations. Two of the eight stop with:

```text
PolicyViolation: request_13: wait requires an earliest date
```

`wait` is eligible only when full payment becomes safe later, which should imply
an earliest date exists. Under `Q26=on, Q28=on` the forecast reaches a state
where the method is chosen and the date is not, and `bow/engine/policy.py`
refuses to emit the row rather than write an inconsistent one. The refusal is
the post-condition working; the inconsistency reaching it is the defect.

**Not reachable in the shipped configuration** (`Q26=on, Q27=off, Q28=off`), and
`output.csv` is unaffected. It is recorded rather than fixed because the pipeline
is frozen for submission and an engine change could move rows for a case that
cannot occur as shipped. The ablation sweep reports the failure per combination
instead of aborting, so the result stays visible.

For the record, what the sweep measures on the shipped path:

```text
Q26   Q27   Q28     status    trough median   invertible   clamped
on    on    off      14/20        10.0%           14          6
on    off   off      14/20        10.0%           14          6     <- shipped
off   *     *        14/20        14.7%           12          8
```

Q26 is worth 4.7 percentage points on the primary metric. Q27 and Q28 change
nothing, which is what their priors predicted.

---
