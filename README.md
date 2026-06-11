# authenticx_practice

A self-contained **conversation-intelligence ingestion pipeline** — a "mini-Authenticx"
that mirrors the forward-deployment flow of a contact-center analytics platform.
Conversation data flows from **Amazon Connect**, through reliable ingestion, into
processing, storage, and outbound signals.

This is an **interview-prep learning project** (Forward Deployment Engineer). The goal
is a defensible, explainable system — both sides of the ingestion contract: the
connector that pushes data in *and* the receiving API that ingests it.

> **Synthetic data only.** No real recordings or personal data anywhere. All sensitive
> text is redacted before it is persisted or logged.

## Architecture

```
Amazon Connect (calls recorded to S3)
  └─ audio  ──────────────┐         metadata (CTR) ─────────┐
                          ▼                                  ▼
        Ingestion connector (S3 ObjectCreated → SQS)   (contact attributes)
                          │
                          ▼
                 SQS queue  ──► dead-letter queue (exhausted items)
                          │
                          ▼
        Mock ingestion API (FastAPI, token auth) — the "AcxAPI" clone
                          │
                          ▼
        Processing: transcribe (Whisper) → redact → detect signals
                          │
                          ▼
                     PostgreSQL
                          │
                          ▼
        Outbound emission (webhook to a downstream system)
                          │
                          ▼
              (optional) Next.js dashboard
```

**Audio and metadata arrive separately** (audio in S3, attributes via contact trace
records) and are matched downstream by **contact ID** — the pipeline preserves that
separation rather than assuming one combined payload.

## Data model

Four distinct entities, all linked by the Connect **contact ID**. Full DDL with
inline rationale: [sql/schema.sql](sql/schema.sql).

| Entity | What it holds | Key relationships |
|---|---|---|
| `interactions` | Contact metadata from CTRs (queue, agent, duration, times) | Anchor; `contact_id UNIQUE` |
| `media_records` | The audio artifact, its S3 location, and ingest status | → `interactions`; idempotency key |
| `transcripts` | Redacted text derived from a media record (1:1) | → `media_records` |
| `signals` | Detected events (sentiment, safety keyword, topic) | → `transcripts` (many) |

Design spine: `contact_id` appears on every table because audio and metadata arrive
out of order and must be reconciled after the fact.

## Reliability (first-class, not polish)

- **Idempotency** — `media_records.idempotency_key` is `UNIQUE`; the database makes
  double-ingest impossible, not just application checks.
- **Retries with backoff** — on transient ingestion failures.
- **Dead-letter queue** — exhausted items are never silently dropped.
- **Authentication** — API-token auth on the ingestion API; least-privilege IAM throughout.
- **Monitoring** — throughput, error rate, and DLQ depth tracked from early on.

## Security & HIPAA/BAA posture (design intent)

This project handles synthetic data *as if* it were PHI:

- **Redact before persisting** — sensitive fields are redacted before any write to
  storage or logs; `transcripts.raw_text` is NULL by default and would be
  encrypted-at-rest + access-restricted in production.
- **Least privilege** — each component gets IAM credentials scoped to only what it needs.
- **No secrets in git** — credentials live in `.env` (ignored) / a vault; see `.env.example`.
- **TLS in transit, encryption at rest** — every network hop uses TLS; storage would be
  encrypted at rest in production.
- **Audit trail** — what was ingested, processed, and emitted is recorded.
- **BAA note** — in production, AWS services touching PHI (S3, SQS, Connect) operate under
  an AWS Business Associate Addendum; only BAA-eligible services are used for PHI.

## Repository layout

```
sql/             schema.sql + validation/ queries
src/
  connector/     S3 → SQS ingestion connector (Phase 2)
  api/           FastAPI ingestion API (Phase 3)
  processing/    transcribe → redact → signals (Phase 4)
  emission/      outbound webhook (Phase 5)
  common/        shared models/config
infra/aws/       IAM policies + setup/teardown notes
docs/progress.html   build journal (newest-first)
tests/
```

## Status

- [x] **Phase 0 — Design & setup** (schema, diagram, journal; AWS account in progress)
- [x] **Phase 1 — Amazon Connect source** (instance + recording flow + CTR streaming via Firehose; synthetic calls land audio in `CallRecordings/` and CTR JSON in `CTR/`)
- [ ] Phase 2 — Ingestion connector & queue
- [ ] Phase 3 — Mock ingestion API
- [ ] Phase 4 — Processing pipeline
- [ ] Phase 5 — Outbound emission
- [ ] Phase 6 — Dashboard & observability (stretch)
