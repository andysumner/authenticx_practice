# CLAUDE.md

Guidance for Claude when working in this repository.

## What this project is

A **conversation-intelligence ingestion pipeline** — a self-contained "mini-Authenticx" that mirrors the forward-deployment flow of a contact-center analytics platform: conversation data flows from **Amazon Connect**, through reliable ingestion, into processing, storage, and outbound signals.

The point is to build **both sides of the ingestion contract** — the connector that pushes data in *and* the receiving API that ingests it.

**This is an interview-prep learning project, not a production system.** The author (Andrew) is preparing for a Forward Deployment Engineer interview. Claude writes most of the code, but the deliverable is Andrew's *understanding*, not just a working repo. Every decision in this file flows from that.

## Learning-first working style (read this before writing any code)

Andrew must be able to explain every part of this system in an interview. Therefore:

1. **Explain before and after.** Before building something non-trivial, give a short plain-language preview of the approach and why. After it works, summarize what was actually done and what changed.
2. **One concept at a time.** When introducing something new to Andrew (SQS visibility timeouts, presigned URLs, Connect contact trace records), pause and explain it in 3–6 sentences with an analogy to something he already knows — webhooks, ETL pipelines, Ignition event-driven services, PostgreSQL.
3. **No magic.** Prefer explicit, readable code over clever abstractions. If a library hides important behavior (retries, auth, serialization), note what it's doing under the hood.
4. **Update the build journal.** No work session is complete until `docs/progress.html` has a new entry (see below) and Claude has offered a walkthrough of the change.
5. **Check understanding.** At the end of each phase, offer 3–5 interview-style questions about what was just built (e.g., "How does the connector guarantee it never double-ingests?"). Andrew answers; Claude corrects gently.
6. **Connect to his experience.** Where the pattern echoes his resume — Ignition webhook ingestion, bidirectional FAS sync, multi-platform ETL into PostgreSQL, RPA monitoring/logging — say so explicitly. Those bridges are what he'll use in the interview.

## Build journal: docs/progress.html

A single **self-contained static HTML page** (inline CSS, no build step, no external dependencies) that Andrew can open in any browser or screen-share in an interview. Claude appends an entry after every meaningful work session.

Each entry contains:

- **Date + phase** and a one-line title.
- **What was built** — files/components touched, in plain language.
- **Why this design** — the alternatives considered and the tradeoff made.
- **How it works** — a short walkthrough a non-expert could follow; diagrams (inline SVG) where helpful.
- **New concepts learned** — each with a one-paragraph explanation.
- **Interview talking point** — one or two sentences Andrew could say verbatim about this piece.

Keep entries newest-first. Keep the page readable and unpretentious — clarity over polish.

## Non-negotiable rules

Hard constraints. Never violate them, even if asked to "just for testing."

- **Synthetic data only.** Never use real call recordings or real personal information anywhere — fixtures, tests, seeds, examples. Generate realistic-but-fake data and treat it *as if* it were PHI. (Test calls placed through Amazon Connect must be scripted/role-played, never real conversations with real personal details.)
- **Redact before persisting.** Sensitive fields (phone numbers, emails, IDs, names) must be redacted before any text is written to storage or logs.
- **Never log sensitive payloads.** Log IDs and status, not content.
- **No hardcoded secrets.** API tokens, AWS keys, DB credentials live in environment variables (or a vault) — never committed, never inlined. Check for accidental secrets before suggesting a commit.
- **Least privilege.** Each component gets IAM credentials scoped to only the resources it needs. Explain each IAM policy when it's created — IAM is new territory for Andrew and a likely interview topic.
- **TLS for every network hop.** Note in code/comments where data would be encrypted at rest in production.
- **Mind the AWS bill.** Stay on free-tier-friendly choices; flag anything that could incur charges *before* creating it, and prefer teardown-able infrastructure (document cleanup steps).

A data-touching feature isn't "done" until it satisfies these.

## Architecture

```
Amazon Connect (calls recorded to S3)
  → Ingestion connector (S3 ObjectCreated → SQS, or CTR stream)
    → Queue (buffer + retries + dead-letter queue)
      → Transform & upload
        → Mock ingestion API (FastAPI — the "AcxAPI" clone)
          → Processing (transcribe → redact → detect signals)
            → PostgreSQL
              → Outbound emission (webhook to downstream system)
                → (optional) Next.js dashboard
```

Recordings and metadata arrive **separately** (audio in S3, contact attributes via contact trace records) and are matched by contact ID downstream — preserve that separation rather than assuming a single combined payload.

## Tech stack

- **Source:** Amazon Connect on the AWS free tier — instance, call recording to S3, a few scripted test calls. Chosen deliberately so Andrew gets hands-on AWS experience (S3, SQS, IAM, Connect).
- **Language & API:** Python 3.11+ with FastAPI for the mock ingestion API.
- **Database:** PostgreSQL — media records, metadata, transcripts, signals.
- **Queue:** AWS SQS with a dead-letter queue.
- **Transcription:** OpenAI Whisper (open-source, runs locally; requires ffmpeg).
- **Redaction & signals:** regex + a lightweight NER library; keyword/sentiment rules (or an LLM API) for signal detection.
- **Dashboard (optional):** Next.js — Andrew's frontend framework.
- **Tooling:** Docker for local orchestration; GitHub for the repo.

Since AWS is the learning goal, prefer the AWS-native option when one exists (SQS over local Redis, S3 events over polling) and explain the AWS concepts as they appear.

## Data model

Four core entities — keep these distinct:

- **Media record** — the audio artifact and its storage location/status.
- **Interaction metadata** — contact ID, timestamp, queue, agent, duration (from Connect contact trace records).
- **Transcript** — raw and redacted text, linked to a media record.
- **Signal** — detected events (negative sentiment, safety keyword, topic tag) linked to a transcript.

## Reliability expectations

The job description explicitly screens for "proper handling of authentication, error handling, retries, and monitoring" — so these are first-class requirements, not polish:

- **Idempotency keys** so the same recording is never double-ingested.
- **Retry with exponential backoff** on transient failures.
- **Dead-letter queue** for exhausted items — never silently drop data.
- **Authentication** on the ingestion API (API token) and least-privilege IAM throughout.
- **Structured logging and basic monitoring** (throughput, error rate, DLQ depth) from early on, not just in Phase 6.

When adding ingestion or emission logic, build these in from the start.

## SQL validation habit

The role requires using "SQL to validate data, troubleshoot issues, and support testing." Bake that in: after each phase that writes to PostgreSQL, write a few validation queries (row counts by status, orphaned media records without metadata, signals without transcripts, duplicate contact IDs) and keep them in `sql/validation/`. These double as interview material.

## Interview alignment map

How project pieces map to what the role screens for — use this framing in journal entries and walkthroughs:

| Job requirement | Where this project proves it |
|---|---|
| Integrations via APIs, webhooks, event-driven systems | S3 event → SQS connector; ingestion API; outbound webhook emission |
| Reliability: auth, error handling, retries, monitoring | Idempotency, backoff, DLQ, API tokens, ingestion-health metrics |
| SQL with production-style data | Schema design + `sql/validation/` queries |
| Cross-system data mapping & workflow logic | Connect CTR metadata → internal schema; signal → downstream action |
| Document architecture, constraints, dependencies | README architecture diagram + build journal |
| Healthcare / regulated environments | PHI-style handling: redaction, least privilege, audit trail, BAA/HIPAA README section |
| CCaaS familiarity (NICE, Genesys, etc.) | Amazon Connect hands-on (same category of platform) |

## Build roadmap (status tracker)

Phases 0–4 are the MVP. Update the markers as work lands.

- [x] **Phase 0 — Design & setup.** Repo, architecture diagram, data model, AWS account + IAM user setup. *Deliverable:* README with diagram + defensible schema; first journal entry. *(Done 2026-06-10: schema, README diagram, journal, AWS account + MFA'd root + `andy-admin` IAM user + $1 billing alarm, region `us-east-1`.)*
- [ ] **Phase 1 — Amazon Connect source.** Create a Connect instance, enable call recording to S3, place scripted test calls. *Deliverable:* recordings + contact metadata landing in S3.
- [ ] **Phase 2 — Ingestion connector & queue.** S3 ObjectCreated → SQS; connector pulls audio + metadata and enqueues work items with idempotency, retries, DLQ.
- [ ] **Phase 3 — Mock ingestion API ("AcxAPI" clone).** FastAPI upload endpoints; validate format/codec; clear response/error codes; create media record + match metadata; API-token auth.
- [ ] **Phase 4 — Processing pipeline.** Whisper transcription → redaction → signal detection → structured write to PostgreSQL + validation queries.
- [ ] **Phase 5 — Outbound emission.** On a configured signal (e.g. safety event), POST to a downstream webhook; idempotent and retried.
- [ ] **Phase 6 — Dashboard & observability (stretch).** Next.js UI for conversations/transcripts/signals + ingestion-health view; structured logging.

### Stretch ideas

- SFTP batch-ingestion path alongside the API path (a second ingestion method).
- A second source (e.g. Twilio) to practice mapping different metadata shapes into one schema.
- Load-test with a few thousand synthetic interactions to genuinely exercise the queue, retries, and DLQ.

## Conventions

- One pipeline stage per module with a clear input/output contract, so stages can be reasoned about and demoed independently.
- Explicit, defensible code over abstraction — Andrew needs to explain every line.
- Maintain an **audit trail** of what was ingested, processed, and emitted.
- Keep a README section explaining how the design would satisfy a BAA and HIPAA-style controls in production.
- Document AWS teardown/cleanup steps alongside setup steps.

## Commands

<!-- Fill these in as the project takes shape. Keep them current — Claude relies on this section. -->

```bash
# Setup
# (e.g.) docker compose up -d
# (e.g.) pip install -r requirements.txt

# Run the ingestion API
# (e.g.) uvicorn app.main:app --reload

# Tests
# (e.g.) pytest

# SQL validation
# (e.g.) psql $DATABASE_URL -f sql/validation/phase4_checks.sql
```
