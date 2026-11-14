"""PostgreSQL access. Callers pass and receive domain values; all SQL lives here."""

from datetime import datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..ingestion.models import (Claim, CronSubscription, EntityLink, Episode, EpisodeGraph, EpisodeMetadata,
                                IngestionJob, Signal, WatchSource)


JOB_COLUMNS = "id, episode_id, status, current_step, error"
CRON_COLUMNS = "id, email, cron, timezone, thesis_ids, next_run_at"  # In CronSubscription's field order.
# Jobs with their episode's podcast name and title, so the UI can say what is being ingested.
LABELED_JOBS = ("SELECT j.id, j.episode_id, j.status, j.current_step, j.error, p.name, e.title FROM ingestion_jobs j "
                "JOIN episodes e ON e.id = j.episode_id JOIN podcasts p ON p.id = e.podcast_id")


def to_job(row) -> IngestionJob | None:
    """From JOB_COLUMNS, optionally followed by the podcast name and episode title."""
    if row is None:
        return None
    job_id, episode_id, status, current_step, error, *label = row
    podcast_name, title = label or (None, None)
    return IngestionJob(job_id=job_id, episode_id=episode_id, status=status, current_step=current_step, error=error,
                        podcast_name=podcast_name, title=title)


async def upsert_entities(conn, entities: list[tuple[str, str]]) -> list[tuple[UUID, str, str]]:
    """Find or create (name, kind) entities; returns (id, name, kind). Upserts run in one global
    order: concurrent writers sharing entities would otherwise lock them in different orders and deadlock."""
    rows = []
    for name, kind in sorted(set(entities)):
        (entity_id,) = await (await conn.execute(
            "INSERT INTO entities (name, kind) VALUES (%s, %s) "
            "ON CONFLICT (kind, name) DO UPDATE SET name = EXCLUDED.name RETURNING id", (name, kind))).fetchone()
        rows.append((entity_id, name, kind))
    return rows


class PostgresClient:
    def __init__(self, url: str):
        self.url = url

    def connect(self):
        """One connection per operation; leaving the `async with` block commits it."""
        return psycopg.AsyncConnection.connect(self.url)

    async def execute(self, query: str, *params) -> None:
        async with await self.connect() as conn:
            await conn.execute(query, params)

    async def one(self, query: str, *params):
        async with await self.connect() as conn:
            return await (await conn.execute(query, params)).fetchone()

    async def all(self, query: str, *params) -> list:
        async with await self.connect() as conn:
            return await (await conn.execute(query, params)).fetchall()

    async def dicts(self, query: str, *params) -> list[dict]:
        """Rows keyed by column name, ready to return as JSON."""
        async with await self.connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            return await cursor.fetchall()

    # ---- Ingestion jobs ---------------------------------------------------

    async def find_job_by_source(self, content_hash: str, source_url: str | None) -> IngestionJob | None:
        return to_job(await self.one(f"{LABELED_JOBS} WHERE e.content_hash = %s OR e.source_url = %s",
                                     content_hash, source_url))

    async def create_episode_job(self, metadata: EpisodeMetadata, source_type: str, transcript: str | None,
                                 content_hash: str, audio: bytes | None = None) -> IngestionJob:
        """Find (updating a provided description) or create the podcast by name, then insert
        the episode, its audio if any, and its pending job."""
        async with await self.connect() as conn:
            podcast = await (await conn.execute(
                "UPDATE podcasts SET description = COALESCE(%s, description) WHERE name = %s RETURNING id",
                (metadata.podcast_description, metadata.podcast_name))).fetchone() \
                or await (await conn.execute("INSERT INTO podcasts (name, description) VALUES (%s, %s) RETURNING id",
                                             (metadata.podcast_name, metadata.podcast_description))).fetchone()
            (episode_id,) = await (await conn.execute(
                "INSERT INTO episodes (podcast_id, title, description, published_at, guests, source_type, source_url, "
                "external_episode_id, content_hash, transcript_text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (podcast[0], metadata.title, metadata.description, metadata.published_at, metadata.guests,
                 source_type, metadata.source_url, metadata.external_episode_id, content_hash, transcript))).fetchone()
            if audio is not None:
                await conn.execute("INSERT INTO episode_audio (episode_id, audio) VALUES (%s, %s)", (episode_id, audio))
            job = await (await conn.execute(
                f"INSERT INTO ingestion_jobs (episode_id) VALUES (%s) RETURNING {JOB_COLUMNS}", (episode_id,))).fetchone()
            return to_job((*job, metadata.podcast_name, metadata.title))

    async def get_job(self, job_id: UUID) -> IngestionJob | None:
        return to_job(await self.one(f"{LABELED_JOBS} WHERE j.id = %s", job_id))

    async def active_job(self) -> IngestionJob | None:
        """The job the worker is running, else the next one it will run."""
        return to_job(await self.one(f"{LABELED_JOBS} WHERE j.status IN ('running', 'pending') "
                                     "ORDER BY j.status = 'running' DESC, j.created_at LIMIT 1"))

    async def next_pending_job(self) -> IngestionJob | None:
        return to_job(await self.one(
            f"SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1"))

    async def requeue_job(self, job_id: UUID) -> None:
        await self.execute("UPDATE ingestion_jobs SET status = 'pending', error = NULL, updated_at = now() "
                           "WHERE id = %s AND status <> 'running'", job_id)

    async def has_active_job(self) -> bool:
        return await self.one("SELECT 1 FROM ingestion_jobs WHERE status IN ('pending', 'running') LIMIT 1") is not None

    async def requeue_running_jobs(self) -> None:
        await self.execute("UPDATE ingestion_jobs SET status = 'pending' WHERE status = 'running'")

    async def update_job(self, job_id: UUID, status: str, current_step: str | None = None, error: str | None = None):
        await self.execute("UPDATE ingestion_jobs SET status = %s, current_step = COALESCE(%s, current_step), "
                           "error = %s, updated_at = now() WHERE id = %s", status, current_step, error, job_id)

    # ---- Episodes and passages -------------------------------------------

    async def get_episode(self, episode_id: UUID) -> Episode:
        row = await self.one("SELECT p.name, e.title, e.description, e.transcript_text, e.claims_extracted_at, "
                             "e.published_at, e.guests "
                             "FROM episodes e JOIN podcasts p ON p.id = e.podcast_id WHERE e.id = %s", episode_id)
        return Episode(*row)

    async def get_audio(self, episode_id: UUID) -> bytes:
        async with await self.connect() as conn:
            # Binary results: in text format Postgres hex-encodes bytea, doubling the audio's size in transit and memory.
            (audio,) = await (await conn.execute(
                "SELECT audio FROM episode_audio WHERE episode_id = %s", (episode_id,), binary=True)).fetchone()
            return audio

    async def save_transcript(self, episode_id: UUID, text: str) -> None:
        await self.execute("UPDATE episodes SET transcript_text = %s WHERE id = %s", text, episode_id)

    async def has_passages(self, episode_id: UUID) -> bool:
        return await self.one("SELECT 1 FROM passages WHERE episode_id = %s LIMIT 1", episode_id) is not None

    async def insert_passages(self, episode_id: UUID, texts: list[str]) -> None:
        async with await self.connect() as conn, conn.cursor() as cursor:
            await cursor.executemany("INSERT INTO passages (episode_id, ordinal, text) VALUES (%s, %s, %s)",
                                     [(episode_id, ordinal, text) for ordinal, text in enumerate(texts)])

    async def passages_without_embeddings(self, episode_id: UUID) -> list[tuple[UUID, str]]:
        return await self.all("SELECT id, text FROM passages WHERE episode_id = %s AND embedding IS NULL", episode_id)

    async def set_embeddings(self, embeddings: list[tuple[UUID, list[float]]]) -> None:
        async with await self.connect() as conn, conn.cursor() as cursor:
            await cursor.executemany("UPDATE passages SET embedding = %s::vector WHERE id = %s",
                                     [(str(vector), passage_id) for passage_id, vector in embeddings])

    # ---- Claims and entities ---------------------------------------------

    async def find_passage_containing(self, episode_id: UUID, quote: str) -> UUID | None:
        row = await self.one("SELECT id FROM passages WHERE episode_id = %s AND strpos(text, %s) > 0 "
                             "ORDER BY ordinal LIMIT 1", episode_id, quote)
        return row and row[0]

    async def save_claim(self, passage_id: UUID, text: str, evidence_quote: str, speaker: str | None,
                         fingerprint: str, entities: list[tuple[str, str]]) -> bool:
        """Insert a claim with its (name, kind) entities in one transaction; False if it already exists."""
        async with await self.connect() as conn:
            claim = await (await conn.execute(
                "INSERT INTO claims (passage_id, text, evidence_quote, speaker, fingerprint) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
                (passage_id, text, evidence_quote, speaker, fingerprint))).fetchone()
            if claim is None:
                return False
            for entity_id, _, _ in await upsert_entities(conn, entities):
                await conn.execute("INSERT INTO claim_entities VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                   (claim[0], entity_id))
            return True

    # ---- Theses ------------------------------------------------------------

    async def add_thesis(self, description: str, entities: list[tuple[str, str]]) -> tuple[UUID, list]:
        """Insert a thesis linked to its (name, kind) entities; returns its ID and (id, name, kind) entities."""
        async with await self.connect() as conn:
            (thesis_id,) = await (await conn.execute(
                "INSERT INTO theses (description) VALUES (%s) RETURNING id", (description,))).fetchone()
            linked = await upsert_entities(conn, entities)
            for entity_id, _, _ in linked:
                await conn.execute("INSERT INTO thesis_entities VALUES (%s, %s)", (thesis_id, entity_id))
            return thesis_id, linked

    async def delete_claims(self, episode_id: UUID) -> None:
        await self.execute("DELETE FROM claims WHERE passage_id IN (SELECT id FROM passages WHERE episode_id = %s)",
                           episode_id)

    async def mark_claims_extracted(self, episode_id: UUID) -> None:
        await self.execute("UPDATE episodes SET claims_extracted_at = now() WHERE id = %s", episode_id)

    # ---- Claim ranking ---------------------------------------------------

    async def unranked_claims(self, episode_id: UUID) -> list[Claim]:
        rows = await self.all("SELECT c.id, c.text, c.evidence_quote, c.speaker FROM claims c "
                              "JOIN passages p ON p.id = c.passage_id WHERE p.episode_id = %s AND c.signal IS NULL",
                              episode_id)
        return [Claim(*row) for row in rows]

    async def set_claim_signal(self, claim_id: UUID, scores: Signal) -> None:
        await self.execute("UPDATE claims SET specificity = %s, materiality = %s, source_standing = %s, signal = %s "
                           "WHERE id = %s", scores.specificity, scores.materiality, scores.source_standing,
                           scores.signal, claim_id)

    async def episode_graph(self, episode_id: UUID) -> EpisodeGraph:
        passages = await self.all("SELECT id FROM passages WHERE episode_id = %s", episode_id)
        claims = await self.all("SELECT c.id, c.passage_id, c.signal FROM claims c "
                                "JOIN passages p ON p.id = c.passage_id WHERE p.episode_id = %s", episode_id)
        links = await self.all("SELECT ce.claim_id, c.passage_id, e.id, e.name, e.kind FROM claim_entities ce "
                               "JOIN entities e ON e.id = ce.entity_id JOIN claims c ON c.id = ce.claim_id "
                               "JOIN passages p ON p.id = c.passage_id WHERE p.episode_id = %s", episode_id)
        return EpisodeGraph(episode_id, [passage_id for (passage_id,) in passages],
                            [tuple(row) for row in claims], [EntityLink(*row) for row in links],
                            await self.stance_edges([row[0] for row in claims]))

    # ---- Thesis reflection and alerts ------------------------------------

    async def unassessed_claims(self, episode_id: UUID | None, limit: int | None) -> list:
        return await self.all(
            "SELECT p.episode_id, c.id, c.text, c.evidence_quote, c.speaker FROM claims c "
            "JOIN passages p ON p.id = c.passage_id WHERE (%s::uuid IS NULL OR p.episode_id = %s) "
            "AND EXISTS (SELECT 1 FROM theses t WHERE t.is_active AND NOT EXISTS "
            "(SELECT 1 FROM thesis_assessments a WHERE a.claim_id = c.id AND a.thesis_id = t.id)) "
            "ORDER BY c.id LIMIT %s", episode_id, episode_id, limit)

    async def missing_theses(self, claim_id: UUID) -> list:
        return await self.all("SELECT id, description FROM theses t WHERE is_active AND NOT EXISTS "
                              "(SELECT 1 FROM thesis_assessments a WHERE a.thesis_id = t.id AND a.claim_id = %s) "
                              "ORDER BY id", claim_id)

    async def save_assessments(self, rows: list) -> None:
        async with await self.connect() as conn, conn.cursor() as cursor:
            await cursor.executemany("INSERT INTO thesis_assessments (claim_id, thesis_id, stance, strength) "
                                     "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", rows)

    async def stance_edges(self, claim_ids: list[UUID]) -> list:
        return await self.all("SELECT claim_id, thesis_id, stance, strength FROM thesis_assessments "
                              "WHERE claim_id = ANY(%s::uuid[])", claim_ids)

    async def add_subscription(self, email: str, thesis_ids: list[UUID]) -> list[str]:
        async with await self.connect() as conn:
            rows = await (await conn.execute("SELECT id, description FROM theses WHERE is_active "
                                             "AND id = ANY(%s::uuid[]) ORDER BY id", (thesis_ids,))).fetchall()
            invalid = set(thesis_ids) - {row[0] for row in rows}
            if invalid:
                raise ValueError(f"Unknown or inactive thesis IDs: {', '.join(sorted(map(str, invalid)))}")
            for thesis_id, _ in rows:
                await conn.execute("INSERT INTO subscriptions (thesis_id, email) VALUES (%s, %s) "
                                   "ON CONFLICT DO NOTHING", (thesis_id, email))
            return [description for _, description in rows]

    async def create_alerts(self, episode_id: UUID, min_strength: int, min_signal: int) -> None:
        await self.execute(
            "INSERT INTO alerts (episode_id, thesis_id, matches, recipient_emails) "
            "SELECT %s, t.id, jsonb_agg(jsonb_build_object('claim_id', c.id, 'strength', a.strength, "
            "'signal', c.signal) ORDER BY a.strength DESC, c.signal DESC, c.id), "
            "ARRAY(SELECT s.email FROM subscriptions s WHERE s.thesis_id = t.id ORDER BY s.email) "
            "FROM thesis_assessments a JOIN claims c ON c.id = a.claim_id "
            "JOIN passages p ON p.id = c.passage_id JOIN theses t ON t.id = a.thesis_id "
            "WHERE p.episode_id = %s AND t.is_active AND a.stance = 'challenges' "
            "AND a.strength >= %s AND c.signal >= %s "
            "AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.thesis_id = t.id) GROUP BY t.id "
            "ON CONFLICT (episode_id, thesis_id) DO NOTHING", episode_id, episode_id, min_strength, min_signal)

    async def unsent_alerts(self, episode_id: UUID) -> list:
        return await self.all("SELECT a.id, t.description, a.recipient_emails, a.matches FROM alerts a "
                              "JOIN theses t ON t.id = a.thesis_id WHERE a.episode_id = %s AND a.status <> 'sent' "
                              "ORDER BY a.id", episode_id)

    async def alert_claims(self, matches: list[dict]) -> list[dict]:
        async with await self.connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "SELECT c.text, c.evidence_quote, c.speaker, pod.name AS podcast, e.title AS episode, "
                "e.published_at, e.source_url, m.signal, m.strength FROM "
                "jsonb_to_recordset(%s) AS m(claim_id uuid, strength int, signal int) "
                "JOIN claims c ON c.id = m.claim_id JOIN passages p ON p.id = c.passage_id "
                "JOIN episodes e ON e.id = p.episode_id JOIN podcasts pod ON pod.id = e.podcast_id "
                "ORDER BY m.strength DESC, m.signal DESC, c.id", (Jsonb(matches),))
            return await cursor.fetchall()

    async def set_alert_status(self, alert_id: UUID, status: str) -> None:
        await self.execute("UPDATE alerts SET status = %s, sent_at = CASE WHEN %s = 'sent' THEN now() ELSE NULL END "
                           "WHERE id = %s", status, status, alert_id)

    async def remove_subscriptions(self, email: str, thesis_ids: list[UUID]) -> list[dict]:
        """Delete this address's immediate subscriptions to these theses; returns the rows removed."""
        return await self.dicts("DELETE FROM subscriptions s USING theses t WHERE t.id = s.thesis_id "
                                "AND s.email = %s AND s.thesis_id = ANY(%s::uuid[]) "
                                "RETURNING s.id AS subscription_id, s.thesis_id, t.description AS thesis",
                                email, thesis_ids)

    # ---- Scheduled thesis digests (alpha) -----------------------------------

    async def active_theses(self, thesis_ids: list[UUID]) -> list[tuple[UUID, str]]:
        """(id, description) of the active theses among these IDs, oldest first."""
        return await self.all("SELECT id, description FROM theses WHERE is_active AND id = ANY(%s::uuid[]) "
                              "ORDER BY created_at, id", thesis_ids)

    async def immediate_subscriptions(self, email: str, thesis_ids: list[UUID]) -> list[dict]:
        return await self.dicts("SELECT s.id AS subscription_id, s.thesis_id, t.description AS thesis "
                                "FROM subscriptions s JOIN theses t ON t.id = s.thesis_id "
                                "WHERE s.email = %s AND s.thesis_id = ANY(%s::uuid[]) ORDER BY t.created_at, s.id",
                                email, thesis_ids)

    async def add_cron_subscription(self, email: str, cron: str, timezone: str, thesis_ids: list[UUID],
                                    next_run_at: datetime) -> tuple[CronSubscription, bool]:
        """Insert a digest schedule, or return the active one with the same address, cron, timezone, and set
        of theses. Returns (subscription, created)."""
        async with await self.connect() as conn:
            # Serialize registrations so two identical requests cannot both insert.
            await conn.execute("LOCK TABLE cron_subscriptions IN SHARE ROW EXCLUSIVE MODE")
            row = await (await conn.execute(
                f"SELECT {CRON_COLUMNS} FROM cron_subscriptions WHERE is_active AND email = %s AND cron = %s "
                "AND timezone = %s AND thesis_ids @> %s::uuid[] AND thesis_ids <@ %s::uuid[] "
                "ORDER BY created_at LIMIT 1", (email, cron, timezone, thesis_ids, thesis_ids))).fetchone()
            if row:
                return CronSubscription(*row), False
            row = await (await conn.execute(
                "INSERT INTO cron_subscriptions (email, cron, timezone, thesis_ids, next_run_at) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {CRON_COLUMNS}",
                (email, cron, timezone, thesis_ids, next_run_at))).fetchone()
            return CronSubscription(*row), True

    async def cancel_cron_subscription(self, subscription_id: UUID) -> dict | None:
        """Deactivate a digest schedule; returns it with `was_active`, or None if the ID is unknown."""
        async with await self.connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SELECT id AS subscription_id, email, cron, timezone, thesis_ids, "
                                 "is_active AS was_active FROM cron_subscriptions WHERE id = %s FOR UPDATE",
                                 (subscription_id,))
            row = await cursor.fetchone()
            if row:
                await cursor.execute("UPDATE cron_subscriptions SET is_active = false WHERE id = %s",
                                     (subscription_id,))
            return row

    async def due_cron_subscriptions(self, now: datetime) -> list[CronSubscription]:
        rows = await self.all(f"SELECT {CRON_COLUMNS} FROM cron_subscriptions WHERE is_active AND next_run_at <= %s "
                              "ORDER BY next_run_at, id", now)
        return [CronSubscription(*row) for row in rows]

    async def advance_cron_subscription(self, subscription_id: UUID, due_at: datetime, next_run_at: datetime,
                                        status: str | None = None) -> bool:
        """Move a due schedule to its next occurrence (optionally recording a status). False if it was cancelled
        or already advanced, so an occurrence is claimed at most once."""
        return await self.one("UPDATE cron_subscriptions SET next_run_at = %s, "
                              "last_delivery_status = COALESCE(%s, last_delivery_status) "
                              "WHERE id = %s AND is_active AND next_run_at = %s RETURNING id",
                              next_run_at, status, subscription_id, due_at) is not None

    async def set_cron_delivery(self, subscription_id: UUID, status: str, delivered_at: datetime | None = None):
        await self.execute("UPDATE cron_subscriptions SET last_delivery_status = %s, "
                           "last_delivered_at = COALESCE(%s, last_delivered_at) WHERE id = %s",
                           status, delivered_at, subscription_id)

    async def digest_evidence(self, thesis_ids: list[UUID], start: datetime, end: datetime) -> list[dict]:
        """Supporting and challenging assessments recorded in [start, end) for these theses, with citations.
        Recorded time (thesis_assessments.created_at), not publish time: an old claim newly assessed against
        a new thesis is new evidence."""
        return await self.dicts(
            "SELECT a.thesis_id, a.stance, a.strength, c.id AS claim_id, c.text, c.evidence_quote, c.speaker, "
            "c.signal, pod.name AS podcast, e.title AS episode, e.published_at, e.source_url "
            "FROM thesis_assessments a JOIN claims c ON c.id = a.claim_id JOIN passages p ON p.id = c.passage_id "
            "JOIN episodes e ON e.id = p.episode_id JOIN podcasts pod ON pod.id = e.podcast_id "
            "WHERE a.thesis_id = ANY(%s::uuid[]) AND a.created_at >= %s AND a.created_at < %s "
            "AND a.stance IN ('supports', 'challenges') "
            "ORDER BY a.strength DESC, c.signal DESC NULLS LAST, c.id", thesis_ids, start, end)

    # ---- Watch sources (RSS watchers) ---------------------------------------

    async def add_watch_source(self, url: str, description: str, ingest_since: datetime) -> bool:
        """False if the URL is already registered."""
        return await self.one("INSERT INTO watch_sources (url, description, ingest_since) VALUES (%s, %s, %s) "
                              "ON CONFLICT (url) DO NOTHING RETURNING id", url, description, ingest_since) is not None

    async def due_watch_sources(self, interval: timedelta) -> list[WatchSource]:
        """Active sources not checked within the interval, least recently checked first."""
        rows = await self.all("SELECT id, url, ingest_since FROM watch_sources WHERE is_active AND "
                              "(last_checked_at IS NULL OR last_checked_at < now() - %s) "
                              "ORDER BY last_checked_at NULLS FIRST", interval)
        return [WatchSource(*row) for row in rows]

    async def mark_watch_source_checked(self, source_id: UUID) -> None:
        await self.execute("UPDATE watch_sources SET last_checked_at = now() WHERE id = %s", source_id)

    async def has_episode(self, podcast_name: str, external_episode_id: str, source_url: str | None) -> bool:
        """Whether the podcast already has an episode with this GUID, or any podcast one with this URL."""
        return await self.one(
            "SELECT 1 FROM episodes e JOIN podcasts p ON p.id = e.podcast_id "
            "WHERE (p.name = %s AND e.external_episode_id = %s) OR e.source_url = %s LIMIT 1",
            podcast_name, external_episode_id, source_url) is not None

    # ---- Catalog (the UI's read-only cabinet) --------------------------------

    async def list_theses(self) -> list[dict]:
        return await self.dicts("SELECT id, description, is_active, created_at FROM theses ORDER BY created_at DESC")

    async def list_watched_podcasts(self) -> list[dict]:
        """Active RSS feeds by name; registration stores the feed's title in `description`."""
        return await self.dicts("SELECT id, COALESCE(description, url) AS name FROM watch_sources WHERE is_active "
                                "ORDER BY lower(COALESCE(description, url))")

    async def list_subscriptions(self) -> list[dict]:
        return await self.dicts("SELECT s.id, s.email, t.description AS thesis FROM subscriptions s "
                                "JOIN theses t ON t.id = s.thesis_id ORDER BY s.email, s.created_at")

    async def recent_ingestions(self, limit: int) -> list[dict]:
        """The most recently updated jobs, with their podcast and episode."""
        return await self.dicts(
            "SELECT j.id, p.name AS podcast, e.title, j.status, j.current_step, j.error, j.updated_at "
            "FROM ingestion_jobs j JOIN episodes e ON e.id = j.episode_id JOIN podcasts p ON p.id = e.podcast_id "
            "ORDER BY j.updated_at DESC LIMIT %s", limit)

    # ---- Graph view ------------------------------------------------------

    async def graph_labels(self, ids: list[str]) -> dict:
        """Batch canonical labels and source details; no graph node is shown as a bare UUID."""
        if not ids:
            return {}
        labels = {}
        async with await self.connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
            for query in (
                "SELECT c.id, c.text AS label, c.signal, c.evidence_quote AS quote, c.speaker, "
                "pod.name AS podcast, e.title AS episode, e.published_at, e.source_url "
                "FROM claims c JOIN passages p ON p.id = c.passage_id JOIN episodes e ON e.id = p.episode_id "
                "JOIN podcasts pod ON pod.id = e.podcast_id WHERE c.id = ANY(%s::uuid[])",
                "SELECT id, description AS label FROM theses WHERE id = ANY(%s::uuid[])",
                "SELECT e.id, e.title AS label, pod.name AS podcast, e.published_at, e.source_url "
                "FROM episodes e JOIN podcasts pod ON pod.id = e.podcast_id WHERE e.id = ANY(%s::uuid[])",
            ):
                await cursor.execute(query, (ids,))
                for row in await cursor.fetchall():
                    node_id, label, signal = str(row.pop("id")), row.pop("label"), row.pop("signal", None)
                    if row.get("published_at"):
                        row["published_at"] = row["published_at"].isoformat()
                    labels[node_id] = (label, signal, row)
        return labels

    # ---- Agent tools -----------------------------------------------------

    async def vector_search(self, embedding: list[float], limit: int) -> list[dict]:
        """Passages closest to the embedding (cosine), with their episode's citation details."""
        async with await self.connect() as conn:
            cursor = await conn.execute(
                "SELECT pod.name AS podcast, e.title AS episode, e.published_at, e.source_url, p.id AS passage_id, "
                "p.text, round((1 - (p.embedding <=> %(v)s::vector))::numeric, 3) AS similarity "
                "FROM passages p JOIN episodes e ON e.id = p.episode_id JOIN podcasts pod ON pod.id = e.podcast_id "
                "WHERE p.embedding IS NOT NULL ORDER BY p.embedding <=> %(v)s::vector LIMIT %(limit)s",
                {"v": str(embedding), "limit": limit})
            columns = [column.name for column in cursor.description]
            return [dict(zip(columns, row)) for row in await cursor.fetchall()]

    async def read_only_query(self, sql: str, max_rows: int) -> list[dict] | str:
        """Run one statement as the SELECT-only role in a READ ONLY transaction (migration 002)."""
        async with await self.connect() as conn:
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute("SET LOCAL ROLE podcast_reader")
            await conn.execute("SET LOCAL statement_timeout = '10s'")
            cursor = await conn.execute(sql, prepare=True)  # A prepared statement allows only one command.
            if cursor.description is None:
                return cursor.statusmessage or "OK"
            columns = [column.name for column in cursor.description]
            rows = [dict(zip(columns, row)) for row in await cursor.fetchmany(max_rows)]
            await conn.rollback()
            return rows
