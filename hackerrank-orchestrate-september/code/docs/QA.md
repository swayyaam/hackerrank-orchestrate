# QA — the questions, and how I answer them

Spoken answers, 60–90 seconds each. Every one is anchored to a file or a number.
Regenerate any figure with the command in `NUMBERS.md`.

---

# Architecture and ownership

### Why a deterministic solver with a narrow perception layer, and what did you reject?

Because the problem is specified, not discovered. The spec gives me a six-key
lexicographic ranking, exact structural rules for partial payment and
installments, and a hard invariant — the balance never goes below the minimum. A
model approximates those. Code satisfies them. So the model does the two things
code genuinely cannot: read an amount off a receipt, and turn a free-text message
into a typed change to an event that already exists. Everything else is
arithmetic.

I rejected four alternatives explicitly, and they are written down in
`ARCHITECTURE.md`. End-to-end LLM decisioning — unbounded cost per request and no
way to guarantee the ranking. A general ReAct agent — no bounded step count means
no cost ceiling and no reproducibility, and there is nothing to explore because
the decision surface is fully specified. RAG with embedding retrieval — the join
keys are explicit and the reconnaissance found zero orphans in every direction,
so embeddings would inject recall error into a problem that has none. And letting
the explanation writer see raw source data, which is how citations drift away
from decisions.

The payoff is concrete: the whole solver runs in about two seconds with no
network, and the test suite is 797 tests that cost nothing to run.

### Why does the model never compute a number in the output?

Because I cannot verify a number a model produced, and I can verify every number
code produced. There is one exception and it is bounded: the model reads an
amount off a document. That number is then validated by code — the label priority
list picks which figure, arithmetic reconciles it against the line items or the
amount-in-words block, and if it does not close we escalate to a second
independent read.

The structural guarantee is in `bow/engine/forecast.py`. An evidence entry can
only become a signed cash flow through `_amendment_flows`, which requires
`effect is AMEND` **and** a direction in the provenance. Vision writes
`effect=extract` and a provenance dict with no direction key. So a document can
supply a magnitude and can never assert which way the money moved. Direction
comes from the event row's typed field, full stop.

I found a real hole in that during review: `parse_amount` kept the minus sign, so
a receipt printing `-15,339.00` would have flipped an expense into income,
because `CashFlow.signed` negates a debit. That is L10. Fixed, and it changed no
row because all sixteen amounts were already positive.

### Walk me through your module boundaries and what enforces them.

Six layers. `contracts/` is pydantic models and imports nothing internal.
`engine/` and `format/` import contracts only and are pure — no I/O, no clock
reads, no randomness, no network. `explain/` reaches contracts, providers and
format. `providers/` is the only package allowed to make a network call and owns
the rate limiter, the job poller, the retries and the usage meter. `perception/`
and `data/` sit above.

None of that is convention. `tests/test_layering.py` walks the AST of every
module and fails the build on a forbidden import, on a clock read, on
`random.*`, on `open()`. `test_no_sdk_leak.py` fails on a vendor SDK imported
outside `providers/`, or on a second vendor appearing at all.

The reason the line matters: `engine/` consuming a frozen ledger and nothing else
is exactly what makes the solver testable with zero API calls. The moment the
engine can reach the network, my test suite becomes a billing event and stops
being run.

### What does the model actually decide?

Two things, and neither is a decision about money.

First, which figure on a document is the total — and even that is shared. The
model transcribes; the label-priority list in `config/thresholds.yaml` chooses.
For `image_13` it returned the figure at the label `Total paid`, and code decided
that `total paid` outranks `subtotal` and is not on the denylist that excludes
`amount paid` and `cash paid`.

Second, what a message asserts, as a typed delta: an effect from a closed set, a
target event id, a field from a three-item allowlist — `amount`, `event_date`,
`status` — and a value. That is it. It cannot touch `minimum_balance_to_keep`,
`requested_amount`, or `payment_methods_user_will_consider`. It cannot set a
direction. It cannot create an event.

On the evaluation set, 198 messages produced 12 surviving deltas, and all 12
named a real event row. 164 were dropped as asserting a fact no event row
supports, and one for naming a field outside the allowlist.

### Where does your evidence ledger live and what does it stop from happening?

`bow/contracts/evidence.py`. One `EvidenceLedger` per request, built during
perception and frozen before the engine ever sees it. Every entry carries a
source type, a source id, the raw value, the normalized value at its exact type,
an effect from a closed enum, and a provenance dict.

What it stops is specific, not decorative. The engine takes the ledger and
nothing else, so there is no path from the raw CSV or from message text into a
decision. `_amendment_flows` is the single place an entry becomes money, and it
demands `effect is AMEND` plus a cash date plus a direction. A vision entry has
none of those, so it cannot become a flow. A message delta that names no existing
event never becomes an entry at all — that is the 164 drops.

The normalized value is strictly typed on purpose. Pydantic's lax union was
silently narrowing a `datetime` to a `date`, which would have moved a cash date
by up to a day. Per-member `Strict()` fixed it.

---

# Depth

### How is `amount_safe_to_pay` computed, and why is a closed form valid?

`clamp(trough − minimum_balance, 0, requested_amount)`. One line in
`bow/engine/capacity.py`.

It is valid because paying an amount today shifts every subsequent day of the
balance curve down by exactly that amount. The curve after today is a running sum
of flows that do not depend on what I pay today — recurring commitments, projected
income, variable spend. So the minimum of the shifted curve is the minimum of the
original curve minus the payment. Safety means that minimum stays at or above the
floor, which rearranges directly to `payment ≤ trough − minimum`. The clamp at
zero is because you cannot pay a negative amount, and at `requested_amount`
because the spec caps it there.

I did not want to take that on reasoning alone, so `amount_safe_by_search`
re-simulates the entire curve under a binary search and is proven equivalent over
500 generated cases in `tests/test_capacity.py`. On `request_84` the closed form
gives 34,997.7188 and the search gives 34,997.71 — they agree to the search's
one-cent resolution, and the search lands low by construction because it returns
the largest value it proved safe.

### Why does paying today shift the whole curve uniformly?

Because nothing downstream depends on the payment. The balance on day *d* is the
opening balance plus the signed sum of every flow between today and *d*. Subtract
*X* from the opening balance and every one of those partial sums drops by exactly
*X* — the flows themselves are unchanged. There is no interest, no fee that scales
with the balance, no overdraft behaviour in this problem.

That is the assumption that makes the closed form legitimate, and it is worth
stating because it is the thing that would break first in a real bank. Add an
overdraft fee that triggers below zero, or interest on a balance, and the shift
stops being uniform and the closed form stops being valid. Then you need the
search, which is why I kept it rather than deleting it once it agreed.

### Explain your six-key ranking and which key most often decides.

The spec gives six keys in order: complete by the deadline, require no spending
changes, minimise total paid, start earlier, use fewer payments, then lowest
`payment_option_id`. They live in one tuple in `sort_key` in
`bow/engine/ranker.py`, negated so smaller is better throughout, and `best_plan`
is `min` over that tuple. Scattering the comparison across call sites is how a
ranking quietly stops matching its own specification.

Which one decides: on the evaluation set, more than one eligible supplied option
survived on exactly **3 requests**. Key 1 decided one of them, key 3 decided the
other two. On `request_56` there are three safe plans; the third misses the
deadline so key 1 drops it, and between the remaining two key 3 picks partial
payment at 881.56 over installments at 916.84, because the installment option
carries a 35.28 financing fee.

Keys 4, 5 and 6 never bound on this data — no two options within a request share
a total, a first date and a payment count. I know that because I checked, and the
check is what exposed a real bug in key 6: it was ordering ids as strings, so
`payment_option_100` sorted before `payment_option_99`. That is L7. Latent, but
real.

### How do you know your recurrence inference works?

I built a holdout that removes the answer. 47 of the 275 users have an explicit
scheduled income row. I delete it and make the inference rediscover it from the
settled history alone.

Result: it produces some predicted inflow for 45 of 47, and hits the **exact
date** on 44 of 47 — 93.6%. On those hits the amount is exactly right 43 times
out of 44, and the median relative error on the amount is zero.

One caveat I carry rather than bury: that holdout is partially burned. `Q22` was
designed by reading its misses, so the post-`Q22` number is not an untouched
holdout figure. It is recorded in the decision register under
`Q22_series_continuation`, and I never present the post-fix number as the untuned
one. `Q22` is also off in the shipped config, for reasons in the next answers.

### How did you handle the fact that only 47 of 275 users have a scheduled salary?

That fact is exactly why the holdout above is the right test — it tells me how
the other 228 users are being handled, because for them inference *is* the only
source of future income.

Mechanically: a scheduled row is a `given_commitment` and lands in the forecast
on its own date. Beyond it, `Q21_income_beyond_scheduled_row` continues the
inferred cadence, because over a 90-day horizon a monthly salary occurs about
three times, and treating the scheduled row as the only future income would
withhold roughly two months of income from every one of those users and make them
look far poorer than they are.

For users with no scheduled row at all, the inferred series carries the whole
thing. On `request_84` the salary is a monthly-anchored series, five occurrences,
anchor day 15, projecting 107,995.68 — and you can see it land on 2026-04-15 in
the walkthrough, lifting the balance from 126,297 to 233,328. That inflow is
entirely inferred. There is no scheduled row behind it.

### Explain the eight lifecycle archetypes and why you implemented six rules.

The reconnaissance found 58 rows carrying a `linked_event_id`, and I grouped them
by the shape of the parent-to-child transition. Refund settled, refund pending,
cancelled-then-settled, failed-then-rescheduled, investment purchase to
unrealized valuation, investment purchase to sale, expense settled to expense
pending — seven distinct shapes as I measure it today, and I had counted eight
earlier when I was splitting one of them more finely.

I did not implement a table of them, and that is the point. Every one of those
shapes is decided correctly by six rules applied to each row **on its own**:
cancelled or failed is dropped, unrealized or non-cash is dropped, a pending
credit is dropped, a pending debit is reserved, a scheduled row is a commitment,
a settled row counts. The link tells you the rows are related; it never tells you
whether a row counts as cash. AGENTS.md says exactly that.

I verified the derivation before building anything on it: the six rules reproduce
every one of the 58 chains with zero mismatches, across all 25,342 events. That
check is `tests/test_archetypes.py`. Had it failed even once, I would have needed
the table.

### How do you handle a blank amount, and why is zero never acceptable?

Sixteen events have a blank amount. Every one is linked by `images.csv` to a
document, and the pipeline order makes vision run *before* FX, because a blank
foreign-currency amount has nothing to convert.

The read is two independent jobs. The first transcribes; code selects the figure
by label priority and then tries to reconcile it against the line-item sum or the
amount-in-words block. If the arithmetic does not close, we try the substitution
rule — if the line items sum to a figure the document also prints, that sum wins —
and only then do we pay for a second, schema-constrained read. If nothing
resolves, the amount stays blank and the row is flagged unresolved.

Zero is never acceptable because zero is a *number*. It would flow into the
forecast as a real event of no value and silently understate spending. Blank means
"we do not know", and the system has to say so rather than guess. On the shipped
run `requests_with_blank_amount_evidence` is 0 and `vision_unresolved` is 0 — all
sixteen resolved, and the gold set is 16 out of 16.

---

# Judgement and tradeoffs

### Your `amount_safe_to_pay` error is high. Why, and what did you do about it?

Two numbers, and I want to be careful which one I quote. The primary metric is
relative error on the **implied trough** — the safe amount plus the minimum
balance — because the trough is what the forecast actually predicts. That is
**10.0%**. The secondary figure, error on the raw safe amount, is **44.8%**. Same
error, smaller denominator. I report both with their names attached and I do not
quote the flattering one on its own.

The cause is diagnosed in `CAPACITY_FORENSICS.md`: the forecast is too optimistic
because variable spending is under-forecast. The trough lands earlier and
shallower than the published answers imply.

What I did: `Q26` now projects irregular series at a trailing-window rate, and
the eight-combination ablation shows that is worth 4.7 points on the primary
metric — 14.7% down to 10.0%. What I did *not* do is add a coefficient to close
the rest of the gap, and I will defend that in the next answer.

The residual is still in the unsafe direction, which is the one that matters, and
it is L4 in `LIMITATIONS.md` stated in exactly those terms.

### Why did you refuse to add a damping coefficient?

Because a coefficient chosen to make twenty rows agree is a number with no
meaning on two hundred and fifty rows I have not seen.

The real problem the coefficient was meant to solve was genuine: an unbounded
trailing rate can extrapolate a single burst of spending into a permanent daily
rate. `request_08` was the example — nine qualifying series spending 13.58 a day
against a 1,536.57 balance.

I fixed the actual defect instead. The rule had no lower bound on evidence, so I
required a configured **minimum number of occurrences inside the trailing
window** before a rate is computed at all. That is a statement about when the
evidence is sufficient, and it generalises. A damping factor is a statement about
how much I want the answer to move, and it does not.

I also kept it when the headline got slightly worse, because an unbounded rule
hurts more on 250 unseen rows than it does on 20 seen ones. The counter
`trailing_window_with_too_few_occurrences` fires 744 times, which is the guard
doing its job.

### Why did you refuse to loosen the staleness threshold?

Because staleness is the only thing standing between the forecast and resurrected
commitments. Last occurrences in this data range from 1 day to 178 days before
the request. If I project every detected series regardless of age, I bring back
subscriptions the user cancelled months ago and charge them for it every month of
the horizon.

Loosening it would have improved a couple of sample rows. That is precisely the
shape of a change I distrust: it makes the visible number better by weakening the
rule that protects the invisible rows.

`request_84` shows it working. *Family dinner* has a clean 21-day period and two
occurrences, but it was last seen 71 days before the request — three and a half
periods. It is refused with the reason `stale`, and that is correct: something
that happens every three weeks and has not happened in ten is over.

What I *did* accept was a related and different point. `Q28_project_stale_series`
asks whether a stale series should still contribute *variable* spending through a
different door. I measured it, it changed nothing, and it stayed off — because
re-admitting stale series through a side entrance would undo the decision without
arguing against it.

### Why is `Q22` off when it reduced zero-income users from 50 to 30?

Because that number measured a change in behaviour, not an improvement in
accuracy, and I withdrew it as a justification once I noticed the difference.

`Q22` continues an income series when the old one goes stale and a new
single-occurrence income appears for the same user. The 50-to-30 figure says it
projected income for twenty more users. It says nothing about whether those
projections were right.

Ground truth then settled it. The published answer for a zero-income gig worker
is `not_affordable` with an empty earliest date — which is exactly what projecting
no income produces. So a high zero-income count is not evidence of a defect at
all.

Measured with it off: `request_05` improves 61%, its trough moves from day 7 to
day 88 which is the shape every other row has, earliest-date accuracy goes from
7/20 to 8/20, and a real defect disappears — with `Q22` on we were projecting
44,220 of income for an employee whose employment had been terminated. That is L1.

The flag and the code path are still there, so the alternative is one config line
away. Nothing measured supports turning it on.

### You have 28 decision flags. Isn't that just deferring the hard calls?

No — every one of them has a chosen default that ships. Nothing is deferred. What
the register does is make the choice *visible and attributable* instead of buried
in a branch someone has to find.

Each entry carries the question, the default, the alternative, whether the sample
set can tell them apart, the rationale, where it is read, and — after I
cross-checked against the operating contract — the line of AGENTS.md that
confirms it. Eleven of the 28 are confirmed by contract. Six are constrained but
not resolved, meaning the principle is stated and the number is still mine.
Eleven the contract is silent on, and those are my real scoring risk.

The register is also load-bearing in a way that matters: 11 flags are marked
`switchable` and are read at runtime, and `tests/test_decision_register.py` fails
the build if a flag marked switchable is not actually consulted, or if a
`read_at` path does not exist. That test exists because the register *had* drifted
— one entry pointed at a module that was never created, and `Q7` described a
permissive rule the code had never implemented.

### Why one provider?

Cost control and honesty about what I could verify in the time available.

Every capability claim in `PROVIDER_CAPABILITIES.md` is measured against a live
endpoint — the token ceiling, which models accept image input, the fact that
Document AI is asynchronous with a create-poll-results cycle, that
`partially_completed` is a flagged partial read and never a success, that
`reasoning_effort` accepts only low, medium and high. Doing that properly for two
vendors would have doubled the probe work and halved the depth.

There is a second reason that is structural. One vendor means one credential,
and one credential means the security boundary is simple enough to assert as a
test: `.env` lives outside `code/`, so zipping `code/` cannot capture it, and
`test_no_sdk_leak.py` fails the build if a second vendor SDK appears anywhere.

The cost of that choice is real and I would name it: I have no fallback if the
provider degrades, and the message-extraction variability I measured — 3, 4 and
13 failures across three identical runs — is a single point of failure I cannot
route around today.

---

# Failure and honesty

### What is most likely wrong in your submission?

Variable spending is under-forecast, and that is the unsafe direction. It
overstates what is safe to pay. L4.

The counter I would point at is `variable_spend_dominates_opening_balance`, which
fires on **56 of 250 requests** — 22%. That is the rule extrapolating a burst
into a permanent rate on nearly a quarter of the set. The minimum-occurrences
guard bounds it but does not eliminate it.

Second most likely: `earliest_date_for_full_payment` at 12 of 20 exact is my
weakest column, and the confusion shows two rows where the truth has a date and I
emit empty — meaning I think full payment never becomes safe inside the horizon
and the published answer says it does. That is the same optimism failure viewed
from the other end.

Third: `Q8` and `Q24` are free choices the sample set cannot discriminate. If
either is wrong, it shifts every opening balance or every safety window, and I
would not know.

### What breaks first on a different dataset?

The label-priority list for documents. It is a general ordered list of the words
receipts use, with no vendor or layout in it, and the lint suite fails the build
if an image id or a vendor name appears in code or prompts. But it is still a
list of English phrases. A document that says "Amount Outstanding" gets nothing,
falls through to the line-item sum, fails to self-reconcile, escalates, and costs
me a second read to maybe recover.

Second: the recurrence bands. A monthly band of 26 to 32 days with a
day-of-month tolerance of 2 is a rule, not an observation, but it is calibrated
for monthly billing. Weekly or fortnightly cycles would land in `fixed_interval`
and still project; a lunar or 4-4-5 retail cycle would not.

Third, and the one I would actually bet on: the assumption that every
blank-amount event has exactly one image. It holds here — sixteen for sixteen —
and there are warning counters for both zero images and many images, so it fails
loudly rather than silently. But the many-image path has never executed.

### Where did you overfit?

The honest answer is the label-priority list. Thirteen phrases ordered by a rule
I can state — a figure naming a remaining obligation beats one naming a computed
total, which beats a subtotal, which beats summing the lines yourself — but the
*phrases* came from reading sixteen documents. If I had seen sixteen different
documents, the list would have different entries.

I constrained it as hard as I could: no vendor, no layout, no image id, enforced
by 18 lint cases including a test that no module can even read the gold set. And
the gold set is isolated — `tests/test_sample_set_isolation.py` re-runs the whole
sample set with every published answer stripped from the input and asserts the
predictions come out byte-identical.

The second place is the decision defaults where `sample_discriminates` is true. I
used the sample set to pick between two readings of an ambiguous rule. I think
that is what a worked example is for, and AGENTS.md explicitly permits it — but I
record it per entry rather than presenting those choices as derived from first
principles.

### What did you get wrong and have to reverse?

Four things, and they are all in the record rather than quietly fixed.

`Q22`. I argued for it on a number that measured behaviour rather than accuracy,
and I withdrew that justification in writing and turned the flag off.

`reasoning_effort`. I instructed a config change across every role on the
strength of a single unreplicated sample. Re-measurement showed the opposite —
omitting it made one role *more* verbose, 11,886 tokens against 2,487. I took
ownership of that rather than leaving it as the assistant's error.

A circular hypothesis in the capacity forensics. I defined the implied trough via
a formula and then confirmed the formula, which proves nothing. Marked untested
and the verdict withdrawn.

And `image_14`. I first accepted "the transcription is unstable, record it and
move on". The stability probe supported that — 15 of 16 images are perfectly
stable — but the instability was a symptom, not the cause. Its own detail string
showed it had read 4543 out of the payload and returned 670. It was a pairing
bug: rupees and paise in separate columns, an undescribed row dropped from the
line-item sum, and the total merged with a signature scrawl in a `colspan`
footer. Fixing it generally took the gold set from 15/16 to 16/16.

### What would you build with another day?

Three things, in order.

First, the realised-outcome replay in `MONITORING.md` — take each recommendation,
wait out the horizon, and count how many plans actually breached the minimum
balance. That is the only metric that measures the promise the system makes.
Everything I report today is a proxy for it.

Second, the variable-spend fix properly. Not a coefficient — a per-category
model, because groceries and transport behave differently from a one-off
purchase, and the current single trailing rate treats them identically.

Third, the bounded resolver I scoped out. There are three places — L1, L2, L3 —
where the typed fields genuinely underdetermine the answer and I settle them with
a configured default that is sometimes wrong. A bounded choice over a closed set
of actions, with the evidence in front of it, is the right tool there, and it is
the one piece of the original architecture I did not get to.

### Which parts were AI-generated and which were yours?

Most of the lines were generated. The constraints were mine, and they are
enforced by tests rather than by my say-so.

`AUTHORSHIP.md` has it module by module. The short version: I set the
architecture, the layering rules, the generalization mandate, the decision
register format, and the rule that no configured value ships unmeasured. I
designed the experiments — the eight-combination ablation with predictions
written down first, the recurrence holdout, the `reasoning_effort` A/B, the
vision stability probe, the best-of-N pre-commitment.

And I overruled the assistant on six calls that are listed with who turned out to
be right. I was right on five — `image_14`, the truncation run, `Q22`, `Q25`, and
leaving `request_08` alone. It was right on one: it flagged at scaffold time that
`output.csv` belonged at the repository root, I overruled it, and I had to
reverse that.

---

# Adversarial

### Your sample accuracy is 76%. Why should I trust the other 250 rows?

You should not trust it as a point estimate, and I do not present it as one.
Twenty-five rows gives you a confidence interval you could drive a bus through.

What I would offer instead is evidence about generalisation rather than about
score. The strongest piece: `multiple_eligible_supplied_options` fired **zero
times on both reconnaissance sets and three times on the evaluation set**. I had
an observation that no request has more than one eligible option. I coded the
full six-key ranking anyway and asserted the observation as a counter rather than
relying on it. Coding to what I saw would have made three rows silently wrong
with no signal at all.

The same pattern runs through the build. The six cash rules were verified to
reproduce all 58 lifecycle chains before anything was built on them. The
recurrence holdout hides the answer and scores 44 of 47 on exact date. Six
structural lint suites fail the build on dataset ids, on branching on free text,
on dataset-derived literals.

I would rather be judged on those than on 19 out of 25.

### You used the sample set for tuning. Isn't that leakage?

It depends entirely on what "used" means, and I can be precise about it.

I used it for three things. Format — the amount rendering, the plan syntax, the
change syntax. As a discriminating test between two readings of an ambiguous
spec rule, which is what a worked example is for and what AGENTS.md explicitly
permits. And as the measurement set for every accuracy figure I quote, which is
why I always say "25 published examples" and never "expected score".

What I did not do: no threshold was fitted to minimise error on those rows, no
request id appears anywhere in the code, no published amount or date or status
appears in `code/`.

And I do not ask you to take that on faith. `tests/test_sample_set_isolation.py`
runs the entire sample set twice — once normally, once with every published
answer stripped out of the input — and asserts the predictions are byte-identical.
If anything were reading a label, that test fails.

The one place I would concede the line is thin is `Q23`, which I resolved by
looking at what the published answer was for an irregular-income user. I record
that in the entry's rationale rather than dressing it up as first principles.

### Your explanation is a template. Isn't that avoiding the hard part?

It is avoiding a specific failure, deliberately, and I would make the same call
again.

The explanation is rendered from the frozen `Decision` by
`bow/explain/template.py`. It can restate the 95,400 and the 91,300 because both
are already in the decision. It has no path to a number that is not — which means
it structurally cannot produce the failure I was most worried about, an
explanation citing a figure the ledger does not contain.

I had a model-written path designed, with a grounding verifier behind it. I did
not build it, and I deleted the configured role rather than leave it sitting in
`models.yaml` looking live. That is in `ARCHITECTURE.md` as a scope decision with
the tradeoff named: what I gave up is prose that reads better and can explain a
borderline case in the user's own terms.

What I would say in its defence: the template is grounded by construction, and
"grounded but plain" is a better failure mode for financial advice than "fluent
but occasionally invented".

### You deleted the agent package. So where is the agentic system?

The agency is in the perception layer and in the escalation logic, and it is
bounded on purpose.

Reading a document is a genuine multi-step loop: transcribe, select a figure by
priority, reconcile it arithmetically, and on failure either substitute the
line-item sum — if the document itself prints that sum — or pay for a second,
schema-constrained read from a different job. That loop has a cost ceiling of two
reads and every branch is code, which is why it is auditable. On the evaluation
set it fired the second read 12 times out of 16 and the substitution once.

What I deleted was `bow/agent/`, which contained an empty `__init__.py`. It was
never built. `ARCHITECTURE.md` claimed "exactly one bounded choice point" and
that claim was false, so I removed the package, removed the two configured roles
behind it, and rewrote the paragraph to say it was scoped out under time pressure
with the tradeoff named.

I would rather be asked this question than have you find an empty package and a
configured role that nothing calls.

### One of your vision reads is wrong and you shipped it. Defend that.

That was true for two of the three runs, and it is not true of the submission.
Let me give you the whole sequence, because the process is the defence.

`image_14` read 670 where the document says 4,543. I cleared its cache and
re-read it — once, under a rule fixed in advance: accept whatever comes back. It
returned 670 again. I shipped 670 and wrote it down, because re-reading until the
answer is welcome is choosing a result rather than measuring one.

Then I was pushed on the detail string, which said the extractor had matched a
label containing "TOTAL 4543" and returned 670 anyway. That is not a
transcription failure — that is a pairing failure, and it turned out to be three
general bugs: rupees and paise in separate columns, an undescribed row silently
dropped from the line-item sum, and the total merged with a signature scrawl in a
`colspan` footer.

I fixed those generally and verified the fix **offline** against the captured
payload — not by re-reading until it passed. The gold set went from 15 of 16 to
16 of 16, miss list empty.

And I will volunteer the part that is least flattering: for two runs my gold
score said 16/16 while the pipeline shipped 670, because the scorer made its own
reads instead of scoring the cache the pipeline actually uses. Two measurements
of two different things reported as one number. That is L9, and fixing the scorer
is what made the defect visible at all.
