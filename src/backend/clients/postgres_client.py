"""PostgreSQL access. Callers pass and receive domain values; all SQL lives here."""

from uuid import UUID

import psycopg

from ..ingestion.models import EntityLink, Episode, EpisodeGraph, EpisodeMetadata, IngestionJob


JOB_COLUMNS = "id, episode_id, status, current_step, error"


def to_job(row) -> IngestionJob | None:
    if row is None:
        return None
    job_id, episode_id, status, current_step, error = row
    return IngestionJob(job_id=job_id, episode_id=episode_id, status=status, current_step=current_step, error=error)


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

    # ---- Ingestion jobs ---------------------------------------------------

    async def find_job_by_source(self, content_hash: str, source_url: str | None) -> IngestionJob | None:
        return to_job(await self.one(
            "SELECT j.id, j.episode_id, j.status, j.current_step, j.error FROM episodes e "
            "JOIN ingestion_jobs j ON j.episode_id = e.id WHERE e.content_hash = %s OR e.source_url = %s",
            content_hash, source_url))

    async def create_episode_job(self, metadata: EpisodeMetadata, transcript: str, content_hash: str) -> IngestionJob:
        """Find (updating a provided description) or create the podcast by name, then insert
        the episode and its pending job."""
        async with await self.connect() as conn:
            podcast = await (await conn.execute(
                "UPDATE podcasts SET description = COALESCE(%s, description) WHERE name = %s RETURNING id",
                (metadata.podcast_description, metadata.podcast_name))).fetchone() \
                or await (await conn.execute("INSERT INTO podcasts (name, description) VALUES (%s, %s) RETURNING id",
                                             (metadata.podcast_name, metadata.podcast_description))).fetchone()
            (episode_id,) = await (await conn.execute(
                "INSERT INTO episodes (podcast_id, title, description, published_at, guests, source_type, source_url, "
                "content_hash, transcript_text) VALUES (%s, %s, %s, %s, %s, 'txt', %s, %s, %s) RETURNING id",
                (podcast[0], metadata.title, metadata.description, metadata.published_at, metadata.guests,
                 metadata.source_url, content_hash, transcript))).fetchone()
            return to_job(await (await conn.execute(
                f"INSERT INTO ingestion_jobs (episode_id) VALUES (%s) RETURNING {JOB_COLUMNS}", (episode_id,))).fetchone())

    async def get_job(self, job_id: UUID) -> IngestionJob | None:
        return to_job(await self.one(f"SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE id = %s", job_id))

    async def next_pending_job(self) -> IngestionJob | None:
        return to_job(await self.one(
            f"SELECT {JOB_COLUMNS} FROM ingestion_jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1"))

    async def requeue_job(self, job_id: UUID) -> None:
        await self.execute("UPDATE ingestion_jobs SET status = 'pending', error = NULL, updated_at = now() "
                           "WHERE id = %s AND status <> 'running'", job_id)

    async def requeue_running_jobs(self) -> None:
        await self.execute("UPDATE ingestion_jobs SET status = 'pending' WHERE status = 'running'")

    async def update_job(self, job_id: UUID, status: str, current_step: str | None = None, error: str | None = None):
        await self.execute("UPDATE ingestion_jobs SET status = %s, current_step = COALESCE(%s, current_step), "
                           "error = %s, updated_at = now() WHERE id = %s", status, current_step, error, job_id)

    # ---- Episodes and passages -------------------------------------------

    async def get_episode(self, episode_id: UUID) -> Episode:
        row = await self.one("SELECT p.name, e.title, e.description, e.transcript_text, e.claims_extracted_at "
                             "FROM episodes e JOIN podcasts p ON p.id = e.podcast_id WHERE e.id = %s", episode_id)
        return Episode(*row)

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

    async def episode_graph(self, episode_id: UUID) -> EpisodeGraph:
        passages = await self.all("SELECT id FROM passages WHERE episode_id = %s", episode_id)
        claims = await self.all("SELECT c.id, c.passage_id FROM claims c JOIN passages p ON p.id = c.passage_id "
                                "WHERE p.episode_id = %s", episode_id)
        links = await self.all("SELECT ce.claim_id, c.passage_id, e.id, e.name, e.kind FROM claim_entities ce "
                               "JOIN entities e ON e.id = ce.entity_id JOIN claims c ON c.id = ce.claim_id "
                               "JOIN passages p ON p.id = c.passage_id WHERE p.episode_id = %s", episode_id)
        return EpisodeGraph(episode_id, [passage_id for (passage_id,) in passages],
                            [tuple(row) for row in claims], [EntityLink(*row) for row in links])

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
