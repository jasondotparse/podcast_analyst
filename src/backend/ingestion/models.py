"""Values passed between the API, the ingestion service, and the database clients."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class EpisodeMetadata(BaseModel):
    podcast_name: str
    podcast_description: str | None = None
    title: str
    description: str | None = None
    published_at: date | None = None
    guests: list[str] | None = None
    source_url: str | None = None
    external_episode_id: str | None = None  # An RSS episode's GUID; the watcher dedupes on it.


class IngestionJob(BaseModel):
    job_id: UUID
    episode_id: UUID
    status: str
    current_step: str | None = None
    error: str | None = None


@dataclass
class Episode:
    podcast_name: str
    title: str
    description: str | None
    transcript_text: str | None  # NULL for an MP3 until the transcribe step fills it.
    claims_extracted_at: datetime | None


@dataclass
class EntityLink:
    claim_id: UUID
    passage_id: UUID
    entity_id: UUID
    name: str
    kind: str


@dataclass
class EpisodeGraph:
    """An episode's rows that the knowledge graph mirrors."""
    episode_id: UUID
    passage_ids: list[UUID]
    claims: list[tuple[UUID, UUID]]  # (claim_id, passage_id)
    entity_links: list[EntityLink]


@dataclass
class FeedEpisode:
    guid: str
    title: str
    description: str | None
    published_at: datetime
    source_url: str | None  # What citations link to: the episode's own web page, else its audio file.
    audio_url: str | None
    transcript_url: str | None = None
    transcript_type: str | None = None


@dataclass
class Feed:
    title: str
    description: str | None
    episodes: list[FeedEpisode]  # Only usable ones: an ID, a publish time, and audio or a supported transcript.


@dataclass
class WatchSource:
    id: UUID
    url: str
    ingest_since: datetime
