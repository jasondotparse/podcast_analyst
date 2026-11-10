-- Uploaded MP3s, kept so the transcribe step can read them later (including after a
-- restart) and kept after transcription. See docs/etl_design.md.
CREATE TABLE episode_audio (
    episode_id UUID PRIMARY KEY REFERENCES episodes ON DELETE CASCADE,
    audio BYTEA NOT NULL
);
-- Migration 002's default privileges grant podcast_reader SELECT on every new table;
-- without this, Codex's read-only SQL tool could select megabytes of audio.
REVOKE SELECT ON episode_audio FROM podcast_reader;
