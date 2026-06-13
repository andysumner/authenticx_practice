-- ============================================================================
-- Phase 3 validation — the ingestion API ("AcxAPI" clone)
--
-- Run after exercising the API (tests, upload_client, or curl):
--   docker exec -i acx_postgres psql -U acx -d acx -f - < sql/validation/phase3_checks.sql
--   (or)  psql "$DATABASE_URL" -f sql/validation/phase3_checks.sql
--
-- These double as interview material: each query answers a "how do you know the
-- ingestion is correct?" question with SQL, not vibes.
-- ============================================================================

-- 1) Media records by status. The API writes 'ingested'; the Phase 2 connector
--    writes 'queued'. This shows the split between the two ingestion paths.
SELECT status, count(*) AS n
FROM media_records
GROUP BY status
ORDER BY status;

-- 2) Idempotency held: no idempotency_key appears twice. Should return ZERO rows.
--    (The UNIQUE constraint makes a duplicate impossible; this proves it stayed so.)
SELECT idempotency_key, count(*) AS n
FROM media_records
GROUP BY idempotency_key
HAVING count(*) > 1;

-- 3) No duplicate (bucket, key) either — the belt-and-suspenders constraint.
--    Should return ZERO rows.
SELECT s3_bucket, s3_key, count(*) AS n
FROM media_records
GROUP BY s3_bucket, s3_key
HAVING count(*) > 1;

-- 4) Every media_record has its interaction anchor (referential integrity).
--    The FK guarantees this; ZERO rows confirms no orphaned media.
SELECT m.id, m.contact_id
FROM media_records m
LEFT JOIN interactions i ON i.contact_id = m.contact_id
WHERE i.contact_id IS NULL;

-- 5) Which interactions did the API actually enrich with metadata?
--    metadata_received_at is stamped by the API's upsert; anchor-only rows the
--    connector created (audio before any metadata) have it NULL.
SELECT
    count(*) FILTER (WHERE metadata_received_at IS NOT NULL) AS enriched,
    count(*) FILTER (WHERE metadata_received_at IS NULL)     AS anchor_only,
    count(*)                                                  AS total
FROM interactions;

-- 6) Spot-check the most recent API ingests with their matched metadata.
--    A quick "did the cross-system mapping work?" view: audio facts + CTR fields
--    joined on contact_id.
SELECT
    m.id            AS media_id,
    m.contact_id,
    m.status,
    m.size_bytes,
    m.duration_seconds,
    i.channel,
    i.queue_name,
    i.agent_username,
    i.metadata_received_at
FROM media_records m
JOIN interactions i ON i.contact_id = m.contact_id
WHERE m.status = 'ingested'
ORDER BY m.received_at DESC
LIMIT 10;
