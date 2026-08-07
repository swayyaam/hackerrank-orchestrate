# DECISION_ENGINE.md

The deterministic policy layer: the precedence table, conflict resolution, and the confidence formula.

Companion documents: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), [GENERALIZATION_POLICY.md](GENERALIZATION_POLICY.md), [PROBLEM_ANALYSIS.md](PROBLEM_ANALYSIS.md), [THREAT_MODEL.md](THREAT_MODEL.md).

---

## 1. What the engine is

`policy.engine.decide(perception: Perception, features: FeatureVector) -> Decision` is a pure function. Same inputs, same output, no I/O, no clock, no randomness.

It receives two things:

- **`Perception`** — the model's proposal and its content claims. Untrusted in the security sense: a successful injection corrupts it. Treated as a *witness about content*, never as an authority about outcome.
- **`FeatureVector`** — facts computed in code from structured CSV fields. Not influenceable by message text.

Every Tier 0 rule that can force `mute` is designed so that its trigger either (a) rests entirely on `FeatureVector`, or (b) rests on a `Perception` claim whose *firing* can only produce `mute` — corrupting one never yields an escalation. A model that is talked into denying `requests_authentication_secret` still faces `IMPERSONATION` and `REPEAT_DISMISSAL`, neither of which it can influence in any direction ([THREAT_MODEL §1](THREAT_MODEL.md#1-trust-model) states what it *can* influence, rule by rule).

### Rule effects

| effect | meaning |
|---|---|
| `HARD_OVERRIDE` | Sets the action outright. First one to fire wins; evaluation stops. |
| `CAP` | The action may not exceed a ceiling. Multiple caps take the lowest. |
| `FLOOR` | The action may not fall below a floor. Multiple floors take the highest. |
| `GUARD` | Fires no action of its own; suppresses named other rules. |
| `DEFAULT` | Applies when nothing else did: take the perception's proposal. |

Action ordering for caps and floors is `mute(0) < digest(1) < notify(2)`.

### Resolution algorithm

```
1. Evaluate GUARD rules. Collect the set of suppressed rule codes.
2. Evaluate HARD_OVERRIDE rules in precedence order.
   - Record every rule that fires.
   - The first firing is APPLIED; any later firing is SUPERSEDED.
   - If one fired: action and type are set by it. All CAP/FLOOR/DEFAULT rules
     are recorded NOT_EVALUATED. Stop.
3. Otherwise:
   a. action := perception.proposed_action
      type   := perception.type_candidates[0]
   b. Apply FLOOR rules that fire and are not suppressed:
        action := max(action, max(floor_actions))
   c. Apply CAP rules that fire and are not suppressed:
        action := min(action, min(cap_actions))
      A floor overridden at this step is recorded OVERRIDDEN_BY_CAP.
   d. If no cap and no floor changed the action, the DEFAULT rule is APPLIED.
      Otherwise the APPLIED firing is the cap or floor that determined the
      final value; the rest are recorded as fired-but-not-determining.
4. Exactly one firing carries status APPLIED. Its justification becomes the reason.
```

**Caps beat floors.** A cap encodes an explicit instruction or entitlement of the receiving user — they muted this conversation, they opted out of this marketing, they have no relationship with this sender. A floor encodes the system's own judgement that something is important. When the user's expressed preference and the system's judgement conflict, the user's preference wins, with one exception: `DIRECT_ADDRESS_GUARD`, which exists because the problem statement requires it — *"A muted family group can still contain an urgent direct mention."*

---

## 2. Precedence table

Rules are evaluated in the order shown. Every trigger is stated in concrete field names from [SYSTEM_DESIGN.md §5](SYSTEM_DESIGN.md#5-schemas). `P.` denotes `Perception`, `F.` denotes `FeatureVector`.

---

### Tier 0 — Safety

Justified by the statement: *"At the same time, clear scam or safety risk should be muted regardless of the user's usual engagement."* and *"Risky messages should use `mute` with an appropriate `message_type` such as `scam` or `spam`."*

---

#### 1. `CREDENTIAL_SOLICITATION`

| | |
|---|---|
| **Effect** | `HARD_OVERRIDE` |
| **Trigger** | `P.requests_authentication_secret` |
| **Sets action** | `mute` |
| **Sets type** | `scam` |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |

**Justification text**
> The message asks the user to hand over an authentication secret, which no legitimate sender requires.

**Why this generalises.** No legitimate sender — bank, delivery service, colleague, or group admin — ever needs a recipient's OTP, PIN, password, or login code. This is a property of how authentication works, not of this corpus. The rule fires regardless of sender verification, admin role, or engagement history, which is exactly what "regardless of the user's usual engagement" requires.

---

#### 2. `ROUTER_DIRECTIVE`

| | |
|---|---|
| **Effect** | `HARD_OVERRIDE` |
| **Trigger** | `P.contains_router_directive` |
| **Sets action** | `mute` |
| **Sets type** | `scam` |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |

**Justification text**
> The message contains instructions aimed at the routing system rather than at the recipient.

**Why this generalises.** A sender writing to a person has no reason to address the delivery system. Content that asserts a routing decision, a priority, a trust attribute, or a processing directive is by construction not addressed to the recipient, and its presence is evidence of an attempt to manipulate delivery. The rule is **independent of whether the injected claim is true** ([PROBLEM_ANALYSIS D12](PROBLEM_ANALYSIS.md#d12--injection-carrying-a-factually-true-claim)); verifying the claim would make attacker-supplied text load-bearing in the trust model, and an attacker can always choose a true claim.

**Type note.** The type is `scam` even when the residual content is benign, because engineering a message to deceive the delivery system is itself the deceptive act. The alternative — typing from the residual content — was considered and rejected as it would reward an attacker for wrapping a directive around innocuous filler.

---

#### 3. `IMPERSONATION`

| | |
|---|---|
| **Effect** | `HARD_OVERRIDE` |
| **Trigger** | `message.conversation_type == BUSINESS` **and** `not F.business_verified` **and** (`F.domain_state == MISMATCH` **or** `not F.business_identity_consistent`) |
| **Sets action** | `mute` |
| **Sets type** | `scam` |
| **Asserts pattern** | no |
| **Trigger source** | **structural** |

**Justification text**
> An unverified business account is presenting under a brand or domain it does not own.

**Why this generalises.** Two independent integrity properties, either sufficient: an account sending from a domain other than the one its brand owns, or an account whose display identity differs from the brand it claims. Verification is the condition that distinguishes a campaign or link-shortener domain operated under a confirmed identity from an unconfirmed party using a lookalike.

**`UNDETERMINED` is not `MISMATCH`.** `F.domain_state` is three-valued precisely because either domain field may be blank. A business with no recorded official domain cannot be *shown* to be sending from a domain it does not own, so the mismatch branch does not fire — it is not evidence of impersonation, it is absence of evidence. This is the [G5](GENERALIZATION_POLICY.md#g5--unseen-input) unseen-input requirement discharged: without it, every account with a blank `official_domain` would be muted as a scam.

**No corroborating signal is required to trigger.** `account_age_days`, `domain_used_by_sender_age_days`, and `user_reports_30d` are *not* in the trigger. They are real signals but any threshold on them would be a source-(c) constant read off the corpus, prohibited by [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance). They are unused.

---

#### 4. `COERCIVE_PAYMENT`

| | |
|---|---|
| **Effect** | `HARD_OVERRIDE` |
| **Trigger** | `P.requests_payment_or_payment_proof` **and** `P.threatens_adverse_consequence` **and** `P.states_deadline_within_day` |
| **Sets action** | `mute` |
| **Sets type** | `scam` |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |

**Justification text**
> The message pairs a payment demand with a threatened consequence and an immediate deadline.

**Why this generalises.** Payment demand + threatened penalty + immediacy is the canonical social-engineering triad, and it is diagnostic because of what it *rules out*: a genuine institutional payment process does not depend on the recipient acting off-channel within hours to avoid a penalty, because institutions have billing cycles, notices, and appeal paths. The rule deliberately **ignores sender standing** — an admin, a verified business, and a stranger all trigger it identically. That is what makes it able to catch an authority-holder who is compromised or malicious, which sender-trust rules structurally cannot.

**All three conjuncts are required.** A deadline without a threat is an ordinary reminder. A threat without a payment demand is not a payment scam. Dropping any conjunct would mute legitimate dated reminders.

---

#### 5. `UNSAFE_INSTRUCTION`

| | |
|---|---|
| **Effect** | `HARD_OVERRIDE` |
| **Trigger** | `P.advises_action_with_safety_consequence` |
| **Sets action** | `mute` |
| **Sets type** | `P.type_candidates[0]` (unchanged) |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |

**Justification text**
> The message advises an action that carries a plausible health or safety consequence.

**Why this generalises.** The `mute` definition includes "unsafe". Content advising a recipient to take or stop a medical, physical, or financial action against professional guidance is unsafe on its face, independent of who sent it. Type is left as perceived because such content is usually a circulated `forward` rather than a `scam` — the sender may be a well-meaning relative — and mislabelling it `scam` would be inaccurate.

---

### Tier 1 — Payment authority

Justified by the statement: *"A payment reminder may be legitimate from a trusted admin but risky from a new sender."*

---

#### 6. `UNAUTHORIZED_PAYMENT_REQUEST`

| | |
|---|---|
| **Effect** | `HARD_OVERRIDE` |
| **Trigger** | `P.requests_payment_or_payment_proof` **and** `P.directs_off_platform_for_money_or_identity` **and** `not F.sender_holds_payment_authority` |
| **Sets action** | `mute` |
| **Sets type** | `scam` |
| **Asserts pattern** | no |
| **Trigger source** | perceptual (with a structural conjunct) |

**Justification text**
> A payment instruction arrived from a sender with no established authority for payments in this conversation.

**`F.sender_holds_payment_authority` is computed in `features/`, not perceived:**

```
group conversation    → sender_membership.role == ADMIN
business conversation → business.verified
                        and domain_state == MATCH
                        and business_relationship is not None
personal conversation → False
```

**Why this generalises.** This is the statement's own sentence turned into a check. "Trusted admin" maps to a recorded role; "new sender" maps to the absence of an authority relationship. The `directs_off_platform` conjunct is what separates a legitimate reminder from a diversion: an authorised party points at the official channel, an unauthorised one supplies its own. Personal conversations are never payment-authoritative because there is no institutional relationship to be authoritative within.

---

### Tier 2 — Guards

---

#### 7. `DIRECT_ADDRESS_GUARD`

| | |
|---|---|
| **Effect** | `GUARD` |
| **Trigger** | `P.addresses_recipient_directly` **and** `F.sender_stance != DISMISSIVE` |
| **Suppresses** | `GROUP_MUTE_CAP`, `COLD_SENDER_CAP` |
| **Sets action** | — (never sets an action) |
| **Trigger source** | perceptual |

**Justification text** *(emitted only when it actually suppresses a rule)*
> The message addresses the user directly, so conversation-level suppression does not apply.

**Why this generalises.** Muting a conversation is a statement about the conversation's ambient traffic, not a refusal to hear from anyone in it. This is the statement's requirement — *"A muted family group can still contain an urgent direct mention"* — implemented at the only place it could be: as a suppressor of the conversation-level caps.

**It does not suppress Tier 0 or Tier 1, and it does not suppress consent caps.** A direct address does not make a credential request safe, and does not revoke a marketing opt-out. The `sender_stance != DISMISSIVE` conjunct prevents a sender the user has repeatedly muted from re-acquiring reach by adding a mention.

---

### Tier 2b — The risk-type cap

Justified by the same two sentences as Tier 0, which Tier 0 alone did not fully deliver: *"clear scam or safety risk should be muted regardless of the user's usual engagement"* and *"Risky messages should use `mute` with an appropriate `message_type` such as `scam` or `spam`."*

---

#### 8. `RISK_TYPE_CAP`

| | |
|---|---|
| **Effect** | `CAP` |
| **Trigger** | `P.type_candidates[0] in {scam, spam}` |
| **Sets action** | `mute` |
| **Sets type** | — |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |
| **Suppressible** | **no** — deliberately absent from every guard's `suppresses` set |

**Justification text**
> The message reads as a scam or as unsolicited bulk contact.

**The defect this closes.** The Tier 0 rules mute a row when a *structural* safety condition holds — a credential request, an unverified brand, a payment from a sender with no authority. They do not fire when the model types a message `scam` on content alone with nothing structural to corroborate it. On such a row the type was `scam` and the action fell through to the floors, and **two corpus rows the model had typed `scam` and proposed muting were raised to `notify`** — one by `TIME_CRITICAL_FLOOR`, one by `DIRECT_REQUEST_FLOOR`. A same-day deadline and a direct request for a response are precisely what a scam manufactures, so the two floors were reading the attack's own payload as grounds to interrupt the user. The table permitted a floor to override a scam classification, and nothing in it said otherwise. That is a coherence defect in the precedence, not a threshold to tune.

**Why a cap rather than an exclusion on the two floors.** Excluding the floors would have fixed those two rows and left the hole open: a model that *proposed* `notify` on a `scam`-typed row would still have emitted it, no floor involved. A cap is evaluated against whatever the action already is, so one rule closes both routes.

**Why it is not suppressible.** `DIRECT_ADDRESS_GUARD` disarms the two conversation-level caps because a message addressed to you personally is not conversation noise. A scam addresses you personally on purpose. Letting the guard reach this rule would rebuild the defect through the guard, so `RISK_TYPE_CAP` is absent from its `suppresses` set and a test asserts no guard may ever contain it.

**Why the trigger reads `type_candidates[0]`.** That *is* the emitted type on this path: the engine sets `message_type` from it before any floor or cap runs, and no floor or cap declares `sets_type`. Only Tier 0 sets a type, and those return before this rule is reached — while also setting `mute`, so they can never produce the state this cap prevents. `test_no_cap_or_floor_may_set_a_type` pins that assumption so a future rule cannot silently reopen the hole.

**Backstop.** `policy/engine._build` raises `PolicyError` if any decision resolves to `notify` with a risk type. It raises rather than correcting the action: a silent repair would hide the fact that the rule table had stopped being sufficient.

**Why this generalises.** It reads the type the system itself assigned, not any property of this corpus. Any message the system is prepared to call a scam is a message it is not prepared to interrupt for — the statement says so directly.

---

### Tier 3 — Consent and relationship caps

---

#### 9. `PROMOTION_OPT_OUT`

| | |
|---|---|
| **Effect** | `CAP` → `mute` |
| **Trigger** | `F.promotions_opted_out` **and** `P.solicits_purchase` |
| **Sets type** | unchanged (typically `promotion`) |
| **Asserts pattern** | no |
| **Trigger source** | **mixed** — a structural consent fact conjoined with a perception claim; counts as **perceptual** for the confidence trigger term (§5) |

**Justification text**
> The user has opted out of marketing messages from this sender.

`F.promotions_opted_out` is `not relationship.allows_promotions or relationship.promotions_opted_out_at is not None`.

**Why the trigger is mixed.** The `P.solicits_purchase` conjunct scopes the cap to marketing: an opted-out user must still receive *transactional* messages from that business (an order update is not a promotion). No structural field distinguishes marketing from transactional content, so the scoping is necessarily perceptual — and that conjunct is what a compromised model would deny to defeat this rule ([THREAT_MODEL §1](THREAT_MODEL.md#1-trust-model)).

**Why this generalises.** Consent is the definition of "unwanted", which is one of the enumerated grounds for `mute`. Marketing sent to someone who withdrew consent is unwanted by construction, irrespective of the offer's quality or relevance. The type is left as perceived so that the output still records *what* was muted.

---

#### 10. `REPEAT_DISMISSAL`

| | |
|---|---|
| **Effect** | `CAP` → `mute` |
| **Trigger** | `F.prior_interaction_count >= MIN_PATTERN_OBSERVATIONS` **and** `F.dismissal_ratio > DISMISSAL_MAJORITY` |
| **Sets type** | unchanged |
| **Asserts pattern** | **yes** → up to `MAX_IDS_PATTERN_RULE` evidence ids |
| **Trigger source** | **structural** |

**Justification text**
> Comparable messages from this sender were repeatedly dismissed or muted by this user.

**Thresholds and their provenance** — both [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance) source-(b):

| constant | value | justification |
|---|---:|---|
| `MIN_PATTERN_OBSERVATIONS` | 2 | One instance is an event; two is the minimum that can constitute a pattern. The justification text says "repeatedly", and one occurrence cannot support that word. |
| `DISMISSAL_MAJORITY` | 0.5 | A strict majority is the weakest claim that supports "usually". Any higher value would be a preference without an argument. |

Neither number was read off a distribution in `dataset/`.

---

#### 11. `FREQUENTLY_FORWARDED_BROADCAST`

| | |
|---|---|
| **Effect** | `CAP` → `mute` |
| **Trigger** | `F.is_frequently_forwarded` **and** `not P.addresses_recipient_directly` **and** `P.is_circulated_content` |
| **Sets type** | unchanged (typically `forward` or `greeting`) |
| **Asserts pattern** | **yes** |
| **Trigger source** | perceptual (with a structural conjunct) |

**Justification text**
> This is widely circulated content that was not written for this particular user.

`F.is_frequently_forwarded` is `message.forwarded_count >= FORWARD_CHAIN_THRESHOLD`.

**Threshold provenance.** `FORWARD_CHAIN_THRESHOLD = 5`, sourced (b) from WhatsApp's own product definition: the platform labels a message "forwarded many times" once it has passed through five or more chats, and restricts onward forwarding at that point. This is a published domain constant about the platform being modelled, not a value read from the corpus.

**The type is not forced.** `forwarded_count` alone never makes a message a `forward` — [PROBLEM_ANALYSIS §4.9](PROBLEM_ANALYSIS.md#49-greeting-vs-forward) establishes that a forwarded salutation is still a `greeting`. That is why `P.is_circulated_content` is a separate conjunct from the type, and why the rule caps the action without touching the type.

---

#### 12. `GROUP_MUTE_CAP`

| | |
|---|---|
| **Effect** | `CAP` → `digest` |
| **Trigger** | `F.group_muted_by_receiver` |
| **Suppressed by** | `DIRECT_ADDRESS_GUARD` |
| **Sets type** | unchanged |
| **Asserts pattern** | no |
| **Trigger source** | **structural** |

**Justification text**
> The user has muted this conversation, so the message is shown later instead of interrupting.

**Why the cap is `digest` and not `mute`.** Muting a conversation in a messaging client means "do not interrupt me for this", not "hide this from me". The correct translation is therefore a ceiling on interruption, not suppression of content. This is a property of what the mute control means to users, and it is also the reading that keeps the rule compatible with the statement's insistence that a muted group can still surface something important.

---

#### 13. `COLD_SENDER_CAP`

| | |
|---|---|
| **Effect** | `CAP` → `digest` |
| **Trigger** | `not F.relationship_established` |
| **Suppressed by** | `DIRECT_ADDRESS_GUARD` |
| **Sets type** | unchanged |
| **Asserts pattern** | no |
| **Trigger source** | **structural** |

**Justification text**
> There is no established relationship with this sender, so the message can wait.

`F.relationship_established` is `prior_interaction_count > 0 or business_relationship is not None`.

**Why this generalises.** An unestablished sender has not earned the right to interrupt. This is a conservative default, not a risk judgement: the message is still delivered, just not urgently. The guard exists so that a genuine first contact that directly asks the recipient something is not held back.

**Note.** Absence of a business relationship is *not* treated as evidence of impersonation — [PROBLEM_ANALYSIS D13](PROBLEM_ANALYSIS.md#d13--whether-group-size-affects-routing) records that the cold-business set mixes lookalike accounts with legitimate brands the user simply has not transacted with. Impersonation is decided by rule 3 on integrity fields alone.

---

### Tier 4 — Priority floors

---

#### 14. `TIME_CRITICAL_FLOOR`

| | |
|---|---|
| **Effect** | `FLOOR` → `notify` |
| **Trigger** | `P.states_deadline_within_day` **and** `P.deadline_has_stated_consequence` **and** `F.sender_stance != DISMISSIVE` **and** `not P.solicits_purchase` |
| **Sets type** | unchanged |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |

**Justification text**
> The message carries a same-day deadline from a sender this user engages with.

**Why "within a day".** A day is the natural unit of a person's schedule: something due today cannot be deferred to the next digest without risk of being missed. No shorter horizon is defensible without inventing a number.

**Why the consequence conjunct.** `P.states_deadline_within_day` asserts only that a time is stated. [PROBLEM_ANALYSIS §4.6](PROBLEM_ANALYSIS.md#46-personal-vs-urgent) defines urgency as a stated window **with a consequence attached to missing it**, and without that second conjunct this floor interrupted on every announced schedule — a fire-alarm test, a delivery window, a class moved to another room — none of which asks anything of the recipient. The claim itself could not be narrowed to carry the consequence, because `COERCIVE_PAYMENT` reads the same field and needs the plain "a deadline is stated" reading; hence a separate claim rather than a redefinition.

**Why `not solicits_purchase`.** Commercial urgency is manufactured — "offer ends tonight" is a sales technique, not a deadline with a consequence for the recipient. Excluding purchase solicitations prevents marketing from claiming the floor.

---

#### 15. `DIRECT_REQUEST_FLOOR`

| | |
|---|---|
| **Effect** | `FLOOR` → `notify` |
| **Trigger** | `P.addresses_recipient_directly` **and** `P.requests_response_or_action` **and** `F.sender_stance == RECEPTIVE` |
| **Sets type** | unchanged |
| **Asserts pattern** | no |
| **Trigger source** | perceptual |

**Justification text**
> The sender is asking this user directly for a response or an action.

**Why the request conjunct — addressed is not asked.** The rule's own justification is "asking this user directly for a response or an action", but `P.addresses_recipient_directly` asserts only that the message speaks to this recipient. Without the second conjunct the floor interrupted on *"Reached home and had dinner. Don't call now, phone is charging. We can talk tomorrow morning"* — a message that names the recipient and explicitly declines a response. As with rule 13, the underlying claim could not be narrowed: [rule 7](#7-direct_address_guard) needs the broad reading, since a mention should defeat a conversation-level mute whether or not it asks for anything.

**Why `RECEPTIVE` and not merely "not dismissive".** A floor to `notify` is the strongest positive claim the engine makes, so it requires positive evidence: this user has actually engaged with this sender before. `NEUTRAL` and `UNKNOWN` are insufficient — the message still routes on the perception's proposal, it just does not get a guaranteed interruption.

---

### Tier 5 — Default

---

#### 16. `PERCEPTION_ACCEPTED`

| | |
|---|---|
| **Effect** | `DEFAULT` |
| **Trigger** | no hard override fired, and no cap or floor changed the action |
| **Sets action** | `P.proposed_action` |
| **Sets type** | `P.type_candidates[0]` |
| **Trigger source** | perceptual |

**Justification text** — composed by `compose.reason` rather than fixed, since there is no single rule to name. The composer renders one sentence from `(message_type, action, dominant_feature)` using a small clause grammar:

```
subject_clause(message_type, conversation_type) + qualifier_clause(action, dominant_feature)
```

**`dominant_feature` is selected by a fixed priority list over `FeatureVector` fields, consulted in order per action; the first predicate that holds for the row wins.** The lists are data in `compose/reason.py`, deterministic, and exhaustive by construction (the last entry always holds):

| priority | `notify` | `digest` | `mute` |
|---:|---|---|---|
| 1 | `sender_is_group_admin` | `media_unread` | `sender_stance == DISMISSIVE` |
| 2 | `sender_stance == RECEPTIVE` | `business_verified` | `promotions_opted_out` |
| 3 | `business_verified and business_relationship_known` | `sender_stance == NEUTRAL` | `is_frequently_forwarded` |
| 4 | `relationship_established` | `not relationship_established` | `group_muted_by_receiver` |
| 5 | *(fallback)* `conversation_type` | *(fallback)* `conversation_type` | *(fallback)* `conversation_type` |

For example, `(BUSINESS_UPDATE, digest, business_verified=True)` renders as *"A verified business is sending a routine update that does not need immediate attention."* The clause fragments were authored from the type definitions in `problem_statement.md`, not copied from the sample's `reason` column, and the composer enforces the register bounds in [SYSTEM_DESIGN.md §7](SYSTEM_DESIGN.md#7-config) (`MIN_CHARS`, `MAX_CHARS`, one sentence).

---

### Summary

| # | code | effect | sets action | type forced | trigger source | pattern |
|---:|---|---|---|---|---|---|
| 1 | `CREDENTIAL_SOLICITATION` | hard | `mute` | `scam` | perceptual | no |
| 2 | `ROUTER_DIRECTIVE` | hard | `mute` | `scam` | perceptual | no |
| 3 | `IMPERSONATION` | hard | `mute` | `scam` | **structural** | no |
| 4 | `COERCIVE_PAYMENT` | hard | `mute` | `scam` | perceptual | no |
| 5 | `UNSAFE_INSTRUCTION` | hard | `mute` | — | perceptual | no |
| 6 | `UNAUTHORIZED_PAYMENT_REQUEST` | hard | `mute` | `scam` | perceptual | no |
| 7 | `DIRECT_ADDRESS_GUARD` | guard | — | — | perceptual | no |
| 8 | `PROMOTION_OPT_OUT` | cap | `mute` | — | mixed → perceptual (§5) | no |
| 9 | `REPEAT_DISMISSAL` | cap | `mute` | — | **structural** | **yes** |
| 10 | `FREQUENTLY_FORWARDED_BROADCAST` | cap | `mute` | — | perceptual | **yes** |
| 11 | `GROUP_MUTE_CAP` | cap | `digest` | — | **structural** | no |
| 12 | `COLD_SENDER_CAP` | cap | `digest` | — | **structural** | no |
| 13 | `TIME_CRITICAL_FLOOR` | floor | `notify` | — | perceptual | no |
| 14 | `DIRECT_REQUEST_FLOOR` | floor | `notify` | — | perceptual | no |
| 15 | `PERCEPTION_ACCEPTED` | default | proposal | — | perceptual | no |

Four of the sixteen trigger on structured fields alone. Trigger provenance is not compromise-resistance, though: `DIRECT_ADDRESS_GUARD` can suppress two of the four on a perception claim, so the rules that remain correct **end to end** under a total compromise of the model are two — `IMPERSONATION` and `REPEAT_DISMISSAL`. The defeat condition for each of the others is enumerated in [THREAT_MODEL §1](THREAT_MODEL.md#1-trust-model).

---

## 3. What no rule does

Recorded so that the absences are deliberate rather than forgotten:

- **No rule reads `do_not_disturb_window`.** [PROBLEM_ANALYSIS D7](PROBLEM_ANALYSIS.md#d7--whether-dnd-windows-change-the-action) — routing decides importance; quiet hours are a delivery concern.
- **No rule reads `member_count`.** [D13](PROBLEM_ANALYSIS.md#d13--whether-group-size-affects-routing) — group size is not a proxy for personal relevance.
- **No rule reads `account_age_days`, `domain_used_by_sender_age_days`, or `user_reports_30d`.** Any threshold on them would be fitted to the corpus.
- **No rule reads `reaction_time_minutes` directly.** It enters only through the bounded engagement score in `features/engagement.py`, never as a comparison against a literal ([GENERALIZATION_POLICY §2.2](GENERALIZATION_POLICY.md#22-reaction_time_minutes-taking-only-three-distinct-values)).
- **No rule fires on `P.media_contradicts_text`.** [D11](PROBLEM_ANALYSIS.md#d11--how-image-content-is-weighted) — mismatch is normal in real messaging and is not a risk signal. It is logged and counted in the report only.
- **No rule reads `F.media_failed`.** A captioned image that failed to decode routes on its caption; only `F.media_unread` — no readable content at all — affects anything, and even that only through the confidence term, never through a rule.
- **No rule branches on the four-way event profile.** [GENERALIZATION_POLICY §2.1](GENERALIZATION_POLICY.md#21-the-four-collapsed-message_events-profiles-and-their-2828-correspondence-with-action).
- **`payment` is never forced by keyword.** It is available to the model as a type candidate and competes on merit ([D5](PROBLEM_ANALYSIS.md#d5--what-payment-means)).

---

## 4. Conflict resolution

Six worked cases. Each shows the firings and the resulting `Decision`.

---

### Case A — Two hard overrides both fire

*An unverified lookalike business account sends a message requesting an OTP.*

| rule | fires | status |
|---|---|---|
| 1 `CREDENTIAL_SOLICITATION` | yes | **`APPLIED`** |
| 3 `IMPERSONATION` | yes | `SUPERSEDED` |
| 4–6 | — | `NOT_EVALUATED` |
| 7–15 | — | `NOT_EVALUATED` |

**Result:** `mute` / `scam`. Reason from rule 1.

Precedence order alone resolves it — the first hard override to fire wins and evaluation stops. Rule 3 is still recorded, so the report shows both the credential attempt and the impersonation. Confidence takes a `W_CONTENTION` penalty for the superseded firing.

---

### Case B — Cap and floor conflict, no guard

*A society admin the user engages with posts a same-day deadline in a group the user has muted. The message does not name the user.*

| rule | fires | status |
|---|---|---|
| 7 `DIRECT_ADDRESS_GUARD` | no | — (`addresses_recipient_directly` is `False`) |
| 11 `GROUP_MUTE_CAP` | yes | **`APPLIED`** → cap `digest` |
| 13 `TIME_CRITICAL_FLOOR` | yes | `OVERRIDDEN_BY_CAP` → floor `notify` |

**Resolution:** start `digest` (proposal) → floor raises to `notify` → cap lowers to `digest`.

**Result:** `digest`. Reason from rule 11.

Caps beat floors. The user muted the conversation; the message is still delivered, just not as an interruption.

---

### Case C — Same as B, but the message names the user

*Identical, except the message directly addresses the recipient and the sender's stance is `RECEPTIVE`.*

| rule | fires | status |
|---|---|---|
| 7 `DIRECT_ADDRESS_GUARD` | yes | `APPLIED` as guard → suppresses 11 and 12 |
| 11 `GROUP_MUTE_CAP` | yes | `SUPPRESSED_BY_GUARD` |
| 13 `TIME_CRITICAL_FLOOR` | yes | **`APPLIED`** → floor `notify` |
| 14 `DIRECT_REQUEST_FLOOR` | yes | fired, not determining (same floor value) |

**Resolution:** start `digest` → guard removes the cap → floors raise to `notify` → no cap remains.

**Result:** `notify`. Reason from rule 13.

This is the statement's *"A muted family group can still contain an urgent direct mention"* discharged exactly. Cases B and C differ in one perception claim and produce opposite actions.

---

### Case D — Consent cap beats a floor

*An opted-out business sends a "sale ends tonight" promotion.*

| rule | fires | status |
|---|---|---|
| 7 `DIRECT_ADDRESS_GUARD` | no | — |
| 8 `PROMOTION_OPT_OUT` | yes | **`APPLIED`** → cap `mute` |
| 13 `TIME_CRITICAL_FLOOR` | no | `P.solicits_purchase` is `True`, so the floor excludes it |

**Result:** `mute` / `promotion`.

Two independent guards produce the right answer here. Even if `TIME_CRITICAL_FLOOR` had fired, the cap would beat it — and the floor's `not solicits_purchase` conjunct means manufactured commercial urgency never reaches the contest in the first place.

---

### Case E — Two caps fire

*A sender the user has repeatedly muted posts a widely-forwarded chain message into a group the user has also muted.*

| rule | fires | status |
|---|---|---|
| 9 `REPEAT_DISMISSAL` | yes | **`APPLIED`** → cap `mute` |
| 10 `FREQUENTLY_FORWARDED_BROADCAST` | yes | fired, not determining |
| 11 `GROUP_MUTE_CAP` | yes | fired, not determining → cap `digest` |

**Resolution:** `min(mute, mute, digest) = mute`. The `APPLIED` firing is the first rule in precedence order among those that produced the winning value — rule 9.

**Result:** `mute`. Reason from rule 9. Because rule 9 asserts a pattern, up to two evidence ids are cited.

---

### Case F — Guard fires but suppresses nothing

*A colleague with a `RECEPTIVE` stance directly asks the user something, in an unmuted group with an established relationship.*

| rule | fires | status |
|---|---|---|
| 7 `DIRECT_ADDRESS_GUARD` | yes | fired; suppresses nothing (11 and 12 did not fire) |
| 14 `DIRECT_REQUEST_FLOOR` | yes | **`APPLIED`** → floor `notify` |

**Result:** `notify`.

A guard that suppresses nothing emits no override code and contributes no justification. This keeps the report's counts meaningful: `DIRECT_ADDRESS_GUARD`'s count is the number of times conversation-level suppression was actually overridden, not the number of times someone was mentioned.

---

## 5. Confidence formula

Confidence is computed in `scoring/confidence.py` from named features and named weights. **The model never supplies it** — `Perception` has no confidence field and `extra="forbid"` rejects a response that adds one.

### Formula

```
confidence = clamp(
      W_BASE
    + trigger_term
    + evidence_term
    + engagement_term
    - media_term
    - ambiguity_term
    - contention_term,
    FLOOR, CEILING
)
```

rounded to `DECIMAL_PLACES`.

### Terms

#### `W_BASE = 0.55`

The starting point for a decision made with full context but no corroboration.

**Justification.** A three-way choice at chance is 0.33. A decision informed by the message content, the sender relationship, and the conversation type should be meaningfully better than chance, but a decision with nothing corroborating it is not settled. 0.55 is the midpoint of that description: better than a coin flip between two plausible options, well short of confident.

#### `trigger_term` — how the action was determined

| condition | weight | value |
|---|---|---:|
| applied rule's trigger uses only `FeatureVector` fields | `W_TRIGGER_STRUCTURAL` | **+0.20** |
| applied rule's trigger includes any `Perception` claim | `W_TRIGGER_PERCEPTUAL` | **+0.10** |
| applied rule is `PERCEPTION_ACCEPTED` | — | **0.00** |

**Justification.** A structural trigger is a fact recorded in the input — an opt-out flag, a membership role, a domain comparison. The decision is then as reliable as the input record, which is high. A perceptual trigger is a content judgement: reliable, but not independently verifiable, so it earns half as much. The default earns nothing because no rule vouched for it.

A rule with a mixed trigger (rules 6 and 8 have both) counts as **perceptual** — the weaker of the two — because a perception claim is load-bearing in the conjunction.

#### `evidence_term` — corroboration from history

| `EvidenceTier` | weight | value |
|---|---|---:|
| `RELATIONSHIP_AND_CONTENT` | `W_EVIDENCE_RELATIONSHIP_AND_CONTENT` | **+0.12** |
| `RELATIONSHIP` | `W_EVIDENCE_RELATIONSHIP` | **+0.07** |
| `TOPICAL` | `W_EVIDENCE_TOPICAL` | **+0.03** |
| `NONE` | — | **0.00** |

**Justification.** A prior message from the same sender with overlapping content is the strongest corroboration available: we have seen this user meet this situation before. Relationship alone is weaker — same sender, different subject. Topical alone is weakest — same subject, no relationship. Absence of evidence adds nothing but is not penalised, because a genuine first contact is a normal, correctly-handled case rather than a failure.

#### `engagement_term` — does history agree with the decision?

```
aligned   = (stance == RECEPTIVE  and action == NOTIFY)
         or (stance == NEUTRAL    and action == DIGEST)
         or (stance == DISMISSIVE and action == MUTE)
opposed   = (stance == RECEPTIVE  and action == MUTE)
         or (stance == DISMISSIVE and action == NOTIFY)
```

| condition | weight | value |
|---|---|---:|
| aligned | `+W_ENGAGEMENT_ALIGNED` | **+0.08** |
| opposed | `-W_ENGAGEMENT_ALIGNED` | **−0.08** |
| otherwise (incl. `UNKNOWN`) | — | **0.00** |

**Justification.** When the receiver's own past behaviour toward this sender points the same way as the decision, two independent lines of reasoning agree. When they point opposite ways — a safety rule muting a sender the user normally engages with — the decision is still correct but is genuinely more surprising, and the confidence should say so. Symmetric weight, because agreement and disagreement are equally informative.

#### `media_term` — decided without the content

| condition | weight | value |
|---|---|---:|
| `F.media_unread` | `W_MEDIA_UNREAD` | **−0.15** |

**Justification.** This fires when the message's *only* content is media that could not be decoded or transcribed — for a voice note, `message_text` is empty, so the entire message is unread. The decision then rests on sender and conversation metadata alone. This is the largest single term because it is the only one representing a decision made without the substance of the message.

`media_unread` is deliberately narrow: `media_failed` (any attached media failed to decode) without `media_unread` — a captioned image whose file could not be read — carries **no** confidence penalty, because the caption still carries the message. The failure is recorded on the row (`media_outcome`), not priced into confidence.

#### `ambiguity_term` — competing type candidates

```
ambiguity_term = W_TYPE_AMBIGUITY * (len(P.type_candidates) - 1)
```

Weight **0.04**, maximum penalty 0.08 (candidates are capped at 3).

**Justification.** The model returns an ordered list of plausible types, which is a claim about content, not a confidence — it is admissible under the perception-only constraint. Every additional candidate is one more reading the model could not rule out. The weight is small because type ambiguity often does not affect the action at all (`personal` vs `urgent` are both frequently `notify`).

#### `contention_term` — contested decisions

```
contention_term = W_CONTENTION * count(status in {SUPERSEDED, OVERRIDDEN_BY_CAP})
```

Weight **0.03** per contested firing.

**Justification.** A decision that several rules disagreed about is less certain than one reached unopposed. Rules suppressed by a guard are *not* counted — the guard is a designed resolution, not a conflict.

### Bounds

| bound | value | justification |
|---|---:|---|
| `CEILING` | **0.95** | No router is certain. Even a structural trigger rests on a CSV snapshot that may be stale, and a claim of certainty would be false. |
| `FLOOR` | **0.35** | Slightly above the 0.33 chance rate for a three-way choice. Below this we would be asserting our own answer is worse than a guess, which is not a claim we can make while still emitting it as the answer. |

Maximum reachable value: `0.55 + 0.20 + 0.12 + 0.08 = 0.95`, exactly the ceiling — the ceiling binds rather than truncates. Minimum before clamping: `0.55 − 0.08 − 0.15 − 0.08 − contention`, which reaches the floor once two or more rules are contested.

### Config

```python
@dataclass(frozen=True)
class ConfidenceConfig:
    W_BASE: float = 0.55
    W_TRIGGER_STRUCTURAL: float = 0.20
    W_TRIGGER_PERCEPTUAL: float = 0.10
    W_EVIDENCE_RELATIONSHIP_AND_CONTENT: float = 0.12
    W_EVIDENCE_RELATIONSHIP: float = 0.07
    W_EVIDENCE_TOPICAL: float = 0.03
    W_ENGAGEMENT_ALIGNED: float = 0.08
    W_MEDIA_UNREAD: float = 0.15
    W_TYPE_AMBIGUITY: float = 0.04
    W_CONTENTION: float = 0.03
    FLOOR: float = 0.35
    CEILING: float = 0.95
    DECIMAL_PLACES: int = 2
```

No weight was fitted to `sample_messages.csv`. The resulting distribution is expected to be wider than the sample's observed [0.78, 0.91]; that divergence is the deliberate, recorded trade in [PROBLEM_ANALYSIS D3](PROBLEM_ANALYSIS.md#d3--what-reasonable-confidence-calibration-means).

### Worked examples

**Payment instruction from a non-authoritative group member, prior messages from that sender dismissed** *(the `msg_022` trace in [SYSTEM_DESIGN.md §8](SYSTEM_DESIGN.md#8-trace-msg_022-from-csv-row-to-output-row))*

```
base                                        0.55
trigger      rule 6, mixed → perceptual    +0.10
evidence     RELATIONSHIP_AND_CONTENT      +0.12
engagement   DISMISSIVE aligns with mute   +0.08
media        readable                       0.00
ambiguity    2 candidates                  −0.04
contention   none                           0.00
                                          ──────
                                            0.81
```

**Unverified lookalike business, no prior relationship, single type candidate**

```
base                                        0.55
trigger      rule 3, structural            +0.20
evidence     NONE (no history with sender)  0.00
engagement   UNKNOWN                        0.00
media        readable                       0.00
ambiguity    1 candidate                    0.00
contention   none                           0.00
                                          ──────
                                            0.75
```

**Voice note that failed to decode, from an established contact, no rule fired**

```
base                                        0.55
trigger      PERCEPTION_ACCEPTED            0.00
evidence     RELATIONSHIP                  +0.07
engagement   NEUTRAL, action digest        +0.08
media        unread                        −0.15
ambiguity    3 candidates                  −0.08
contention   none                           0.00
                                          ──────
                                            0.47
```

The third example is the shape the formula exists to produce: a decision made without the message's content, on a sender we know only loosely, with three competing readings — and a confidence that admits it.

---

## 6. Report

`emit.record` writes every firing to the per-row record. The metrics engine derives the distribution post hoc ([METRICS.md](METRICS.md)); the pipeline itself counts nothing. The report it produces contains:

```json
{
  "rows_written": 110,
  "rows_expected": 110,
  "action_counts":        {"notify": 0, "digest": 0, "mute": 0},
  "override_code_counts": {"CREDENTIAL_SOLICITATION": 0, "...": 0},
  "threat_event_counts":  {"MEDIA_DECODE_FAILED": 0, "...": 0},
  "evidence_tier_counts": {"3": 0, "2": 0, "1": 0, "0": 0},
  "confidence_min": 0.0, "confidence_max": 0.0, "confidence_mean": 0.0,
  "media_unreadable": [],
  "failures": []
}
```

Two invariants are asserted before the report is written, and a violation fails the run rather than shipping a broken submission:

- `rows_written == rows_expected` and the id set equals `messages.csv`'s.
- Every `OutputRow` has exactly one firing with status `APPLIED`.

A rule whose count is zero across the whole run is reported, not hidden. A safety rule that never fires is either unnecessary or broken, and the count is how that gets noticed.
