# CODE_TOUR.md — what this project is, and what every file in it does

**Read this if you have never seen the project before.** It assumes no prior context. It explains the problem, the shape of the solution, then walks every directory and every file, in the order data actually flows through them.

The [README](../README.md) is the operator's guide — how to install and run. This is the reader's guide — what exists and why.

**Contents**

1. [The problem in plain terms](#1-the-problem-in-plain-terms)
2. [The data you are given](#2-the-data-you-are-given)
3. [The core idea: perception vs. policy](#3-the-core-idea-perception-vs-policy)
4. [The life of one message, stage by stage](#4-the-life-of-one-message-stage-by-stage)
5. [Directory map](#5-directory-map)
6. [Every file, explained](#6-every-file-explained)
7. [The 16 rules](#7-the-16-rules)
8. [Where the outputs come from](#8-where-the-outputs-come-from)
9. [The caches, and why the project ships one](#9-the-caches-and-why-the-project-ships-one)
10. [Tests: what each file guards](#10-tests-what-each-file-guards)
11. [The documents](#11-the-documents)
12. [Recurring ideas you will see everywhere](#12-recurring-ideas-you-will-see-everywhere)
13. [If you only read five files](#13-if-you-only-read-five-files)

---

## 1. The problem in plain terms

A WhatsApp user gets far more messages than deserve a buzz in their pocket. This system decides, for each incoming message, which of three things should happen:

| action | meaning |
|---|---|
| **`notify`** | interrupt the user now |
| **`digest`** | safe but low priority — show it later, in a batch |
| **`mute`** | suppress it: low-value, repetitive, unwanted, suspicious, or unsafe |

The decision must be **personalised**. The same message can deserve different treatment for different people: a promotion is `digest` for someone who shops there and `mute` for someone who opted out of marketing; a group message is `notify` if it names you and `mute` if you muted that group and it doesn't.

Messages come in three modalities — plain text, **images** (posters, screenshots), and **voice notes** — so the system has to read all three.

For every one of the **110 target messages**, it emits one row:

```
message_id,action,message_type,reason,confidence,evidence_message_ids
```

- **`message_type`** — one of eleven labels: `personal`, `urgent`, `event`, `payment`, `business_update`, `promotion`, `greeting`, `forward`, `spam`, `scam`, `unknown`.
- **`reason`** — one human sentence explaining the decision.
- **`confidence`** — a number in [0, 1].
- **`evidence_message_ids`** — historical message ids supporting the decision, `;`-separated, or the literal `none`.

There are **30 labeled example rows** (`sample_messages.csv`). They are used for *format and style* — the register of the reason sentence, the separator, the column order — and as a **held-out check** on accuracy. They are never a tuning target. That distinction is enforced throughout and is the single most consequential decision in the project ([D16](PROBLEM_ANALYSIS.md), [GENERALIZATION_POLICY](GENERALIZATION_POLICY.md)).

---

## 2. The data you are given

Thirteen CSVs plus the media bytes, in `dataset/` (a sibling of `code/`, not shipped in the archive — it is organiser-supplied).

| file | rows | what it is |
|---|---|---|
| `messages.csv` | **110** | the target messages — one output row each |
| `sample_messages.csv` | 30 | labeled examples: format authoritative, labels held out |
| `users.csv` | 54 | the receivers: DND window, 30-day open/reply/dismiss/report counts |
| `groups.csv` | 23 | group name, type, member count, admin count, activity |
| `group_members.csv` | 401 | who is in which group, their role, and their per-group activity |
| `business_accounts.csv` | 110 | business senders: display name, brand name, **verified**, official domain, domain actually used |
| `user_business_history.csv` | 106 | this user's relationship with this business, incl. `promotions_opted_out_at` |
| `message_history.csv` | 412 | the past-messages corpus — every `evidence_message_ids` value must resolve here |
| `message_events.csv` | 412 | what the receiver *did* with each past message: opened, replied, dismissed, muted-after, reported |
| `daily_notification_summary.csv` | 756 | per-user per-day notification load (used only as a stable baseline) |
| `images.csv` | 20 | image id → file path |
| `voice_notes.csv` | 13 | voice-note id → file path |
| `output.csv` | 110 | **the deliverable** — written by this program |
| `media/images/` | 20 files | the image bytes |
| `media/audio/` | 13 files | the audio bytes |

Two facts about this data drive real design decisions:

- **`message_events.csv` is the personalisation signal.** It is what makes "this user ignores this sender" a fact rather than a guess.
- **19 of the 33 media files have a misleading extension.** All 13 voice notes are named `.mp3` but 5 are WAV and 4 are AAC/M4A; of the 20 `.jpg` images, 7 are PNG, 2 are WebP, and one is an MP4 video. A decoder that trusted filenames would fail on more than half the media — which is why the system sniffs magic bytes and never looks at the extension.

---

## 3. The core idea: perception vs. policy

This is the one thing to understand. Everything else follows from it.

The obvious approach is to hand the whole problem to a language model: give it the message and the context, ask for the action. This project deliberately does **not** do that.

Instead the work is split in two:

```
                    ┌─────────────────────────────┐
   message  ───────▶│   PERCEPTION  (the model)   │
   + context        │                             │
                    │  reads the message and      │
                    │  reports what it SEES       │
                    └──────────────┬──────────────┘
                                   │
                    proposed_action, type_candidates,
                    14 boolean claims about content
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │   POLICY  (deterministic)   │
                    │                             │
                    │  16 rules in fixed          │
                    │  precedence DECIDE          │
                    └──────────────┬──────────────┘
                                   │
                    action, type, reason, confidence, evidence
```

The model produces a **structured perception record** — a proposal and a set of observations — and never the final answer. Deterministic Python owns the action, the confidence, the reason sentence, and the evidence selection.

### Why this is worth the extra machinery

**1. Rules the code cannot express the wrong way.** `IMPERSONATION` is a pure function of `(business.verified, domain_state, brand_name)`. The message text is *not a parameter*. No phrasing inside a message can influence whether it fires, because the message is not in scope. Compare the prompt-only version — "mute messages from businesses impersonating a brand" — evaluated by a model that has just read attacker-controlled text, where every instruction in that text is a candidate input. The guarantee here is **structural, not behavioural**: it does not rely on the model resisting anything, and it does not weaken as the attack gets better written.

**2. Every decision is attributable.** Each row records which rule determined it (`override_code_applied`) and every rule that fired and lost, with a status (`applied`, `superseded`, `overridden_by_cap`, `suppressed_by_guard`, `fired_not_determining`, `did_not_fire`, `not_evaluated`). When a row is wrong you can tell whether the model misread it or the policy mishandled a correct read. That analysis was actually done, for all nine wrong labeled rows — see [D21](PROBLEM_ANALYSIS.md).

**3. Iteration is cheap.** Model answers are cached. Changing a rule threshold re-runs the whole corpus in under a second with zero API calls, because the policy configuration is deliberately excluded from the cache key.

**4. It is reproducible.** Given the same cached perception, the pipeline is byte-deterministic. Run it twice, get identical files.

### What it costs

The model's proposal is only a proposal, so when the model is right and a rule is wrong, the rule wins. That happened: two rows the model correctly typed `scam` and proposed muting were raised to `notify` by priority floors, because nothing in the precedence consulted the type. Fixed by adding a rule ([D22](PROBLEM_ANALYSIS.md)) — but it is the characteristic failure mode of this design and worth knowing about.

---

## 4. The life of one message, stage by stage

This is `pipeline.route_one()`, the only function that composes modules. Every stage is pure except the two marked.

```
 1. extract media          media/extract.py      ← cache, network only if cold
 2. build context          ingest/context.py
 3. perceive               perception/cached.py  ← cache, network only if cold
 4. build features         features/assemble.py
 5. decide                 policy/engine.py
 6. select evidence        scoring/evidence.py
 7. score confidence       scoring/confidence.py
 8. derive threat events   features/integrity.py
 9. compose reason         compose/reason.py
10. build the OutputRow    pipeline.py
```

Walking it with a concrete message — a group message with an image attached:

**1 — Extract media.** `media/extract.py` resolves the image id to a path *confined to the dataset root* (a `..` or a symlink pointing outside raises), sniffs the container from magic bytes, checks size and pixel limits, then either finds a cached description keyed by the **sha256 of the file's bytes** or calls the vision model. Result: a `MediaArtifact` that is either readable with content, or unreadable with a **named cause** (`duration_exceeded`, `unsupported_container`, …). It never guesses.

**2 — Build context.** `ingest/context.py` gathers everything about this one message into a `MessageContext`: the message, the receiving user's profile, the group and both memberships (sender's and receiver's), the business profile and the user's relationship with it, the receiver's prior messages with this counterparty, their recorded interactions with those messages, and the media artifact.

**3 — Perceive.** `perception/cached.py` builds the prompt, checks the cache, and calls the model only on a miss. The prompt puts every piece of untrusted content — message text, image description, voice transcript — inside blocks fenced with a **random per-run nonce**, and the system prompt says content inside a fence is data, never instructions. The model returns a proposed action, ranked `type_candidates`, and 14 boolean claims.

**4 — Build features.** `features/assemble.py` derives structural facts *without any model*: is the sender a group admin, has the receiver muted this group, does the business's sending domain match its official one, is the relationship established, what is the receiver's engagement stance toward this sender, was the media unreadable.

**5 — Decide.** `policy/engine.py` applies the 16 rules in fixed precedence: guards mark suppressions, then hard overrides (first to fire wins and returns immediately), then floors raise the action and caps lower it, with the model's proposal as the default. Exactly one firing ends with status `APPLIED` — enforced, not assumed.

**6 — Select evidence.** `scoring/evidence.py` ranks the receiver's past messages by tier (same counterparty *and* shared content > same counterparty > shared content only), then by shared-token count, then recency, then id. It cites the *minimum* supporting the claim the reason will make: one id normally, two when the reason asserts a repeated pattern.

**7 — Score confidence.** `scoring/confidence.py` sums ten named weights — a base, a trigger term, an evidence term, engagement alignment, a media-unread penalty, type ambiguity, rule contention — and clamps to [0.35, 0.95].

**8 — Derive threat events.** `features/integrity.py` computes the row's integrity warnings *independently of which rules fired*. This ordering is deliberate: it runs before composition because the reason must be able to name the warning.

**9 — Compose the reason.** `compose/reason.py` builds one sentence from a clause grammar, then enforces the register: one sentence, 55–120 characters, no identifiers. A violation **raises** — a reason is never silently truncated, because a trimmed reason is a wrong explanation.

**10 — Build the row.** `pipeline.py` assembles the `OutputRow` and returns it along with the perception, features, evidence shortlist and telemetry, so the emit layer *projects* and never recomputes.

---

## 5. Directory map

```
code/
├── main.py            CLI entry point — the only place argv is parsed
├── pipeline.py        composes the stages for one message and for a run
├── config.py          every tunable constant, each with its justification
├── schemas.py         all enums and pydantic models (a leaf: imports config only)
├── errors.py          the typed exception hierarchy
├── logging_setup.py   structured JSON-Lines logging
├── make_zip.py        builds the submission archive + secret scan
├── pyproject.toml     the ONLY dependency manifest
│
├── ingest/            CSVs → validated objects → per-message context
├── media/             images and voice notes → text
├── perception/        the model boundary: prompt, transport, parse, cache
├── features/          context → derived structural facts (no model)
├── policy/            the 16 rules and the engine that applies them
├── scoring/           evidence selection and the confidence formula
├── compose/           the reason sentence and its register
├── emit/              output.csv, the per-row record, the manifest, validation
├── metrics/           post-hoc analysis: report, diff, consistency
├── cache/             the content-addressed store shared by media and perception
├── docs/              14 design documents
├── tests/             337 tests
└── .cache/            541 shipped cache entries — why the archive runs offline
```

A note on layout: **`code/` is a source root, not a package.** Imports read `from policy.engine import decide`. There is deliberately no `code/__init__.py`, because that would make this directory shadow the standard-library `code` module whenever the parent is on `sys.path`, which breaks `pdb` and therefore `pytest` on Python 3.14.

---

## 6. Every file, explained

### Top level

| file | loc | what it does |
|---|---|---|
| **`main.py`** | 508 | The CLI. Five subcommands: `route`, `warm-media`, `warm-perception`, `validate`, `bank`. Also holds `load_dotenv()`, which reads `code/.env` at start-up — it **never overrides a real export and never prints a value**. Contains preflight checks that refuse to start a run whose cache coverage is incomplete, rather than silently routing rows with no content. |
| **`pipeline.py`** | 223 | `route_one()` composes the ten stages above for one message; `run()` loops the corpus, catches per-row failures without aborting the run, and writes the run directory. Defines `PerceptionClient` as a single-method `Protocol` so tests inject a stub and touch no network. |
| **`config.py`** | 520 | Ten frozen dataclasses holding **every** threshold, weight, cap and limit. Each constant carries its justification as an inline docstring tagged with a provenance source: **(a)** stated in the problem statement, **(b)** derived from a principle or an external published fact, **(c)** observed in this corpus — *prohibited*. Nothing is tunable from the command line: a threshold changeable at the call site is a threshold with no single recorded justification. Also `load_model_id()` and `DEFAULT_MODEL_ID`. |
| **`schemas.py`** | 617 | 18 enums and 24 pydantic models — every typed value in the system. Deliberately a **leaf**: it imports `config` and nothing else, so no cycle can form. Key types: `IncomingMessage`, `MessageContext`, `Perception`, `FeatureVector`, `Decision`, `RuleFiring`, `EvidenceSelection`, `OutputRow`, `RoutingResult`. |
| **`errors.py`** | 95 | 17 exception types, all descending from `RouterError`, so a caller can catch the family. The taxonomy is meaningful: `ProviderRateLimitedError` is retryable, `ProviderCredentialsRejectedError` is not, `EvidenceIntegrityError` means a citation broke an invariant and should never be swallowed. |
| **`logging_setup.py`** | 81 | JSON-Lines structured logging to `events.jsonl`. `content_digest()` logs a hash of untrusted content rather than the content itself. |
| **`make_zip.py`** | 236 | Builds `code.zip`. Excludes venvs, caches, the dataset, run directories and `.env`; **includes `.cache/`**, without which a reviewer without credentials could not run anything. `scan_for_secrets()` reads the **bytes of every archived member** against 8 credential patterns — an earlier version checked only the file list, which proved nothing. |

### `ingest/` — CSVs in, typed context out

| file | loc | what it does |
|---|---|---|
| **`loader.py`** | 468 | Reads and validates all 13 CSVs and builds the lookup indexes (by message id, user id, group id, business id, and the composite keys). Validation is strict: a malformed row raises `DatasetError` rather than being skipped. Returns one `Dataset` object holding everything. |
| **`context.py`** | 71 | `build_context()` — given the dataset and one message id, assemble the `MessageContext`: message, user, group, both memberships, business, business relationship, prior messages, prior interactions, media artifact. This is the only place the "what do we know about this message" question is answered. |

### `media/` — images and voice notes become text

| file | loc | what it does |
|---|---|---|
| **`locate.py`** | 31 | `resolve_media_path()` — turns a media id into a real path and **confines it to the dataset root**. A path escaping the root, or a symlink pointing outside it, raises `MediaPathError`. Small file, load-bearing: this is threat mitigation M4. |
| **`decode.py`** | 243 | `sniff_container()` identifies the format from **magic bytes, never the filename**. Also image dimensions and, if `ffprobe` is present, audio duration — and if it is absent, duration is reported as `None` rather than guessed. |
| **`extract.py`** | 359 | Turns a message's attachment into a `MediaArtifact`. Checks the size and pixel ceilings *before* decoding, applies the duration pre-check that names `duration_exceeded` **before any request is sent**, consults the cache, and calls the provider only on a miss. Every failure carries a **named cause** that survives into the row's reason text. `warm_one()` is the idempotent path used by `warm-media`. |

### `perception/` — the model boundary

| file | loc | what it does |
|---|---|---|
| **`client.py`** | 517 | **The single network boundary. No other module may make a network call**, and `test_no_network_outside_the_client` asserts it. Holds the Anthropic client (image description, the routing call) and the Sarvam client (voice transcription via `saaras:v3` in `translit` mode). Both SDKs are imported **lazily** so the package stays importable and the whole test suite runs with neither installed. Credential helpers never print or return a key. |
| **`prompt.py`** | 331 | Builds the prompt. `new_nonce()` generates a per-run random token; `fence()` wraps untrusted content in it; `sanitise()` strips control characters and caps length. Holds `RESPONSE_SCHEMA`, the eleven type definitions, the three action definitions, and the 14 boolean claims with their precise wording. `PROMPT_TEMPLATE_VERSION` (currently `1.3.0`) is part of the perception cache key, so re-wording a claim cannot silently reuse answers given to the old wording. |
| **`parse.py`** | 63 | `to_perception()` — validates a raw reply into a `Perception`. **Strict, never lenient.** The schema does not even offer fields the model may not set (`action_final`, `override_code`, `confidence`), and the parser rejects them if they appear anyway. |
| **`cached.py`** | 247 | `CachedPerceptionClient` — the object the pipeline actually holds. Checks the cache, calls the transport on a miss, records telemetry. The cache key covers the exact bytes sent (nonce-normalised, so a fresh nonce is not a false miss) but **deliberately excludes `PolicyConfig`**, which is what makes policy iteration free. |

### `features/` — derived facts, no model involved

Everything here is a pure function of the context. This is where "what do we structurally know" is computed, separately from "what did the model see".

| file | loc | what it does |
|---|---|---|
| **`assemble.py`** | 61 | `build_features()` composes all of the below into one `FeatureVector`. Also decides `sender_holds_payment_authority`, mapping the statement's own poles — "legitimate from a trusted admin but risky from a new sender" — onto recorded facts. |
| **`engagement.py`** | 74 | Converts interaction events into a bounded engagement score, a dismissal ratio, and an `EngagementStance` (`RECEPTIVE` / `NEUTRAL` / `DISMISSIVE` / `UNKNOWN`). Below `MIN_STANCE_OBSERVATIONS` the stance is `UNKNOWN` rather than asserted from a single interaction. |
| **`sender.py`** | 29 | The receiver's prior interactions with this sender, and whether the relationship counts as established. |
| **`business.py`** | 52 | `domain_state()` returns a **three-valued** `DomainState`: `MATCH`, `MISMATCH`, or `UNDETERMINED`. The third value is load-bearing — absence of evidence is not evidence of impersonation, and an ablation test proves the distinction changes behaviour. Also identity consistency and promotion opt-out. |
| **`group.py`** | 15 | Is the sender an admin; has the receiver muted this group. |
| **`content.py`** | 34 | Forward-chain length, frequently-forwarded, media failed, and `media_unread` (media failed **and** there is no text — i.e. nothing was read at all). |
| **`integrity.py`** | 77 | `threat_events()` derives the row's integrity warnings from perception claims, features, media outcome and telemetry — **independently of which rules fired**. An earlier version tied warnings to rule fires, and the row-level field was hardcoded empty, so "0 unexplained warnings" was arithmetically guaranteed while a rule had fired 7 times with nothing saying so. |

### `policy/` — the decision

| file | loc | what it does |
|---|---|---|
| **`rules.py`** | 504 | The rule table: one `Rule` per policy rule, in precedence order. A `Rule` is **data, not behaviour** beyond its trigger predicate — it declares its `effect`, `trigger_source`, `justification` text, the `reads` set naming its inputs, what action and type it sets, and what it suppresses. The `reads` set is what the ablation tests check against. |
| **`engine.py`** | 169 | `decide()` applies the table: guards → hard overrides (first wins, returns) → floors raise → caps lower. Caps beat floors, because a cap encodes the *user's expressed preference* and a floor encodes the *system's judgement of importance*. Enforces exactly one `APPLIED` firing, and holds a post-condition that raises if any decision ever resolves to `notify` with a `scam`/`spam` type. |

### `scoring/` — evidence and confidence

| file | loc | what it does |
|---|---|---|
| **`evidence.py`** | 156 | Deterministic retrieval — **no vector index, just a sort over a small list**. Tiers candidates (`RELATIONSHIP_AND_CONTENT` > `RELATIONSHIP` > `TOPICAL`), then ranks by shared content tokens, recency, and id for a total order. Enforces integrity: evidence belonging to another user **raises** rather than being quietly filtered, and evidence dated after the target message is never cited. |
| **`confidence.py`** | 98 | `score_confidence()` — ten named terms from `ConfidenceConfig`, summed and clamped. Every term is traceable to a named feature; nothing is fitted. |

### `compose/` — the sentence

| file | loc | what it does |
|---|---|---|
| **`reason.py`** | 374 | Builds the one-sentence reason from a **clause grammar** over (message_type, action, dominant_feature), or from the applied rule's own justification. Enforces the register and raises `ReasonRegisterError` on violation. Also holds the three predicates that *measure* the sentence — `cites_evidence`, `references_integrity`, `names_decisive_factor` — which read **the composed text**, not rule metadata. (Reading metadata was a real defect, twice.) |

### `emit/` — the deliverable

| file | loc | what it does |
|---|---|---|
| **`writer.py`** | 142 | `write_output()` projects `OutputRow` to the six contract columns in exact order, preserving `messages.csv` row order. `validate_submission()` is the contract checker behind `main.py validate`. |
| **`record.py`** | 255 | Projects a `RoutingResult` into the rich per-row record (`rows.jsonl`) — every field the metrics engine later reads, **computed once here and never recomputed downstream**. Also derives `sender_relationship_class` and `integrity_state`, writes the eval truth file, and builds the run manifest with the config hash, dataset fingerprint, git commit, prompt version and media extraction version. |

### `metrics/` — post-hoc analysis

The metrics engine is **strictly post-hoc**: it reads a run directory and never imports the pipeline. A dedicated test enforces that, so metrics can never accidentally influence routing.

| file | loc | what it does |
|---|---|---|
| **`loader.py`** | 113 | Reads a run directory into typed frames. The only I/O module in the engine. `unavailable()` handles older run schemas gracefully rather than crashing. |
| **`label_free.py`** | 269 | Metrics over all 110 target rows — no labels needed: action and type distributions with entropy, override counts, evidence coverage and tiers, integrity warnings, reason health, cache coverage, budget. |
| **`labeled.py`** | 204 | Metrics over the 30 labeled rows only: confusion matrix, per-class precision/recall/F1, macro F1, joint accuracy, evidence overlap, ECE and Brier. |
| **`consistency.py`** | 99 | Groups rows by a composite key (conversation type, relationship class, proposed type, integrity state) and flags groups whose actions diverge **without a rule explaining the divergence**. Catches "these two rows look the same and got different answers". |
| **`diff.py`** | 232 | Compares two runs: gross churn, net labeled change, and the **churn-to-net ratio** — many rows changed for no net gain means the change reshuffled behaviour rather than improving it. `assert_runs_identical()` is the determinism gate. |
| **`report.py`** | 355 | Assembles `report.json` and renders the console report. |
| **`__main__.py`** | 66 | The `python -m metrics` CLI: `report`, `diff`, `assert-identical`. |

### `cache/`

| file | loc | what it does |
|---|---|---|
| **`store.py`** | 269 | The **only** module that reads or writes the cache directory — enforced by a test. Content-addressed: `media_key()` is the sha256 of the file's bytes plus the extraction version plus a per-kind `extractor` sub-object; `perception_key()` covers the exact prompt bytes, nonce-normalised. Entries are **never invalidated**, because the key covers everything that could change the answer. |

---

## 7. The 16 rules

Evaluated in this order. Full specifications with triggers and rationale in [DECISION_ENGINE.md](DECISION_ENGINE.md).

| # | rule | effect | fires when | sets |
|---|---|---|---|---|
| 1 | `CREDENTIAL_SOLICITATION` | hard override | the message asks for an OTP, PIN or password | mute + `scam` |
| 2 | `ROUTER_DIRECTIVE` | hard override | the message contains instructions aimed at the router | mute + `scam` |
| 3 | `IMPERSONATION` | hard override | unverified business using a domain the brand does not own | mute + `scam` |
| 4 | `COERCIVE_PAYMENT` | hard override | payment demand + threatened consequence + immediate deadline | mute + `scam` |
| 5 | `UNSAFE_INSTRUCTION` | hard override | advice with a plausible health or safety consequence | mute (**not** `scam` — the sender may be a well-meaning relative) |
| 6 | `UNAUTHORIZED_PAYMENT_REQUEST` | hard override | payment instruction from a sender with no payment authority here | mute + `scam` |
| 7 | `DIRECT_ADDRESS_GUARD` | guard | the message addresses this user directly | suppresses the two conversation-level caps |
| 8 | `RISK_TYPE_CAP` | cap | the emitted type is `scam` or `spam` | mute — **not suppressible by any guard** |
| 9 | `PROMOTION_OPT_OUT` | cap | marketing to a user who opted out | mute |
| 10 | `REPEAT_DISMISSAL` | cap | comparable messages from this sender were repeatedly dismissed | mute |
| 11 | `FREQUENTLY_FORWARDED_BROADCAST` | cap | widely circulated content not written for this user | mute |
| 12 | `GROUP_MUTE_CAP` | cap | the user muted this conversation | digest |
| 13 | `COLD_SENDER_CAP` | cap | no established relationship with this sender | digest |
| 14 | `TIME_CRITICAL_FLOOR` | floor | same-day deadline with a stated consequence, from an engaged sender | notify |
| 15 | `DIRECT_REQUEST_FLOOR` | floor | the sender asks this user directly for a response or action | notify |
| 16 | `PERCEPTION_ACCEPTED` | default | nothing above determined the row | the model's proposal |

**Reading the precedence.** Hard overrides return immediately — a credential request is muted regardless of everything else, including a direct address. Guards only mark suppressions. Then floors raise and caps lower, and **caps beat floors**. Rule 8 exists because they did not always: two rows the model had typed `scam` *and proposed muting* were raised to `notify` by rules 14 and 15, since a manufactured deadline and a direct request are exactly what a scam produces. That was a coherence defect in the precedence, not a tuning question ([D22](PROBLEM_ANALYSIS.md)).

---

## 8. Where the outputs come from

| column | produced by | how |
|---|---|---|
| `message_id` | `emit/writer.py` | copied through, in `messages.csv` order |
| `action` | `policy/engine.py` | the rule table — never the model directly |
| `message_type` | `policy/engine.py` | the model's top type candidate, unless a Tier 0 rule sets `scam` |
| `reason` | `compose/reason.py` | clause grammar or the applied rule's justification; register enforced |
| `confidence` | `scoring/confidence.py` | ten named weights, summed, clamped to [0.35, 0.95] |
| `evidence_message_ids` | `scoring/evidence.py` | tiered ranking over the receiver's history; `none` when nothing is defensible |

Alongside `output.csv`, each run writes a directory containing `rows.jsonl` (the rich per-row record), `eval_rows.jsonl`, `eval_truth.csv`, `events.jsonl` (the structured log) and `manifest.json` (config hash, dataset fingerprint, git commit, prompt and extraction versions).

---

## 9. The caches, and why the project ships one

Two caches under `code/.cache/`:

- **`media/`** — 33 entries: image descriptions and voice transcripts, keyed by the sha256 of the file's bytes.
- **`perception/`** — 508 entries: the model's routing answers, keyed by the exact prompt bytes.

Both are **content-addressed and never invalidated**, because the key covers everything that could change the answer — file bytes, extraction version, extractor settings, prompt template version, model id.

**They ship inside `code.zip`**, which is unusual for a cache and is the point: it means anyone can run `python main.py route` with **no API keys and no network**, and reproduce the submitted `output.csv` byte for byte. Without them the archive would be unrunnable for a reviewer without credentials.

One subtlety worth knowing, because it caused a real failure: the model id is part of the perception cache key (a cached answer must be attributable to a model), and the archive ships no `.env`. With `ROUTER_MODEL_ID` unset, every row got a different key and missed. Hence `config.DEFAULT_MODEL_ID` — a public model name, deliberately kept separate from the three credential variables, which have no defaults and never will.

---

## 10. Tests: what each file guards

**337 tests collected — 334 pass, 3 skip** (the ablations skip cleanly when no dataset is present, which is why the archive can exclude it). Several exist specifically to make other checks *capable of failing*.

| file | tests | what it guards |
|---|---|---|
| **`test_policy_table.py`** | 106 | The full rule table and the six conflict cases. Every case is built from **typed values, never a dataset row** — a table-driven test that read the corpus would be pinned to this corpus. |
| **`test_threat_model.py`** | 23 | One test per mitigation in THREAT_MODEL: fence integrity, extraction prompts carrying no routing vocabulary, free-text fields never entering a prompt, the parser rejecting forbidden fields, path and symlink escapes, evidence integrity. The strongest is the one asserting a **totally compromised model cannot escalate anything**. |
| **`test_ablations.py`** | 8 | Degrades an input a rule claims to read and requires the rule to stop firing. **A null result is a defect, not a pass.** Includes a baseline guard (a rule that never fires cannot be shown to stop) and a **control** — renaming a group must change *nothing*, which catches a harness bug that would make every other ablation "pass" for the wrong reason. |
| **`test_media_and_cache.py`** | 51 | Container sniffing against the real misleading extensions, the cache substrate, and the extract contract. |
| **`test_perception.py`** | 47 | Prompt construction, strict parsing, and cache-key behaviour. |
| **`test_pipeline_units.py`** | 33 | The pipeline modules, table-driven, no network. |
| **`test_metrics.py`** | 22 | The metrics engine against a fixture run directory — no pipeline execution. |
| **`test_metrics_isolation.py`** | 4 | The metrics engine is post-hoc and cannot import the pipeline. |
| **`test_cache_isolation.py`** | 3 | Only `cache/store.py` touches the cache directory. |
| **`test_architecture.py`** | 9 | Import-graph gates: `schemas` is a leaf, only `perception/client.py` makes network calls, no module reads the environment except the two that may. |
| **`test_contract.py`** | 14 | The submission contract and loader/writer round-trips. |
| **`test_packaging.py`** | 17 | The archive ships the cache and excludes `.env`; and **every secret pattern is exercised against a planted example**, because a scan that has never been shown to fail is not evidence. |

---

## 11. The documents

Fourteen under `docs/`. If you read three, read `SYSTEM_DESIGN`, `DECISION_ENGINE`, `PROBLEM_ANALYSIS`.

| document | what it is |
|---|---|
| `SYSTEM_DESIGN.md` | Architecture, module boundaries, the perception→policy contract |
| `DECISION_ENGINE.md` | All 16 rules: precedence, trigger, `reads` set, justification |
| `PROBLEM_ANALYSIS.md` | The decision log D1–D22 and contradictions C1–C2, each with the alternative rejected |
| `DATASET_ANALYSIS.md` | What is actually in the corpus, and what it cannot support |
| `GENERALIZATION_POLICY.md` | G1–G6 — the rules against fitting to this corpus, including numeric provenance |
| `EVALUATION_STRATEGY.md` | Merge gates, the anti-overfitting protocol, the measured noise floor |
| `METRICS.md` | Every metric's definition and the calibration caveat |
| `THREAT_MODEL.md` | T1–T7: prompt injection and untrusted input, and what closes each |
| `FAILURE_MODES.md` | What degrades, how, and what the row says when it does |
| `CACHING.md` | Content-addressed keys and the never-invalidate invariant |
| `ENGINEERING_CONVENTIONS.md` | Code conventions, error taxonomy, what a comment is for |
| `IMPLEMENTATION_PLAN.md` | Phases 0–6, each with verification and rollback |
| `INTERVIEW_PREP.md` | Constants with justifications, condensed decision log, measured facts, defect table |
| `CODE_TOUR.md` | This file |

---

## 12. Recurring ideas you will see everywhere

**Numeric provenance.** Every constant is tagged **(a)** stated in the problem statement, **(b)** derived from a principle or published fact, or **(c)** observed in this corpus. Source (c) is prohibited. `FORWARD_CHAIN_THRESHOLD = 5` is (b) because WhatsApp itself labels a message "forwarded many times" at five chats — a published fact about the platform being modelled, not a number fitted to these 110 rows.

**Named failure, never silent degradation.** When something cannot be read, the cause has a name that survives all the way into the row's reason text. `duration_exceeded` is not `EXTRACTION_UNAVAILABLE`, and the row says the attachment could not be read rather than quietly deciding without it.

**Fail loudly over repairing quietly.** Evidence belonging to another user raises rather than being filtered. A reason outside the register raises rather than being truncated. A `scam` row resolving to `notify` raises rather than being corrected. In each case a silent fix would hide the fact that an invariant had stopped holding.

**A check must be capable of failing.** The most-repeated defect in this build was a green check that measured something adjacent to what it claimed — a flag instead of a sentence, a count instead of the entries, a file list instead of file bytes, applied codes instead of rule firings. Hence the planted-secret tests, the ablation control, and the baseline guard. Twelve such cases are catalogued in [INTERVIEW_PREP §4](INTERVIEW_PREP.md).

**Untrusted content is fenced, and some fields never enter a prompt at all.** Message text, image descriptions and transcripts go inside nonce-fenced blocks. `group_name`, `display_name` and `brand_name` are never interpolated into a prompt — that vector is closed by architecture rather than by escaping, and a test keeps it closed.

**The labeled rows are a check, not a target.** 30 rows means one row is 3.33 percentage points. Re-deriving the model's answers from cold moves the labeled set by ±1 row on its own, so the measured noise floor is two rows. No claim in the project rests on a one-row difference.

---

## 13. If you only read five files

1. **`policy/rules.py`** — the 16 rules. This is the system's actual judgement, and each rule declares what it reads.
2. **`policy/engine.py`** — 169 lines, and how a decision is really made.
3. **`perception/prompt.py`** — what the model is asked, how untrusted content is fenced, and the exact wording of the 14 claims.
4. **`config.py`** — every constant with its justification. The best evidence for whether the numbers were reasoned or fitted.
5. **`docs/PROBLEM_ANALYSIS.md`** — 22 decisions with the alternative rejected for each, including the ones that turned out wrong and were retracted.

Then run it:

```bash
cd code && pip install -e . && python main.py --dataset ../dataset route --out /tmp/out.csv
```

No keys, no network, about half a second.
