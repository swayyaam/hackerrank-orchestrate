# Buy or Wait? Personal Affordability Agent

This project answers the question **"Can I afford this?"** for 250 people at once. For each request
(a laptop, a flight, a school fee, a transfer home) it rebuilds the person's cash position from their
transaction history, projects their balance 90 days forward, and decides whether they should **pay in
full now**, **pay part now and the rest later**, **take one of the seller's installment plans**,
**wait**, or **not go ahead**. It also works out the largest amount that's safe to pay today, the
earliest date the full amount becomes safe, any flexible spending they'd need to cut, and a short
explanation built from the same numbers.

I built it for the **HackerRank Orchestrate** hackathon in September 2026, a 24-hour event to design
and ship an AI agent. This is the cleaned-up public version. I'm sharing it so you can see how it's
put together and borrow the parts that are useful to you.

---

## Score breakdown

The scored field was **3,062 participants**. I finished **48th**, with **70.9 out of 100**, which
earned a silver medal. The best total in the field was 80.0.

The score came from four stages. The one I did best on was the Chat Transcript, where I got full
marks:

| Stage | My score | Best in the field | Where I ranked |
|---|---|---|---|
| Chat Transcript Evaluation | **10.0 / 10** | 10.0 | **tied 1st** (19 of 3,062 got full marks) |
| Interview Evaluation | **24.6 / 30** | 27.6 | **85th of 3,062** |
| Technical Review & Final Results (the code) | 21.6 / 30 | 27.9 | 242nd |
| Output Evaluation | 14.7 / 30 | 21.9 | 472nd |
| **Total** | **70.9 / 100** | 80.0 | **48th of 3,062** |

The per-stage scores come from [`leaderboard-september.json`](./leaderboard-september.json), scraped
from the public leaderboard with [`scrape_leaderboard.py`](../scrape_leaderboard.py).

The honest read: **same shape as August, and Output Evaluation is again what cost me.** 471 people
scored higher on it, and the code review (242nd) wasn't far behind. The transcript and the interview
held the total up. More on why in [Results](#results).

## The problem

For each request, you get:

- the **request itself**: an amount, a date, a deadline, whether partial payment is allowed, and the
  person's question in their own words,
- the person's **financial profile**: home currency, balance, the minimum they want to keep, their
  priorities, and which payment methods they're willing to use,
- their **transaction history**: 25,342 events across 275 people, including pending, failed,
  cancelled, duplicated, and foreign-currency rows, some of them with a **blank amount** that only
  exists on a linked image of a payslip, bill, or receipt,
- the **payment options** the seller offers for that request,
- and **messages** from banks, employers, and merchants that can amend, delay, cancel, or confirm a
  specific transaction.

Your job is to fill in eight columns per request: the safe amount, an affordability status, a payment
method, the exact payment plan, the earliest safe date for paying in full, the spending changes
needed, and an explanation.

Three things make this harder than it sounds:

- **It's arithmetic with a strict contract, not a judgement call.** A plan is only safe if the balance
  stays above the person's minimum on every one of the next 90 days. Installments must match a
  supplied option exactly, a partial payment must be exactly two payments that add up to the full
  amount, and when several plans are safe there's a six-key tie-break order to follow.
- **The history has to be interpreted before it can be added up.** You have to decide what recurs and
  what was a one-off, reserve pending debits, ignore pending credits and unrealized investments,
  collapse duplicates, and convert currencies at the right date. Each of those is a place to be
  quietly wrong.
- **Messages and images are untrusted.** They can legitimately change a number (a bill was
  rescheduled, a refund hasn't landed), but they can also be scams. Two messages in this dataset are
  advance-fee frauds promising a cash prize if you "pay the release charge today".

HackerRank's original problem statement, lightly formatted, is in
[`problem_statement.md`](./problem_statement.md) if you want the task in their own words.

## The core idea

It's the same bet I made in June and August, taken as far as it will go:

**A deterministic financial solver with a narrow model layer. The model never produces a number that
reaches `output.csv`.**

```
requests.csv   (250 rows, each solved independently)
   │
   ▼
1. LOAD AND NORMALIZE  ·  plain code
   Load 8 CSVs into typed records that fail on unknown values. Collapse each
   transaction's lifecycle (pending → settled, charged → reversed) with six rules
   that read typed fields only, then convert every amount at its settlement date.
   │
   ▼
2. PERCEIVE  ·  models, behind a guard
   Two jobs only. Read the amount off a document when an event's amount is blank
   (two independent reads, reconciled by arithmetic in code), and turn a message
   into a typed change to an event that already exists. Anything else is dropped.
   │
   ▼
3. FORECAST  ·  plain code
   Detect which income and expenses recur, project the balance day by day over
   90 days, and find the trough.
   │
   ▼
4. DECIDE  ·  plain code
   Compute the safe amount and the earliest safe date, enumerate every valid plan,
   rank the safe ones by the six-key order, search for the smallest set of
   flexible-spending changes if nothing is safe, and derive the status.
   │
   ▼
5. EXPLAIN AND GATE  ·  plain code
   Render the explanation from the frozen decision, then check every row against
   the output contract. A row that breaks a rule raises instead of being written.
   │
   ▼
output.csv   (8 columns, in a fixed order)
```

The line that matters is between stage 2 and stage 3. The model gets to transcribe a figure off an
image and to propose a change like "event 412's date moves to the 20th". Code decides whether that
change is allowed, and code does every calculation after it.

Keeping "reading" and "deciding" apart buys the same things it did before:

- **It's testable with zero API calls.** `engine/` and `format/` do no I/O, read no clock, and use no
  randomness, and a test enforces that. The whole decision layer is covered by 797 tests that run
  offline in about 15 seconds.
- **It can't break the contract.** The ranking, the partial-payment shape, and the installment match
  are code, so they're satisfied rather than approximated. The output gate re-checks all of them.
- **The explanation can't invent a figure.** `decision_explanation` is rendered from the finished
  decision, not written by a model that has seen the raw data. Every number in it is a number the
  ledger already contains.
- **Scams can't add money.** A message can only amend the `amount`, `event_date`, or `status` of an
  event that exists. It can't create income, can't move the minimum balance, and can't change the
  request.

### The guard on messages

`bow/perception/untrusted.py` is the whole boundary, and it's four rules:

- **Referential integrity.** A proposed change must name an event that exists. A model that invents
  an ID, or attaches a claim to an unrelated row, gets dropped.
- **Field allowlist.** Only `amount`, `event_date`, and `status` can be amended.
- **No new financial facts.** A message asserting income or a charge that no event records is kept as
  evidence and dropped as a change. The two advance-fee scams are exactly this shape, and they fall
  out without any scam detection at all.
- **Every drop is logged** with the rule that dropped it.

On the final run, 198 messages were processed and 12 changes survived the guard. All 12 named a real
event.

## How to run it

Everything runs from the `code/` folder. It uses [uv](https://docs.astral.sh/uv/) and **Python
3.11**. The virtualenv lives *outside* `code/` on purpose, because `code/` is the folder that gets
zipped and graded.

```bash
cd code
UV_PROJECT_ENVIRONMENT=../.venv uv sync --python 3.11

# The test suite needs no key and makes no network call:
UV_PROJECT_ENVIRONMENT=../.venv uv run python -m pytest             # 797 tests

# The sample-set scorecard against the 25 published answers, offline:
UV_PROJECT_ENVIRONMENT=../.venv uv run python evaluation/harness.py

# The deterministic path over all 250 requests: no vision, no messages, no network, ~2 seconds:
UV_PROJECT_ENVIRONMENT=../.venv uv run python main.py --no-perception
```

The full run, which is what produced the submitted `output.csv`, needs a Sarvam key. Put it in a
`.env` file **next to** `code/`, not inside it, so the zip can't capture it:

```bash
echo 'SARVAM_API_KEY=...' > ../.env
UV_PROJECT_ENVIRONMENT=../.venv uv run python scripts/check_env.py   # checks the key's shape, prints only its length
UV_PROJECT_ENVIRONMENT=../.venv uv run python main.py                # ~24 minutes, ~₹118
```

That writes `../output.csv`, `evaluation/run_manifest.json`, and regenerates
`evaluation/usage_report.md` from that manifest, then exits `0` only if the output gate passes on the
file it just wrote. `dataset/` is never written to. If the dataset or the key live somewhere else,
set `BOW_DATASET_DIR` and `BOW_ENV_PATH`; nothing else is configurable by environment.

Other commands worth knowing:

```bash
python main.py --sample                    # run over sample_requests.csv instead
python main.py --config-check              # resolve configuration, print a summary, exit
python evaluation/ablations.py             # the eight-combination variable-spend experiment
python scripts/score_vision_gold.py        # the document reader against a hand-built gold set
python scripts/build_zip.py                # build code.zip, refusing if a secret or excluded path leaked
```

One gotcha: `uv sync` byte-compiles the package and leaves `__pycache__` folders inside `code/`, and
`tests/test_submission_hygiene.py` fails on them by design, because they must never reach the zip.
Clear them with `find . -name __pycache__ -type d -prune -exec rm -rf {} +` before running the suite.

## How the code is laid out

At the top level:

```
.
├── README.md                  you are here
├── problem_statement.md       the original task (HackerRank's wording), formatted
├── leaderboard-september.json the full final leaderboard, 3,062 entries
├── output.csv                 the exact predictions I submitted
├── dataset/                   the challenge data (see "About the data" below)
└── code/                      the contents of the submitted code.zip, plus five docs written after it
    ├── bow/          the solution, mapped out below
    ├── config/       the model registry, the decision register, prices, thresholds
    ├── docs/         13 write-ups: the reasoning behind every choice, and what's still wrong
    ├── evaluation/   the scorecard, the ablation, the run manifest, the usage report
    ├── scripts/      the live provider probes, the zip builder, the env check
    ├── prompts/      the one prompt: message text to typed changes
    └── tests/        797 tests, no API key needed
```

Inside `code/bow/`, each folder is one layer, and the imports only point one way:

| Folder | What lives there |
|---|---|
| `contracts/` | The pydantic models every layer shares. Imports nothing internal. |
| `data/` | CSV loading, normalization (lifecycle, cash treatment, currency), and recurrence detection |
| `perception/` | The document reader, the message extractor, language routing, and the untrusted-content guard |
| `providers/` | The only package allowed a network call: the Sarvam client, rate limiter, retries, usage meter |
| `engine/` | The solver: forecast, capacity, plans, ranking, spending changes, status. Pure functions only. |
| `format/` | Number and column formatting. Pure functions only. |
| `explain/` | The explanation template, rendered from the frozen decision |
| `validate/` | The output gate |
| `settings/` | Config loading and model-role resolution |

The layering is enforced by `tests/test_layering.py`, not by review. `engine/` and `format/` may
import `contracts/` and nothing else.

## A guide to the files

**Entry point and top level**

| File | What it does |
|---|---|
| [main.py](code/main.py) | The CLI: a full run, `--no-perception`, `--sample`, `--config-check`. |
| [bow/pipeline.py](code/bow/pipeline.py) | The end-to-end path: dataset in, `output.csv` out. |
| [bow/paths.py](code/bow/paths.py) | Resolves every path relative to the run root, never the working directory. |
| [bow/usage.py](code/bow/usage.py) | Generates `evaluation/usage_report.md` from the run manifest, so the cost report can't drift from the run. |
| [config/decisions.yaml](code/config/decisions.yaml) | The decision register: 28 named ambiguities, each with a default, an alternative, and a rationale. |
| [config/models.yaml](code/config/models.yaml) | Every model role, endpoint, seed, and token limit, with the probe that measured it. |

**`data/`: turning raw events into a ledger**

| File | What it does |
|---|---|
| [data/loaders.py](code/bow/data/loaders.py) | Reads the CSVs into typed objects. pandas never leaves this module. |
| [data/normalize.py](code/bow/data/normalize.py) | Lifecycle collapse, the six cash-treatment rules, and currency conversion at the settlement date. |
| [data/recurrence.py](code/bow/data/recurrence.py) | Works out which series recur and which cash events fall inside the 90-day horizon. |

**`perception/` and `providers/`: the only place a model is involved**

| File | What it does |
|---|---|
| [perception/vision.py](code/bow/perception/vision.py) | Reads an amount off a document. Two independent reads, reconciled by checking the line items against a printed total. |
| [perception/parsing.py](code/bow/perception/parsing.py) | Locale-aware number parsing, and which labelled figure counts as "the amount". |
| [perception/messages.py](code/bow/perception/messages.py) | Message text to typed changes. |
| [perception/routing.py](code/bow/perception/routing.py) | Detects the message's language and picks the model trained for it. |
| [perception/untrusted.py](code/bow/perception/untrusted.py) | The four-rule guard described above. |
| [perception/resolve.py](code/bow/perception/resolve.py) | Applies surviving reads and changes to the ledger. |
| [providers/sarvam.py](code/bow/providers/sarvam.py) | The only file that makes a network call. |
| [providers/response.py](code/bow/providers/response.py) | Classifies a response before anything trusts it, so an empty truncated reply is never read as "nothing found". |

**`engine/`: the decision (all pure code)**

| File | What it does |
|---|---|
| [engine/forecast.py](code/bow/engine/forecast.py) | Day-by-day balance projection, including a trailing-rate forecast for irregular essential spending. |
| [engine/capacity.py](code/bow/engine/capacity.py) | The safe amount and the earliest safe date. A closed form, checked against an independent binary search over 500 generated cases. |
| [engine/plans.py](code/bow/engine/plans.py) | Enumerates every structurally valid plan and checks each one over the whole horizon. |
| [engine/ranker.py](code/bow/engine/ranker.py) | The six ranking keys, in one tuple, in one function. |
| [engine/changes.py](code/bow/engine/changes.py) | The smallest set of flexible-expense cuts that makes a plan safe, capped at three. |
| [engine/policy.py](code/bow/engine/policy.py) | Turns the chosen plan into a status and method, and raises rather than emit a row that breaks a rule. |

**`explain/`, `format/`, `validate/`: the output**

| File | What it does |
|---|---|
| [explain/template.py](code/bow/explain/template.py) | The explanation, rendered from the frozen decision. |
| [format/amounts.py](code/bow/format/amounts.py) | The three different number formats the output needs. |
| [format/columns.py](code/bow/format/columns.py) | Assembles the eight columns. |
| [validate/gate.py](code/bow/validate/gate.py) | Checks every row against the contract before the run is allowed to succeed. |

## A few decisions worth noticing

The longer reasoning lives in [`code/docs/ARCHITECTURE.md`](code/docs/ARCHITECTURE.md) and
[`code/config/decisions.yaml`](code/config/decisions.yaml). The short version:

- **Every ambiguity became a named switch.** The spec leaves 28 things open (does a pending
  duplicate debit count, when does the horizon end, should a stale income series continue). Each one
  is an entry in the decision register with a default, the alternative, a rationale, and whether the
  25 sample answers can even tell the two apart. Several can't, and the register says so.
- **When both answers are wrong, pick the cautious one.** Two sample requests fail in opposite
  directions for one reason: in one, a salary series ended because the job ended; in the other, it
  resumed after leave. The typed fields are identical, and only the description text differs.
  Reading descriptions would be memorising this dataset, so I turned series continuation **off**.
  Inventing income for someone who just lost their job produces a confident "yes, you can afford it";
  the opposite error only produces an over-cautious "no".
- **Rules, not observations.** No dataset ID, no branching on free text, and no literal copied out of
  the data is allowed in the source. `tests/test_no_hardcoding.py` enforces it, so the generalization
  rule breaks the build instead of being a guideline.
- **Measure the provider, don't read its docs.** Every setting in `models.yaml` has a probe behind it.
  That's how I found that temperature 0 alone isn't reproducible on this provider but temperature 0
  with a fixed seed is, and that a too-small token budget comes back as HTTP 200 with empty content,
  which a naive client reads as "this message changes nothing".
- **A `reasoning_effort` A/B decided on reliability, not cost.** Same 45 messages, same seed, one
  field different. `low` was 45/45 valid against 43/45, and it happened to be 14% cheaper too.
- **I cut the model-written parts I didn't have time to make safe.** The original design had a
  bounded model step for ambiguous cases and a model-written explanation. Neither was built, and both
  were **removed** from the config rather than left looking live, because configuration that promises
  behaviour the code doesn't have is worse than none.

## Speed, cost, and repeatability

- **The full run costs about ₹118 (≈ USD 1.34).** That's 408 model calls, 1.52 million tokens, about
  24 minutes, and ₹0.47 per request. Three quarters of the bill is the message extractor, whose
  reasoning tokens dwarf its input. The breakdown is in
  [`code/evaluation/usage_report.md`](code/evaluation/usage_report.md), generated from the run
  manifest rather than typed in.
- **The deterministic path costs nothing.** `--no-perception` runs every decision rule over all 250
  requests in about 2 seconds with no network.
- **Document reads are cached** by image under `code/.cache/`, so a re-run doesn't pay for them twice.
  The cache is local only; it isn't in the zip or in this repo.
- **What isn't repeatable.** Pinned seeds made single calls reproducible in isolation, but three full
  runs with identical code and config still had 3, 4, and 13 message-extraction failures. The
  submitted `output.csv` is the output of one specific run, and a fresh run can move a row or two.
  That's recorded as open item L5 in [`LIMITATIONS.md`](code/docs/LIMITATIONS.md).

## Testing

```bash
cd code && UV_PROJECT_ENVIRONMENT=../.venv uv run python -m pytest    # 797 tests, ~15 seconds
```

All 797 pass with no key and no network, including the ones that read `dataset/`. Add
`-m "not dataset"` to skip those. The suites worth a look are the structural ones, because they
enforce design rules rather than behaviour:

- `test_layering.py`: the import graph points only one way, and the solver does no I/O.
- `test_no_hardcoding.py`: no dataset IDs or data-derived literals in the source.
- `test_no_sdk_leak.py`: one vendor, imported in one place.
- `test_untrusted.py`: the message guard drops what it should.
- `test_submission_hygiene.py`: nothing that shouldn't be zipped is inside `code/`.
- `test_recurrence_holdout.py`: removes every scheduled income row and makes recurrence detection
  rediscover it. It gets 44 of 47 dates exactly right.

## About the data

Like August, this repo **does** carry the challenge dataset, because the September starter repo
shipped it publicly. It's HackerRank's data, not mine; it's here so the project is runnable and the
numbers are checkable, and it stays under their terms rather than this repo's license.

| File | Rows |
|---|---|
| `requests.csv` (the rows to solve) | 250 |
| `sample_requests.csv` (solved examples) | 25 |
| `financial_profiles.csv` | 275 |
| `financial_events.csv` | 25,342 |
| `request_payment_options.csv` | 790 |
| `messages.csv` | 215 |
| `exchange_rates.csv` | 134 |
| `images.csv` / `media/images/` | 16 / 16 PNGs |

### Data format

**Output (`output.csv`), 8 columns in a fixed order:**

| Column | Values |
|---|---|
| `request_id` | The request being answered, one row per input row |
| `amount_safe_to_pay` | The most that's safe to pay today, `0 <= x <= requested_amount` |
| `affordability_status` | `affordable_now` / `affordable_with_plan` / `affordable_later` / `not_affordable` |
| `recommended_payment_method` | `full_payment` / `partial_payment` / `installments` / `wait` / `not_recommended` |
| `payment_plan` | `YYYY-MM-DD:amount` entries joined by `\|`, or `none` |
| `earliest_date_for_full_payment` | A date, or empty if never safe within 90 days |
| `spending_changes_needed` | Up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` joined by `\|`, or `none` |
| `decision_explanation` | A short explanation built from the decision's own numbers |

## Results

**On the 25 published examples.** They're the only labelled data there is, so they're the only
accuracy numbers there are. Five of them hinge on a blank amount that only an image supplies, so the
deterministic path can score 20 and the full path all 25:

| Metric | Deterministic path (20 rows) | With perception (25 rows) |
|---|---|---|
| `affordability_status` | 14/20 | **19/25** |
| `recommended_payment_method` | 17/20 | **21/25** |
| `payment_plan`, exact | 16/20 | **20/25** |
| `earliest_date_for_full_payment`, exact | 12/20 | **16/25** |
| Balance trough, median relative error | 10.0% | 10.4% |
| `amount_safe_to_pay`, median relative error | 44.8% | 45.8% |

The two error rows measure the same mistake. The trough is what the forecast predicts; the safe
amount is the trough minus the minimum balance, so the same absolute error looks much larger against
it. The document reader scored 16/16 on a hand-built gold set, and 15 of 16 images read the same
figure five times out of five.

**On all 250 requests.** The submitted file has 81 `not_affordable`, 62 `affordable_now`,
60 `affordable_with_plan`, and 47 `affordable_later`. By method: 81 `not_recommended`,
64 `full_payment`, 52 `installments`, 47 `wait`, and 6 `partial_payment`. Only 4 requests got
spending changes. Every row passed the output gate.

### What went wrong, and where

Output Evaluation was my weakest stage (472nd). HackerRank's written feedback put it plainly: the
output held up on simple "pay now, no adjustments" cases and fell apart once the right answer needed
a constrained plan, above all **when it needed specific spending cuts or stops**, and the status,
safe amount, dates, and installment structure drifted along with it.

The sample set shows the exact mechanism. Three of the 25 published answers need a spending change.
My pipeline called all three `affordable_now` with no changes. The chain is:

1. The forecast under-counts everyday spending, so the balance looks healthier than it is.
2. On that healthier balance, paying in full today looks safe.
3. `engine/changes.py` only searches for spending cuts when **no plan is safe without them**, so it
   never runs.
4. The status, the safe amount, and the explanation all follow from the wrong plan.

So the spending-change search was correct and almost never got the chance to run: it fired on 4 of
250 requests, while the samples suggest roughly one request in eight needs a change. The
feedback's recommendation matches what I'd do now: make "which spending actions does this plan need"
a first-class step, then recompute every dependent field from that plan. The underlying causes, which
my own docs had already named before the results came out:

- **Essential variable spending is under-forecast, which is the unsafe direction.** This was the big
  one. Early on the forecast didn't include irregular spending at all, so the balance never eroded
  between paydays and every safe amount came out too high. Adding a trailing-rate forecast for
  irregular spending was worth 4.7 points on the trough error, but a series seen only once, or one
  that's gone stale, still isn't forecast. The trough still lands earlier and shallower than the
  published answers, so `amount_safe_to_pay` is over-predicted more often than under.
  [`CAPACITY_FORENSICS.md`](code/docs/CAPACITY_FORENSICS.md) is the investigation.
- **Some answers are undecidable from the typed fields.** The salary-continuation case above, plus two
  switches (when the horizon ends, and what "balance as of" means) where the 25 samples can't tell the
  two options apart. Those are settled by a configured default, and the default is sometimes wrong.
- **The earliest date is the weakest column** at 12/20 on the deterministic path. It's the most
  sensitive to the forecast error, because a slightly-too-high trough moves the first safe day
  forward.

With hindsight the pattern is the same as August. I spent the time on making the system provably
correct against its own contract (layering, guards, a decision register, 797 tests), and that shows up
in the transcript and interview scores. The output stage rewards agreement with hidden numbers, and
the lever there was the forecast of everyday spending, which I diagnosed properly but only half
fixed. The feedback on the other stages pointed the same way: the transcript review asked for earlier
priority on the outputs that drive the final decision, so deep work on one subsystem doesn't get
ahead of end-to-end correctness, and the code review asked for a bounded, auditable step where a
model chooses between actions when judgement is genuinely needed, which is exactly the part I cut.

## What I'd pass on to you

- **Keep the model out of the arithmetic.** On a problem with an exact contract, code satisfies the
  rules and a model approximates them. Let the model read documents and messages, and nothing else.
- **Make the untrusted-content guard structural.** "A message may only amend three fields of an event
  that already exists" stopped both scams without any scam detection, because there was nothing for
  them to amend.
- **Write every ambiguity down as a switch.** When the spec is silent, name the question, pick a
  default, record why, and record whether your labelled data can even tell. It turns an argument into
  a lookup.
- **Probe the provider before you configure it.** A truncated reply that arrives as a successful empty
  response is the kind of bug that silently costs you rows for the whole weekend.
- **Score the whole row, not the subsystem.** Every component here had its own metric and most of
  them looked fine. The failure lived in the handoff: an optimistic forecast meant a correct
  spending-change search never ran. One end-to-end report of where final rows diverge from the
  published answers would have shown that in minutes.
- **Check the direction of your error, not just its size.** An affordability agent that's wrong on the
  optimistic side tells people they can afford things they can't. I knew my error was in that
  direction, and it's the one I should have spent the last hours on.
- **Be honest about which stage you lost.** Same lesson as August, and it's still the most useful part
  of the write-up.

There are deeper write-ups in [`code/docs/`](code/docs/), with an index in
[`code/docs/INDEX.md`](code/docs/INDEX.md). If the project is new to you, start with
[`code/docs/WALKTHROUGH.md`](code/docs/WALKTHROUGH.md), which follows one request through every step
with the real values, then [`code/docs/LIMITATIONS.md`](code/docs/LIMITATIONS.md), which opens with a
status table of everything that's still wrong. [`code/docs/AUTHORSHIP.md`](code/docs/AUTHORSHIP.md)
says plainly which parts the AI assistant wrote and which calls were mine.

## A note on reuse

My code here is released under the **MIT License** (see the [`LICENSE`](../LICENSE) file at the repo
root), so you're free to read it, reuse it, and build on it. That license covers my code only.
HackerRank's original problem statement is included for context in
[`problem_statement.md`](./problem_statement.md), and the challenge dataset under `dataset/` is
HackerRank's, included because their starter repo published it, and it is not mine to license.
