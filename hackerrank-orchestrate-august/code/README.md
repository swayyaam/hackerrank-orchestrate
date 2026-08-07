# Message Notification Router

For every incoming WhatsApp message in `dataset/messages.csv`, decide whether it should **`notify`** the user now, wait in a **`digest`**, or be **`mute`**d — personalised to the receiving user, across text, image posters and screenshots, and voice notes.

Output: one row per message in `output.csv` —
`message_id,action,message_type,reason,confidence,evidence_message_ids`.

This directory is the whole submission. Everything needed to run, test, or understand the system lives under it, including the design documents and the warmed cache.

---

## 1. How it decides

A language model reads each message and returns a **structured perception record**: a proposed action, a ranked list of `type_candidates`, and a set of boolean claims about the content ("this asks for an authentication secret", "this states a deadline with a stated consequence"). It does not return the answer. **Deterministic Python owns the final action**, through a precedence table of **16 rules** applied in a fixed order — hard overrides, then guards, then caps and floors, then the model's proposal as the default. Whichever rule determined the row is named on the output record (`override_code_applied`), alongside every rule that fired and lost. `confidence` is computed in code from named features, `reason` is composed from a clause grammar, and `evidence_message_ids` is selected by a tiered ranking over the receiver's own history. None of the four is written by the model.

**The property this buys that a prompt cannot: a rule the code physically cannot express the wrong way, because the inputs are not in scope.**

`IMPERSONATION` is a pure function of `(business.verified, domain_state, brand_name)`. The message text is not a parameter, so no phrasing inside the message can influence whether it fires. Compare the prompt-only version of the same rule — "mute messages from businesses impersonating a brand" — which is evaluated by a model that has just read attacker-controlled text, where every instruction in that text is a candidate input. The rule table's guarantee is structural rather than behavioural: it does not depend on the model resisting anything, and it does not weaken as the attack gets better written. `tests/test_threat_model.py` (23 tests) asserts that boundary. `tests/test_ablations.py` (8 tests) asserts the converse — that each rule genuinely reads what its `reads` set claims — by degrading that input and requiring the rule to stop firing. A null result there is a defect, not a pass.

The split also localises error: when a labeled row is wrong, the recorded proposal and firing statuses say which layer produced it. All nine are attributed in [PROBLEM_ANALYSIS D21](docs/PROBLEM_ANALYSIS.md#d21--layer-attribution-of-the-labeled-errors-and-why-the-model-was-not-escalated).

---

## 2. Running it

**Working directory is `code/` for every command below.** `code/` is a source root, not a package: imports read `from policy.engine import decide`, and nothing above it is referenced.

```bash
cd code
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .                                     # add ".[dev]" for the test suite
```

Requires **Python 3.12+**. `pyproject.toml` is the **only** dependency manifest — there is deliberately no `requirements.txt` (§5). Runtime dependencies are **pydantic** and **pandas** plus the two provider SDKs (**anthropic**, **sarvamai**), both imported lazily inside `perception/client.py` so the package stays importable and the entire test suite runs with neither installed and no credentials set. No vector database, no agent framework, no plotting library.

`ffmpeg`/`ffprobe` are optional. Without them container detection still works — it is pure magic-byte inspection — but audio duration is reported as `null` rather than guessed.

### Route — produce `output.csv`

```bash
python main.py --dataset ../dataset route --out ../dataset/output.csv
```

Offline by default: reads the shipped caches and **makes no network call**. `--out` defaults to `<dataset>/output.csv`, so it follows `--dataset` rather than the working directory. Writes a timestamped run directory (`--runs-dir`, default `./runs`) holding `output.csv`, `rows.jsonl`, `eval_rows.jsonl`, `events.jsonl`, and `manifest.json`.

### Validate — check a CSV against the contract

```bash
python main.py --dataset ../dataset validate ../dataset/output.csv
```

Checks row count and ordering against `messages.csv`; the six columns in exact contract order; allowed `action` and `message_type` values; `confidence` numeric in [0, 1]; no empty field; and that every `evidence_message_ids` value is either the literal `none` or `;`-joined `message_NNNN` ids that resolve in `message_history.csv`.

### Metrics

```bash
python -m metrics report runs/<run_id>                 # label-free + labeled, full report
python -m metrics diff runs/<old_id> runs/<new_id>     # what moved, and the churn-to-net ratio
python -m metrics assert-identical runs/<a> runs/<b>   # determinism gate
```

### Warming the caches — only to re-derive from scratch

Neither is needed to reproduce the submitted output. Both are idempotent: a warm run over unchanged inputs makes zero calls, because entries are content-addressed by the **sha256 of the file's bytes**, never the path or the id.

```bash
python main.py --dataset ../dataset warm-media --allow-network        # 33 media files
python main.py --dataset ../dataset warm-perception --allow-network   # 140 rows
```

Without `--allow-network` each command reports coverage and calls nothing. **With it, they cost money.**

Container format is detected from **magic bytes, never the filename**, which matters on this corpus: **19 of the 33 media files carry an extension that misidentifies them** — all 13 voice notes are named `.mp3` but 5 are WAV and 4 are AAC/M4A, and of the 20 `.jpg` images 7 are PNG, 2 are WebP, and one is an MP4 video. A decoder trusting the extension would fail on more than half the media.

### Tests

```bash
python -m pytest            # 337 tests (334 pass, 3 skip without the dataset)
```

---

## 3. Environment variables

Read from the environment, or from a `code/.env` file loaded at start-up by `main.load_dotenv()`, which never overrides a real export and never prints a value. Copy `.env.example` to `.env`.

| variable | needed for | where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | `--allow-network` only | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `ROUTER_MODEL_ID` | `--allow-network` only | An Anthropic model id, verified against `GET /v1/models` rather than assumed. This build used `claude-sonnet-5`. |
| `SARVAM_API_KEY` | `--allow-network` only | [dashboard.sarvam.ai](https://dashboard.sarvam.ai) → API Keys. Voice transcription is Sarvam `saaras:v3`, not Anthropic: the corpus is code-mixed Indic speech and transcripts must land in the same romanised register as the text channel. |
| `ROUTER_DATASET` | contract tests only | A path; defaults to `../dataset`. Those tests skip cleanly when it is absent, which is why the archive can exclude the dataset. |

**All four are optional.** The offline path — `route`, `validate`, `metrics`, and the whole test suite — needs none of them. `.env` is gitignored, excluded from the archive by filename, and the archive's secret scan reads the bytes of every shipped member to confirm no key leaked by some other route (§5).

---

## 4. Files the program expects

Read from `--dataset` (default `../dataset`, a sibling of `code/`):

```
dataset/
├── messages.csv                    the 110 target rows — one output row each
├── sample_messages.csv             30 labeled rows: format authoritative, labels held out
├── users.csv  groups.csv  group_members.csv
├── business_accounts.csv  user_business_history.csv
├── message_history.csv             the corpus every evidence id must resolve into
├── message_events.csv              the receiver's own past interactions
├── images.csv  voice_notes.csv     media manifests
├── daily_notification_summary.csv  per-user baseline load only
└── media/images/  media/audio/     the bytes
```

The dataset is organiser-supplied and is **not** in the archive. `output.csv` is written back into it by default.

Shipped inside the archive, and the reason a reviewer needs no credentials:

```
code/.cache/perception/   508 entries — the model's recorded answers
code/.cache/media/         33 entries — image descriptions and voice transcripts
```

---

## 5. Reproducing the submitted `output.csv` with zero network calls

The property most worth checking, and it needs no keys.

```bash
unzip code.zip -d /tmp/verify && cd /tmp/verify/code
python -m venv .venv && source .venv/bin/activate
pip install -e .
python main.py --dataset /path/to/dataset route --out /tmp/verify/output.csv
diff /tmp/verify/output.csv /path/to/dataset/output.csv && echo byte-identical
```

No `--allow-network`, no credentials, no calls. Every row is served from `.cache/`, which is why it ships. The run's own report shows `cache media 23/23   perception 110/110   fully-cached rows 110` and `budget 0 in / 0 out tokens`.

Two consecutive routes are byte-identical to each other; `python -m metrics assert-identical` is the gate that says so.

**What this proves and what it does not.** It proves the pipeline is deterministic given fixed model answers. It does **not** prove the model's answers are stable. That was measured separately, by building a second cache from cold against the live APIs and diffing: **97.3% action stability, 81/110 rows identical across all six columns**, all rules in the table at the time (15; `RISK_TYPE_CAP` was added after) still firing and all 8 ablations still holding — with the labeled set moving by ±1 row from re-derivation alone. Recorded in [EVALUATION_STRATEGY §6a](docs/EVALUATION_STRATEGY.md#6a-reproducibility-under-independent-re-derivation--measured), and it is why no claim in this repository rests on a one-row difference.

**On packaging.** `pyproject.toml` is canonical and there is no `requirements.txt`: two dependency manifests are two sources of truth that drift, and `pyproject.toml` already has to exist to declare the package layout, the pytest configuration, and the ruff configuration. `python make_zip.py` builds the archive and refuses to finish if the cache is missing, if an excluded path leaked, or if a **content** scan over every member matches any of eight credential shapes. Each of those eight is exercised against a planted example in `tests/test_packaging.py` — a scan that has never been shown to fail is not evidence.

---

## 6. The design documents

Fourteen documents under `docs/`. **New to the project? Start with [CODE_TOUR.md](docs/CODE_TOUR.md)** — it explains the problem, the architecture, and what every file does, assuming no prior context. Then these three:

1. **[SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)** — the architecture and the perception→policy contract. Start here.
2. **[DECISION_ENGINE.md](docs/DECISION_ENGINE.md)** — all 16 rules: precedence, trigger, what each reads, and why.
3. **[PROBLEM_ANALYSIS.md](docs/PROBLEM_ANALYSIS.md)** — the decision log D1–D21 and the two specification contradictions, each with the alternative rejected.

| document | what it is |
|---|---|
| [SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | Architecture, module boundaries, and the perception→policy contract |
| [DECISION_ENGINE.md](docs/DECISION_ENGINE.md) | The 16-rule precedence table: trigger, effect, `reads` set, justification |
| [PROBLEM_ANALYSIS.md](docs/PROBLEM_ANALYSIS.md) | Decision log D1–D21, contradictions C1–C2, and what each rejected alternative would have cost |
| [DATASET_ANALYSIS.md](docs/DATASET_ANALYSIS.md) | What is actually in the corpus, and what it cannot support |
| [GENERALIZATION_POLICY.md](docs/GENERALIZATION_POLICY.md) | G1–G6: the rules against fitting to this corpus, including numeric provenance |
| [EVALUATION_STRATEGY.md](docs/EVALUATION_STRATEGY.md) | Merge gates, the anti-overfitting protocol, and the measured noise floor |
| [METRICS.md](docs/METRICS.md) | Every metric's definition, the run-directory schema, and the calibration caveat |
| [THREAT_MODEL.md](docs/THREAT_MODEL.md) | T1–T7: prompt injection and untrusted input, and what closes each |
| [FAILURE_MODES.md](docs/FAILURE_MODES.md) | What degrades, how, and what the row says when it does |
| [CACHING.md](docs/CACHING.md) | Content-addressed keys, the never-invalidate invariant, and its one exception |
| [ENGINEERING_CONVENTIONS.md](docs/ENGINEERING_CONVENTIONS.md) | Code conventions, the error taxonomy, and what a comment is for |
| [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Phases 0–6, each with its verification and rollback |
| [CODE_TOUR.md](docs/CODE_TOUR.md) | **Start here if the project is new to you** — the problem, the architecture, and a walk through every file |
| [INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md) | Every routing constant with its justification, the condensed decision log, the measured facts, and the defect table |

---

## 7. Directory map

| path | what lives there |
|---|---|
| `ingest/` | CSV loading and per-message context assembly |
| `media/` | Image and voice extraction, container detection from magic bytes |
| `perception/` | Prompt construction, fencing, response schema, the model client and its cache |
| `features/` | Derived facts: engagement stance, business domain state, group role, content, integrity |
| `policy/` | The 16 rules and the precedence engine that applies them |
| `scoring/` | Evidence selection and the confidence formula |
| `compose/` | The reason clause grammar and the register check |
| `emit/` | The per-row record, the submission CSV writer, and contract validation |
| `metrics/` | Label-free and labeled metrics, run diff, consistency scan, console report |
| `cache/` | The content-addressed store shared by media and perception |
| `docs/` | The fourteen documents above |
| `tests/` | 337 tests: contract, architecture, threat model, ablations, packaging |
| `config.py` | Every tunable constant, each with its provenance recorded inline |
| `pipeline.py` | The end-to-end run |
| `main.py` | CLI entry point |
| `make_zip.py` | Archive build, content secret scan, and the required-member check |

---

## 8. Limitations

Stated plainly, with the measurement behind each.

**Digest is under-served, and the cause sits in the model's proposal distribution rather than in the rules.** The system emits `notify` 27.3% / `digest` 24.6% / `mute` 48.2%. Four of the five labeled action errors are rows the model proposed as `digest` where truth was `notify` or `mute` — it read them correctly and drew the priority line elsewhere. The boundary was already moved twice by sending resolved definitions to the model rather than by tuning a threshold (`PROMPT_TEMPLATE_VERSION` 1.2.0 for the eleven type definitions, 1.3.0 for C1's action definitions). What remains is a judgement call, and shifting it further without a labeled signal would be fitting to 30 rows.

**Type accuracy is 80.0% but macro F1 is 0.63**, and the gap is structural rather than cosmetic. Six of the eleven types have one or two labeled examples, so per-class F1 on them is noise wearing a decimal point. More importantly, five Tier-0 safety rules *set* `message_type` to `scam` via `sets_type`, which makes type partly a function of the action rule rather than an independent judgement — accuracy rewards the majority classes, macro F1 does not. See [D20](docs/PROBLEM_ANALYSIS.md#d20--whether-a-safety-rule-should-own-the-message_type).

**Two labeled errors are owned by the policy layer and need no model change to fix** ([D21](docs/PROBLEM_ANALYSIS.md#d21--layer-attribution-of-the-labeled-errors-and-why-the-model-was-not-escalated)). In `sample_msg_043`, `IMPERSONATION.sets_type` wrote `scam` over a truth of `spam`. In `sample_msg_049`, `COLD_SENDER_CAP` fired carrying exactly the truth action and was suppressed by `DIRECT_ADDRESS_GUARD`. Both are worth one row each — below the measured two-row floor — and both are left alone rather than changed late on a signal indistinguishable from noise.

**Confidence is narrow.** 23 of 30 labeled rows sit in a single bucket, so ECE 0.119 means the predictor is not *mis*-calibrated, not that it is informative; Brier 0.139 is the more honest number. Widening the spread is the correct next step for a build with more time, and the reasoning for stopping here is recorded in [METRICS.md](docs/METRICS.md).

---

Built for the HackerRank Orchestrate 24-hour challenge, August 2026.
