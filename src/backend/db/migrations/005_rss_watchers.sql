-- RSS watchers (docs/etl_design.md#rss-watchers). Registration sets ingest_since to the feed's
-- newest episode, so that episode and later ones are ingested, and the archive is not backfilled.
-- No code wrote watch_sources before this migration, so the table is empty.
ALTER TABLE watch_sources ADD COLUMN ingest_since TIMESTAMPTZ NOT NULL;
