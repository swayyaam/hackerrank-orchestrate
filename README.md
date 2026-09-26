# HackerRank Orchestrate: my hackathon projects

This repo holds my entries to **HackerRank Orchestrate**, a monthly 24-hour event where you design
and ship an AI agent. Each month lives in its own folder with its own data, code, and a single README
that walks through the whole thing. If you're here to learn from the code, start with whichever
project sounds most interesting and read its README top to bottom.

| Month | Project | What it does | Model | Result |
|---|---|---|---|---|
| **September 2026** | [Buy or Wait? Personal Affordability Agent](./hackerrank-orchestrate-september/) | Answers "can I afford this?" by rebuilding a user's cash position, forecasting 90 days ahead, and choosing between paying in full, part now, installments, waiting, or not going ahead. Fills in 8 columns. | Sarvam `sarvam-105b` and `sarvam-vision` (single vendor) | 🥈 **48th of 3,062** |
| **August 2026** | [WhatsApp Message Notification Router](./hackerrank-orchestrate-august/) | Decides whether an incoming message should interrupt the user now, wait in a digest, or be muted, personalised per receiver across text, image posters, and voice notes. Fills in 6 columns. | Claude Sonnet 5, plus Sarvam `saaras:v3` for voice | **37th of 1,983** |
| **June 2026** | [Multi-Modal Damage-Claim Verification](./hackerrank-orchestrate-june/) | Looks at the photos on a damage claim and decides whether they back up the claim, contradict it, or aren't enough to tell. Fills in 14 explained columns. | Claude Opus 4.8 (vision) | 🥉 **3rd of 1,773** |
| **May 2026** | [Support Triage Agent](./hackerrank-orchestrate-may/) | Reads support tickets for three help desks (HackerRank, Claude, Visa) and either answers them from local docs or sends them to a human. Fills in 5 columns. | Claude Haiku 4.5 | **60th of 1,349** |

Each project keeps its own scraped final leaderboard:
[`leaderboard-september.json`](./hackerrank-orchestrate-september/leaderboard-september.json),
[`leaderboard-august.json`](./hackerrank-orchestrate-august/leaderboard-august.json),
[`leaderboard-june.json`](./hackerrank-orchestrate-june/leaderboard-june.json), and
[`leaderboard-may.json`](./hackerrank-orchestrate-may/leaderboard-may.json). They're all pulled with
[`scrape_leaderboard.py`](./scrape_leaderboard.py) at the repo root.

---

## September 2026: Buy or Wait? Personal Affordability Agent

The same split as June and August, taken as far as it goes: **a deterministic financial solver with
a narrow model layer, where the model never produces a number that reaches the output.** The model
does two jobs only: read an amount off a payslip or receipt when a transaction's amount is blank, and
turn a bank or employer message into a typed change to a transaction that already exists. Code does
the rest: cleaning up the transaction history, spotting what recurs, forecasting the balance 90 days
out, working out the safe amount and the earliest safe date, ranking every valid payment plan, and
writing the explanation. The part worth stealing is the message guard: a message may only amend the
amount, date, or status of an event that exists, so the two advance-fee scams in the dataset had
nothing to change and fell out without any scam detection at all. Every open question in the spec
became a named switch in a 28-entry decision register, and the chat transcript scored full marks.

**[Read the September README](./hackerrank-orchestrate-september/README.md)** for the architecture,
the decision register, the cost of a full run, and an honest account of the forecasting error that
cost me on output accuracy.

## August 2026: WhatsApp Message Notification Router

Same core idea as June, pushed a step further because this problem is adversarial: **the model
describes and proposes, and plain code makes the actual decision.** The model returns a structured
record (a proposed action, ranked type candidates, and yes/no claims about the content), and a table
of 16 rules in fixed precedence order owns the final call. None of the four scored output values is
written by the model. The part worth stealing is that every rule declares which inputs it reads, so a
rule like "mute a business impersonating a brand" can be made a pure function of verification and
domain state, with the message text simply not in scope. A scam can't talk its way past a rule that
never reads it, and one test file proves injected text can't reach those rules while another proves
each rule really does read what it claims.

**[Read the August README](./hackerrank-orchestrate-august/README.md)** for the architecture, the
rule table, the offline reproduction path, and an honest account of which stage cost me the most.

## June 2026: Multi-Modal Damage-Claim Verification

The whole project rests on one idea: **the vision model only describes what it sees, and plain code
makes the actual decision.** Keeping those two jobs apart pays off in a few ways. You get the same
answer every time (the decision is just code, so you can save the model's observations and re-run the
logic offline with no API calls). It's auditable, since every column traces back to a specific rule.
And it resists trickery: an instruction hidden inside an image can confuse one observation but can
never reach the code that decides. A person's claim history can add caution, but the structure makes
it impossible for history to flip a verdict on its own.

**[Read the June README](./hackerrank-orchestrate-june/README.md)** for the architecture, the
commands, the decisions, and the results.

## May 2026: Support Triage Agent

A command-line agent that answers or escalates support tickets using only a local set of help docs,
with no internet calls while it runs. Each ticket goes through a pipeline: filter out junk, clean up
the search query, search the docs (fast keyword search, then a small local model re-orders the best
hits), score the confidence, check a few hard escalation rules, have Claude draft a reply, validate
and if needed repair it, strip anything the model made up, and log the reasoning. As in June, the
model only writes; the routing decisions are plain code you can read.

**[Read the May README](./hackerrank-orchestrate-may/README.md)** for setup, a file-by-file guide,
the decisions behind it, and the results.

---

## How this repo is organized

```
.
├── hackerrank-orchestrate-september/ September project (code/, dataset/, output.csv, README.md, leaderboard-september.json)
├── hackerrank-orchestrate-august/ August project (code/, dataset/, submission/, README.md, leaderboard-august.json)
├── hackerrank-orchestrate-june/   June project   (code/, dataset/, README.md, leaderboard-june.json)
├── hackerrank-orchestrate-may/    May project    (code/, data/, support_tickets/, README.md, leaderboard-may.json)
├── scrape_leaderboard.py          pulls a final leaderboard into leaderboard-<month>.json
├── .gitignore                     one shared ignore file for the whole repo
└── README.md                      you are here
```

## Good to know

- There's **one `.gitignore` at the top** covering all four projects (secrets, virtual environments,
  caches, and generated run files). Each project declares its own dependencies: May and June use a
  `requirements.txt`, August and September use a `pyproject.toml` (September also ships a `uv.lock`).
- **Secrets stay out of git.** The `.env` files are ignored and never committed. To run the May or
  June agent, copy that project's `.env.example` to `.env` and paste in your own
  `ANTHROPIC_API_KEY`. August needs no key at all on its default path: it ships a cache and
  reproduces its submitted output offline. September runs its test suite and its deterministic path
  (`--no-perception`) offline; the full run needs a `SARVAM_API_KEY` in a `.env` placed *next to*
  its `code/` folder, not inside it.
- The challenge **datasets, problem statements, and rulebooks belong to HackerRank.** Each project's
  README says what's included and what isn't.
- **License.** My code is released under the [MIT License](./LICENSE). That covers my code only, not
  HackerRank's problem statements or any bundled help-center data, which stay their owners' property.
