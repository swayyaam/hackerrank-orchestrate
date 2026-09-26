# Token usage and cost — final full-dataset run

Generated from `evaluation/run_manifest.json` by `bow/usage.py`. Every figure below is read from that manifest; every rate from `config/pricing.yaml`. Nothing here is typed in by hand, so the report cannot drift from the run it describes.

Single provider: **sarvam**, one credential, no other vendor.

Rates from `docs.sarvam.ai/api-reference-docs/pricing`, read 2026-09-12. Amounts are in INR; the USD figures use 0.01133 USD per INR, read 2026-09-13.

## Models used

| role | provider | model | endpoint | billed by |
|---|---|---|---|---|
| `lang_detect` | sarvam | `text-lid` | `/text-lid` | characters |
| `message_extract` | sarvam | `sarvam-105b` | `/v1/chat/completions` | tokens |
| `message_extract_oos` | sarvam | `deepseekv4-flash` | `/v2/chat/completions` | tokens |

## Per-model totals

| role | model | calls | input | output tokens | INR |
|---|---|---:|---:|---:|---:|
| `lang_detect` | `text-lid` | 198 | 49,780 characters | — | 17.42 |
| `message_extract` | `sarvam-105b` | 171 | 141,361 | 1,169,038 | 89.71 |
| `message_extract_oos` | `deepseekv4-flash` | 39 | 32,891 | 174,652 | 11.03 |
| **overall** | | **408** | **174,252** | **1,343,690** | **118.16** |

## Rates applied

| model | rate |
|---|---|
| `deepseekv4-flash` | INR 19.8 / 1,000,000 input, INR 59.4 / 1,000,000 output |
| `sarvam-105b` | INR 29.28 / 1,000,000 input, INR 73.2 / 1,000,000 output |
| `text-lid` | INR 3.5 / 10,000 characters |

Reasoning tokens are billed as completion tokens by this provider, so no separate reasoning line appears: the completion figure already contains them.

## Totals

| metric | value |
|---|---|
| requests | 250 |
| model calls | 408 |
| input tokens | 174,252 |
| output tokens | 1,343,690 |
| **total tokens** | **1,517,942** |
| **average tokens per request** | **6,072** |
| **average model calls per request** | **1.63** |
| **estimated total cost** | **INR 118.16 (~USD 1.3388)** |
| **estimated cost per request** | **INR 0.473 (~USD 0.0054)** |
| wall time | 1,432.8 s |

## Where the cost goes

- `message_extract` — INR 89.71, 75.9% of the bill across 171 call(s), averaging 6836 completion tokens per call.
- `lang_detect` — INR 17.42, 14.7% of the bill across 198 call(s).
- `message_extract_oos` — INR 11.03, 9.3% of the bill across 39 call(s), averaging 4478 completion tokens per call.

## Reproducibility

Every chat role pins `temperature: 0`, `top_p: 1` and a per-role `seed`. See `docs/LIMITATIONS.md` L5 for the measured limits of that guarantee.

No API key, credential, endpoint secret or other sensitive configuration appears in this report. The provider is named; the credential is read from an environment variable that is never printed.

Run shape: 250 requests, 250 rows written, 0 unresolved item(s).
