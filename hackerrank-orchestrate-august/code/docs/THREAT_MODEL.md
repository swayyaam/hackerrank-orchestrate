# THREAT_MODEL.md

Every path by which attacker-controlled content reaches the model or the decision, the mitigation for each, where it is enforced, and what it logs.

Companion documents: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), [DECISION_ENGINE.md](DECISION_ENGINE.md), [DATASET_ANALYSIS.md](DATASET_ANALYSIS.md).

---

## 1. Trust model

### What the attacker controls

An attacker is anyone who can send to the receiving user, or who operates an account the receiving user is exposed to. They control:

| field | how |
|---|---|
| `messages.message_text` | they write it |
| media bytes under `media/images/`, `media/audio/` | they attach it |
| `business_accounts.display_name`, `brand_name` | they choose them at registration |
| `business_accounts.domain_used_by_sender` | they choose which domain they send from |
| `groups.group_name` | if they created the group |
| `message_history.message_text` for their own prior messages | they wrote those too |
| `messages.forwarded_count` | partially — they can forward |
| `group_members.role` **for themselves** | if they hold or obtain admin in a group |

### What the attacker does not control

| field | owner |
|---|---|
| `business_accounts.verified` | the platform |
| `business_accounts.official_domain` | the brand registry, not the sender |
| `business_accounts.user_reports_30d` | other users |
| `message_events.*` | the receiving user's own behaviour |
| `group_members.group_muted_by_user` | the receiving user |
| `user_business_history.allows_promotions`, `promotions_opted_out_at` | the receiving user |
| `users.*` | the receiving user |
| `group_members.role` **for the receiver** | the group |

**This split is the architecture — and the compromise-resistance claim it supports is smaller than it looks.** Four rules in [DECISION_ENGINE.md](DECISION_ENGINE.md#2-precedence-table) — `IMPERSONATION`, `REPEAT_DISMISSAL`, `GROUP_MUTE_CAP`, `COLD_SENDER_CAP` — have triggers that read only fields in the second table. (`PROMOTION_OPT_OUT` does not: its trigger is **mixed**, conjoining the structural consent fact with the perception claim `solicits_purchase`.) But trigger provenance is not the end of the analysis, because `DIRECT_ADDRESS_GUARD` — itself driven by a perception claim — can suppress two of the four.

**The rules that survive total model compromise end to end are two: `IMPERSONATION` and `REPEAT_DISMISSAL`.**

| rule | survives? | what a compromised model would have to assert to defeat it |
|---|---|---|
| `IMPERSONATION` | **yes** | Nothing available. Hard override on `verified`, `domain_state`, and identity consistency only; hard overrides are evaluated before caps and floors, and no guard suppresses Tier 0/1. No model output reaches any input of this rule. |
| `REPEAT_DISMISSAL` | **yes** | Nothing available. Cap on `prior_interaction_count` and `dismissal_ratio`, both derived from `message_events`, which the receiving user authored. It appears in no guard's suppression list. |
| `PROMOTION_OPT_OUT` | no | Assert `solicits_purchase = False` on marketing content. The consent cap never engages, and the opted-out promotion routes on the — equally compromised — proposal. |
| `GROUP_MUTE_CAP` | no | Assert `addresses_recipient_directly = True`. When the sender's recorded stance is not `DISMISSIVE` (a structural fact the model cannot change), `DIRECT_ADDRESS_GUARD` suppresses this cap; the fabricated mention plus a compromised proposal (and, for the floor, a fabricated `states_deadline_within_day`) carries the action up to `notify`. |
| `COLD_SENDER_CAP` | no | The same assertion, and it is *cheapest* here: a cold sender has fewer than `MIN_STANCE_OBSERVATIONS` events, so their stance is `UNKNOWN`, which satisfies `!= DISMISSIVE`. The fabricated mention pierces exactly the cap that exists for unknown senders. |

Residual structural backstop for the two defeated caps: a sender whose recorded stance is `DISMISSIVE` cannot be re-elevated by any assertion — the guard's structural conjunct refuses them, and `REPEAT_DISMISSAL` independently caps them at `mute`.

**Candidates, recorded and deliberately not implemented.** Two changes would enlarge the surviving set; each has a real cost, so they are noted here for a future decision rather than designed in:

- Tighten the guard's structural conjunct from `sender_stance != DISMISSIVE` to `sender_stance == RECEPTIVE`. A fabricated mention could then pierce a mute only for senders the receiver has actually engaged with — `COLD_SENDER_CAP` would fully rejoin the surviving set, since a cold sender is never `RECEPTIVE`. Cost: a genuine first direct address, and a `NEUTRAL`-stance sender's genuine mention in a muted group, would no longer interrupt.
- Compute the mention itself in code — a `features/` check for a literal `@<receiver user_id>` in the message text — as the guard's trigger or a required conjunct. This removes the model from the suppression path entirely; the attack becomes "literally name the receiver", which mass-blast campaigns cannot do per-recipient for free. Cost: unmarked direct address ("Can you come online now?") stops qualifying, and the labelled sample treats such messages as direct.

Any such change goes through [DECISION_ENGINE.md](DECISION_ENGINE.md) and the [review gate](GENERALIZATION_POLICY.md#3-the-review-question) first; this document states the claim at its true size, it does not redesign the rules.

### Adversary goals

1. **Escalation** — get a malicious message routed `notify`, maximising the chance the user acts on it.
2. **Trust laundering** — get a scam typed as `business_update`, `payment`, or `urgent` rather than `scam`.
3. **Suppression** — get a legitimate competing message routed `mute` (e.g. a bank's genuine fraud alert).
4. **Resource exhaustion** — make the run fail or hang so no output is produced.

---

## 2. Mitigation architecture

Five layers. Each path in §3 and §4 references them rather than restating them.

### M1 — Authority separation

The model returns a `Perception`: a proposal and content claims. It cannot set the final action. A fully successful injection changes what the model believes about the content and nothing else. Four of fifteen policy rules trigger without consulting `Perception`; after accounting for guard suppression, **two** — `IMPERSONATION`, the single strongest anti-scam rule, and `REPEAT_DISMISSAL` — remain correct end to end under total model compromise. The defeat conditions for the others are enumerated in §1.

**Enforced:** the type signature of `policy.engine.decide(perception, features)`, plus the import-graph test asserting `policy/` does not import `perception/`.

### M2 — Fenced untrusted blocks

Untrusted text is never concatenated into the instruction stream. It is placed inside a block delimited by a run-scoped random nonce, and the system prompt states that content inside such a block is data to be described and never instructions to be followed.

```
<<UNTRUSTED:{nonce}:message_text>>
...content...
<</UNTRUSTED:{nonce}>>
```

The nonce is generated once per run from `secrets.token_hex` and is not derivable from anything the attacker can see. Attacker text that contains the literal token `<<UNTRUSTED` (without the correct nonce) is neutralised before insertion.

**Enforced:** `perception.prompt.fence(label, content) -> str`.

### M3 — Schema-constrained output

`Perception` uses `extra="forbid"`. A response containing a `confidence`, a `reason`, an `evidence_message_ids`, or any field not in the schema is rejected, not silently trimmed. An out-of-enum `action` or `message_type` is rejected. Rejection raises; it does not fall back to a default.

**Enforced:** `perception.parse.to_perception`.

### M4 — Length and resource caps

Every untrusted field is truncated to `LimitsConfig.MAX_UNTRUSTED_CHARS` before insertion. Media is rejected above `MAX_MEDIA_BYTES`, `MAX_AUDIO_SECONDS`, or `MAX_IMAGE_PIXELS`.

**Enforced:** `perception.prompt.fence` for text; `media.decode`, `media.extract` for media.

### M5 — Minimal context surface

Only **enumerated categorical values** from context enter the prompt: `conversation_type`, `group_type`, `business.category`, `verified`, `domain_state`, `sender_role`, `receiver_role`. No free-text context field is ever sent. This closes five of the eight injection paths by architecture rather than by filtering — see §3.

**Enforced:** `perception.prompt.build_prompt` constructs its context block from a fixed allowlist of enum-typed fields. A test asserts that no free-text field value appears anywhere in the rendered prompt.

---

## 3. Paths closed by architecture

These five paths carry attacker-controlled text, and **none of them reaches the model at all**. Closing a path is stronger than filtering it: there is no sanitiser to bypass.

| # | path | why it is not needed | enforced at |
|---|---|---|---|
| **T4** | `business_accounts.display_name`, `brand_name` | The model needs to know the sender is a business and what *kind* — `category`, `verified`, `domain_state` supply that. The literal names are needed only by `IMPERSONATION`, which compares them **in code** and never renders them. | `perception.prompt.build_prompt`; `features.business.identity_consistent` |
| **T5** | `groups.group_name` | `group_type` is the categorical equivalent and is what routing actually depends on. A group's chosen name is pure attacker text with no decision value. | `perception.prompt.build_prompt` |
| **T6** | `message_history.message_text` | Evidence selection is deterministic ([SYSTEM_DESIGN §6](SYSTEM_DESIGN.md#6-interfaces)); content similarity is computed by token overlap in code. Sender stance comes from `message_events`, which the attacker does not control. **The model never sees a historical message.** | `scoring.evidence.select_evidence` runs after perception and returns ids only |
| **T7** | `user_business_history.why_user_knows_account` | 90 distinct values over 106 rows — effectively free text ([DATASET_ANALYSIS §1.8](DATASET_ANALYSIS.md#18-user_business_historycsv--106-rows--11-cols)). The router uses the *structured* relationship fields (`allows_promotions`, `promotions_opted_out_at`, `activity_count_180d`) instead. | `perception.prompt.build_prompt`; `features.business` |
| **T8** | `business_accounts.official_domain`, `domain_used_by_sender` | Compared in code to a three-valued `DomainState`. Only the enum is sent, never the domain strings — so a domain crafted to read as an instruction has no channel. | `features.business.domain_state` |

**T6 deserves emphasis.** Retrieval-augmented systems routinely re-inject attacker-authored history into the prompt, which turns a past message into a live injection vector. Because per-user history is 3–32 rows and retrieval is a deterministic sort, this system has no reason to put historical text in front of the model, and does not.

---

## 4. Paths that must reach the model

Three paths carry content the model genuinely needs to read.

---

### T1 — `messages.message_text`

The primary channel. Directly attacker-authored.

| | |
|---|---|
| **Mitigation** | M1 authority separation, M2 fencing, M3 schema constraint, M4 length cap. The `contains_router_directive` claim gives the model an explicit place to report an attempt rather than being silently swayed by it, and `ROUTER_DIRECTIVE` ([rule 2](DECISION_ENGINE.md#2-router_directive)) hard-overrides to `mute` when it does. |
| **Enforced in** | `perception.prompt.fence("message_text", ...)`; `perception.parse.to_perception`; `policy.rules.ROUTER_DIRECTIVE` |
| **Logged** | `FENCE_TOKEN_IN_UNTRUSTED_INPUT` if the content contained a fence-like token; `UNTRUSTED_FIELD_TRUNCATED` with the original length if capped; `CONTROL_CHARACTERS_STRIPPED` with a count. When rule 2 fires, the `RowRecord` lists `ROUTER_DIRECTIVE` in `override_codes_fired`; the metrics engine counts it post hoc. |

**Detection is semantic, not pattern-based.** The model is asked whether the content asserts a routing decision, a priority, a trust attribute, or a processing directive — a structural question. No regex over observed phrasings is used, per [GENERALIZATION_POLICY §2.3](GENERALIZATION_POLICY.md#23-the-five-identified-injection-bearing-message-ids); an attacker writes whatever phrasing a pattern list omits.

---

### T2 — Image content (OCR / visual description)

An attached image is attacker-controlled and may contain rendered text saying anything, including instructions.

| | |
|---|---|
| **Mitigation** | The image is read in a **separate, isolated model call** whose only task is to describe visible content. Its output is a string that is then fenced (M2) and inserted into the perception call as untrusted data — never as instruction. The describing call is given no routing vocabulary: it does not know what `notify`, `digest`, or `mute` mean, so text in an image telling it to "mark this urgent" has nothing to act on. Resource caps per M4. |
| **Enforced in** | `media.extract.extract`; `perception.prompt.fence("image_content", ...)` |
| **Logged** | `MEDIA_LIMIT_EXCEEDED` with the limit breached; `MEDIA_DECODE_FAILED` with the reason; `FENCE_TOKEN_IN_UNTRUSTED_INPUT` if the extracted description contained a fence-like token |

**Two-stage isolation is the point.** A single vision call that both reads the image and decides routing would let text rendered in a poster address the router directly. Splitting them means the image's text reaches the routing call already demoted to a quoted description.

---

### T3 — Voice transcripts

Eight of 110 target messages are voice notes with **empty `message_text`** ([DATASET_ANALYSIS §3](DATASET_ANALYSIS.md#3-media-presence)), so the transcript is the entire message. Spoken instructions to the router are as available to an attacker as written ones.

| | |
|---|---|
| **Mitigation** | Identical to T2: transcription is a separate isolated call with no routing vocabulary; the transcript is fenced as untrusted data. Duration and byte caps per M4. A failed or empty transcription sets `MediaArtifact.readable = False`, which sets `F.media_unread`, which costs `W_MEDIA_UNREAD` in confidence ([DECISION_ENGINE §5](DECISION_ENGINE.md#5-confidence-formula)) — the system says out loud that it decided without the content. |
| **Enforced in** | `media.decode.decode_audio`; `media.extract.extract`; `features.content.media_unread` |
| **Logged** | `MEDIA_DECODE_FAILED` with `media_id` and the detected container; `MEDIA_LIMIT_EXCEEDED`; the row's `media_outcome` is recorded as `failed` with a `media_failure_cause` |

**Never silently empty.** A decode failure that produced an empty transcript and routed the message as if it were blank would be an invisible misrouting. `readable=False` is a distinct state from "read, and it was empty", and only the latter is treated as content.

---

## 5. Non-injection integrity

### N1 — Unverifiable media

**Concern.** All 13 audio files carry a `.mp3` extension; only 4 are MP3, 5 are WAV, 4 are AAC ([DATASET_ANALYSIS §3](DATASET_ANALYSIS.md#3-media-presence)). A decoder that trusts the extension fails on 9 of 13 — and if that failure is swallowed, 6 of the 8 target voice notes route on no content at all.

**Mitigation.** Container identification is by content sniffing, never by filename. One uniform decode path. Failure is a first-class state, not an exception to swallow: `MediaArtifact.readable=False` with a `failure_reason`, propagated to `F.media_unread` and penalised in confidence.

**Enforced in** `media.decode.decode_audio` (sniff), `media.extract.extract` (state), `features.content` (propagation).

**Logged.** `MEDIA_DECODE_FAILED` with `media_id`, detected container, and reason. The row's `media_outcome` is `failed`, so the metrics engine's media-coverage section reports a run that could not read half its audio rather than leaving it in nobody's notes.

---

### N2 — Domain mismatch against `business_accounts`

**Concern.** Two failure directions. Reading a mismatch where none can be established would mute legitimate senders; missing one would let impersonation through.

**Mitigation.** `DomainState` is three-valued. `MISMATCH` requires **both** domain fields to be non-blank and different. A blank on either side yields `UNDETERMINED`, which no rule treats as adverse. `IMPERSONATION` additionally requires `not verified`, which separates an unconfirmed party using a lookalike domain from a confirmed brand using a campaign or link-shortener domain.

**Enforced in** `features.business.domain_state`, `policy.rules.IMPERSONATION`.

**Logged.** `BUSINESS_DOMAIN_MISMATCH` whenever the state is `MISMATCH`, whether or not `IMPERSONATION` fires — so verified-brand campaign domains are visible in the report as a distinct population from impersonation. `BUSINESS_IDENTITY_INCONSISTENT` when `display_name != brand_name`.

**Comparison is normalised, not literal:** case-folded, whitespace-trimmed, leading `www.` removed. Nothing beyond that — no lookalike-character analysis, no edit-distance threshold, because any threshold would be a source-(c) constant.

---

### N3 — Contradiction between media content and message text

**Concern.** 7 of the 11 images used by `messages.csv` disagree with their caption or the sender's brand — including on unambiguously legitimate traffic ([DATASET_ANALYSIS §3](DATASET_ANALYSIS.md#3-media-presence)). The tempting inference — mismatch means deception — is wrong here, and a mismatch detector would fire mostly on legitimate senders.

**Mitigation.** `P.media_contradicts_text` is recorded and **no rule reads it**. It raises no risk and changes no action. It is counted in the report so a human can review the population.

**Enforced in** `policy/rules.py` — by the absence of any rule referencing the field, asserted by a test that fails if any rule's trigger names it.

**Logged.** `MEDIA_CONTRADICTS_TEXT` with `message_id` and `media_id`, recorded in the row's `integrity_warnings` and counted post hoc by the metrics engine.

**Recorded rationale.** [PROBLEM_ANALYSIS D11](PROBLEM_ANALYSIS.md#d11--how-image-content-is-weighted). Stock and recycled campaign imagery is normal in consumer messaging; disagreement between an attachment and a caption carries almost no information about legitimacy. The inverse artifact — skipping images because most mismatch — is also refused: some images carry the entire substance of their message.

---

### N4 — Media path traversal

**Concern.** `file_path` values come from a CSV. A crafted path (`../../etc/passwd`, an absolute path, a symlink out of the tree) would read outside the dataset.

**Mitigation.** Resolve the joined path, resolve the dataset root, and require the former to be relative to the latter after both are fully resolved (symlinks included). Reject otherwise.

**Enforced in** `media.locate.resolve_media_path`.

**Logged.** `MEDIA_PATH_ESCAPED_ROOT` with the offending `media_id` and the rejected path. Raises `MediaPathError`; the message is routed with `media_failed=True` (`media_unread` too when it has no text).

---

### N5 — Resource exhaustion

**Concern.** A 110-minute audio file exists in this dataset (`vn_014`). A hostile corpus could contain a decompression bomb, a gigapixel image, or an audio file long enough to stall the run.

**Mitigation.** Hard caps on bytes, audio duration, and image pixel count, checked **before** decode. The model client has a per-call timeout and a bounded attempt count. A single message that exceeds a cap is degraded, never fatal: it routes with `media_failed=True` (`media_unread` too when it has no text).

**Enforced in** `media.decode`, `media.extract`, `perception.client.perceive`.

**Logged.** `MEDIA_LIMIT_EXCEEDED` with the limit name and the observed value.

---

### N6 — Evidence integrity

**Concern.** Citing a history row belonging to a different user, or one that post-dates the target, would be a wrong answer and a small privacy leak — the evidence field would point at another user's message.

**Mitigation.** Two invariants asserted at selection time: every candidate's `user_id` equals the target's `user_id`, and every candidate's `created_at` is strictly earlier than the target's. Violations raise rather than filter silently, because either indicates an indexing bug rather than a data condition.

**Enforced in** `scoring.evidence.select_evidence`.

**Logged.** `EVIDENCE_OWNER_MISMATCH`, `EVIDENCE_NOT_IN_PAST`, each with both message ids. Both raise `EvidenceIntegrityError`.

---

### N7 — Model response tampering

**Concern.** A response that adds a `confidence`, supplies an `evidence_message_ids`, or returns an unrecognised action would bypass the code-owned scoring path if accepted leniently.

**Mitigation.** M3 — `extra="forbid"`, enum-constrained fields, no lenient coercion, no default fallback on parse failure. Retry with the same bundle up to `MODEL_MAX_ATTEMPTS`; then raise and record the message as a failure.

**Enforced in** `perception.parse.to_perception`.

**Logged.** `MODEL_RESPONSE_SCHEMA_REJECTED` with the validation error path and the attempt number. Never log the raw response body — it contains untrusted text.

---

### N8 — Determinism

**Concern.** Non-determinism makes a wrong output unreproducible and violates `AGENTS.md` §6.3.

**Mitigation.** Temperature 0 and a fixed seed where the API supports it; sorted iteration over every mapping; ties in evidence ranking broken by a total order (tier, then recency, then `message_id` ascending) so no two runs can differ; no `datetime.now()` anywhere in decision logic — all time comparisons are between dataset timestamps.

**Enforced in** `perception.client`, `scoring.evidence`, and a convention forbidding wall-clock reads outside `logging_setup`, `code/main.py`, `emit/record.py`, and `cache/store.py` — the stamping modules; never in decision logic ([ENGINEERING_CONVENTIONS §7](ENGINEERING_CONVENTIONS.md#7-what-may-not-appear-in-the-codebase)).

---

## 6. Residual risk

Stated plainly, because a threat model that claims full coverage is not one.

| # | residual risk | why it is not closed |
|---|---|---|
| R1 | **A group admin making a calm off-channel payment request.** `UNAUTHORIZED_PAYMENT_REQUEST` requires `not sender_holds_payment_authority`, which an admin satisfies; `COERCIVE_PAYMENT` requires a threat and a deadline. An admin who asks politely for an off-channel payment with no threat passes both. | Closing it would mean treating every admin payment request as a scam, which contradicts the statement's own example of a legitimate admin payment reminder. The trade is deliberate. |
| R2 | **A compromised verified business.** `IMPERSONATION` requires `not verified`. A verified account that is taken over sends from its own domain with its own identity. | `verified` is the platform's assertion and the only identity anchor available. Nothing in the dataset can detect its compromise. Rules 1 and 4 still apply and would catch a credential or coercive-payment attempt from such an account. |
| R3 | **A router directive the model does not recognise.** Detection is semantic, so it has a false-negative rate. | Pattern matching would be worse — it fails on anything not previously observed. The consequence is bounded: a missed directive means the message is judged on its residual content, which for every injection observed in this corpus is itself a credential request caught by rule 1. |
| R4 | **Adversarial media.** An image or audio crafted to be misdescribed by the extraction model. | The two-stage isolation means the misdescription enters as untrusted data, so it can mislead the *type* but cannot issue an instruction. The two compromise-resistant rules (§1) are unaffected; the perception-dependent rules are exposed to a misdescription exactly as they are to any other perception error. |
| R5 | **Suppression of a legitimate message.** An attacker who can raise `user_reports_30d` against a rival business could in principle degrade it. | Not exploitable here: no rule reads `user_reports_30d` ([DECISION_ENGINE §3](DECISION_ENGINE.md#3-what-no-rule-does)). Noted because it would become live if that field were ever added to a trigger. |

R1 is the sharpest of these. It is accepted rather than fixed because the alternative fails the statement's explicit case, and because the graded engagement signal still applies: an admin whose prior messages this user dismissed carries a `DISMISSIVE` stance, and `REPEAT_DISMISSAL` caps the action at `mute` independently of any payment reasoning.

---

## 7. Logging

Structured JSON Lines to `run/events.jsonl`. One object per line, fixed field set.

```json
{
  "ts": "2026-08-01T19:55:12.481Z",
  "level": "WARNING",
  "event": "MEDIA_DECODE_FAILED",
  "message_id": "…",
  "media_id": "…",
  "path": "media.decode.decode_audio",
  "detail": {"detected_container": "aac", "reason": "…"},
  "action_taken": "media_unread"
}
```

Rules for what may be logged:

- **Never log untrusted text verbatim.** `message_text`, transcripts, image descriptions, display names, and group names are logged as a SHA-256 prefix and a length, never as content. The hash lets repeated payloads be correlated across a run without storing any of them.
- **Never log secrets.** API keys are read from the environment in `perception.client` and never enter a log record, an exception message, or a traceback. Per `AGENTS.md` §5.4.
- **Every `ThreatEvent` value is a loggable event name**, and every event name is a `ThreatEvent` value. A test asserts the two sets are equal, so a mitigation cannot fire without a counter existing for it.
- **`action_taken` is mandatory** on every threat event. A mitigation that logged a concern without recording what it did about it would be a warning nobody can act on.

Every event is also recorded on the originating row's `integrity_warnings`, so the metrics engine can count it post hoc without parsing the log. A run in which `MEDIA_DECODE_FAILED` fired six times, or in which `ROUTER_DIRECTIVE` fired at all, says so in its metrics report ([METRICS.md](METRICS.md)).
