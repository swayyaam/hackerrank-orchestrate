# CACHING.md

Three caches with distinct keys, distinct lifetimes, and one owning module.

Companion documents: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [METRICS.md](METRICS.md), [THREAT_MODEL.md](THREAT_MODEL.md).

---

## 1. Why three, and why these boundaries

The three layers differ in what makes a result stale, so they cannot share a key.

| layer | keyed by | invalidated when | cost to recompute |
|---|---|---|---|
| **media** | the file's bytes | never — bytes are immutable | one vision or ASR call |
| **perception** | everything sent to the model | the prompt, model, or params change | one routing call |
| **policy** | *not cached* | — | microseconds |

Policy is deliberately uncached. It is pure, it is cheap, and it is the thing under active iteration — caching it would mean invalidating on every edit to the code being edited, which is a cache that is always cold and always in the way.

The dividing line between the media cache and the perception cache is what makes offline iteration work. Media analysis is expensive and never changes. Perception depends on the prompt, which does change. Separating them means a prompt revision re-warms 140 routing calls but re-uses all 33 media analyses.

---

## 2. Media cache

### Key

```python
key = hashlib.sha256(path.read_bytes()).hexdigest()
```

**The path is not in the key. The `media_id` is not in the key.** Only the bytes.

Three properties follow. The same image referenced by two `media_id`s is analysed once — this dataset reuses `img_008` across six rows and `img_010` across four ([DATASET_ANALYSIS §9.3](DATASET_ANALYSIS.md#93-repetition-within-messagescsv)). Renaming or moving a file cannot produce a stale hit. And a file whose contents change gets a new key automatically, so there is no mtime check to get wrong.

### Stored payload

```json
{
  "content_sha256": "…",
  "kind": "image" | "voice",
  "container_format": "jpeg" | "png" | "mp3" | "aac" | "wav",
  "container_detected_from": "magic_bytes",
  "byte_length": 88042,

  "extraction_status": "complete" | "unsupported" | "duration_exceeded" | "decoded_only",
  "extraction_failure": null,
  "declared_extension": "jpg",
  "extension_matches_container": false,

  "image": {
    "visual_observation": "…",
    "extracted_text": "…",
    "extracted_text_present": true
  },

  "voice": {
    "transcript": "…",
    "language_code": "en-IN",
    "language_probability": 0.93,
    "confidence_kind": "language_probability" | "unavailable",
    "transcription_mode": "translit",
    "duration_seconds": 28.35,
    "sample_rate_hz": 44100,
    "channels": 2
  }
}
```

Exactly one of `image` / `voice` is present; the other is `null`.

`container_format` is recorded because the extension lies for 9 of 13 audio files ([DATASET_ANALYSIS §3](DATASET_ANALYSIS.md#3-media-presence)), and the *detected* format is a fact worth persisting — it is what the metrics engine reports as media coverage, and what a failure diagnosis starts from.

`language_probability` is the **provider's own** detection confidence, returned only when `language_code` was left unset so the model auto-detects. It is persisted because it is a genuine external signal — one of the few numbers in this system that code did not derive — and the confidence formula may read it later. `confidence_kind` names what the number is, so a later comparison is not silently comparing incomparable quantities; when the provider gives nothing it is `unavailable` and the signal is `null`, never a fabricated default.

### Invalidation

**Never.** There is no TTL, no `--clear-media-cache` flag, no mtime comparison, no manual eviction path. File content is immutable, and the key is the content. A media entry written today is valid forever.

Deleting an entry that is *no longer addressable by any key* is a different operation and is permitted — see [§4, Invalidation is not the same as pruning](#invalidation-is-not-the-same-as-pruning).

The only way to change a media result is to change the extraction prompt, model, or settings — each of which is a field in the key material below. That produces a new key, so old entries are simply never read again rather than being invalidated.

Full media key material:

```python
key_material = {
    "cache_schema_version": 1,
    "content_sha256": "<sha256 of the file bytes>",
    "media_extraction_version": "<MEDIA_EXTRACTION_VERSION from media/extract.py>",
    "extractor": { … },   # kind-specific; see below
}
```

`extractor` is **kind-specific**, because the two media kinds go to different providers and a single flat `model_id` cannot describe both:

```python
# images  -> Anthropic vision
{"provider": "anthropic", "model_id": "<ROUTER_MODEL_ID>", "prompt_version": "<VISION_PROMPT_VERSION>"}

# voice notes -> Sarvam AI speech-to-text
{"provider": "sarvam", "model_id": "saaras:v3", "mode": "translit", "language_code": None}
```

Two consequences worth stating, since both are the point of the split:

* Switching the vision model re-keys every image and **leaves every transcript reachable** — an Anthropic model has no bearing on a result Sarvam produced.
* Changing the transcription `mode` or pinning `language_code` re-keys every transcript. A transcript produced in `transcribe` mode (Devanagari) must never be served for a `translit` request (romanised), and pinning a language suppresses the `language_probability` signal, so it is a materially different result.

### Extraction status

Every entry records an `extraction_status`, and exactly one of the four is a statement about the *run* rather than about the file:

| status | meaning | retried by a later warm pass? |
|---|---|---|
| `complete` | the provider returned content | no |
| `unsupported` | the container is not one the provider accepts | no — a fact about the file |
| `duration_exceeded` | longer than the transcription endpoint's ceiling | no — a fact about the file |
| `decoded_only` | container facts cached; extraction **never attempted**, because no client was configured | **yes**, once a client is available |

Without that last row, a `warm-media` run without `--allow-network` would write 33 content-free entries and silently turn the next paid pass into a no-op. The three terminal verdicts stay hits — recomputing them on every pass would make the command non-idempotent for no benefit — but they still report their cause, so a second pass shows the same incomplete counts as the first.

### Warm command

```
python main.py warm-media
```

Standalone. Walks `images.csv` and `voice_notes.csv`, resolves each path, hashes the bytes, and populates any missing entry. Processes all media **once, before any routing run**. Idempotent: a second invocation makes zero network calls and reports 33/33 hits.

`warm-media` is the only command that may write media entries. A routing run reads the media cache and never writes it — if an entry is missing during a live routing pass, the row degrades to `media_failed` (`media_unread` too when the message has no text) and the missing `media_id` is reported. This keeps a routing run's network behaviour honest: `route` cannot quietly become a paid media pass.

---

## 3. Perception cache

### Key

sha256 over the canonical serialisation of everything that was sent to the model, plus the prompt template version and the model identifier.

```python
key_material = {
    "cache_schema_version": 1,
    "prompt_template_version": PROMPT_TEMPLATE_VERSION,   # e.g. "1.0.0"
    "model_id": "<exact model identifier string>",        # e.g. "claude-sonnet-5"
    "model_params": {                                     # PerceptionConfig.model_params()
        "effort": "medium",
        "max_output_tokens": 2048,
        "thinking": "adaptive",
    },
    "response_schema_sha256": "<sha256 of perception.prompt.RESPONSE_SCHEMA>",
    "system_text": "<fully rendered system prompt, nonce-normalised>",
    "user_text": "<fully rendered user prompt, nonce-normalised>",
}

canonical = json.dumps(
    key_material,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
)
key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

There is no `temperature` and no `seed` in `model_params`, and their absence is not an oversight: the current model family **rejects sampling parameters outright** — a request carrying `temperature`, `top_p`, or `top_k` is a 400. Determinism therefore cannot be bought at the request level, and is not what this cache provides. What it provides is stronger: a result is computed **once** and every later run reads that same result, so the pipeline downstream of the cache is exactly reproducible even though the call that filled it was not.

### Nonce normalisation — required for the cache to work at all

[THREAT_MODEL M2](THREAT_MODEL.md#2-mitigation-architecture) fences untrusted content with a **per-run random nonce**. If that nonce went into the hashed text, every run would produce different keys and the hit rate would be exactly zero.

So the text that is *hashed* is nonce-normalised: every occurrence of the run's nonce is replaced by the literal `{NONCE}` before hashing. The text that is *sent* keeps the real random nonce.

```python
def normalise_for_key(rendered: str, nonce: str) -> str:
    """Replace the run nonce with a stable placeholder so keys are run-independent."""
    return rendered.replace(nonce, "{NONCE}")
```

Both properties survive: the nonce is unpredictable to an attacker in every live request, and the cache key depends only on content the author controls.

### What is in the key, and what is deliberately not

| in the key | why |
|---|---|
| `prompt_template_version` | an explicit version bump forces a miss even if the rendered text happens to collide |
| `system_text`, `user_text` (normalised) | **the actual bytes sent**. Any change to wording, ordering, or included context misses |
| `model_id`, `model_params` | a different model, effort level, or output ceiling is a different result |
| `response_schema_sha256` | changing `Perception`'s fields changes what the model is asked to produce |
| `cache_schema_version` | a one-line global invalidation lever if the envelope format ever changes |

| **not** in the key | why |
|---|---|
| `PolicyConfig`, `ConfidenceConfig`, `EvidenceConfig` | **policy must not miss the cache.** This is the whole point: rule and threshold iteration is free |
| `message_id`, `user_id` | already reflected in the rendered text; including them would prevent two identical prompts from sharing an entry |
| the run nonce | normalised out, see above |
| dataset file mtimes | content is already in the rendered text |

**Test that pins this:** change one character of the prompt template and assert 140/140 misses. Change `FORWARD_CHAIN_THRESHOLD` and assert 140/140 hits. Both are in the Phase 2 verification list.

### Stored payload

```json
{
  "perception": { "…validated Perception object…" },
  "raw_response": "<the model's response body, verbatim>",
  "retry_count": 0,
  "retry_reasons": [],
  "tokens": { "input": 1180, "output": 96 },
  "wall_clock_ms": 1432.7,
  "model_id": "…",
  "prompt_template_version": "3.1.0",
  "finish_reason": "stop"
}
```

The raw response is kept so that a parse change can be re-run offline against what the model actually said, without paying again. `retry_count` and `retry_reasons` are stored rather than recomputed, so the metrics engine can report schema-retry counts from cached rows.

### Invalidation

**By key construction only. Never manually.**

There is no TTL, no eviction, no `--clear-cache`, no "force refresh" flag. If a result should change, the thing that determined it has changed, and that changes the key. An entry that is no longer reachable simply stops being read.

This is a deliberate constraint rather than a convenience. A manual invalidation path is how a cache becomes untrustworthy: once anyone can clear an entry, a stale-looking result is ambiguous between "the key is wrong" and "someone cleared the wrong thing". Removing the path removes the ambiguity — **a hit is always the result that these exact inputs produce.**

### Invalidation is not the same as pruning

The invariant above is stated precisely so that it does not have to be weakened later. It is a rule about **reachable** entries, and its whole force comes from the ambiguity it prevents.

* **Invalidation** deletes an entry that *some key still addresses*. Forbidden. A later lookup for that key would miss, and the miss is ambiguous between "the inputs changed" and "someone deleted it" — which is exactly the ambiguity the invariant exists to remove.
* **Pruning** deletes an entry that *no key addresses*. Permitted. When a key scheme changes — a new field in the key material, a renamed component — the entries written under the old scheme become unreachable by construction. No lookup can ever return one, so removing it changes no result, produces no miss, and creates no ambiguity. It reclaims disk and nothing else.

The test for whether a deletion is a prune is mechanical: **enumerate every key the current code can construct for every input in the dataset; an entry whose key is not in that set is unreachable.** Two safeguards apply before any prune:

1. Enumeration is done from the live code path (`extractor_material` and `media_key`), never from a hand-written list, so it cannot drift from what the code actually addresses.
2. An unreachable entry that holds *extracted content* is still not deleted without saying so, because extracted content was paid for. Every entry pruned to date held container facts only.

The key-scheme change that split `model_id` into a per-kind `extractor` orphaned 33 media entries this way — 19 images and 13 voice notes at `decoded_only`, one image at `unsupported`, none holding extracted content. They were pruned; the 33 reachable entries were untouched, and `warm-media` remained a 33/33 no-op across the operation.

### Warm command

```
python main.py warm-perception
```

Computes the key for every row in `messages.csv` **and** every row in `sample_messages.csv` (140 rows — the labeled eval rows are routed through the identical pipeline, see [METRICS.md §2](METRICS.md#2-run-directory-layout)), and calls the model only for keys that are absent.

Requires the media cache to be warm first, because media content is part of the rendered prompt for the 23 media-bearing rows. `warm-perception` refuses to run if any required media entry is missing, and names the missing ones.

---

## 4. Policy — not cached

The policy layer, confidence scoring, evidence selection, and reason composition are recomputed on every run.

They are pure functions over typed inputs, they run in microseconds, and they are the code under iteration. Caching them would invalidate on every edit — a cache with a 0% hit rate during the only phase that matters, plus a whole class of "did my change take effect?" confusion.

---

## 5. On-disk layout

```
.cache/                                  gitignored
├── CACHE_SCHEMA_VERSION                 a single integer, newline-terminated
├── media/
│   ├── 0a/
│   │   └── 0a3f…c81.json
│   ├── 1b/
│   │   └── 1b77…4de.json
│   └── …                                256 shard directories, created on demand
└── perception/
    ├── 0c/
    │   └── 0c19…7ab.json
    └── …
```

- **Sharding** by the first two hex characters of the key keeps any one directory under a few hundred entries. With 33 media and ~140 perception entries this is not yet necessary; it is there so that growth never requires a migration.
- **One JSON file per entry**, UTF-8, `\n` line endings, `indent=2` for diffability.
- **Atomic writes.** Write to `<key>.json.tmp` in the same shard directory, `fsync`, then `os.replace`. A killed run can leave a `.tmp` file but never a truncated `.json`. Stray `.tmp` files are ignored by the reader and cleaned on the next write to that shard.
- `.cache/` is in `.gitignore`. The **submission** ships a pruned copy under `cache/` containing only the entries the 140 rows need, so that `python main.py route` reproduces the submitted output on a clean clone ([IMPLEMENTATION_PLAN §8](IMPLEMENTATION_PLAN.md#8-phase-6--submission-packaging)).

### Entry envelope

Every entry, both layers, uses the same envelope:

```json
{
  "cache_schema_version": 1,
  "kind": "media" | "perception",
  "key": "<the sha256 hex digest, matching the filename>",
  "created_at_utc": "2026-08-01T14:22:07.481Z",
  "key_material_digest": {
    "prompt_template_version": "3.1.0",
    "model_id": "…",
    "model_params_sha256": "…",
    "response_schema_sha256": "…",
    "system_text_sha256": "…",
    "user_text_sha256": "…"
  },
  "payload": { "…layer-specific, as specified above…" }
}
```

`key_material_digest` stores the **per-component** hashes rather than the full texts. Two purposes: an unexpected miss can be diagnosed by comparing components to see exactly which one changed, and the cache does not store a second copy of every prompt on disk. The full untrusted text is not persisted here — it is already in `dataset/`.

### Integrity check on read

A read validates, in order: the file parses as JSON; `cache_schema_version` matches the current version; `key` equals the filename stem; `kind` matches the requested namespace; `payload` validates against the layer's schema. **Any failure is a miss.**

---

## 6. Module boundary

```python
# code/cache/store.py — the ONLY module that touches .cache/

# CacheOutcome (HIT / MISS / CORRUPT / NOT_APPLICABLE) lives in code/schemas.py,
# not here: CallTelemetry carries it on every RoutingResult, and schemas.py is
# a leaf that must not import this module. See SYSTEM_DESIGN §4.

@dataclass(frozen=True)
class CacheRead[T]:
    outcome: CacheOutcome
    value: T | None              # populated iff outcome is HIT


def media_key(path: Path, cfg: CacheConfig) -> str: ...
def perception_key(bundle: PromptBundle, nonce: str, cfg: CacheConfig) -> str: ...

def read_media(key: str, cfg: CacheConfig) -> CacheRead[MediaCacheEntry]: ...
def write_media(key: str, entry: MediaCacheEntry, cfg: CacheConfig) -> None: ...

def read_perception(key: str, cfg: CacheConfig) -> CacheRead[PerceptionCacheEntry]: ...
def write_perception(key: str, entry: PerceptionCacheEntry, cfg: CacheConfig) -> None: ...

def missing_perception_keys(keys: Mapping[str, str], cfg: CacheConfig) -> list[str]: ...
    """Return the message_ids whose keys are absent. Used by the offline preflight."""
```

**No other module opens a file under `.cache/`.** Enforced by `tests/test_cache_isolation.py`, which greps the package for the cache-directory config name and for path literals under `.cache`, failing on any hit outside `code/cache/store.py`.

### Corrupt entries never raise

```python
def read_perception(key, cfg):
    path = _shard_path(cfg.perception_dir, key)
    if not path.exists():
        return CacheRead(CacheOutcome.MISS, None)
    try:
        entry = _load_and_validate(path, key, kind="perception")
    except (OSError, JSONDecodeError, ValidationError) as exc:
        log.warning(
            LogEvent.CACHE_ENTRY_CORRUPT,
            path="cache.store.read_perception",
            detail={"key": key, "error_type": type(exc).__name__},
            action_taken="treated_as_miss",
        )
        return CacheRead(CacheOutcome.CORRUPT, None)
    return CacheRead(CacheOutcome.HIT, entry)
```

A corrupt entry is logged once and treated as a miss. It is **never** raised, because a single bad file should not fail a 110-row run — and it is never silently ignored either, because `CacheOutcome.CORRUPT` is distinct from `MISS` and lands in the row record, so the metrics engine reports corruption separately from a genuine first-time miss.

In offline mode, a `CORRUPT` outcome for a required perception record counts as missing: the row is named in the preflight failure list rather than being called out to the network.

### The cache computes no metrics

`store.py` has no counters, no totals, no hit-rate calculation, no summary at exit. It returns a `CacheOutcome` per lookup. The pipeline puts that outcome on the row record:

```json
"cache": { "media": "hit", "perception": "hit" },
"served_entirely_from_cache": true
```

`code/metrics/label_free.py` derives media hit rate, perception hit rate, corruption counts, and the count of fully-cached rows from those fields, post hoc. This is what makes hit rate computable for **past** runs, including runs made before the metric was written.

---

## 7. Offline mode

```
python main.py route
```

Executes the full pipeline with **zero network calls**. Never falls back to a live call.

### Preflight

Before routing any row, the preflight resolves every dependency and reports everything missing at once — not one failure at a time.

1. **Media stage.** For each row with a `media_id`, resolve the path, hash the bytes, compute the media key, and check presence.
2. **Perception stage.** For each row, render the prompt — which requires the media artifact from step 1 — normalise the nonce, compute the perception key, and check presence.

A row with a missing media entry cannot have its perception key computed, so it is reported under `media` and skipped in the perception check rather than reported twice.

### Failure output

```
$ python main.py route

PREFLIGHT FAILED — 3 rows cannot be served from cache.

  missing media (1)
    msg_083   vn_006   media/audio/vn_006.mp3   key 4f2a…d19

  missing perception (2)
    msg_022   key 0c19…7ab
    msg_046   key 8b31…02f
       ↳ prompt_template_version 3.1.0, model_id claude-…
       ↳ 138/140 perception entries present (98.6%)

Run `python main.py warm-media` then `python main.py warm-perception` to populate.
No network calls were made. No output was written.

exit status 2
```

The run exits non-zero, writes no `output.csv`, and makes no call. It **never** silently calls out — that is the failure mode the flag exists to prevent, because a "free" iteration that quietly costs money is worse than one that fails.

`--offline` is the default for `route`. A live routing pass requires an explicit `--allow-network`, so the expensive thing is the one you have to ask for.

---

## 8. Configuration

```python
@dataclass(frozen=True)
class CacheConfig:
    # Repository-local so a clean clone with the shipped cache reproduces the run.
    root: Path = Path(".cache")
    media_dir: Path = Path(".cache/media")
    perception_dir: Path = Path(".cache/perception")

    # Bumping this makes every existing entry unreachable. The only global lever,
    # and it changes the key rather than deleting anything.
    schema_version: int = 1

    # Two hex characters -> 256 shards. Chosen so no directory grows unbounded;
    # not tuned to the current corpus size.
    shard_prefix_length: int = 2

    # Pretty-printed for diffability; entries are small and read once per run.
    json_indent: int = 2
```

`PROMPT_TEMPLATE_VERSION` lives in `code/perception/prompt.py` beside the template it versions, and `MEDIA_EXTRACTION_VERSION` in `code/media/extract.py`, because a version that lives away from the thing it versions is a version that gets forgotten. Both are read by `cache.store` through the key-material builders.
