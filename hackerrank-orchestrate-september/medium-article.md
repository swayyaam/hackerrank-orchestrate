# Every part was right. The answer was still wrong.

*What an AI affordability agent taught me about testing components versus testing outcomes. It placed 48th of 3,062 at HackerRank Orchestrate, and the reason it didn't place higher is the useful part.*

---

Here's a sentence I could say truthfully about my September hackathon build: every subsystem was tested, measured, and working. The spending-change search found the smallest valid set of cuts. The installment matcher followed every supplied schedule exactly. The output gate refused to write any row that broke a rule. 797 tests passed with no network.

And here's another sentence that is also true: when the correct answer needed the user to cut some spending, my agent got it wrong essentially every time.

Both are true at once, and the gap between them is the most useful thing I learned all year about building agents. This is the story of that gap, and what I'd do differently.

---

## The problem: "Can I afford this?"

The September edition of HackerRank Orchestrate, a 24-hour build-an-agent event, asked for a personal finance agent. For 250 requests (a laptop, a flight, a school fee, money sent home), decide whether the person should **pay in full now**, **pay part now and the rest later**, **take one of the seller's installment plans**, **wait**, or **not go ahead**.

It sounds like a chatbot question. It isn't. The rules are strict:

- A plan is only safe if the balance stays above the person's chosen minimum on **every one of the next 90 days**.
- Installments must match a seller's offer exactly. A partial payment must be exactly two payments that add up to the full amount.
- If nothing is safe as-is, you may cut or reduce **flexible** recurring expenses, up to three of them, to make a plan work.
- When several plans are safe, a six-level tie-break decides which one wins.

And the inputs are messy. There are 25,000 transactions across 275 people, with pending charges, failed payments, duplicates, refunds that haven't landed, five currencies, and some transactions whose amount is **blank** because it only exists on a photo of a receipt or payslip. There are also messages from banks and employers that can legitimately change a transaction ("your bill has moved to the 20th"). Two of them are scams promising a cash prize if you "pay the release charge today".

---

## The design: keep the model out of the arithmetic

I've made the same bet in three straight editions, and I'd make it again: **let the model read, and let plain code decide.**

On this problem that bet gets sharper, because the output is almost entirely arithmetic under a strict contract. A model approximates a six-key ranking. Code satisfies it. So the model got exactly two jobs:

1. **Read an amount off a document** when a transaction's amount is blank. Two independent reads, reconciled in code by checking the line items against a printed total.
2. **Turn a message into a typed change** to a transaction that already exists: "event 412's date moves to the 20th".

Everything else is code: cleaning the history, detecting what recurs, forecasting the balance day by day, computing the safe amount and the earliest safe date, enumerating and ranking plans, searching for spending cuts, and writing the explanation from the finished decision. The model never produces a number that reaches the output file.

### The guard that stopped the scams without looking for scams

The part of this design I'd most want you to steal is how messages are handled. A message can change a financial picture, and anything a model reads is potentially hostile, so the model's proposed changes go through a four-rule guard:

- The change must name a transaction that **exists**. An invented ID is dropped.
- It may only touch **three fields**: amount, date, or status. It can't move the minimum balance, the request, or the user's preferences.
- It may not create a **new financial fact**. A message claiming income that no transaction records is kept as evidence and dropped as a change.
- Every drop is **logged** with the rule that dropped it.

The two advance-fee scams in the dataset didn't need a scam detector. They asserted money that no transaction recorded, so there was nothing for them to amend, and they fell out on rule three. That's the general lesson: a structural limit on what untrusted input is *allowed to change* beats trying to recognize bad input.

### Every ambiguity became a named switch

The spec leaves a lot open. Does a pending duplicate debit count? When exactly does the 90-day window end? If someone's salary series stops, do you assume it continues? Instead of burying those choices in code, I put each one in a **decision register**: 28 named entries, each with a default, the alternative, the reasoning, and a note on whether the 25 published example answers can even tell the two options apart.

One entry shows why this matters. Two example requests failed in opposite directions for the same reason: in one, a salary ended because the job ended; in the other, it resumed after leave. Every typed field was identical. Only the free-text description differed, and reading descriptions would be memorizing this dataset. Both options are wrong somewhere. I chose to **not** continue the series, because inventing income for someone who just lost their job produces a confident "yes, you can afford it", while the opposite mistake only produces an over-cautious "no". When both answers are wrong, pick the one that fails safe, and write down that you did.

---

## The result

3,062 people were scored. I finished **48th, with 70.9 out of 100**, and a silver medal.

| Stage | My score | Best in the field | Rank |
|---|---|---|---|
| Chat Transcript | **10.0 / 10** | 10.0 | tied 1st (19 people got full marks) |
| Interview | **24.6 / 30** | 27.6 | 85th |
| Technical Review (the code) | 21.6 / 30 | 27.9 | 242nd |
| Output accuracy | 14.7 / 30 | 21.9 | 472nd |
| **Total** | **70.9 / 100** | 80.0 | **48th of 3,062** |

Full marks on the transcript, which grades how you worked with your AI tools. A strong interview. And an output score that 471 people beat. That last row is the interesting one.

---

## Where it broke: a correct component that never ran

HackerRank sends written feedback after each edition, and the output review was specific: my file handled the straightforward "pay now, nothing to adjust" cases well, and performance dropped sharply once the right answer depended on a constrained plan. **The most consistent gap was leaving out the spending cuts needed to make the plan work**, and everything downstream (the status, the safe amount, the dates, the installment structure) drifted with it.

When I went back to the 25 published examples, the mechanism was right there. Three of them need a spending change. My agent called all three "affordable now, no changes needed".

Here's the chain:

1. **The forecast under-counted everyday spending.** Groceries, fuel, and other irregular essentials don't follow a neat monthly pattern, and my forecast was too optimistic about them. So the balance looked healthier than it really was.
2. **On that healthier balance, paying in full today looked safe.**
3. **The spending-change search only runs when nothing is safe without it.** Since full payment looked safe, it never ran.
4. **Everything else followed the wrong plan.** Wrong status, wrong safe amount, wrong explanation, all perfectly consistent with each other and all wrong.

Across all 250 requests, the spending-change search fired **4 times**. The examples suggest roughly one request in eight needs a change.

The painful part is that I *knew* about step 1. I had a whole forensics document on it. I'd measured that my forecast was optimistic, fixed about half of it (adding a trailing-rate forecast for irregular spending improved my trough error by almost five points), and written the rest down as a known limitation. What I hadn't done was follow that error *downstream* and ask what it switched off.

Every component had its own metric, and most of them looked fine. The failure wasn't in any component. It was in a handoff.

---

## What I'd do differently

**1. Build one end-to-end divergence report, and look at it first.** I had a scorecard for the sample set, but it reported column by column. What I needed was one report that answered: for each example where my final row differs from the published one, *which decision point diverged first?* Grouped that way, three rows would have said "you predicted no changes; the answer has changes" in one line. The code review feedback asked for exactly this: a single, repeatable run that shows where final decisions diverge, so fixes come from observed error patterns rather than subsystem metrics.

**2. Make the risky decision a first-class step, not a fallback.** My spending-change search was a fallback: it only ran when every other plan had failed. That made it hostage to anything upstream that made plans look too safe. The feedback's suggestion is the right one: make "which spending actions does this plan need?" a mandatory step, then recompute every dependent number and date from the result.

**3. Follow an error in the direction it hurts.** An affordability agent that's wrong on the optimistic side tells people they can afford things they can't. I knew my error pointed that way. A known error in the unsafe direction should get the last hours of a build, ahead of polishing anything else.

**4. Prioritize the outputs that drive the decision.** The transcript feedback was kind about my process and made one sharp point: deep work on a subsystem got ahead of end-to-end correctness. I spent real time on a document-reading bug (a receipt where the rupees and paise sat in separate columns, which made one total read as 670 instead of 4,543). That was worth fixing. It affected one transaction. The forecast issue affected a large share of the file.

**5. Leave room for bounded judgement.** I originally planned one small, bounded step where a model would choose between a closed set of actions when the typed fields genuinely couldn't decide, like the salary case above. I cut it for time, and removed it from the config rather than leave it looking live. The code review flagged its absence. Keeping arithmetic in code is right; refusing the model *any* judgement, even bounded and auditable judgement, left some cases settled by a default that's sometimes wrong.

---

## What held up

It's worth saying what worked, because it's the reason the total held at 48th despite the output score.

**The process.** The transcript got full marks, and the feedback described it as "technical owner plus AI implementer": the architecture decided up front, tight limits on what the model may do, and a steady rhythm of probing, measuring, and reversing when evidence contradicted a hypothesis. Concretely, that meant things like measuring the model provider instead of trusting its docs. That's how I found that temperature 0 alone wasn't reproducible but temperature 0 with a fixed seed was, and that a too-small token budget came back as a *successful, empty* response that a naive client would read as "this message changes nothing".

**Pre-committed experiments.** For the variable-spending question I ran all eight on/off combinations of three switches, and wrote down my prediction for each one *before* running any of them. It's the cheapest defense against fooling yourself I know.

**Honest limitations.** The project ships a limitations file that opens with a status table of what's still wrong. In the interview, the strongest moments were the ones where I could defend a specific choice at the implementation level and say clearly what I had and hadn't verified. The feedback's advice for next time is good general advice too: **lead with the number, then the reasoning**, name the file where a behavior lives, and bring up your riskiest limitation before you're asked, with a symptom-to-root-cause walkthrough of one real miss. The spending-change story above is the walkthrough I wish I'd told.

---

## The takeaway

Testing parts is necessary, and it isn't the same as testing the answer. A system can be correct in every component and still be wrong at the output, because the failure lives in how one component's error changes what another component gets to do.

So measure both, and when they disagree, trust the one that scores the whole row.

---

## The code

The full project is public and MIT licensed:

**[github.com/swayyaam/hackerrank-orchestrate](https://github.com/swayyaam/hackerrank-orchestrate)**

If it helps you, a star is genuinely appreciated.

- The [project README](./README.md) covers the architecture, the commands, a file-by-file guide, and the full results.
- The design docs are in [`code/docs/`](./code/docs/). Start with `WALKTHROUGH.md`, which follows one request through every step with real values, then `LIMITATIONS.md`.
- The test suite and the no-network path (`--no-perception`) run without any API key.
