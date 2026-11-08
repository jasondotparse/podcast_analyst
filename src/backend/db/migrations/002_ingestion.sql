-- Ingestion support from docs/etl_design.md.
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    UNIQUE (kind, name)
);

CREATE TABLE claim_entities (
    claim_id UUID NOT NULL REFERENCES claims ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES entities ON DELETE CASCADE,
    PRIMARY KEY (claim_id, entity_id)
);
CREATE INDEX claim_entities_entity_id_idx ON claim_entities (entity_id);

-- Set when Codex finishes an episode's claim extraction, so retries skip it.
ALTER TABLE episodes ADD COLUMN claims_extracted_at TIMESTAMPTZ;

-- SELECT-only role for Codex's postgres_database_query tool, entered with
-- SET LOCAL ROLE. Roles are cluster-wide, so tolerate an existing one.
DO $$ BEGIN
    CREATE ROLE podcast_reader NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT USAGE ON SCHEMA public TO podcast_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO podcast_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO podcast_reader;
