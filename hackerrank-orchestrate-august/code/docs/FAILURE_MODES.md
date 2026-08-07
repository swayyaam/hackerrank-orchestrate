# FAILURE_MODES.md

What goes wrong, how it shows, where it is caught, what happens instead, and what the report says.

Companion documents: [THREAT_MODEL.md](THREAT_MODEL.md), [ENGINEERING_CONVENTIONS.md](ENGINEERING_CONVENTIONS.md), [CACHING.md](CACHING.md), [METRICS.md](METRICS.md).

---

## 1. Principle

Three dispositions, and every failure below is assigned exactly one.

| disposition | when | effect |
|---|---|---|
| **ABORT** | the failure means every row is suspect, or the output would be silently wrong | the run stops, no `output.csv` is written, exit non-zero |
| **DEGRADE** | one row can proceed with less information, and the loss is representable | the row is routed with a named degraded state, recorded and counted |
| **FAIL ROW** | one row cannot be routed at all | the row is recorded as a failure, the run continues, exit non-zero |

A partial `output.csv` is never written. It looks like a submission, and that is worse than no file.

**Nothing degrades silently.** Every DEGRADE writes a named state onto the row record, so the metrics engine counts it post hoc. A degraded row that produced a plausible answer with no trace is the failure mode this document exists to prevent.

---

## 2. Failure table

| # | failure | how it manifests | where caught | what the system does instead | what appears in the report |
|---|---|---|---|---|---|
| 1 | **Malformed model output** | response has an extra field, a missing field, an out-of-enum `action`, a `confidence` field, or is not JSON | `perception.parse.to_perception` — pydantic `extra="forbid"`, enum validation | retry the same bundle up to `MODEL_MAX_ATTEMPTS`; then **FAIL ROW**. Never coerce, never default | `schema_retry_count` on the row; `perception_health.hard_parse_failure_count`; `MODEL_RESPONSE_SCHEMA_REJECTED` event. **Hard merge gate at 0** |
| 2 | **Media unreadable** | decode raises, or produces an empty transcript / empty description | `media.decode.decode_audio`, `media.extract.extract` | **DEGRADE**: `MediaArtifact(readable=False, failure_reason=…)` → `F.media_failed`; additionally `F.media_unread` (and `−W_MEDIA_UNREAD` confidence) only when the message has no text, i.e. the media was its only content | `media_outcome="failed"`, `media_failure_cause`; `media.failures_by_cause`; `MEDIA_DECODE_FAILED` event |
| 3 | **Media misidentified** | extension says `.mp3`, container is WAV or AAC | `media.decode` — magic-byte sniffing, extension never consulted | correct decoder selected by content; `container_format` recorded | `media.container_format` in the cache entry; a run where detected ≠ extension is normal here and is not an error |
| 4 | **Corrupt cache entry** | file truncated, invalid JSON, key ≠ filename, wrong `cache_schema_version`, payload fails schema | `cache.store._load_and_validate` | **DEGRADE**: treated as a miss, logged once, never raised. In `--offline` mode counts as *missing*, so the row is named in the preflight | `cache.*_outcomes.corrupt` count, distinct from `miss`; `CACHE_ENTRY_CORRUPT` event |
| 5 | **Missing foreign key** | a `group_id` / `business_id` / `media_id` / `sender_user_id` with no matching row | `ingest.loader` at load time (referential sweep), `ingest.context.build_context` per row | **ABORT** at load. A broken join means the indexes are wrong, so every row is suspect | run aborts before any row is routed; `DatasetError` names the table, column, and offending key |
| 6 | **Empty `message_text`** | `message_text == ""` | not a failure — `ingest.loader` validates it as legitimate | expected for voice messages (8 of 110). Routing proceeds on the transcript. If text is empty **and** there is no media, `Perception` receives an explicit empty-content marker and `type_candidates` will typically be `unknown` | no event. Visible as `media_kind="voice"` rows; a text-empty row with no media appears in `message_type` distribution as `unknown` |
| 7 | **Budget exhausted mid-run** | token or wall-clock ceiling hit during a live pass | `perception.client.perceive` checks the running total before each call | **ABORT**, but *ordered*: every completed row's perception is already written to the cache, so a re-run resumes from the cache and only pays for what is left. No `output.csv` is written | `BUDGET_EXHAUSTED` event with the ceiling and the row index reached; exit non-zero with the resume instruction |
| 8 | **Ungrounded citation** | `evidence_cited` contains an id not in `evidence_shortlist` | `scoring.evidence.select_evidence` — structurally impossible, since the selector picks only from the shortlist. Verified post hoc by the metrics engine | cannot occur by construction; if the invariant is violated it raises `EvidenceIntegrityError` → **FAIL ROW** | `evidence.ungrounded_citation_count` and `ungrounded_rows`. **Hard merge gate at 0** |
| 9 | **Evidence owner or temporal violation** | a cited history row belongs to another user, or is not strictly older than the target | `scoring.evidence.select_evidence` — asserted invariants | **FAIL ROW** and raise. Not filtered silently: a violation means the index is wrong, not that the data is sparse | `EVIDENCE_OWNER_MISMATCH` / `EVIDENCE_NOT_IN_PAST` events with both ids; `manifest.failures` |
| 10 | **Injected instruction in message text** | content asserts a routing decision, priority, trust attribute, or processing directive | `perception` reports `contains_router_directive`; `policy.rules.ROUTER_DIRECTIVE` hard-overrides | **mute / scam**, regardless of whether the injected claim is true ([D12](PROBLEM_ANALYSIS.md#d12--injection-carrying-a-factually-true-claim)) | `ROUTER_DIRECTIVE` in `override_codes_fired`; counted in `override_codes.fire_counts` |
| 11 | **Injected instruction in an image** | rendered text in a poster or screenshot addresses the router | two-stage isolation: the extraction call has no routing vocabulary; its output is fenced as untrusted data before the routing call | the image's text arrives already demoted to a quoted description. It can mislead the *type*; it cannot issue an instruction | `FENCE_TOKEN_IN_UNTRUSTED_INPUT` if the description contained a fence token; otherwise indistinguishable from ordinary image content, by design |
| 12 | **Injected instruction in a voice note** | spoken directive in the transcript | identical to #11 — transcription is a separate call with no routing vocabulary, transcript is fenced | judged as content; `ROUTER_DIRECTIVE` applies to a transcript exactly as to text | same as #10 |
| 13 | **Fence token in untrusted input** | attacker text contains `<<UNTRUSTED` | `perception.prompt.fence` — neutralised before insertion | the token is escaped; the block boundary holds because the real nonce is unguessable | `FENCE_TOKEN_IN_UNTRUSTED_INPUT` event with a content hash, never the content |
| 14 | **Two rules fire with conflicting directions** | e.g. `GROUP_MUTE_CAP` (→ digest) and `TIME_CRITICAL_FLOOR` (→ notify) both fire | `policy.engine.decide` — the resolution algorithm, not an error path | **caps beat floors**; the overridden floor is recorded `OVERRIDDEN_BY_CAP`. `DIRECT_ADDRESS_GUARD` is the one documented escape ([DECISION_ENGINE §4](DECISION_ENGINE.md#4-conflict-resolution)). Deterministic, never ambiguous | both firings on the row; `override_codes.rows_with_multiple_firings`; `−W_CONTENTION` on confidence |
| 15 | **Two hard overrides both fire** | e.g. `CREDENTIAL_SOLICITATION` and `IMPERSONATION` | `policy.engine.decide` | first in precedence order is `APPLIED`, the rest `SUPERSEDED`; evaluation stops | both recorded; the superseded firing is visible in `firings` so the second concern is not lost |
| 16 | **No rule has status APPLIED** | a rule-table or resolution bug | `policy.engine.decide` post-condition assertion | **FAIL ROW** and raise `PolicyError`. Also asserted across the whole run before `output.csv` is written | run aborts at the pre-write invariant check |
| 17 | **Media path escapes the dataset root** | `..`, an absolute path, or a symlink out of the tree | `media.locate.resolve_media_path` — resolve both sides, require containment | raise `MediaPathError` → **DEGRADE** the row to `media_failed` (`media_unread` too when the message has no text) | `MEDIA_PATH_ESCAPED_ROOT` event with the rejected path; `media.failures_by_cause.path_rejected` |
| 18 | **Media exceeds a resource cap** | file bytes, audio duration, or image pixels over the limit | `media.decode`, `media.extract` — checked **before** decode | rejected without decoding → **DEGRADE** to `media_failed` (`media_unread` too when the message has no text) | `MEDIA_LIMIT_EXCEEDED` with the limit name and observed value |
| 19 | **Reason violates its register** | length outside `[MIN_CHARS, MAX_CHARS]`, more than one sentence, or contains an identifier | `compose.reason.render_reason` | raise `ReasonRegisterError` → **FAIL ROW**. Not truncated: a silently trimmed reason is a wrong explanation | `reason_health.rows_outside_register_bounds`. **Hard merge gate at 0** |
| 20 | **Required perception record missing in offline mode** | cache entry absent or corrupt for a row | `code/main.py` offline preflight, before any row is routed | **ABORT** with the full list of missing rows and their keys. **Never** falls back to a live call | preflight failure block on stdout; no run directory is created |
| 21 | **Config value out of range** | a weight outside its bound, a negative threshold, a cap below a floor | `config.validate()` at startup | **ABORT** before any message is processed | `ConfigError` naming the field, the value, and the permitted range |
| 22 | **Dataset changed under a cached rerun** | `dataset_fingerprint` differs from the baseline run's manifest | `code/main.py` at load, compared against the previous manifest when diffing | warn loudly; the diff labels the comparison as spanning a dataset change, since row-level deltas are then not attributable to the code change | `diff.dataset_fingerprint_changed: true` and a banner on the diff console |
| 23 | **Model nondeterminism** | an offline rerun with an unchanged cache changes a row's action while every override code and warning is identical | `metrics.diff` — `unexplained_action_changes` | not caught at runtime; caught by the diff. On a cached rerun this must be 0 | `diff.unexplained_action_changes` with the row ids. **Hard merge gate at 0 for cached reruns** |
| 24 | **Row count mismatch** | fewer or more than 110 output rows, or an id set that differs | pre-write invariant in `emit.writer` | **ABORT**. No file is written | run aborts; the missing or extra ids are named |
| 25 | **One message raises for any other reason** | an unhandled path in a single row | the per-row boundary in `route_one`'s caller | **FAIL ROW**: recorded, the run continues so the other 109 are still evaluated, exit non-zero | `manifest.failures` lists the `message_id`; the run cannot be submitted while non-empty |

---

## 3. Notes on the harder cases

### Budget exhaustion (#7) is ordered, not abrupt

The cache is written **per row, as each call returns**, not batched at the end. So a run that dies at row 96 of 140 has 96 entries already on disk. Re-running after raising the ceiling pays only for the remaining 44. This is why the perception cache is written by `warm-perception` incrementally rather than transactionally — a transactional write would lose 96 paid calls to one failure.

### Empty message text (#6) is two different situations

`message_text == ""` with `media_kind == "voice"` is normal: 8 of 110 target rows, and the transcript carries the content. `message_text == ""` with **no media** would be a message with no content at all. That does not occur in this dataset ([DATASET_ANALYSIS §3](DATASET_ANALYSIS.md#3-media-presence) — blank text and voice are perfectly correlated), but the code must not assume it: the prompt builder emits an explicit empty-content marker rather than an empty fenced block, so the model is asked about a message with no content instead of being handed something that looks like a truncation. This is the [G5](GENERALIZATION_POLICY.md#g5--unseen-input) unseen-input answer for that field.

### Conflicting rules (#14) is not an error path

Two rules pointing opposite ways is the *expected* case that the resolution algorithm exists for, not an exception. It is in this document because it is a failure of *reasoning* when handled ad hoc — an engine that resolves conflicts by whichever rule happens to be checked last produces decisions nobody can explain. The mitigation is that precedence and the cap-beats-floor rule are stated in advance, every firing is recorded with its status, and the contention costs confidence.

### Ungrounded citations (#8) are structurally impossible

The selector receives a shortlist and returns a subset of it. There is no code path by which an id outside the shortlist can be emitted. The metric exists to **verify the invariant**, not to measure a rate — which is why its gate is exactly zero rather than a threshold. Both the shortlist and the citation are written to the row record, so the check is performed post hoc on saved data rather than trusted.

### Model nondeterminism (#23) is only detectable by diff

Nothing at runtime can tell that a row's action would have been different on another run. The diff detects it by the absence of a cause: if the action changed and no override code, integrity warning, or config value changed, then the only remaining input that could have moved is perception. On a fully cached offline rerun perception cannot move, so a non-zero count there means a genuine determinism bug — unsorted iteration, a set traversal, an unseeded shuffle — and it is a hard gate.

---

## 4. Exit codes

| code | meaning |
|---:|---|
| `0` | 110 rows written, `manifest.failures` empty, all pre-write invariants held |
| `1` | run completed but one or more rows failed; `output.csv` **not** written |
| `2` | offline preflight failed; nothing was attempted, no network call was made |
| `3` | abort before routing: dataset error, config error, or missing foreign key |
| `4` | budget exhausted mid-run; partial cache written and resumable |

Any non-zero exit means there is no submittable artifact from that run. The previous phase's run directory remains the fallback ([IMPLEMENTATION_PLAN §8](IMPLEMENTATION_PLAN.md#8-phase-6--submission-packaging)).
