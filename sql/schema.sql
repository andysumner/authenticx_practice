-- ============================================================================
-- authenticx_practice — core schema (Phase 0)
--
-- Four core entities, kept deliberately distinct:
--   interactions   — metadata about a contact (from Connect contact trace records)
--   media_records  — the audio artifact + its S3 location + ingest status
--   transcripts    — text derived from a media record (redacted by default)
--   signals        — detected events derived from a transcript
--
-- Design spine: the Amazon Connect CONTACT ID.
--   Audio (media) and metadata (CTR) arrive SEPARATELY and out of order.
--   contact_id is the business key that lets us match them after the fact,
--   so it appears on every table and is the join we rely on everywhere.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) interactions — one row per Connect contact (the "anchor")
--    Either the audio path or the metadata path may create this row first;
--    whichever arrives second ENRICHES it (upsert), rather than failing.
-- ----------------------------------------------------------------------------
CREATE TABLE interactions (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id           text        NOT NULL UNIQUE,   -- Connect ContactId; the match key
    channel              text,                           -- VOICE | CHAT | TASK
    initiation_method    text,                           -- INBOUND | OUTBOUND | TRANSFER | CALLBACK
    queue_name           text,
    agent_username       text,
    started_at           timestamptz,
    ended_at             timestamptz,
    duration_seconds     integer,
    disconnect_reason    text,
    metadata_received_at timestamptz,                    -- when CTR metadata landed (audit)
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 2) media_records — the audio artifact and its lifecycle
--    Idempotency lives here: the same S3 object must never be ingested twice.
-- ----------------------------------------------------------------------------
CREATE TABLE media_records (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id       text        NOT NULL REFERENCES interactions(contact_id),
    s3_bucket        text        NOT NULL,
    s3_key           text        NOT NULL,
    -- Idempotency key the connector computes (e.g. the object's ETag, or a
    -- sha256 of bucket/key). A UNIQUE constraint makes double-ingest impossible
    -- at the database level, not just in application logic.
    idempotency_key  text        NOT NULL UNIQUE,
    content_type     text,                               -- e.g. audio/wav
    size_bytes       bigint,
    duration_seconds integer,
    status           text        NOT NULL DEFAULT 'discovered'
        CHECK (status IN ('discovered','queued','ingested','transcribed','failed')),
    received_at      timestamptz,                        -- when the object was discovered
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    -- A given S3 object is one logical recording — belt-and-suspenders with idempotency_key.
    UNIQUE (s3_bucket, s3_key)
);

CREATE INDEX idx_media_records_contact_id ON media_records(contact_id);
CREATE INDEX idx_media_records_status     ON media_records(status);

-- ----------------------------------------------------------------------------
-- 3) transcripts — text derived from one media record (1:1)
--    NON-NEGOTIABLE: redact before persisting. The pipeline writes redacted_text.
--    raw_text stays NULL by default; it exists only to document that in
--    production it would be an encrypted-at-rest, access-restricted column.
-- ----------------------------------------------------------------------------
CREATE TABLE transcripts (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    media_record_id   bigint      NOT NULL UNIQUE REFERENCES media_records(id),
    contact_id        text        NOT NULL REFERENCES interactions(contact_id),  -- denormalized for queries
    language          text,
    redacted_text     text,                               -- SAFE to store/display
    raw_text          text,                               -- NULL by default; see note above
    model             text,                               -- e.g. whisper-base
    confidence        numeric,
    redaction_status  text        NOT NULL DEFAULT 'pending'
        CHECK (redaction_status IN ('pending','redacted','failed')),
    transcribed_at    timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_transcripts_contact_id ON transcripts(contact_id);

-- ----------------------------------------------------------------------------
-- 4) signals — detected events derived from a transcript (many per transcript)
--    Snippets stored here must already be redacted.
-- ----------------------------------------------------------------------------
CREATE TABLE signals (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transcript_id bigint      NOT NULL REFERENCES transcripts(id),
    contact_id    text        NOT NULL REFERENCES interactions(contact_id),  -- denormalized for queries
    signal_type   text        NOT NULL,                   -- negative_sentiment | safety_keyword | topic_tag
    label         text,                                   -- specific value, e.g. self_harm, billing_dispute
    score         numeric,                                -- confidence / severity
    snippet       text,                                   -- REDACTED excerpt only
    detector      text,                                   -- which rule/model produced it
    detected_at   timestamptz NOT NULL DEFAULT now(),
    emitted_at    timestamptz,                            -- when outbound webhook fired (Phase 5 audit); NULL until then
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_signals_transcript_id ON signals(transcript_id);
CREATE INDEX idx_signals_contact_id    ON signals(contact_id);
CREATE INDEX idx_signals_type          ON signals(signal_type);

-- ----------------------------------------------------------------------------
-- updated_at maintenance — keep the column honest without trusting the app.
-- A trigger guarantees updated_at moves on every UPDATE, even from psql.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_interactions_updated_at  BEFORE UPDATE ON interactions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_media_records_updated_at BEFORE UPDATE ON media_records
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_transcripts_updated_at   BEFORE UPDATE ON transcripts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
