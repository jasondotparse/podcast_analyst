import asyncio
import json
from pathlib import Path
import secrets
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import psycopg
from psycopg.conninfo import make_conninfo

from src.backend.agent_tools import AgentTools
from src.backend.clients.graph_client import GraphClient
from src.backend.clients.postgres_client import PostgresClient
from src.backend.db.db import migrate_postgres, open_graph
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
        self.tools = AgentTools(self.db, graph=None, embedder=None)
        self.extractor = ClaimExtractor(self.db, harness=None, tools=self.tools, skill=None)
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

    async def query(self, sql):
        return await self.tools.call("postgres_database_query", {"sql": sql})

    async def test_query_tool_is_read_only_and_single_statement(self):
        self.assertIn('"title": "Optics"', await self.query("SELECT title FROM episodes"))
        for sql in ("DELETE FROM passages", "SELECT 1; DELETE FROM passages"):
            with self.assertRaises(psycopg.Error):
                await self.query(sql)
        self.assertIn('"ordinal": 0', await self.query("SELECT ordinal FROM passages"))

    async def test_vector_search_returns_the_nearest_passages_with_citation_details(self):
        def one_hot(index):
            return [1.0 if position == index else 0.0 for position in range(1536)]
        async with await self.db.connect() as conn:
            await conn.execute("UPDATE passages SET embedding = %s::vector", (str(one_hot(0)),))
            await conn.execute("INSERT INTO passages (episode_id, ordinal, text, embedding) "
                               "VALUES (%s, 1, 'Copper is cheap.', %s::vector)", (self.episode, str(one_hot(1))))
        self.tools.embedder = SimpleNamespace(embed=AsyncMock(return_value=[one_hot(1)]))
        rows = json.loads(await self.tools.call("vector_search", {"query": "copper costs", "limit": 1}))
        self.assertEqual([(row["text"], row["podcast"], row["episode"], float(row["similarity"])) for row in rows],
                         [("Copper is cheap.", "Chips", "Optics", 1.0)])
        self.tools.embedder.embed.assert_awaited_once_with(["copper costs"])

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

    async def test_concurrent_claims_sharing_entities_do_not_deadlock(self):
        names = [f"Company {i}" for i in range(6)]
        claims = [{"text": f"Claim {i}.", "evidence_quote": "Lasers ship next year.", "speaker": None,
                   "entities": [{"name": name, "kind": "company"} for name in (names if i % 2 else names[::-1])]}
                  for i in range(12)]
        results = await asyncio.gather(*(self.extractor.write_claim(self.episode, claim) for claim in claims))
        self.assertEqual(results, ["Saved."] * 12)

    async def test_submit_queues_once_and_returns_the_existing_job_for_a_duplicate(self):
        service = IngestionService(self.db, graph=None, openai=None, embedder=None, extractor=None)
        metadata = EpisodeMetadata(podcast_name="Chips", podcast_description="Semiconductor news", title="Packaging")
        job, created = await service.submit(metadata, "Advanced packaging is the bottleneck.")
        self.assertTrue(created)
        self.assertEqual(job.status, "pending")
        duplicate, created = await service.submit(metadata, " Advanced  packaging is\nthe bottleneck. ")
        self.assertFalse(created)
        self.assertEqual(duplicate, job)
        self.assertEqual(await service.get_job(job.job_id), job)
        self.assertEqual(await self.db.all("SELECT name, description FROM podcasts"), [("Chips", "Semiconductor news")])

    async def test_add_thesis_links_existing_and_new_entities_in_postgres_and_the_graph(self):
        await self.extractor.write_claim(self.episode, {
            "text": "Lasers ship next year.", "evidence_quote": "Lasers ship next year.", "speaker": None,
            "entities": [{"name": "Amazon", "kind": "company"}]})
        with tempfile.TemporaryDirectory() as directory:
            self.tools.graph = GraphClient(open_graph(Path(directory) / "graph.lbug"))
            result = json.loads(await self.tools.call("add_thesis", {
                "description": " AWS data center capex will keep accelerating through 2028. ",
                "entities": [{"name": "Amazon", "kind": "company"}, {"name": "AWS", "kind": "company"},
                             {"name": "AWS ", "kind": "company"}]}))
            self.assertEqual(result["saved"], "AWS data center capex will keep accelerating through 2028.")
            self.assertEqual(result["about"], ["AWS (company)", "Amazon (company)"])
            linked = json.loads(await self.query(
                "SELECT t.description, e.name FROM theses t JOIN thesis_entities te ON te.thesis_id = t.id "
                "JOIN entities e ON e.id = te.entity_id ORDER BY e.name"))
            self.assertEqual(sorted(row["name"] for row in linked), ["AWS", "Amazon"])
            self.assertEqual(await self.db.all("SELECT count(*) FROM entities"), [(2,)])  # Amazon was reused.
            graph_rows = self.tools.graph.read_only_query(
                f"MATCH (t:Thesis {{id: '{result['thesis_id']}'}})-[:ABOUT]->(e:Entity) RETURN e.name AS name", 10)
            self.assertEqual(sorted(row["name"] for row in graph_rows), ["AWS", "Amazon"])
            for empty in ({"description": " ", "entities": [{"name": "AWS", "kind": "company"}]},
                          {"description": "AWS grows.", "entities": []}):
                with self.assertRaises(ValueError):
                    await self.tools.call("add_thesis", empty)
            self.assertEqual(await self.db.all("SELECT count(*) FROM theses"), [(1,)])
            self.tools.graph.close()
