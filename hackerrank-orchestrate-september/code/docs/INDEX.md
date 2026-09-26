# Documentation index

One line per document: the question it answers. Read in this order if you have
no context.

| document | the question it answers |
|---|---|
| [../README.md](../README.md) | What is this, how do I run it, and what does each output column come from? |
| [WALKTHROUGH.md](WALKTHROUGH.md) | What actually happens to one request, hop by hop, with the real values at every step? |
| [QA.md](QA.md) | The questions a reviewer will ask, and the grounded answer to each. |
| [NUMBERS.md](NUMBERS.md) | Every figure I might be asked for, with the command that regenerates it. |
| [MONITORING.md](MONITORING.md) | What would I watch in production, at what threshold, and who gets paged? |
| [AUTHORSHIP.md](AUTHORSHIP.md) | Which parts were AI-generated, which were mine, and where did I overrule the assistant? |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Why a deterministic solver with a narrow perception layer, what was rejected and why, and what the layering rules are? |
| [LIMITATIONS.md](LIMITATIONS.md) | What is wrong with it, what did we fix, and where does each fault bite? Start at the status table. |
| [DATA_REPORT.md](DATA_REPORT.md) | What is actually in the dataset — every enum, join, chain, blank amount, image, currency pair and untrusted-content instance? **Read its Corrections section first.** |
| [CAPACITY_FORENSICS.md](CAPACITY_FORENSICS.md) | Why was `amount_safe_to_pay` wrong, and which hypotheses about the ground-truth formula survived testing? |
| [VARIABLE_SPEND_EXPERIMENT.md](VARIABLE_SPEND_EXPERIMENT.md) | Should irregular, single-occurrence and stale spending be forecast? Eight combinations, with the predictions written down before the run. |
| [PROVIDER_CAPABILITIES.md](PROVIDER_CAPABILITIES.md) | What does the provider actually support — endpoints, models, ceilings, job states, prices — measured rather than assumed? |
| [MULTILINGUAL_PROBE.md](MULTILINGUAL_PROBE.md) | Does the in-set model handle the dataset's non-English messages, and is the language-routing machinery earning its place? |

## Reproducing the numbers

| claim | command |
|---|---|
| the sample-set scorecard | `python evaluation/harness.py` |
| the end-to-end trace of one request | `python -m pytest tests/test_endtoend_groundtruth.py -s` |
| the variable-spend experiment | `python evaluation/ablations.py` |
| the vision gold score | `python scripts/score_vision_gold.py` |
| document-read stability | `python scripts/probe_vision_stability.py --trials 5` *(billed)* |
| `reasoning_effort` A/B | `python scripts/probe_reasoning_effort.py --arm low` *(billed)* |
| provider capabilities | `python scripts/probe_sarvam.py` *(billed)* |
| the credential and zip boundary | `python scripts/check_env.py` |

Everything above runs from `code/`. Only the four marked *billed* make API calls.
