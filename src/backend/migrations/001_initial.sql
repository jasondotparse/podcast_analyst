-- Initial schema from docs/database_design.md. Migrations are forward-only:
-- add a new numbered file for changes; never edit an applied one.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE podcasts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT
);

-- Dedup keys: normalized source URL or external ID when available, otherwise
-- a server-computed content hash. NULLs never collide.
CREATE TABLE episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    podcast_id UUID NOT NULL REFERENCES podcasts ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    published_at DATE,
    guests TEXT[],
    source_type TEXT NOT NULL CHECK (source_type IN ('txt', 'mp3', 'youtube')),
    source_url TEXT UNIQUE,
    external_episode_id TEXT,
    content_hash TEXT UNIQUE,
    transcript_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (podcast_id, external_episode_id)
);

-- Embeddings come from OpenAI text-embedding-3-large, shortened to 1536
-- dimensions with the API's `dimensions` parameter. Query embeddings must use the same.
CREATE TABLE passages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID NOT NULL REFERENCES episodes ON DELETE CASCADE,
    ordinal INT NOT NULL,
    text TEXT NOT NULL,
    start_seconds REAL,
    end_seconds REAL,
    embedding vector(1536),
    UNIQUE (episode_id, ordinal)
);

CREATE TABLE claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    passage_id UUID NOT NULL REFERENCES passages ON DELETE CASCADE,
    text TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    speaker TEXT,
    fingerprint TEXT NOT NULL,
    UNIQUE (passage_id, fingerprint)
);

CREATE TABLE theses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    description TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE watch_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url TEXT NOT NULL UNIQUE,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_checked_at TIMESTAMPTZ
);

CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID NOT NULL UNIQUE REFERENCES episodes ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    current_step TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The application lowercases email before writing.
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thesis_id UUID NOT NULL REFERENCES theses ON DELETE CASCADE,
    email TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thesis_id, email)
);

CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id UUID NOT NULL REFERENCES episodes ON DELETE CASCADE,
    thesis_id UUID NOT NULL REFERENCES theses ON DELETE CASCADE,
    matches JSONB NOT NULL,
    recipient_emails TEXT[] NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    UNIQUE (episode_id, thesis_id)
);

-- Every other join FK is the leading column of a UNIQUE index above.
CREATE INDEX alerts_thesis_id_idx ON alerts (thesis_id);
