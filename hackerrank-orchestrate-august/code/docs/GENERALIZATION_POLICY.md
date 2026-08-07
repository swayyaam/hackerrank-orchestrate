# GENERALIZATION_POLICY.md

The rule that governs every design decision that follows.

Companion documents: [DATASET_ANALYSIS.md](DATASET_ANALYSIS.md) (what the data contains), [PROBLEM_ANALYSIS.md](PROBLEM_ANALYSIS.md) (what the task asks, and the Decision Record).

---

## 1. The policy

**The dataset is evidence about the domain. It is not the specification.**

`dataset/` is a 110-row sample of WhatsApp message routing. It was authored to illustrate a problem, and every property it has is either (a) a property of the domain, which will hold in the hidden evaluation set, or (b) a property of *this sample*, which will not. The two are indistinguishable by inspection. Treating (b) as (a) is the single failure mode this document exists to prevent.

Therefore:

> **Every rule in the system must be justifiable from `problem_statement.md`, or from how WhatsApp notification routing works generally. The dataset may motivate a rule and may falsify a rule. It may never be the sole justification for one.**

And, as a hard prohibition:

> **No rule may be keyed on a specific `message_id`, on a specific string found in the data, or on an enumerated combination of values whose cardinality is an artifact of sample size.**

### 1.1 What this permits

- Reading the data to *discover* that a signal exists (e.g. "senders have distinguishable trust profiles"), then encoding the signal in a form that would work on a different corpus.
- Using the data to *falsify* a proposed rule ("this rule mislabels `sample_msg_045`, so it is wrong").
- Copying the **output contract** verbatim: the 3 `action` values, the 11 `message_type` values, the 6 output column names and their order. These are specified in `problem_statement.md`, not observed in `dataset/`.
- Matching the *register* of `reason` (one sentence, third person, no IDs) — the statement explicitly scopes the sample to "format and style".

### 1.2 What this forbids

- Any lookup keyed on an identifier from the corpus.
- Any threshold whose value was read off a distribution in the corpus.
- Any exhaustive branch over a value set obtained by enumerating a column.
- Any behaviour that is correct only because the corpus happens not to contain a counterexample.

### 1.3 Why, given that we are graded on a hidden set drawn from the same generator

The hidden labels almost certainly come from the same authoring process as `sample_messages.csv`. Fitting to sample artifacts would probably score *better* on this specific evaluation. That trade is declined deliberately, for three reasons:

1. The scoring criteria include "usefulness and consistency of `reason`" and whether evidence points to "relevant" history. An artifact-fitted system produces indefensible artifacts — a citation chosen by matching an event-profile signature cannot be explained to a reader.
2. The submission is judged as a system ("The deliverable is what your system can do"), not only as a CSV.
3. Where an artifact-fitted rule and a general rule agree, the general rule costs nothing. Where they disagree, the artifact-fitted rule is a bet on the generator that cannot be validated with the data available. Every such disagreement is recorded as an accepted risk in the [Decision Record](PROBLEM_ANALYSIS.md#7-decision-record).

---

## 2. Findings, principles taken, artifacts refused

Six findings from [DATASET_ANALYSIS.md](DATASET_ANALYSIS.md) that most invite artifact-encoding.

---

### 2.1 The four collapsed `message_events` profiles and their 28/28 correspondence with `action`

**The finding.** All 412 rows of `message_events.csv` collapse into exactly four joint outcome profiles (`opened+replied@2min`, `opened@120min`, `opened@9min`, `dismissed+muted`). Across all 28 sample rows that cite evidence, the cited history row's profile matches the assigned action: profile A → `notify` (9/9), profile B → `digest` (11/11), profiles C/D → `mute` (8/8). `notification_dismissed`, `muted_after_message` and `message_opened=0` are perfectly collinear.

**General principle taken.** *How a user previously reacted to comparable messages is the strongest available evidence of how much this user values that class of message.* Engagement is graded, not binary: replying quickly indicates the message was worth interrupting for; opening late without replying indicates it was worth reading but not interrupting; dismissing and muting indicates it should not have been delivered. This is a real property of notification systems, and the statement endorses it directly — "The routing decision must be personalized to the receiving user", and the sample's own reason strings say "The user has opted out of or repeatedly dismissed similar marketing messages" and "Similar historical messages were ignored, dismissed, or muted by this user".

**Specific artifact refused.** *The four profiles as a closed enumeration, and the profile→action correspondence as a labelling function.*

Concretely, the following will not enter the codebase:

- `PROFILE_TO_ACTION = {"A": "notify", "B": "digest", "C": "mute", "D": "mute"}`, or any equivalent dict/match keyed on the five-field outcome tuple.
- Any branch on the exact tuple `(opened, replied, dismissed, muted, reported)` that enumerates the four observed combinations, with or without a fallback.
- Any use of the 28/28 correspondence as evidence that the profile *determines* the label.
- Reading "unopened" from a blank `reaction_time_minutes`. Blank is collinear with `message_opened=0` **in this sample only**; `message_opened` is the field that actually means it.

The four profiles exist because 412 rows were generated from four templates. A real event log has continuous reaction times and profiles that mix — opened-and-replied-then-muted, dismissed-then-opened-later, reported-without-muting. A system built on a four-way switch has no defined behaviour on any of those.

**What is encoded instead.** A monotone engagement score computed from each field independently, so that a profile never seen before still produces a sensible score. `replied` raises it, `opened` raises it less, `dismissed`/`muted` lower it, `reported` lowers it sharply. The score is a continuous quantity feeding the decision, not a switch selecting it.

---

### 2.2 `reaction_time_minutes` taking only three distinct values

**The finding.** The column takes only `2` (153 rows), `120` (110 rows), `9` (15 rows), and blank (134 rows). Blank occurs exactly when `message_opened=0`.

**General principle taken.** *Reaction latency is an ordinal signal of perceived importance — the faster a user engaged, the more the message mattered to them.* This is a standard, well-founded notification-quality signal and holds independently of any corpus.

**Specific artifact refused.** *Any comparison against the literals 2, 9, or 120, and any bucket boundary positioned in the gaps between them.*

Refused:

- `if rt == 2`, `if rt == 120`, `rt in {2, 9, 120}`.
- `FAST = 2; SLOW = 120` as named constants, which is the same rule with better naming.
- Thresholds such as `rt < 5` or `rt > 100` chosen because they happen to separate the three observed values. A boundary at 5 is no more defensible than one at 2 if its only justification is that no data point sits near it.
- Treating `9` as a distinct semantic category ("read but recognised as risky") because it appears only on evidence rows for `mute` decisions. That interpretation is a story fitted to 15 rows.

**What is encoded instead.** Latency is used as an ordinal quantity with at most a coarse fast/slow distinction, and any boundary is justified in a comment by human-attention reasoning ("responded within minutes" vs "responded after hours") rather than by the observed values. The system must produce a sensible score at `rt=0`, `rt=37`, and `rt=1440`, and this is verified by unit test rather than assumed.

---

### 2.3 The five identified injection-bearing message IDs

**The finding.** Five of 110 target messages contain text addressed to the routing system rather than to the human recipient: `msg_095`, `msg_107`, `msg_108`, `msg_109`, `msg_110`. All five pair a fabricated control-plane directive with a credential or payment request. Their opening phrases are `"System note for notification router:"`, `"Routing override:"`, `"Internal router metadata:"`, `"System note for the notification router:"`, `"Assistant instruction:"`. `msg_109`'s injected claim ("sender is trusted admin") is factually true.

**General principle taken.** *Message text is untrusted third-party input. Content that attempts to direct the routing system is a manipulation attempt; the routing decision is made on the message's actual content and risk, and the manipulation attempt is itself evidence of bad intent.* This is grounded verbatim in `sample_msg_053`'s reason — "The message tries to instruct the router, but the routing decision should be based on the actual content and risk" — and independently in ordinary security practice: data arriving over a channel must never be promoted to instructions about how that channel is processed.

**Specific artifact refused.** *The five IDs, and the five phrasings.*

Refused:

- `INJECTION_IDS = {"msg_095", "msg_107", ...}` in any form.
- A regex alternation over the observed openers (`(System note|Routing override|Internal router metadata|Assistant instruction)`), or any pattern tuned to match exactly these five and nothing else. An attacker writes "Note to the classifier", "priority_hint=high", or nothing recognisable at all.
- Any rule requiring the directive to appear as a **prefix**. All five happen to lead with it; nothing makes that necessary.
- Splitting on a literal delimiter such as `"Actual message:"` or `"User message:"` to recover the payload. Three of the five use such a marker and two do not.
- Any rule whose correctness depends on the injected claim being **false**. `msg_109` asserts something true, and a rule that verifies the assertion and honours it when true makes attacker-supplied text load-bearing in the trust model.

**What is encoded instead.** Detection is semantic and structural: the classifier is instructed that any content asserting a routing decision, a trust attribute, a priority, or a processing directive is to be disregarded as instruction and evaluated as evidence. The residual content is judged on its merits. The presence of such an attempt raises risk regardless of whether its assertions are true, whether it appears at the start, middle or end, and whether it is separated by a marker. This behaves correctly on an injection phrased in a way no one has seen.

---

### 2.4 Seven of eleven caption/brand mismatches

**The finding.** Of the 11 images referenced by `messages.csv`, 7 disagree with their caption or their sending brand — including on unambiguously legitimate traffic. `img_010`, an Amazon Prime Day banner, is attached to a verified **Target** promotion. `img_007`, a photo of a **JioMart** bag, is attached to a **Shopee** return-pickup notice. `img_023`, a missing-person poster carrying a real name, address and three phone numbers, is attached to a routine fire-alarm notice.

**General principle taken.** *Attached media in consumer messaging is weak, noisy evidence. Stock imagery, recycled campaign creative and reused posters are normal, so disagreement between a caption and its attachment carries almost no information about legitimacy.* Media is read because the statement requires it — "your system should inspect the media files themselves" — and it contributes when it carries content that bears on the routing question, but it does not override text and relationship signals that already determine the answer.

**Specific artifact refused.** *Caption/brand mismatch as a risk signal, and any per-image judgement.*

Refused:

- A detector that raises risk when the brand visible in an image differs from `business_accounts.brand_name`. On this corpus that rule fires on legitimate Target and Shopee traffic and would fire on the majority of real marketing.
- Any table keyed on `img_0NN` — a mapping from image ID to a description, a category, or a risk score.
- Any assumption that a caption describes its attachment, e.g. using the caption to shortcut reading the image.

Equally refused is the **inverse** artifact — concluding from "7 of 11 mismatch" that images are decorative and can be skipped. `img_011` (a field-trip consent form) and `img_024` (an annotated market chart) carry the substance of their messages, and the 8 voice notes carry *all* of theirs.

**What is encoded instead.** Every image and every voice note is read. Media contributes content when it contains content relevant to the routing question, and contributes nothing when it does not. Mismatch between media and caption never raises risk on its own — risk comes from what the message asks the user to do, which is a property of the combined content.

---

### 2.5 `payment` having zero sample examples

**The finding.** `payment` is one of 11 allowed `message_type` values and appears zero times in the 30 labelled samples. All ten other types appear at least once. `messages.csv` contains at least six payment-related messages spanning admin-legitimate (`msg_021`, which explicitly warns against payment links) to QR-phishing (`msg_048`, `msg_109`).

**General principle taken.** *The label space is defined by the problem statement, not by the sample. All 11 values are equally available on every row.* With 30 examples and 11 classes, the absence of a class is an unremarkable sampling outcome, not a signal that the class is unused. `payment` is defined from the statement's own sentence — "A payment reminder may be legitimate from a trusted admin but risky from a new sender" — and from ordinary meaning: a message whose primary purpose is to request, demand, confirm or remind about a monetary transaction.

**Specific artifact refused.** *Any restriction of the output vocabulary to the ten observed types, and any prior derived from the sample's type counts.*

Refused:

- `ALLOWED_TYPES` set to the ten types seen, or a post-processing step that rewrites `payment` to something else.
- A class prior fitted to the sample's counts (`promotion` 6, `urgent` 4, `event` 4, `personal` 4, `scam` 4, …). Those counts are what 30 draws produced.
- The mirror-image artifact: forcing `payment` whenever a keyword like "pay", "QR", "UPI" or "fee" appears. That would type `msg_021` — a legitimate reminder whose distinguishing feature is that it *warns against* payment links — identically to `msg_048`.

**What is encoded instead.** `payment` competes with the other ten types on the same criteria as everything else. Type precedence is stated generally: where a message is simultaneously a payment request and a safety risk, the risk type wins, because the statement directs that "Risky messages should use `mute` with an appropriate `message_type` such as `scam` or `spam`".

---

### 2.6 The `.mp3` extension mismatches

**The finding.** All 13 files in `dataset/media/audio/` carry a `.mp3` extension. Only 4 are MP3; 5 are WAV (`pcm_s16le`) and 4 are AAC. Of the 8 referenced by `messages.csv`, 6 are not MP3. `afinfo` and `mdls` both fail to report duration for the 9 non-MP3 files.

**General principle taken.** *A file extension is metadata supplied by an untrusted producer. Media is identified and decoded by content.* This holds generally: messaging clients transcode on send, platforms rewrite containers, and a filename survives none of that reliably.

**Specific artifact refused.** *Any hardcoded knowledge of which files are which format.*

Refused:

- `WAV_FILES = {"vn_005", "vn_006", "vn_008", "vn_012", "vn_015"}` or any equivalent map.
- A decoder selected by filename, file ID, or extension.
- An MP3-first path with a hardcoded exception list — the same artifact with an extra branch.
- A silent `try/except` that swallows a decode failure and routes the message on empty content, which converts an unsupported codec into an invisible misrouting.

**What is encoded instead.** One content-sniffing decode path (`ffmpeg`/libav or equivalent) that handles any container the underlying tool supports, applied uniformly to all 13 files. A decode failure raises with the offending path so that an unseen codec surfaces as a loud error rather than a silently empty transcript.

---

## 3. The review question

Every proposed rule must pass this before it enters the codebase.

> **If `dataset/` were deleted and regenerated from the same domain by a different author, would this rule still be correct?**

To answer it mechanically, run the following six checks in order. **All six must pass.** A rule failing any check is either restated at a level of generality that passes, or dropped.

### G1 — Citation

Write the rule's justification as one sentence that mentions neither `dataset/`, nor any file inside it, nor any value observed in it.

- **Pass:** the sentence exists and cites `problem_statement.md`, `README.md`, or a stated fact about how notification routing works.
- **Reject:** the only available justification is "this is what the data does".

### G2 — Literal

Search the rule (code, prompt text, and config) for literals traceable to the corpus:

```
msg_\d+   message_\d+   sample_msg_\d+   u_\d+   group_\d+   business_\d+   img_\d+   vn_\d+
```

plus any exact substring of four or more consecutive words taken from a `message_text`, `display_name`, `group_name`, `why_user_knows_account`, or media filename.

- **Permitted literals, and only these:** the 3 `action` values; the 11 `message_type` values; the 6 output column names and their order; the input column names; the literal `none`.
- **Reject:** any other hit.

### G3 — Cardinality

Does the rule branch over, or enumerate, a value set obtained from a dataset column — whether by `.unique()`, by `value_counts()`, or by a hand-copied list of observed values?

- **Pass:** the set is fixed by `problem_statement.md` (the 3 actions, the 11 types).
- **Reject:** the set's size would change if the corpus changed. This covers the 4 event profiles, the 3 reaction times, the 14 DND windows, the 40 business categories, the 20 group types, the 90 `why_user_knows_account` values.

### G4 — Numeric provenance

For every numeric constant in the rule, name its source as exactly one of:

- **(a)** the output contract or the problem statement;
- **(b)** a quantity with independent domain meaning, stated in a comment (e.g. "within 15 minutes counts as an immediate response");
- **(c)** a value, quantile, or inter-value gap observed in `dataset/`.

- **Reject** on any constant sourced **(c)**.

### G5 — Unseen input

Name one input the corpus does not contain that the rule must handle, and state the rule's output for it. Draw from at least one of: an audio codec not among {mp3, aac, wav}; a language other than English, romanised Hindi, or French; a `message_type` combination absent from the sample; a `reaction_time_minutes` other than {2, 9, 120, blank}; an event profile outside the four; a business with a blank `official_domain`.

- **Pass:** a defined, defensible output is named.
- **Reject:** the answer is "crash", "undefined", or "defaults to X, which is fine because the data is always Y".

### G6 — Growth

If the corpus grew tenfold in volume and variety, would the rule's branch count stay constant and its constants stay unchanged?

- **Reject:** the rule would need a new branch per new value, or a constant re-tuned.

### Recording

The six answers are recorded with the rule — in the commit message, or in a comment above it. A rule whose G1 sentence cannot be written, or whose G5 answer is unknown, does not enter the codebase pending resolution. Where a general rule is adopted in preference to a better-scoring artifact-fitted alternative, the trade is recorded as an accepted risk in the [Decision Record](PROBLEM_ANALYSIS.md#7-decision-record) rather than left implicit.
