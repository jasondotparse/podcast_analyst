import secrets
from types import SimpleNamespace
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

from src.backend.db import migrate_postgres
from src.backend.ingestion import OVERLAP_WORDS, postgres_database_query, split_passages, write_claim
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
class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.name = f"podcast_test_{secrets.token_hex(4)}"
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"CREATE DATABASE {self.name}")
        self.state = SimpleNamespace(database_url=make_conninfo(DATABASE_URL, dbname=self.name))
        await migrate_postgres(self.state.database_url)
        async with await psycopg.AsyncConnection.connect(self.state.database_url) as conn:
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
        self.assertIn('"title": "Optics"', await postgres_database_query(self.state, "SELECT title FROM episodes"))
        for sql in ("DELETE FROM passages", "SELECT 1; DELETE FROM passages"):
            with self.assertRaises(psycopg.Error):
                await postgres_database_query(self.state, sql)
        self.assertIn('"ordinal": 0', await postgres_database_query(self.state, "SELECT ordinal FROM passages"))

    async def test_write_claim_requires_a_verbatim_quote_and_is_idempotent(self):
        claim = {"text": "Lasers ship next year.", "evidence_quote": "Lasers  ship\nnext year.",
                 "speaker": None, "entities": [{"name": " Sivers ", "kind": "company"}]}
        self.assertEqual(await write_claim(self.state, self.episode, claim), "Saved.")
        self.assertEqual(await write_claim(self.state, self.episode, claim), "Already saved.")
        with self.assertRaises(ValueError):
            await write_claim(self.state, self.episode, {**claim, "evidence_quote": "Lasers shipped."})
        async with await psycopg.AsyncConnection.connect(self.state.database_url) as conn:
            rows = await (await conn.execute(
                "SELECT c.evidence_quote, e.name FROM claims c JOIN claim_entities ce ON ce.claim_id = c.id "
                "JOIN entities e ON e.id = ce.entity_id")).fetchall()
        self.assertEqual(rows, [("Lasers ship next year.", "Sivers")])
