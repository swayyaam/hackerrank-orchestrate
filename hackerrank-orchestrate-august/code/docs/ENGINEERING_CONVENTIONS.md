# ENGINEERING_CONVENTIONS.md

Conventions for the router codebase. These are enforceable, and where a rule can be checked mechanically the check is named.

Companion documents: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), [DECISION_ENGINE.md](DECISION_ENGINE.md), [THREAT_MODEL.md](THREAT_MODEL.md), [GENERALIZATION_POLICY.md](GENERALIZATION_POLICY.md).

Target: Python 3.12+. Formatter: `ruff format`. Linter: `ruff` with `ALL` enabled and a documented, per-rule ignore list. Type checker: `mypy --strict`.

---

## 1. Naming

| kind | convention | example |
|---|---|---|
| module | `snake_case`, singular noun, names the thing it produces | `confidence.py`, `evidence.py`, `context.py` |
| package | `snake_case`, names the pipeline stage | `perception/`, `features/`, `policy/` |
| class | `PascalCase`, a noun | `MessageContext`, `FeatureVector` |
| enum | `PascalCase` singular; members `SCREAMING_SNAKE` | `Action.NOTIFY` |
| function | `snake_case`, verb-first if it acts, noun if it derives | `build_context`, `domain_state` |
| predicate | `is_` / `has_` / `holds_` prefix, returns `bool` | `is_frequently_forwarded` |
| config constant | `SCREAMING_SNAKE`, on a frozen dataclass | `FORWARD_CHAIN_THRESHOLD` |
| confidence weight | `W_` prefix | `W_TRIGGER_STRUCTURAL` |
| policy rule + its code | identical `SCREAMING_SNAKE` string | `UNAUTHORIZED_PAYMENT_REQUEST` |
| threat event | `SCREAMING_SNAKE`, past tense or state | `MEDIA_DECODE_FAILED` |
| exception | `PascalCase`, `Error` suffix | `EvidenceIntegrityError` |
| test | `test_<unit>_<condition>_<expectation>` | `test_domain_state_blank_official_returns_undetermined` |

### Rules

- **A rule's `OverrideCode` value, its `Rule` object name, and its section heading in [DECISION_ENGINE.md](DECISION_ENGINE.md) are the same string.** A reader grepping an override code out of `runs/<id>/rows.jsonl` lands on the rule and on its documentation without translation.
- **Feature names are claims, not measurements.** `sender_holds_payment_authority`, not `sender_auth_score`. A feature whose name does not state what it asserts belongs in a lower-level function.
- **No abbreviations** except `id`, `csv`, `ocr`, `asr`, `url`. Not `msg`, `ctx`, `cfg`, `perc`, `feats`.
- **No Hungarian prefixes, no `_v2`, no `_new`, no `_final`.** The version is in git.

---

## 2. Module layout

### Dependency direction is strictly downward

```
config.py, schemas.py, errors.py          ← leaves; importable by everything
    ↑
ingest.loader → media/ → ingest.context → perception/ → features/ → policy/ → scoring/ → compose/ → emit/
```

Media extraction runs **before** context construction ([SYSTEM_DESIGN §3](SYSTEM_DESIGN.md#3-data-flow-for-a-single-message)): `extract` takes an `IncomingMessage` (a `schemas.py` type), and `build_context` takes the resulting `MediaArtifact` as a parameter. Neither `media/` nor `ingest/` imports the other; the chain above is execution order and the import rule holds either way.

- No module imports a module that appears later in the pipeline.
- No cycles.
- `config.py` imports nothing from the package. `schemas.py` imports `config` only.
- **`policy/` imports `schemas` and `config` and nothing else.** It must not import `perception/`, `ingest/`, or `media/` — the decision layer takes typed values, never fetches them.

**Enforced by** `tests/test_architecture.py::test_import_graph`, which walks the AST of every module and fails on a cycle or a forbidden edge.

### One responsibility per module

If a module's one-line description in [SYSTEM_DESIGN.md §2](SYSTEM_DESIGN.md#2-module-layout-and-responsibilities) needs an "and", the module is split. `features/` is six files rather than one for exactly this reason.

### Public surface

Each module exposes the functions named in [SYSTEM_DESIGN.md §6](SYSTEM_DESIGN.md#6-interfaces). Helpers are `_`-prefixed and are not imported across module boundaries. There is no `utils.py`, no `helpers.py`, no `common.py` — a function with no home has not been placed yet.

### Data crossing a boundary is a pydantic model

Never a `dict`, never a tuple of positionals, never `Any`. If two modules exchange something, it has a schema in `schemas.py`.

---

## 3. Typing

- `mypy --strict` passes with zero errors and zero `# type: ignore` without an adjacent comment naming the reason.
- **Every function is fully annotated**, including the return type. `-> None` is written explicitly.
- **`Any` is forbidden** outside `perception/parse.py`, where it appears only as the input type of the JSON body being validated, and only until validation.
- **`dict[str, Any]` may not cross a module boundary.** Inside a function, briefly, is fine.
- `from __future__ import annotations` at the top of every module.
- Pydantic models use `model_config = ConfigDict(frozen=True, extra="forbid")`. Frozen because a context or a decision that mutates after construction cannot be reasoned about; `extra="forbid"` because a silently-absorbed field is how a schema drifts.
- **Collections in models are `tuple`, not `list`** — immutability, and a `tuple` in a frozen model is hashable.
- `Sequence` / `Mapping` for function parameters, concrete types for returns. Accept broadly, return precisely.
- **No `Optional` as a failure channel.** `X | None` means "this legitimately may not exist" (a group message has no `business_id`). Failure raises. `select_evidence` returns an empty tuple for "no useful evidence" — a real, expected answer that renders as `none` — and raises for "the index is broken".

---

## 4. Docstrings

Every public function, class, and module has one. Format: a one-line imperative summary, then optional detail, then the sections that apply.

```python
def select_evidence(context: MessageContext, decision: Decision) -> EvidenceSelection:
    """Rank this receiver's history and select the ids that support the decision.

    Only records strictly older than the target are eligible, and every record
    must belong to the target's receiving user. Both are invariants rather than
    filters: a violation means the index is wrong, not that the data is sparse.

    The number of ids returned is 1 by default and up to 2 when the applied rule
    asserts a pattern, because a claim of repetition needs more than one instance.

    Raises:
        EvidenceIntegrityError: a candidate belongs to another user or is not in
            the past.
    """
```

### Required sections

- **`Raises:`** on any function that can raise. If it is absent, the function does not raise, and a reader may rely on that.
- **`Justification:`** on every function that encodes a policy threshold or a weight. It states *why the number is what it is* and names its [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance) source — (a) contract, or (b) independent domain quantity. This is the [G1 citation gate](GENERALIZATION_POLICY.md#g1--citation) written down at the point of use.

### What a docstring does not do

It does not restate the signature (`Args: context: the context`), and it does not describe the implementation. It states the contract and the reasoning.

---

## 5. Error handling

### Hierarchy

```python
# errors.py
class RouterError(Exception):
    """Base for every error this package raises."""


class DatasetError(RouterError): ...          # a CSV is malformed or missing
class ContextError(RouterError): ...          # a join failed for one message
class MediaPathError(RouterError): ...        # a media path escapes the dataset root
class MediaDecodeError(RouterError): ...      # a media file could not be decoded
class PerceptionTransportError(RouterError): ...  # network, timeout, rate limit
class PerceptionSchemaError(RouterError): ...     # the response did not validate
class PolicyError(RouterError): ...           # a rule table invariant broke
class EvidenceIntegrityError(RouterError): ...    # a cited id fails an invariant
class ReasonRegisterError(RouterError): ...   # a reason violated its bounds
class ConfigError(RouterError): ...           # a config value is out of range
```

### Policy

**Fail loud by default.** An unexpected condition raises. Nothing is silently defaulted, coerced, or skipped.

**Exactly three conditions are recoverable**, and each degrades to a *named state*, never to silence:

| condition | degrades to | visible as |
|---|---|---|
| media decode or extraction failure | `MediaArtifact(readable=False, failure_reason=...)` | `F.media_failed`; additionally `F.media_unread` and its confidence penalty when the message has no text; `media_outcome="failed"` on the row record; a `MEDIA_DECODE_FAILED` event |
| transport failure from the model | retry to `MODEL_MAX_ATTEMPTS`, then fail the message | the `message_id` in `RunResult.failures` |
| one message raising | that message fails; the run continues | the `message_id` in `RunResult.failures`, and the run exits non-zero |

Everything else — a malformed CSV, a broken join, a failed evidence invariant, a config value out of range — aborts the run. A partial `output.csv` is worse than no `output.csv`, because it looks like a submission.

### Rules

- **No bare `except:`.** No `except Exception:` without re-raising or converting to a `RouterError` with the original attached via `raise ... from e`.
- **Never catch an exception to return a default.** `except Exception: return Action.DIGEST` is prohibited; it converts a bug into a plausible-looking wrong answer, which is the worst outcome available.
- **Exception messages name the subject and the expectation**, and never contain untrusted text: `"business_042 has domain_used_by_sender but no official_domain"`, not `"bad data"` and not the message body.
- **Assertions are for invariants the code guarantees**, never for input validation. Input validation is pydantic's job and raises a typed error.
- The run's exit code is `0` only when `rows_written == rows_expected` and `failures` is empty.

---

## 6. Logging and config access

### Logging

Structured JSON Lines to `run/events.jsonl`. Fixed field set, described in [THREAT_MODEL §7](THREAT_MODEL.md#7-logging).

```python
log.warning(
    ThreatEvent.MEDIA_DECODE_FAILED,
    message_id=message.message_id,
    media_id=artifact.media_id,
    path="media.decode.decode_audio",
    detail={"detected_container": "aac"},
    action_taken="media_unread",
)
```

- **The event name is always a `ThreatEvent` or a `LogEvent` enum member, never a free string.** A test asserts the set of enum values equals the set of names emitted anywhere in the package.
- `action_taken` is mandatory on every `WARNING` and `ERROR`.
- **No f-strings in log calls.** Structured fields only — an f-string is how untrusted text ends up interpolated into a log line.
- **Untrusted text is logged as `sha256(content)[:16]` plus a length.** Never the content. This covers `message_text`, transcripts, image descriptions, display names, and group names.
- **Secrets never appear** in a log record, an exception message, or a traceback.
- Levels: `DEBUG` per-stage timings; `INFO` run lifecycle and per-message decisions; `WARNING` a mitigation fired or a message degraded; `ERROR` a message failed; `CRITICAL` the run aborted.

### Config access

```python
# correct
from code import config

def is_frequently_forwarded(forwarded_count: int, cfg: config.PolicyConfig) -> bool:
    return forwarded_count >= cfg.FORWARD_CHAIN_THRESHOLD
```

```python
# prohibited
from code.config import FORWARD_CHAIN_THRESHOLD    # binds at import; untestable
THRESHOLD = 5                                       # a literal in logic
```

- **Config objects are injected as parameters**, not read from module globals. A test constructs an alternative `PolicyConfig` and passes it; no monkeypatching, no global mutation, no fixture ordering hazards.
- Config dataclasses are `frozen=True`. There is no setter and no reload.
- **Every constant carries the reason for its value as an adjacent comment**, naming its [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance) source. A constant without one does not merge.
- Environment variables are read **only** in `config.py`, at one function, and only for the model identifier and credentials. No `os.environ` anywhere else.
- Config is validated at startup: ranges checked, weights checked to sum within the confidence bounds. A bad value raises `ConfigError` before any message is processed.

---

## 7. What may not appear in the codebase

Each item is checkable; the check is named where one exists.

### Absolutely prohibited

| # | prohibited | why | check |
|---|---|---|---|
| 1 | **Any identifier from `dataset/`** — `msg_\d+`, `message_\d+`, `sample_msg_\d+`, `u_\d+`, `group_\d+`, `business_\d+`, `img_\d+`, `vn_\d+` | [G2](GENERALIZATION_POLICY.md#g2--literal). A per-row branch is a hardcoded label. | `test_no_corpus_literals` greps the package; `tests/fixtures/` is exempt |
| 2 | **Any four-or-more-word substring of a `message_text`, `display_name`, `group_name`, or `why_user_knows_account`** | [G2](GENERALIZATION_POLICY.md#g2--literal). Phrase matching does not survive rephrasing. | same test, n-gram scan against the corpus |
| 3 | **A behavioural literal outside `config.py`** — any number or string that changes what the code does | Constraint: one config module. | `test_no_magic_numbers` flags numeric literals outside `config.py`, allowing only `0`, `1`, `-1`, and array indices |
| 4 | **A comparison against an observed value** — `reaction_time == 2`, `forwarded_count > 4`, `confidence in (0.78, 0.91)` | [G4](GENERALIZATION_POLICY.md#g4--numeric-provenance) source-(c). | covered by 3, plus review |
| 5 | **An exhaustive branch over a corpus-derived value set** — the four event profiles, the three reaction times, the 40 business categories, the 14 DND windows | [G3](GENERALIZATION_POLICY.md#g3--cardinality). | review; the [G5](GENERALIZATION_POLICY.md#g5--unseen-input) unseen-input answer is required in the commit |
| 6 | **A network call outside `perception/client.py`** | Every other module must be testable offline. | `test_no_network` scans for `requests`, `httpx`, `urllib`, `socket` imports outside that module |
| 7 | **A confidence value originating from the model** | Constraint: confidence is derived in code. `Perception` has no such field and `extra="forbid"`. | `test_perception_rejects_confidence_field` |
| 8 | **`datetime.now()`, `time.time()`, `date.today()` in decision logic** | Breaks determinism and reproducibility. All time comparisons are between dataset timestamps. | `test_no_wall_clock`, exempting `logging_setup.py`, `code/main.py`, `emit/record.py`, and `cache/store.py` — the modules that legitimately stamp `run_id`, manifest and cache-entry timestamps, and per-row `wall_clock_ms` |
| 9 | **`random` without an explicit seed, or set/dict iteration order dependence** | Same run must produce the same bytes. | review; evidence ranking uses a total order with `message_id` as final tie-break |
| 10 | **A mutable module-level global** | Import-order-dependent behaviour, untestable. | `test_no_mutable_globals` |
| 11 | **A bare `except:` or an `except Exception:` that returns a default** | Converts a bug into a plausible wrong answer. | `ruff` `BLE001`, `E722`; review for the return-default pattern |
| 12 | **Untrusted text in a log line, an exception message, or a prompt outside a fence** | [THREAT_MODEL M2, §7](THREAT_MODEL.md#2-mitigation-architecture). | `test_prompt_fencing`; review for logging |
| 13 | **A secret in the repository** — key, token, cookie, `.env` file | `AGENTS.md` §6.3. | pre-commit secret scan; `.env` in `.gitignore` |
| 14 | **`utils.py`, `helpers.py`, `common.py`, `misc.py`** | A function with no home has not been placed. | `test_no_junk_drawer_modules` |
| 15 | **A commented-out block of code** | git holds history. | `ruff` `ERA001` |

### Requires an explicit, reviewed justification

| prohibited by default | when it may be allowed |
|---|---|
| `# type: ignore` | with an adjacent comment naming the upstream cause |
| a `ruff` per-line `noqa` | with the rule code and the reason |
| a function longer than 50 lines | a rule table or an enum-dense mapping, where splitting would obscure it |
| more than three parameters | when all are typed models and the call site reads better than a wrapper struct |

### The commit gate

Every commit that adds or changes a policy rule, a threshold, or a weight records the six [review-question](GENERALIZATION_POLICY.md#3-the-review-question) answers in the commit message:

```
policy: add UNAUTHORIZED_PAYMENT_REQUEST

G1 The problem statement says a payment reminder may be legitimate from a
   trusted admin but risky from a new sender; this checks that distinction.
G2 No corpus literals. Uses Membership.role, BusinessProfile.verified,
   DomainState, and three Perception claims.
G3 No enumeration over a corpus-derived value set.
G4 No constants introduced.
G5 Personal conversation with a payment request and no authority concept:
   sender_holds_payment_authority is False by construction, so the rule fires
   and the message is muted. Defensible — a stranger asking for off-platform
   payment in a direct chat is the case the rule exists for.
G6 Branch count is fixed at three conjuncts regardless of corpus size.
```

A commit that cannot produce a G1 sentence, or whose G5 answer is "crash" or "undefined", does not merge.
