# INTERVIEW_PREP.md

Assembled from what the build actually recorded. Every number here came out of a run directory, a test, or a config docstring — nothing is estimated, and where a figure is soft it says so.

Four sections:

1. [Every constant that affects a routing decision](#1-every-constant-that-affects-a-routing-decision)
2. [The decision log, D1–D22 in one line each](#2-the-decision-log-d1d22)
3. [The measured facts](#3-the-measured-facts)
4. [The defect table — checks that passed while verifying nothing](#4-the-defect-table--checks-that-passed-while-verifying-nothing)

---

## 1. Every constant that affects a routing decision

All of them live in **`config.py`**, in a frozen dataclass per concern, each with its justification as an inline docstring beside the value. Nothing is tunable from the command line and nothing is read from the environment except credentials — a threshold that can be changed at the call site is a threshold with no single recorded justification.

**The provenance tags** come from [GENERALIZATION_POLICY G4](GENERALIZATION_POLICY.md#g4--numeric-provenance):
**(a)** stated in `problem_statement.md` · **(b)** derived from a stated principle or an external published fact · **(c)** observed in this corpus — **prohibited**. Every constant below is (a) or (b). There are no (c) constants; the one that was, is documented in §4.

### 1.1 `PolicyConfig` — what a rule is allowed to conclude

| name | value | provenance & justification |
|---|---|---|
| `MIN_PATTERN_OBSERVATIONS` | `2` | (b) One instance is an event; two is the minimum that can constitute a pattern. `REPEAT_DISMISSAL`'s justification says "repeatedly", and one occurrence cannot support that word. |
| `DISMISSAL_MAJORITY` | `0.5` | (b) A strict majority is the weakest claim that supports "usually". Any higher value would be a preference without an argument. |
| `FORWARD_CHAIN_THRESHOLD` | `5` | (b) WhatsApp's own product definition — the platform labels a message "forwarded many times" at five or more chats and restricts onward forwarding there. A published constant about the platform being modelled. |
| `MIN_STANCE_OBSERVATIONS` | `2` | (b) A stance is a claim about a pattern of behaviour, so it needs the same minimum as any other pattern claim. Below it the stance is `UNKNOWN` rather than asserted from one interaction. |

### 1.2 `ConfidenceConfig` — the ten terms of the confidence sum

| name | value | provenance & justification |
|---|---|---|
| `W_BASE` | `0.55` | (b) A three-way choice at chance is 0.33. A decision informed by content, relationship and conversation type should beat chance, but one with nothing corroborating it is not settled; 0.55 is the midpoint of that description. |
| `W_TRIGGER_STRUCTURAL` | `0.20` | (b) The trigger is a fact recorded in the input, so the decision is as reliable as the input record. |
| `W_TRIGGER_PERCEPTUAL` | `0.10` | (b) A content judgement is reliable but not independently verifiable, so it earns half of a structural trigger. |
| `W_EVIDENCE_RELATIONSHIP_AND_CONTENT` | `0.12` | (b) The strongest corroboration available — this user has met this situation from this sender before. |
| `W_EVIDENCE_RELATIONSHIP` | `0.07` | (b) Same sender, different subject. Weaker than the above. |
| `W_EVIDENCE_TOPICAL` | `0.03` | (b) Same subject, no relationship. The weakest corroboration. |
| `W_ENGAGEMENT_ALIGNED` | `0.08` | (b) Agreement between the decision and the user's own past behaviour is two independent lines of reasoning converging. Applied symmetrically as a penalty on disagreement, since both are equally informative. |
| `W_MEDIA_UNREAD` | `0.15` | (b) The largest single term, because it is the only one standing for a decision made without the substance of the message. |
| `W_TYPE_AMBIGUITY` | `0.04` | (b) Each extra candidate is one more reading the model could not rule out. Small, because type ambiguity often does not change the action. |
| `W_CONTENTION` | `0.03` | (b) A decision several rules disagreed about is less certain than one reached unopposed. |
| `FLOOR` | `0.35` | (b) Slightly above the 0.33 chance rate. Below it we would assert our own answer is worse than a guess, which is not a claim we can make while still emitting it. |
| `CEILING` | `0.95` | (b) No router is certain. Even a structural trigger rests on a snapshot that may be stale; 1.0 would be a false claim. |
| `DECIMAL_PLACES` | `2` | (a) The contract asks for a number in [0, 1]; two places is the resolution at which a confidence is meaningful to a reader. |

### 1.3 `EvidenceConfig` — what gets cited

| name | value | provenance & justification |
|---|---|---|
| `MAX_IDS_DEFAULT` | `1` | (b) An explanation cites the minimum evidence supporting the claim it makes. A claim about one prior interaction needs one instance. |
| `MAX_IDS_PATTERN_RULE` | `2` | (b) A claim of repetition needs at least two instances. |
| `MIN_SHARED_CONTENT_TOKENS` | `2` | (b) Two messages are topically related when they share at least two content words. One is coincidence — any two sentences about a building share "gate". **This replaced a Jaccard threshold of 0.25; see §4.** |
| `MIN_TOKEN_LENGTH` | `3` | (b) Tokens under three characters are overwhelmingly function words, which carry no topical signal in any language. |
| `SEPARATOR` | `";"` | (a) The output contract specifies semicolon-separated ids. |
| `NONE_LITERAL` | `"none"` | (a) The output contract specifies the literal `none`. |

### 1.4 `ReasonConfig` — the register

| name | value | provenance & justification |
|---|---|---|
| `MIN_CHARS` / `MAX_CHARS` | `55` / `120` | (a), style only. `problem_statement.md` scopes `sample_messages.csv` to "the expected output format and style", which licenses matching the observed register. A padded envelope around it, not a fitted value — **no decision depends on where exactly they sit**. Violation raises `ReasonRegisterError`; a reason is never truncated, because a silently trimmed reason is a wrong explanation. |
| `MAX_SENTENCES` | `1` | (a) Every sample reason is a single sentence; register is the one thing the statement explicitly licenses copying. |

### 1.5 `LimitsConfig` — resource and safety ceilings

| name | value | provenance & justification |
|---|---|---|
| `MAX_UNTRUSTED_CHARS` | `4_000` | (b) A bound on how much attacker-controlled text enters one prompt. Well above any legitimate chat message, well below a size at which a wall of text could crowd out the instruction context. |
| `MAX_MEDIA_BYTES` | `25_000_000` | (b) WhatsApp's own per-file media ceiling. A file larger than the platform permits did not arrive over the platform. |
| `MAX_AUDIO_SECONDS` | `300.0` | (b) Five minutes — longer than any voice note a person sends in a chat, short enough that one file cannot stall a run. |
| `MAX_IMAGE_PIXELS` | `40_000_000` | (b) 40 MP exceeds any consumer camera likely to have produced a shared photo, and bounds decompression cost. |
| `MODEL_TIMEOUT_SECONDS` | `60.0` | (b) A perception call that has not returned in a minute has failed in practice, whatever the transport reports. |
| `MODEL_MAX_ATTEMPTS` | `3` | (b) One retry for a transient fault, one for a second, then stop. A third failure is not transient. |
| `MAX_RUN_INPUT_TOKENS` / `MAX_RUN_OUTPUT_TOKENS` | `5_000_000` / `1_000_000` | (b) About an order of magnitude above one full live pass over 140 rows: stops a runaway loop without interrupting legitimate work. |
| `MAX_RUN_WALL_CLOCK_SECONDS` | `3_600.0` | (b) One hour. A live pass over 140 rows that has not finished in an hour is stuck, not slow. |

### 1.6 `ExtractionConfig` and `PerceptionConfig` — what the model is asked

| name | value | note |
|---|---|---|
| `VISION_PROVIDER` / `VOICE_PROVIDER` | `anthropic` / `sarvam` | Two providers on purpose. Voice is Sarvam `saaras:v3` because the corpus is code-mixed Indic speech. |
| `VOICE_MODEL_ID` / `VOICE_MODE` | `saaras:v3` / `translit` | `translit` puts transcripts in the same romanised register as the text channel, so one prompt reads both. The mode is **in the media cache key** — a mode change cannot silently reuse a transcript. |
| `VOICE_MAX_SECONDS` | `30.0` | The REST endpoint's own cap. Two of 13 voice notes exceed it and are named `duration_exceeded` **before any request is sent** (D17). |
| `VISION_EFFORT` / `EFFORT` | `low` / `medium` | Description is transcription-like; routing is judgement. |
| `VISION_MAX_OUTPUT_TOKENS` / `MAX_OUTPUT_TOKENS` | `1_024` / `2_048` | |
| `THINKING` | `adaptive` | |
| `RETRY_BACKOFF_SECONDS` | `(1.0, 4.0, 16.0)` | Bounded by `MODEL_MAX_ATTEMPTS`. |
| `PROMPT_TEMPLATE_VERSION` | `1.3.0` | In `perception/prompt.py`, and in the perception cache key. 1.0.0 → 1.1.0 narrowed two over-broad claims (D18); 1.2.0 added the eleven type definitions; 1.3.0 added C1's action definitions. |
| `MEDIA_EXTRACTION_VERSION` | `1.0.0` | In the media cache key. |

**The one thing deliberately excluded from a cache key:** `PolicyConfig`. Policy iteration must be free, so changing a rule threshold does not invalidate a single paid perception entry. Everything the *model saw* is in the key; nothing about what the *code did with it* is.

---

## 2. The decision log, D1–D22

Each line: **the decision**, then *the alternative rejected*. Full text in [PROBLEM_ANALYSIS.md](PROBLEM_ANALYSIS.md).

| # | decision | alternative rejected |
|---|---|---|
| **D1** | Optimise `action` and `message_type`; treat `reason`, `evidence`, `confidence` as per-row quality gates that must hold on every row. | Assuming a weight vector (40/30/10/10/10) and optimising the weighted sum — the weights would be invented, and any such vector licenses trading a correct action for a cosmetic gain. |
| **D2** | Cite the minimum evidence supporting the claim the reason actually makes: 1 id by default, 2 for a pattern claim, `none` when nothing is defensible. | Citing top-*k* with *k*=3–5 to maximise the chance of including the right one — inflates recall against a criterion phrased in terms of relevance. |
| **D3** | Emit confidence reflecting actual belief, computed identically per row, monotone in evidence strength, bounded below 1.0 and above chance. | Clamping to the sample's observed [0.78, 0.91] band and reproducing its `notify`/`digest` separation. |
| **D4** | Match the sample's **register** exactly; generate the **content** per message. | Selecting from a bank of the 24 observed reason strings — literally encoding strings found in the data, prohibited by G2. |
| **D5** | `payment` = a message whose primary purpose is a monetary transaction, where the money movement is the point rather than incidental. | Never emitting `payment`, which exactly matches the sample distribution — fitting to an absence. |
| **D6** | `unknown` is the residual: fits none of the other ten with reasonable confidence. Sender unfamiliarity alone never forces it. | Defining it as "unfamiliar sender + benign content", the literal reading of the one labelled row — misfires on unfamiliar senders whose type is perfectly clear. |
| **D7** | DND windows do not affect any output column. Not read by the router. | Downgrading `notify`→`digest` inside the window — zero of 30 samples fall in one, making the rule unfalsifiable. |
| **D8** | `daily_notification_summary.csv` used only as a stable per-user baseline over its 14-day window. | A same-day interruption budget — the data cannot support it for any target row, and a budget makes each message's routing depend on unrelated messages. |
| **D9** | The four `message_events` profiles are graded evidence of preference, not an enumeration. | A direct profile→action lookup, which reproduces **28/28** of the sample's correspondence. |
| **D10** | Evidence must be defensible to a human reading target and cited message side by side. | Selecting evidence by matching the target action's event profile — rejected under G1. |
| **D11** | Read every image and voice note; media contributes when it bears on the routing question. Caption/media mismatch never raises risk alone. | A brand-consistency check on images — on this corpus it fires mostly on legitimate senders. |
| **D12** | The truth of an injected claim is irrelevant: content aimed at the router is disregarded as instruction, the residual judged on its own risk. | Validating injected claims against `group_members.csv` and deferring when they check out — an attacker who can read the group can always choose a true claim. |
| **D13** | Group size is not a signal; what matters is whether the message concerns this receiver. | A `member_count` threshold — the cut point would be a source-(c) constant prohibited by G4. |
| **D14** | Write `output.csv` in `messages.csv` order, preserving its non-sorted sequence. | Sorting by `message_id` — no benefit, breaks positional comparison. |
| **D15** | Every timestamp is naive local time in one implicit timezone; no conversion anywhere. | Inferring per-user timezones from group membership — users span regions and the mapping would be invented. |
| **D16** | `sample_messages.csv`: **format authoritative**, **labels held out**. | Tuning thresholds, prompts or type priors to maximise accuracy on the 30 rows. |
| **D17** | Voice notes over the endpoint's 30 s cap **degrade**: named `duration_exceeded` before any request, cached as that verdict, cause named in the row's reason. | Chunking at the 30 s boundary and concatenating — the seam falls mid-utterance and the stitched transcript is not a faithful record. |
| **D18** | When a rule fires correctly on a claim that meant the wrong thing, fix the **question**, not the answer: narrow the claim descriptions and bump `PROMPT_TEMPLATE_VERSION`. | Adding a policy-side exclusion to stop the rule firing — would leave the claim still meaning the wrong thing for every future consumer. |
| **D19** | **RETRACTED.** `evidence_message_ids` agreement is a live metric and a legitimate target. | The retracted position: that the eval evidence column is index-aligned rather than meaningful. Tested against the counter-hypothesis, found wrong, and withdrawn. |
| **D20** | Leave `sets_type` as written: five Tier-0 rules mint `scam`. | Letting safety rules own only the *action* and leaving type to perception — coherent, but it would change five specifications on an argument rather than a measurement. One labeled counterexample now exists (D21). |
| **D21** | `ROUTER_MODEL_ID` stays; no re-warm. Of 9 wrong labeled rows, 4/5 action errors and 5/6 type errors are perception-owned, but only **3** are true comprehension failures. | Escalating the perception model — best case 3 rows against a measured 2-row floor, for 508 invalidated entries and a fresh unmeasured variance. |
| **D22** | A floor could raise a `scam`-typed row to `notify` — two rows did, both of which the model had typed `scam` *and proposed muting*. New `RISK_TYPE_CAP` caps any `scam`/`spam` row at `mute`, unsuppressible, with a `PolicyError` post-condition behind it. | Excluding the two floors from risk-typed rows — fixes those rows, leaves the hole open for a model that proposes `notify` outright. Also rejected: silently correcting the action in `_build`, which would hide the table becoming insufficient. |
| **C1** | The §"Allowed values" wording controls: `digest` = safe but low priority; `mute` needs low value **plus** a positive reason. "Not useful" is never on its own enough to mute. | Treating "useful" as controlling — contradicts `sample_msg_009` and `sample_msg_010`, both benign and both `digest`. |
| **C2** | As D16: format authoritative, labels a held-out check. | — |

---

## 3. The measured facts

### 3.1 Phase 0 rules-only baseline versus current

The Phase 0 run is a real run directory (`20260801T183432Z-…`, `model none (rules-only)`), not a reconstruction — the pipeline ran end to end with no model at all.

| | Phase 0 — rules only | current | delta |
|---|---|---|---|
| labeled action accuracy | 17/30 = **56.7%** | 25/30 = **83.3%** | +8 rows |
| action macro F1 | 0.46 | **0.83** | +0.37 |
| labeled type accuracy | 7/30 = **23.3%** | 24/30 = **80.0%** | +17 rows |
| type macro F1 | 0.09 | **0.63** | +0.54 |
| joint (both correct) | 5/30 = **16.7%** | 21/30 = **70.0%** | +16 rows |
| evidence exact match | 12/30 | 13/30 | +1 |
| action distribution | notify **0**, digest 66, mute 44 | notify 30, digest 27, mute 53 | — |

**The line worth saying out loud:** the rules-only baseline emitted **zero `notify`** across 110 rows. The rule table can suppress and defer on structural grounds, but nothing in it can decide that a message is *worth interrupting for* — that requires reading the message. This is the empirical case for the hybrid: not that the model is better than rules, but that the two answer different questions.

### 3.2 Reproducibility under independent re-derivation

Measured once, deliberately, by building a **second cache from cold** against the live APIs (30 media extractions, 127 routing calls) in an isolated cache directory, then diffing against the cached run it should reproduce. [EVALUATION_STRATEGY §6a](EVALUATION_STRATEGY.md#6a-reproducibility-under-independent-re-derivation--measured).

| column | rows differing / 110 |
|---|---|
| `action` | **3 — 97.3% stable** |
| `evidence_message_ids` | 3 |
| `message_type` | 9 |
| `reason` | 14 |
| `confidence` | **24 — the most volatile column** |
| identical across **all six** | **81 / 110** |
| labeled action accuracy | 25/30 → 24/30 (**−1 row**) |

Every structural invariant survived: **all 15 rules in the table at the time still fired** (`RISK_TYPE_CAP` was added afterwards; the table is now 16), **all 8 ablations still held**, ungrounded citations stayed 0, unexplained consistency divergences stayed 0, and two consecutive routes over the fresh cache were byte-identical to each other.

**The consequence, and it binds every other number here:** re-derivation alone moves the labeled set by ±1 row, so **the noise floor is two rows measured, not assumed**. A change worth ≤1 labeled row is not evidence. The cold run scored −1 against the committed artifact and the committed artifact was kept, because swapping on a noise-level difference is re-rolling for a better number.

### 3.3 Why the per-column sensitivity is ordered that way

Not an accident — it is the clearest evidence the perception/policy split does what it claims.

- **`action` is most stable because a deterministic rule table owns it.** The rules that fire most read structural facts (`IMPERSONATION`, `REPEAT_DISMISSAL`, `GROUP_MUTE_CAP`, `COLD_SENDER_CAP`) which are identical between runs by construction. A perception claim must flip *and* be the determining input before an action moves.
- **`confidence` is least stable because it aggregates ten terms with no thresholding.** Every other column is a discrete choice that absorbs small perturbations — a claim can flip without changing which rule wins. A sum has no such absorption: any moved term moves the number. Its *ordering* properties were unchanged (`mute` > `notify` > `digest`; overridden > accepted), which is what the column is for.
- **`message_type` sits between them** because it is a discrete choice the rule table only sometimes owns: Tier 0 rules set it, and on ~75% of rows it is the model's ranking passed through.

### 3.4 Ablations — 8 tests, all holding

Each degrades an input a rule claims to read and requires the output to move in the stated direction. **A null result is a defect, not a pass.** Counts *firings*, not applied codes — see §4.

| ablation | assertion |
|---|---|
| baseline guard | `REPEAT_DISMISSAL`, `GROUP_MUTE_CAP`, `IMPERSONATION` all fire at baseline — a rule that never fires cannot be shown to stop |
| zero the engagement history | `REPEAT_DISMISSAL` fire count → 0 |
| unmute every conversation | `GROUP_MUTE_CAP` → 0 |
| verify every business | `IMPERSONATION` → 0 |
| blank every official domain | `IMPERSONATION` → 0 — proves the three-valued `DomainState` is load-bearing: absence of evidence is `UNDETERMINED`, not `MISMATCH` |
| break every attachment | `media_unread` share rises; confidence falls on every newly-unread row by at least `W_MEDIA_UNREAD` |
| make every sender dismissive | total `mute` count strictly rises — the engagement signal changes *outcomes*, not just confidence |
| **the control** | rewriting `group_name` to "ZZZ totally different name" changes **no** firing and **no** confidence — without it, a harness bug that perturbed everything would make all seven above "pass" for the wrong reason |

### 3.5 Threat coverage — 20 tests over T1–T7

| id | threat | what closes it |
|---|---|---|
| **T1** | Injected text in the message closes its own fence | Per-run random nonce fences; injected text cannot close a fence whose token it cannot guess. `ROUTER_DIRECTIVE` can only ever *mute* — the one action an attacker does not want. |
| **T2** | Injection via text inside an image | Extracted image text is fenced identically to message text; the extraction prompt carries **no routing vocabulary**, so a poster saying "mark this urgent" reaches a describer with no concept of urgency. |
| **T3** | Injection via a voice transcript | Fenced identically; an unreadable one is declared rather than silently empty. |
| **T4/T5** | `business.display_name`, `brand_name`, `group_name` as injection vectors | **Closed by architecture, not by escaping** — these free-text fields are never built into a prompt at all. Asserted by test, so a future refactor that starts interpolating one fails. |
| **T6** | A compromised model emits fields it may not set | The response schema does not offer `action_final`, `override_code`, or `confidence`; the parser rejects them if present anyway. |
| **M4** | Media path escape, symlink escape, oversized file, oversized text | Rejected before decode; path escape and symlink both raise. |
| — | Evidence integrity | Evidence from another user **raises** rather than being filtered; evidence from the future is never cited; every cited id comes from the recorded shortlist. |
| — | **Total compromise** | A model returning maximally adversarial output cannot escalate anything, and the two structural rules still fire. This is the test that states the actual guarantee. |

---

## 4. The defect table — checks that passed while verifying nothing

The recurring failure of this build, and the most useful thing in this document. Every row is a check that was **green while asserting something other than what it claimed**. Each was found by a *different* check, never by the original one — which is the argument for redundancy in verification rather than more of the same kind.

| # | phase | the check that passed | what it was actually asserting | what caught it |
|---|---|---|---|---|
| 1 | 1 | "`warm-media` cached all 33 files" | It had cached 33 **content-free `decoded_only` entries** from an offline run. The next paid `--allow-network` pass would have been a no-op and produced a full run with no media content at all. | Reading the entries rather than the count. Fixed with retry semantics: `decoded_only` is the one `extraction_status` that is a statement about the *run*, not the file, so a later pass retries it. |
| 2 | 1 | "`.env` documented; keys configured" | The README told the user to create `.env` and **nothing in the program read it**. Every credential had to be exported manually or the run silently went offline. | Running the documented setup end to end instead of trusting the doc. Fixed with `main.load_dotenv()`, which never overrides a real export and never prints a value. |
| 3 | 2 | `extraction_status` reported per row | `extract()` **hardcoded `EXTRACTION_UNAVAILABLE`**, discarding the stored cause. `duration_exceeded` — the whole point of D17 — never reached a row. | Tracing one known-bad voice note through to its output row. Fixed by propagating the stored `extraction_failure`. |
| 4 | 3 | `reason_cites_evidence` = 33.6% | It read the applied rule's **`cites_history` metadata flag**, not the composed sentence. A rule not so flagged scored as not-citing even when its sentence described the user's history in as many words. The metric measured rule bookkeeping and reported it as a property of the output. | Reading ten reasons by hand and disagreeing with the number. Both predicates now read the text. |
| 5 | 3 | `integrity_warnings: 0 unexplained` | `OutputRow.threat_events` was **hardcoded `()`**. Every computed threat event was discarded, so "0 unexplained" was arithmetically guaranteed — the denominator was zero. `ROUTER_DIRECTIVE` had fired 7 times with no row saying so. | Noticing a metric that is 0 while a rule it depends on fires 7×. Warnings are now derived in `features/integrity.py` **independently of rule fires** and propagated. |
| 6 | 4 | `MIN_CONTENT_OVERLAP = 0.25`, "a principled threshold" | Its provenance was flagged **UNRESOLVED at design time and never resolved**. Measured against the corpus it sat at roughly the **92nd percentile of non-zero overlaps** — which is what a value fitted to this corpus looks like, and exactly what the G4 flag warned about. | Auditing the flag rather than the value. Replaced with `MIN_SHARED_CONTENT_TOKENS = 2`: a claim about how language works, which does not move if the corpus grows. |
| 7 | 4/5 | `validate` passed on `output.csv` | `route --out` defaulted **cwd-relative**, so a run from `code/` wrote `code/dataset/output.csv` while the real artifact sat stale and unmentioned. `validate` was passing on the *old* file. | A path that existed but should not have. `--out` now defaults to `<dataset>/output.csv`, following `--dataset`; regression test added. |
| 8 | 5 | An ablation "passed" for `GROUP_MUTE_CAP` | The harness counted **applied codes, not firings**. `GROUP_MUTE_CAP` fires 12× but is never the determining rule, so a perfectly working rule reported zero — and a broken one would have reported zero identically. | The baseline guard test (`test_baseline_fires_every_rule_under_test`), added specifically because a null ablation result is indistinguishable from a broken harness. Fixed to count firings. |
| 9 | 5 | `_HISTORY_CLAUSES` membership | Rebuilt from the `cites_history` flag again — **defect #4 resurfacing in a different module** after being fixed once. | Reading each sentence and classifying it by what it says. The set is now derived from the composer's own tables. |
| 10 | 6 | "reason register: 0 rows outside bounds" | It measured the **110 rows that happened to occur**. **19 clause × subject combinations** would have exceeded 120 chars and raised `ReasonRegisterError` — a crash, not a degraded sentence — on any corpus that produced them. | A systematic check over every pairing, now `test_every_subject_and_clause_pairing_stays_in_register`. |
| 11 | 6 | `names_decisive_factor` = 100% | The three clause sets were not **exhaustive** over what the composer can emit. `DIRECT_ADDRESS_GUARD`'s justification was classified by none of them, so a row determined by it would have scored as naming nothing. | `test_every_emittable_clause_is_classified`, on its **first run**. |
| 12 | 6 | `make_zip.py` "verified: no excluded content leaked" | It checked the **file list**, never file **contents**. Excluding `.env` by name proves one filename is absent; it says nothing about a key pasted into a doc or a config default. It also excluded `.cache/`, which silently made the archive's headline claim — offline reproducibility — **false**. | Asking what a reviewer without credentials could actually run. Both fixed: the cache ships, and an 8-pattern content scan runs over every member, each pattern exercised against a planted example. |
| 13 | 6 | The new secret scan "passed clean" | On its first run it flagged **its own test file** — the planted example literals were shipping inside the archive. The tempting fix (waiving `tests/`) would have carved a hole exactly where a pasted key is most likely to go unnoticed. | The scan itself. Planted shapes are now assembled from fragments so the matchable literal never appears in shipped bytes, and `tests/` stays fully scanned. |
| 14 | 6 | A firing record shows what a rule did | `RuleFiring` records `would_set_action` but **not** `would_set_message_type`, so `sample_msg_043` shows `type=None` while `IMPERSONATION.sets_type` demonstrably wrote `scam`. The telemetry understates the rule. | The D21 attribution pass. **Recorded, not fixed** — that pass was scoped diagnostic-only, and it touches no routing behaviour. |
| 15 | 6 | "`route` reproduces the submitted CSV offline from the shipped cache" | The claim had **never been executed anywhere the environment was actually clean**. It was only ever run in a shell where `.env` had already exported `ROUTER_MODEL_ID` — and the model id is part of the perception cache key, correctly, so an unset variable gives a *different key for every row*. A reviewer following the README, which correctly says no credentials are needed offline, would have got 140 cache misses and a hard failure on the archive's headline property. | The Phase 6 clean-room test, run with `env -i`. Fixed with `config.DEFAULT_MODEL_ID` — a public model name, not a credential, kept separate from the three variables that have no defaults and never will. An explicit `ROUTER_MODEL_ID` still overrides and still re-keys. |
| 16 | 6 | Every row's `action` is owned by the rule table, and the table is coherent | Nothing in the table consulted the `message_type` when a floor raised the action, so a row could be emitted `scam`/`notify` — self-contradictory on the face of the output, against an instruction the statement makes twice. Two rows were. Both had been typed `scam` **and proposed `mute` by the model**: the policy layer overrode a correct read *upward*, using the deadline and direct-request signals a scam manufactures. | Reading the submitted `output.csv` against the specification rather than against the metrics — no test, no metric, and no consistency scan had a reason to look at the (type, action) pair. Fixed as [D22](PROBLEM_ANALYSIS.md#d22--a-floor-could-override-a-scam-classification). |

**The shape they share.** Twelve of the sixteen are the *same defect*: a check that read **adjacent bookkeeping instead of the thing it named**. A flag instead of a sentence. A count instead of the entries. A file list instead of file bytes. Applied codes instead of firings. The rows that occurred instead of the combinations that could. The lesson the build actually taught is that a green check is only information if the check has been shown capable of going red — which is why `tests/test_packaging.py` plants a secret for every pattern, why `test_an_ablation_that_should_change_nothing_changes_nothing` exists as a control, and why `test_baseline_fires_every_rule_under_test` guards the ablations that follow it.

**The four that differ**, and each names a distinct hazard worth stating separately:

* **#6** is not a broken check but a *fitted constant* — a threshold flagged UNRESOLVED at design time, shipped anyway, and later measured at the 92nd percentile of this corpus. The hazard is that a provenance flag with no deadline is a comment, not a control.
* **#13** is the secret scan flagging **its own test file** on first run. Listed because the tempting fix — waiving `tests/` — would have carved a hole exactly where a pasted key is most likely to go unnoticed. The hazard is fixing the check to accommodate the finding.
* **#16** is not a check that verified the wrong thing but a property **no check was watching at all**. Every layer was individually correct — the model read the rows right, the floors fired on their stated conditions, the consistency scan found no unexplained divergence — and the *combination* was wrong. The hazard is that a system verified layer by layer can still emit a self-contradictory row, and only reading the output against the specification finds it.
* **#15** is a claim that was true in the environment it was tested in and false in the one it was written for. The hazard is verifying in the developer's shell: `.env` had been exported for hours, so nothing local could ever have revealed it. **`env -i` is the control that made it visible**, and it is the reason the clean-room test extracts the archive rather than reusing the working tree.

**The one repeat.** Defect #9 is #4 recurring in a second module after being fixed in the first. Fixing an instance is not fixing a class; what closed it was making the classification sets exhaustive and adding a test that fails when they are not (#11), rather than fixing the symptom a third time.
