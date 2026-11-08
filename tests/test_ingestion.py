import secrets
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

from src.backend.clients.postgres_client import PostgresClient
from src.backend.db.db import migrate_postgres
from src.backend.ingestion.claim_extractor import ClaimExtractor
from src.backend.ingestion.ingestion_service import OVERLAP_WORDS, IngestionService, split_passages
from src.backend.ingestion.models import EpisodeMetadata
from tests.test_db import DATABASE_URL


class SplitPassagesTests(unittest.TestCase):
    def test_any_overlap_length_quote_fits_in_one_passage(self):
        words = [f"w{i}" for i in range(2600)]
        passages = split_passages(" ".join(words))
        self.assertEqual(len(passages), 5)
        self.assertEqual(passages[-1].split()[-1], "w2599")
        for start in range(0, len(words) - OVERLAP_WORDS + 1, 37):
            quote = " ".join(words[start:start + OVERLAP_WORDS])
            self.assertTrue(any(quote in passage for passage in passages), start)


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL is not set")
class PostgresBackedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.name = f"podcast_test_{secrets.token_hex(4)}"
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"CREATE DATABASE {self.name}")
        self.db = PostgresClient(make_conninfo(DATABASE_URL, dbname=self.name))
        self.extractor = ClaimExtractor(self.db, harness=None, skill=None)
        await migrate_postgres(self.db.url)
        async with await self.db.connect() as conn:
            (podcast,) = await (await conn.execute("INSERT INTO podcasts (name) VALUES ('Chips') RETURNING id")).fetchone()
            (self.episode,) = await (await conn.execute(
                "INSERT INTO episodes (podcast_id, title, source_type) VALUES (%s, 'Optics', 'txt') RETURNING id",
                (podcast,))).fetchone()
            await conn.execute("INSERT INTO passages (episode_id, ordinal, text) VALUES (%s, 0, %s)",
                               (self.episode, "We are power constrained. Lasers ship next year."))

    async def asyncTearDown(self):
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"DROP DATABASE {self.name} WITH (FORCE)")

    async def test_query_tool_is_read_only_and_single_statement(self):
        self.assertIn('"title": "Optics"', await self.extractor.query("SELECT title FROM episodes"))
        for sql in ("DELETE FROM passages", "SELECT 1; DELETE FROM passages"):
            with self.assertRaises(psycopg.Error):
                await self.extractor.query(sql)
        self.assertIn('"ordinal": 0', await self.extractor.query("SELECT ordinal FROM passages"))

    async def test_write_claim_requires_a_verbatim_quote_and_is_idempotent(self):
        claim = {"text": "Lasers ship next year.", "evidence_quote": "Lasers  ship\nnext year.",
                 "speaker": None, "entities": [{"name": " Sivers ", "kind": "company"}]}
        self.assertEqual(await self.extractor.write_claim(self.episode, claim), "Saved.")
        self.assertEqual(await self.extractor.write_claim(self.episode, claim), "Already saved.")
        with self.assertRaises(ValueError):
            await self.extractor.write_claim(self.episode, {**claim, "evidence_quote": "Lasers shipped."})
        async with await self.db.connect() as conn:
            rows = await (await conn.execute(
                "SELECT c.evidence_quote, e.name FROM claims c JOIN claim_entities ce ON ce.claim_id = c.id "
                "JOIN entities e ON e.id = ce.entity_id")).fetchall()
        self.assertEqual(rows, [("Lasers ship next year.", "Sivers")])

    async def test_submit_queues_once_and_returns_the_existing_job_for_a_duplicate(self):
        service = IngestionService(self.db, graph=None, openai=None, extractor=None)
        metadata = EpisodeMetadata(podcast_name="Chips", title="Packaging")
        job, created = await service.submit(metadata, "Advanced packaging is the bottleneck.")
        self.assertTrue(created)
        self.assertEqual(job.status, "pending")
        duplicate, created = await service.submit(metadata, " Advanced  packaging is\nthe bottleneck. ")
        self.assertFalse(created)
        self.assertEqual(duplicate, job)
        self.assertEqual(await service.get_job(job.job_id), job)
