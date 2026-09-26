# Authorship

Written to be accurate rather than flattering. The submission was built by one
person working with an AI assistant across ten working sessions. The division was
consistent: **I set the architecture and made every judgement call; the assistant
wrote most of the code and ran the measurements.** Where the assistant's
recommendation was wrong and I overruled it, that is recorded below, and so is
the reverse.

## By module

| module | authorship | what I materially redirected |
|---|---|---|
| `bow/contracts/` | primarily AI-generated | I set the rule that enums fail on unknown values rather than defaulting, and that money is parsed from raw strings and never through `float`. |
| `bow/data/loaders.py` | primarily AI-generated | — |
| `bow/data/normalize.py` | AI-generated, **hand-directed** | The six-rule cash treatment is mine as a specification: I required that the rules read typed fields only, never a description, and that the seven lifecycle shapes be *derived* from them rather than enumerated. I demanded the reproduction be verified before anything was built on it. |
| `bow/data/recurrence.py` | AI-generated, hand-corrected | I set the minimum-occurrence floor at two and refused to loosen the staleness multiple. I approved `Q22` scoped to income, then turned it off after the ground truth contradicted the justification I had accepted. |
| `bow/engine/forecast.py` | AI-generated, **hand-directed** | The trailing-rate variable-spend rule is mine. I refused a damping coefficient and required a minimum-occurrences guard instead — evidence, not a fudge factor. |
| `bow/engine/capacity.py` | primarily AI-generated | I required that the closed form be falsified by an independent binary search rather than merely tested. |
| `bow/engine/plans.py`, `ranker.py` | primarily AI-generated | I required the six ranking keys live in one tuple in one function. |
| `bow/engine/changes.py` | AI-generated, hand-corrected | I specified that `reduce_to` uses the event's own `minimum_allowed_amount`, never a computed shortfall, and that a plan needing four changes is rejected rather than truncated to three. |
| `bow/engine/policy.py` | primarily AI-generated | I required post-conditions that raise rather than emit a bad row. |
| `bow/perception/` | AI-generated, **hand-corrected twice** | The untrusted-content boundary is mine. The `image_14` diagnosis is mine — see below. |
| `bow/providers/` | primarily AI-generated | I required a live capability probe before any config value was written down. |
| `bow/explain/` | primarily AI-generated | I required the explanation be written from a frozen ledger with no access to raw source data. |
| `bow/validate/`, `bow/usage.py` | primarily AI-generated | I required the cost report be *generated* from the run manifest after finding it had been hand-written and stale. |
| `tests/` (797) | primarily AI-generated | The six structural lint suites exist because I made the generalization mandate a build-breaking rule rather than a guideline. |
| `config/*.yaml` | AI-generated, hand-directed | The decision-register format is mine, including `sample_discriminates`, and later `switchable` and `contract`. |
| `docs/` | AI-generated, hand-directed | Every document exists because I asked for it. The honesty framing — corrections appended rather than edited in place — is mine. |
| `evaluation/harness.py`, `ablations.py` | primarily AI-generated | I rejected stubs outright: runnable or deleted. |
| `scripts/probe_*.py` | primarily AI-generated | Each probe exists because I refused to accept a configured value that had not been measured. |

## Architecture decisions I set

- Deterministic financial solver with a **narrow** perception layer; the model
  never produces a number that reaches `output.csv`.
- Four alternatives rejected explicitly: end-to-end LLM decisioning, a general
  ReAct agent, RAG with embedding retrieval, and letting the explanation writer
  see raw source data.
- One vendor, one credential, `.env` outside the zip boundary as a structural
  property rather than a convention.
- The layering rules, and that they be enforced by test rather than by review.
- The generalization mandate: encode rules, never observations — no dataset ids,
  no branching on free text, no dataset-derived literals.
- Pipeline order as a constraint, not a convention.
- The decision register itself, and that every ambiguity become a named flag
  with a default, an alternative and a rationale.

## Experiments I designed

- **The eight-combination variable-spend ablation**, with the requirement that
  predictions for all eight be written down *before* any was run.
- **The recurrence holdout**: remove every scheduled income row and make
  inference rediscover it. 44/47 exact-date.
- **The `reasoning_effort` A/B**: same 45 messages, same seed, one field
  different, adopt only if validity holds.
- **The vision stability probe**: 16 images × 5 reads, agreement measured
  against the other reads and never against the gold set, so it could not
  become a search for the best-scoring run.
- **Best-of-N**: N and the tie rule fixed in config *before* the measurement, and
  declined afterwards because the measurement said one image, not several.
- **The sample-set blinding test**: re-run the whole sample set with every
  published answer stripped from the input and assert the predictions do not move.

## Calls I made against the assistant's recommendation

| call | assistant's position | mine | who was right |
|---|---|---|---|
| **The `image_14` read** | "unstable transcription, a provider limitation, record it and move on" | "that is a pairing bug — its own detail string shows it read 4543 and returned 670" | **Mine.** Split Rs/Ps columns, an undescribed row dropped from the line-item sum, and a `colspan` footer merging the label with its value. Gold went 15/16 → 16/16. |
| **The truncation re-run** | called it tuning, and resisted | "sizing a completion budget so the model can emit its answer is fixing a truncation bug, not choosing a threshold because it improves a score" | **Mine.** |
| **`Q22_series_continuation`** | argued for it on a fall in zero-income users, 50 → 30 | withdrew that justification — it measured a change in behaviour, not accuracy — and turned the flag off | **Mine.** Ground truth showed zero projected income is often the correct answer. |
| **`Q25`** | wanted to implement it | refused: regrouping five grocery series under one key does not create outflow, and that mechanism was already measured | **Mine.** |
| **`request_08`** | wanted to chase it | left it: nine qualifying series spending 13.58/day against a 1,536.57 balance means the user is genuinely broke | **Mine.** |
| **`output.csv` location** | flagged at scaffold time that the README wanted it at the repo root | overruled it, then reversed after re-reading the README | **The assistant's**, and I said so in the register. |

## Errors I made and corrected in the record

- I instructed that `reasoning_effort` be omitted across every role on the
  strength of a **single unreplicated sample**. Re-measurement showed the
  opposite. I took ownership of that rather than leaving it as the assistant's
  mistake, and the budgets were re-derived from measured maxima.
- I put `output.csv` inside `code/` against the assistant's flag, and reversed it.

## What this means for the code you are reading

Most of the lines were generated. The **constraints** that make those lines
what they are — no numbers from the model, no observations encoded as rules, no
value in config that was not measured, no stub shipped, no claim in a document
that the code does not support — are mine, and they are enforced by 797 tests
rather than by my say-so. The parts I would defend hardest under questioning are
the six-rule derivation, the refusal to add a damping coefficient, and the
`image_14` diagnosis, because those are the three places where I overruled a
plausible recommendation and the measurement then agreed with me.
