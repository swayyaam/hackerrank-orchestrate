# Problem Statement: Message Notification Router

> The original problem statement and instructions for the **HackerRank Orchestrate** hackathon
> (August 2026), kept here for context. The wording is HackerRank's; I only cleaned up the
> formatting and folded the starter repo's submission notes into one place. The output schema as
> my system actually implements it is written up in the project [README](./README.md#data-format).

## Overview

Build an AI-powered system for WhatsApp that decides which messages deserve immediate attention,
which should wait, and which should be muted.

The system must reason over **multimodal messages**, including text messages, image
posters/screenshots, and voice notes.

WhatsApp is noisy. A user can receive family chats, society notices, school updates, co-worker
messages, business account promotions, image posters, voice notes, and scams in the same message
stream. Treating every message the same creates two bad outcomes: important messages get missed, and
unwanted or risky messages interrupt the user.

Your task is to build a **message notification router** for this platform. For every incoming
WhatsApp message, the system must decide whether the user should be interrupted now, whether the
message can be batched into a digest, or whether it should be muted.

The routing decision must be personalized to the receiving user. A sale poster may be useful for one
user and unwanted noise for another. A payment reminder may be legitimate from a trusted admin but
risky from a new sender. A muted family group can still contain an urgent direct mention. At the same
time, clear scam or safety risk should be muted regardless of the user's usual engagement.

## What the system should do

Review each incoming message and decide how it should be handled for that user:

- `notify`: important enough to interrupt now
- `digest`: useful, but can be shown later
- `mute`: low-value, repetitive, unwanted, suspicious, or unsafe

Your system should use the provided message, user, group, business, media, and historical interaction
data to make personalized routing decisions.

## Files provided

All participant-facing files are inside `dataset/`. Only `dataset/messages.csv` needs predictions.
The other files provide context.

| File | What it holds |
|---|---|
| `messages.csv` | Incoming messages that your system must route |
| `sample_messages.csv` | Example messages with expected `action`, `message_type`, `reason`, `confidence`, and `evidence_message_ids` filled in. Use this only to understand the expected output format and style |
| `users.csv` | Basic user notification behavior: quiet hours, recent opens, replies, dismissals, reports |
| `groups.csv` | Group chat information: group type, size, admins, recent activity |
| `group_members.csv` | How each user relates to each group: role, activity, read/reply behavior, dismissals, mute state |
| `business_accounts.csv` | Business senders: brand identity, verification, sending domain, account age, reports |
| `user_business_history.csv` | Whether a user has a recent relationship with a business: orders, bookings, payments, opt-ins, opt-outs |
| `message_history.csv` | Past messages received by users, for identifying repeated patterns, ignored messages, useful updates, and risky content |
| `message_events.csv` | How users reacted to those past messages: opened, replied, dismissed, muted, reported |
| `images.csv` | Image IDs and file paths for image messages |
| `voice_notes.csv` | Voice note IDs and file paths for audio messages |
| `daily_notification_summary.csv` | Daily notification load for each user |
| `output.csv` | Blank submission template. Fill this file with your predictions |

Media files referenced by `images.csv` and `voice_notes.csv` are available under `dataset/media/`.

## Input schema

Each row in `dataset/messages.csv` represents one incoming message.

- `message_id`: unique incoming message ID
- `user_id`: user receiving the message
- `conversation_type`: `personal`, `group`, or `business`
- `group_id`: group ID if the message is from a group
- `business_id`: business ID if the message is from a business account
- `sender_user_id`: sender user ID if the message is from a user
- `created_at`: message timestamp
- `message_text`: text content for text messages; empty for voice-note messages
- `media_type`: empty, `image`, or `voice`
- `media_id`: linked image or voice-note ID, if present
- `forwarded_count`: forwarding signal

## Required output

For every row in `dataset/messages.csv`, generate one row in `output.csv`. Required columns, in
order:

```text
message_id,action,message_type,reason,confidence,evidence_message_ids
```

- `action`: final routing decision
- `message_type`: best-fit message category
- `reason`: short human-readable explanation for the decision
- `confidence`: number from `0` to `1`
- `evidence_message_ids`: semicolon-separated historical message IDs used as evidence; write `none`
  if no useful historical message exists

## Allowed values

`action`:

- `notify`: interrupt the user now
- `digest`: safe but low priority; show later
- `mute`: repetitive, unwanted, low-value, suspicious, scam-like, or unsafe for this user

`message_type`: `personal`, `urgent`, `event`, `payment`, `business_update`, `promotion`,
`greeting`, `forward`, `spam`, `scam`, `unknown`

## Important behavior

Your system should make personalized decisions using the full context provided. Similar-looking
messages may need different actions depending on the user, sender, conversation, business
relationship, and media content.

The final decision should balance usefulness, urgency, repetition, and risk. Risky messages should
use `mute` with an appropriate `message_type` such as `scam` or `spam`.

## Requirements

Your solution must:

- be runnable from the terminal
- read the provided files from `dataset/`
- produce a valid `output.csv`
- include one prediction for every `message_id` in `dataset/messages.csv`
- not use organizer-only files or hardcoded labels

If you use API keys or secrets, read them from environment variables. Never hardcode secrets in the
repo.

You may use any language or runtime. Python, JavaScript, and TypeScript are all reasonable choices.

## Evaluation

Your `output.csv` will be compared against hidden ground-truth labels. The scoring considers:

- correctness of `action`
- correctness of `message_type`
- usefulness and consistency of `reason`
- whether `evidence_message_ids` point to relevant historical messages
- reasonable confidence calibration

Strong systems will combine retrieval, structured metadata, behavioral history, safety checks,
OCR/ASR handling, and contextual reasoning.

## Chat transcript logging

The starter repo shipped an `AGENTS.md` rules file for AI coding tools, asking compatible tools to
append conversation summaries to a log file kept outside the repository:

| Platform | Path |
|---|---|
| macOS / Linux | `$HOME/hackerrank_orchestrate_august26/log.txt` |
| Windows | `%USERPROFILE%\hackerrank_orchestrate_august26\log.txt` |

That log is uploaded as the chat transcript at submission time. Secrets must not be pasted into the
chat.

## Submission

Three files are uploaded on the HackerRank platform:

| File | Description |
|---|---|
| `code.zip` | Full runnable solution, prompts/configs, and README |
| `output.csv` | Predictions for all rows in `dataset/messages.csv` |
| `chat_transcript` | The `log.txt` described above |

Before submitting, confirm that `output.csv` has one row per row in `dataset/messages.csv`, that it
has the exact required columns in the exact required order, and that runnable code with setup
instructions is included in `code.zip`.

Beyond these must-haves, participants are encouraged to improve retrieval, prompting, evidence
selection, confidence handling, batching, caching, or safety logic.

## AI Judge interview

After a successful submission, an AI Judge interview opens. The AI Judge can see your submission and
may ask about your approach, your decisions, and how you used AI while building the solution.

Results were announced on August 7, 2026.
