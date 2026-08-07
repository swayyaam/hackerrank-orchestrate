# WhatsApp Message Notification Router

This project reads an incoming WhatsApp message (a text, an image poster, or a voice note) and
decides one of three things for the person receiving it: **interrupt them now** (`notify`), **save it
for later** (`digest`), or **keep it out of their way** (`mute`). It also says what kind of message it
is, writes a one-line reason, attaches a confidence number, and points at the specific past messages
that justified the call, so a human can see exactly why each decision was made.

I built it for the **HackerRank Orchestrate** hackathon in August 2026, a 24-hour event to design and
ship an AI agent. This is the cleaned-up public version. I'm sharing it so you can see how it's put
together and borrow the parts that are useful to you.

---

## Score breakdown

The scored field was **1,983 participants**. I finished **37th**, with **75.8 out of 100**. The best
total in the field was 84.2.

The score came from four stages. The two I did best on were the Chat Transcript and the AI Judge
interview:

| Stage | My score | Best in the field | Where I ranked |
|---|---|---|---|
| Chat Transcript Evaluation | **9.8 / 10** | 9.9 | **8th of 1,983** |
| Interview Evaluation | **26.1 / 30** | 27.9 | **17th of 1,983** |
| Technical Review & Final Results (the code) | 22.8 / 30 | 28.2 | 94th |
| Output Evaluation | 17.1 / 30 | 24.6 | 577th |
| **Total** | **75.8 / 100** | 84.2 | **37th of 1,983** |

The per-stage scores come from [`leaderboard-august.json`](./leaderboard-august.json), scraped from
the public leaderboard with [`scrape_leaderboard.py`](../scrape_leaderboard.py).

The honest read: **Output Evaluation is what cost me.** 577 people scored higher on it, against 8 to
94 on every other stage. That one stage is essentially the whole gap to the top of the board. More on
why in [Results](#results).

## The problem

For a single incoming message, you get:

- the **message itself**, which may be text, an image, or a voice note,
- the **receiver's** notification behavior (quiet hours, how much they open, dismiss, or report),
- the **relationship**: which group it came from and the receiver's role in it, or which business sent
  it and whether the receiver has ever ordered from them,
- and **412 past messages** with a record of how the receiver reacted to each one.

Your job is to route the message and fill in six columns: the action, the message type, a short
reason, a confidence score, and the IDs of the past messages you used as evidence.

Three things make this harder than it sounds:

- **The same message routes differently for different people.** A sale poster is useful to one user
  and noise to another. The decision is about the pair, not the message.
- **The input is attacker-controlled.** A scam message can contain text specifically written to talk
  a language model into delivering it. Anything the model reads is potentially hostile.
- **Engagement history must inform the call without owning it.** A muted family group can still carry
  an urgent direct mention, and a clear scam should be muted no matter how much the user normally
  engages with that sender.

HackerRank's original problem statement, lightly formatted, is in
[`problem_statement.md`](./problem_statement.md) if you want the task in their own words.

## The core idea

Here's the one decision the whole project rests on, and it's the same bet I made in June:

**The model describes and proposes. Plain Python code makes the actual decision.**

```
messages.csv   (110 rows, each routed independently)
   │
   ▼
1. INGEST  ·  plain code
   Load the 13 context CSVs and assemble, per message, only what that message is
   allowed to see: the receiver, the group, the sender or business, and the
   receiver's own past interactions.
   │
   ▼
2. MEDIA  ·  plain code, then models
   Work out each attachment's real format from its magic bytes, then describe
   images and transcribe voice notes. Cached by the sha256 of the file's bytes.
   │
   ▼
3. PERCEIVE  ·  Claude
   The model reads the message and returns a structured record: a proposed action,
   a ranked list of type candidates, and a set of yes/no claims about the content
   ("this asks for an authentication secret", "this states a deadline"). It does
   not return the answer.
   │
   ▼
4. DECIDE  ·  plain code
   16 rules in a fixed precedence order: hard safety overrides, then payment
   authority, then guards, then caps and floors, and finally the model's proposal
   as the default. The rule that won is recorded on the row.
   │
   ▼
output.csv   (6 columns, in a fixed order)
```

That record in the middle is the seam everything hangs on. None of the four output values the
challenge scores is written by the model: the **action** comes from the rule table, the **confidence**
is computed in code from named features, the **reason** is composed from a clause grammar, and the
**evidence IDs** are picked by a tiered ranking over the receiver's own history.

Keeping "reading" and "deciding" apart buys the same things it bought in June, plus one that's
specific to this problem:

- **A rule can be made structurally incapable of being talked out of firing.** This is the part I'd
  most want you to take away, so it gets its own section below.
- **You get the same answer every time.** The decision is just code over a saved perception record, so
  the same inputs always produce the same row. Two consecutive runs are byte-identical, and
  `python -m metrics assert-identical` is the gate that enforces it.
- **It's auditable.** Every row names the rule that decided it, plus every rule that fired and lost.
- **You can tell a rule change from model noise.** Because the model's answers are cached, you can
  change a rule and re-run with zero API calls and see only your own effect.

### The rule that can't be argued with

`IMPERSONATION` mutes a message from a business that is posing as a brand it isn't. It's a pure
function of three things: whether the business is verified, the state of its sending domain, and
whether its claimed identity is consistent. **The message text is not one of its inputs.**

That's the whole point. Compare the prompt-only version of the same rule, "mute messages from
businesses impersonating a brand", which gets evaluated by a model that has just read attacker-written
text. There, every sentence in the message is a candidate input, and the rule holds only as long as
the model resists whatever the attacker wrote. The rule-table version doesn't depend on the model
resisting anything, and it doesn't get weaker as the attack gets better written.

Two test files defend that boundary from opposite directions:

- `tests/test_threat_model.py` (23 tests) asserts injected text can't reach the rules that don't read
  it.
- `tests/test_ablations.py` (8 tests) asserts the converse, that each rule genuinely reads what it
  claims to. It degrades a declared input and requires the rule to stop firing. A rule that keeps
  firing with its input removed was lying about what it reads, so a null result there is a defect,
  not a pass.

## How to run it

Everything runs from the `code/` folder, and the default path needs **no API key and makes no network
call**.

```bash
cd code
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                              # drop [dev] if you don't want the test suite

# The test suite needs no key and no dataset:
python -m pytest                                     # 337 tests

# Produce output.csv from the shipped cache, offline:
python main.py --dataset ../dataset route --out ../dataset/output.csv

# Check a CSV against the challenge contract:
python main.py --dataset ../dataset validate ../dataset/output.csv
```

Requires **Python 3.12+**. `pyproject.toml` is the only dependency manifest; there is deliberately no
`requirements.txt`, because two manifests are two sources of truth that drift. Runtime dependencies
are pydantic and pandas plus two provider SDKs, and both SDKs are imported lazily so the package stays
importable and the whole test suite runs with neither installed.

The rest of the commands:

```bash
cd code        # run everything from here

# Metrics on a run directory
python -m metrics report runs/<run_id>                 # label-free + labeled, full report
python -m metrics diff runs/<old_id> runs/<new_id>     # what moved, and the churn-to-net ratio
python -m metrics assert-identical runs/<a> runs/<b>   # the determinism gate

# Re-derive the caches from scratch. Not needed to reproduce the submitted output,
# and these are the only commands that cost money.
python main.py --dataset ../dataset warm-media --allow-network        # 33 media files
python main.py --dataset ../dataset warm-perception --allow-network   # 110 rows

# Build the submission archive (refuses to finish if a secret or excluded path leaked)
python make_zip.py
```

Every `route` writes a timestamped run directory under `runs/` holding `output.csv`, `rows.jsonl`,
`eval_rows.jsonl`, `events.jsonl`, and `manifest.json`.

Four environment variables exist and **all four are optional**: `ANTHROPIC_API_KEY` and
`ROUTER_MODEL_ID` for perception, `SARVAM_API_KEY` for voice transcription, and `ROUTER_DATASET` for
the contract tests. They're only read on the `--allow-network` path. Copy `code/.env.example` to
`code/.env` if you want to use them.

## How the code is laid out

At the top level:

```
.
├── README.md             you are here
├── problem_statement.md  the original task (HackerRank's wording), formatted
├── leaderboard-august.json   the full final leaderboard, 1,983 entries
├── dataset/              the challenge data (see "About the data" below)
├── submission/           the exact artifacts submitted, plus baseline and final metrics
└── code/
    ├── docs/       14 design write-ups: the reasoning behind every choice
    ├── tests/      337 tests, no API key needed
    └── ...         the solution itself, mapped out below
```

Inside `code/`, each folder is one stage of the pipeline:

| Folder | What lives there |
|---|---|
| `ingest/` | CSV loading and per-message context assembly |
| `media/` | Image and voice extraction, container detection from magic bytes |
| `perception/` | Prompt construction, fencing, the response schema, the model client and its cache |
| `features/` | Derived facts: engagement stance, business domain state, group role, content, integrity |
| `policy/` | The 16 rules and the precedence engine that applies them |
| `scoring/` | Evidence selection and the confidence formula |
| `compose/` | The reason clause grammar and the register check |
| `emit/` | The per-row record, the submission CSV writer, and contract validation |
| `metrics/` | Label-free and labeled metrics, run diff, consistency scan, console report |
| `cache/` | The content-addressed store shared by media and perception |

## A guide to the files

**Entry point and top level**

| File | What it does |
|---|---|
| [main.py](code/main.py) | The CLI: `route`, `validate`, `warm-media`, `warm-perception`. |
| [pipeline.py](code/pipeline.py) | The end-to-end run for one message and for the batch. |
| [config.py](code/config.py) | Every tunable constant in one place, each with its provenance recorded inline. |
| [schemas.py](code/schemas.py) | The single source of truth: the perception record, the feature vector, the output row, and the allowed values. |
| [errors.py](code/errors.py) | A small structured error taxonomy, so a failure on one row is classified rather than swallowed. |
| [make_zip.py](code/make_zip.py) | Builds the submission archive and refuses if the cache is missing, a path leaked, or a content scan matches a credential shape. |

**`ingest/` and `media/`: getting the inputs in**

| File | What it does |
|---|---|
| [ingest/loader.py](code/ingest/loader.py) | Reads the 13 dataset CSVs. |
| [ingest/context.py](code/ingest/context.py) | Assembles the per-message view: only what this message's receiver is allowed to see. |
| [media/locate.py](code/media/locate.py) | Resolves a `media_id` to a file on disk. |
| [media/decode.py](code/media/decode.py) | Detects the real container from magic bytes, never the filename. |
| [media/extract.py](code/media/extract.py) | Image description and voice transcription, both cached by content hash. |

**`perception/`: what the model is asked, and what it may return**

| File | What it does |
|---|---|
| [perception/prompt.py](code/perception/prompt.py) | Builds the prompt and fences the untrusted message content inside it. |
| [perception/client.py](code/perception/client.py) | The only place the provider SDKs live, imported lazily. |
| [perception/parse.py](code/perception/parse.py) | Validates the model's response into a `Perception`, or fails loudly. |
| [perception/cached.py](code/perception/cached.py) | The read-through cache that makes offline runs possible. |

**`features/`: turning context into named facts**

| File | What it does |
|---|---|
| [features/engagement.py](code/features/engagement.py) | The receiver's stance toward this sender, from their own past reactions. |
| [features/business.py](code/features/business.py) | Verification state, domain state, and whether a real relationship exists. |
| [features/group.py](code/features/group.py) | The receiver's role and mute state in the group. |
| [features/sender.py](code/features/sender.py) | How established the sender is. |
| [features/content.py](code/features/content.py) | Content-derived facts, kept separate because they originate in untrusted text. |
| [features/integrity.py](code/features/integrity.py) | Integrity warnings: what looked wrong about this row. |
| [features/assemble.py](code/features/assemble.py) | Combines the above into the one `FeatureVector` the rules read. |

**`policy/`: the decision (all pure code)**

| File | What it does |
|---|---|
| [policy/rules.py](code/policy/rules.py) | The 16 rules as data. Each declares its effect, its justification, and its `reads` set. |
| [policy/engine.py](code/policy/engine.py) | Applies them in precedence order and records which one won. |

**`scoring/`, `compose/`, `emit/`: the other three output columns**

| File | What it does |
|---|---|
| [scoring/evidence.py](code/scoring/evidence.py) | Tiered ranking over the receiver's history to pick evidence IDs. |
| [scoring/confidence.py](code/scoring/confidence.py) | The confidence formula, computed from named features. |
| [compose/reason.py](code/compose/reason.py) | The clause grammar that writes the reason, plus a register check. |
| [emit/record.py](code/emit/record.py) | The full per-row audit record written to `rows.jsonl`. |
| [emit/writer.py](code/emit/writer.py) | Writes the six output columns in the exact order, and validates a CSV against the contract. |

The files under `policy/`, `scoring/`, and `compose/` are deliberately boring: plain functions that
take values in and return values out, with no network calls and no shared state. That's why they can
be unit-tested and re-run on saved perception records with zero API calls.

### The 16 rules

They run in tiers, and the first rule that determines the action wins:

| Tier | Rules |
|---|---|
| 0. Safety | `CREDENTIAL_SOLICITATION`, `ROUTER_DIRECTIVE`, `IMPERSONATION`, `COERCIVE_PAYMENT`, `UNSAFE_INSTRUCTION` |
| 1. Payment authority | `UNAUTHORIZED_PAYMENT_REQUEST` |
| 2. Guards | `DIRECT_ADDRESS_GUARD`, `RISK_TYPE_CAP` |
| 3. Consent and relationship caps | `PROMOTION_OPT_OUT`, `REPEAT_DISMISSAL`, `FREQUENTLY_FORWARDED_BROADCAST`, `GROUP_MUTE_CAP`, `COLD_SENDER_CAP` |
| 4. Priority floors | `TIME_CRITICAL_FLOOR`, `DIRECT_REQUEST_FLOOR` |
| 5. Default | `PERCEPTION_ACCEPTED` |

`ROUTER_DIRECTIVE` is the one that exists purely because of this challenge's threat model: it fires
when the message text is trying to address the router itself.

On the final run, 15 of 110 rows were overridden away from the model's proposal, all of them
demotions, and 78 rows had two or more rules fire. The full table with each rule's trigger, effect,
and `reads` set is in [`code/docs/DECISION_ENGINE.md`](code/docs/DECISION_ENGINE.md).

## A few decisions worth noticing

The longer reasoning for each of these lives in
[`code/docs/PROBLEM_ANALYSIS.md`](code/docs/PROBLEM_ANALYSIS.md), which logs 21 decisions and two
places where the spec contradicts itself. The short version:

- **Never trust a file extension.** 19 of the 33 media files in this dataset have an extension that
  misidentifies them. All 13 voice notes are named `.mp3`, but 5 are WAV and 4 are AAC/M4A. Of the 20
  `.jpg` images, 7 are PNG, 2 are WebP, and one is an MP4 video. A decoder that trusted the filename
  would fail on more than half the media. Detection is done from magic bytes.
- **Voice transcription went to a different provider than perception.** The voice notes are code-mixed
  Indic speech, and transcripts had to land in the same romanized register as the text channel to be
  comparable. Sarvam's `saaras:v3` did that; a general transcriber didn't.
- **The cache is keyed by the sha256 of the file's bytes, never the path or the ID.** That's what makes
  a warm re-run cost nothing, and it's what lets the submission ship a cache that a reviewer can
  reproduce from without any credentials.
- **Two known-wrong labeled rows were left alone.** In one, a safety rule wrote `scam` over a truth of
  `spam`; in the other, a guard suppressed a cap that was carrying exactly the right answer. Each is
  worth one row out of 30, which is below the measured noise floor, so "fixing" them would have been
  fitting to noise. They're documented instead.
- **Evidence selection is a ranking, not a generation.** The model never writes an evidence ID.
  Ungrounded citations on the final run: 0.

## Speed, cost, and repeatability

- **Reproducing the submission costs nothing.** `route` is offline by default. Every row is served
  from the shipped content-addressed cache, so the submitted `output.csv` regenerates with **0 tokens
  in, 0 tokens out, in 0.4 seconds wall time**, and it's byte-identical.
- **What that proves and what it doesn't.** It proves the pipeline is deterministic given fixed model
  answers. It does not prove the model's answers are stable. That was measured separately by building
  a second cache from cold against the live APIs and diffing: **97.3% action stability, with 81 of 110
  rows identical across all six columns.** Every rule still fired and all 8 ablations still held, and
  the labeled set moved by ±1 row from re-derivation alone. That ±1 is the reason nothing in this
  project rests on a one-row difference.

## Testing

```bash
cd code && python -m pytest      # 337 tests, no API key and no dataset needed
```

334 pass and 3 skip; the 3 skips are the contract tests, which need the dataset and skip cleanly
without it. That's deliberate, because it's what lets the submitted archive exclude the dataset.

The suite is worth a look for the two unusual files described earlier: `test_threat_model.py` proves
untrusted text can't reach rules that don't read it, and `test_ablations.py` proves each rule actually
reads what it declares. `test_packaging.py` exercises all eight credential-shape patterns in the
archive's secret scanner against planted examples, because a scanner that has never been shown to fail
is not evidence that it works.

## About the data

Unlike the June project, this repo **does** carry the challenge dataset, because the August starter
repo shipped it publicly. It's HackerRank's data, not mine; it's here so the project is runnable and
the numbers are checkable, and it stays under their terms rather than this repo's license.

| File | Rows |
|---|---|
| `messages.csv` (the rows to route) | 110 |
| `sample_messages.csv` (labeled examples) | 30 |
| `message_history.csv` / `message_events.csv` | 412 each |
| `users.csv` / `groups.csv` / `business_accounts.csv` | 54 / 23 / 110 |
| `images.csv` / `voice_notes.csv` | 20 / 13 |
| `media/` (the actual bytes) | 33 files |

### Data format

**Output (`output.csv`), 6 columns in a fixed order:**

| Column | Values |
|---|---|
| `message_id` | The incoming message ID, one row per input row, in input order |
| `action` | `notify` / `digest` / `mute` |
| `message_type` | `personal, urgent, event, payment, business_update, promotion, greeting, forward, spam, scam, unknown` |
| `reason` | Short free text, grounded in the decisive factor |
| `confidence` | A number in `[0, 1]` |
| `evidence_message_ids` | `;`-joined historical message IDs, or `none` |

## Results

**On the 30 labeled rows.** The honest framing first: at n=30, one row is 3.33 percentage points, so
these numbers are indicative and I don't describe anything under two rows as an improvement.

| Metric | Baseline | Final |
|---|---|---|
| Action accuracy | 56.7% | **83.3%** (macro F1 0.83) |
| Message type accuracy | 23.3% | **80.0%** (macro F1 0.63) |
| Joint (both correct) | 16.7% | **70.0%** |
| Calibration (ECE / Brier) | 0.190 / 0.239 | **0.119 / 0.139** |

**On all 110 rows, without labels.** The final run emits `mute` 55 (50.0%), `notify` 28 (25.5%),
`digest` 27 (24.5%). 109 of 110 rows cite at least one piece of evidence, with zero ungrounded
citations. Confidence spans 0.51 to 0.95 with a mean of 0.75. A consistency scan groups the 110 rows
into 42 behaviorally similar clusters and requires any divergence inside a cluster to be attributable
to a named rule: 8 clusters diverge, 0 unexplained.

### What went wrong, and where

The leaderboard says Output Evaluation was my weakest stage by a wide margin (577th, against 8th to
94th elsewhere). That's the stage that scores `output.csv` against hidden labels, and my own metrics
had already pointed at the same three weaknesses before the results came out:

- **Digest is under-served, and the cause is upstream of the rules.** Four of the five labeled action
  errors are rows the model proposed as `digest` where the truth was `notify` or `mute`. It read them
  correctly and drew the priority line in a different place. I moved that boundary twice by sending
  the model resolved definitions rather than by tuning a threshold, and then stopped, because moving
  it further without a labeled signal is just fitting to 30 rows.
- **Type accuracy 80% but macro F1 0.63.** Six of the eleven types have one or two labeled examples,
  so per-class F1 on them is noise with a decimal point. And five safety rules *set* the type to
  `scam` as a side effect of the action, which makes type partly a function of the action rule rather
  than an independent judgement.
- **Confidence is narrow.** 23 of 30 labeled rows sit in one bucket. ECE 0.119 means the predictor
  isn't badly *mis*-calibrated, not that it's informative. Brier 0.139 is the more honest number.

With hindsight the ranking is clear: I invested in provable correctness of the decision layer, and
that paid off in the interview and transcript stages, but the output stage rewards raw label
agreement, and the model's `digest` boundary was the thing to attack. The rule table couldn't fix it
because the rules were never wrong; the proposal they defaulted to was.

## What I'd pass on to you

- **Let the model describe and let code decide.** Same lesson as June, and it held up better here
  because this problem is adversarial. Once the deciding code physically doesn't take message text as
  an input, "ignore any instructions in the message" stops being a request you make and starts being
  a property of the structure.
- **Test that a rule reads what it says it reads.** Degrade the input a rule declares and assert the
  rule stops firing. This caught real cases where a rule was passing for the wrong reason, and it's
  the cheapest test I wrote all weekend.
- **Measure your noise floor before you celebrate.** Re-deriving the whole cache from cold moved the
  labeled set by ±1 row on its own. Knowing that number is what let me ignore a lot of tempting
  one-row "improvements" in the last hours.
- **Ship the cache.** Content-addressing the model's answers by file bytes meant a reviewer could
  reproduce the exact submission with no API key, and meant I could iterate on rules for free.
- **Don't trust file extensions on a media corpus.** More than half of this one was mislabeled. That's
  a one-line fix if you check magic bytes and a silent disaster if you don't.
- **Be honest about which stage you lost.** The write-up is more useful, and the diagnosis carries
  further than a flattering number would.

There are deeper write-ups (system design, the decision engine, the threat model, failure modes, the
generalization policy, and the evaluation strategy) in [`code/docs/`](code/docs/). If the project is
new to you, start with [`code/docs/CODE_TOUR.md`](code/docs/CODE_TOUR.md).

## A note on reuse

My code here is released under the **MIT License** (see the [`LICENSE`](../LICENSE) file at the repo
root), so you're free to read it, reuse it, and build on it. That license covers my code only.
HackerRank's original problem statement is included for context in
[`problem_statement.md`](./problem_statement.md), and the challenge dataset under `dataset/` is
HackerRank's, included because their starter repo published it, and it is not mine to license.
