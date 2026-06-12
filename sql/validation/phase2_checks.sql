-- ============================================================================
-- Phase 2 validation — connector ingestion (run after the connector has run)
--
--   docker exec -i acx_postgres psql -U acx -d acx -f - < sql/validation/phase2_checks.sql
--   (or: docker exec acx_postgres psql -U acx -d acx -f /path/in/container)
--
-- These double as interview material: each asks a question a reviewer would ask
-- of an ingestion pipeline ("did anything get dropped / duplicated / orphaned?").
-- ============================================================================

-- 1) Throughput by status. After Phase 2 everything the connector ingested
--    should be 'queued' (handed off, awaiting the Phase 3 ingestion API).
SELECT status, count(*) AS n
FROM media_records
GROUP BY status
ORDER BY status;

-- 2) Idempotency held: no duplicate idempotency keys, no duplicate S3 objects.
--    Both are UNIQUE in the schema, so a non-empty result means something is very
--    wrong (or the constraint was dropped). Expect zero rows.
SELECT idempotency_key, count(*) AS n
FROM media_records
GROUP BY idempotency_key
HAVING count(*) > 1;

SELECT s3_bucket, s3_key, count(*) AS n
FROM media_records
GROUP BY s3_bucket, s3_key
HAVING count(*) > 1;

-- 3) Orphan check: every media_record must point at a real interaction anchor.
--    The FK makes this impossible to violate, but proving it is the habit.
--    Expect zero rows.
SELECT m.id, m.contact_id
FROM media_records m
LEFT JOIN interactions i ON i.contact_id = m.contact_id
WHERE i.contact_id IS NULL;

-- 4) Anchors still awaiting CTR metadata (audio arrived first, metadata not yet).
--    Perfectly normal mid-pipeline; this counts how many interactions exist only
--    because the connector created the anchor. Phase 3 will enrich these.
SELECT count(*) AS anchors_without_metadata
FROM interactions
WHERE metadata_received_at IS NULL;

-- 5) Recordings per contact — Connect produces a main leg + an ivr/ leg, so a
--    single contact can legitimately have multiple media_records. This shows the
--    fan-out and confirms they share one anchor.
SELECT contact_id, count(*) AS recordings
FROM media_records
GROUP BY contact_id
ORDER BY recordings DESC, contact_id
LIMIT 20;
