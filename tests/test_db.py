import os
import secrets
import tempfile
import unittest
from pathlib import Path

from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import make_conninfo
import real_ladybug as ladybug

from src.backend.db import migrate_postgres, open_graph


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")


class GraphSchemaTests(unittest.TestCase):
    def test_schema_is_idempotent_and_data_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ladybug" / "graph.lbug"
            database = open_graph(path)
            ladybug.Connection(database).execute(
                "CREATE (:Claim {id: 'c1'})-[:EVIDENCED_BY]->(:Passage {id: 'p1'})")
            database.close()

            database = open_graph(path)
            connection = ladybug.Connection(database)
            tables = {row[0] for row in connection.execute("CALL show_tables() RETURN name").get_all()}
            self.assertEqual(tables, {"Episode", "Passage", "Claim", "Thesis", "Entity", "CONTAINS",
                                      "EVIDENCED_BY", "ABOUT", "SUPPORTS", "CHALLENGES", "MENTIONS"})
            rows = connection.execute("MATCH (:Claim {id: 'c1'})-[:EVIDENCED_BY]->(p) RETURN p.id").get_all()
            self.assertEqual(rows, [["p1"]])
            database.close()


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL is not set")
class PostgresMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.name = f"podcast_test_{secrets.token_hex(4)}"
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"CREATE DATABASE {self.name}")
        self.url = make_conninfo(DATABASE_URL, dbname=self.name)

    async def asyncTearDown(self):
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"DROP DATABASE {self.name} WITH (FORCE)")

    async def test_migrations_apply_once_and_enforce_the_design(self):
        self.assertEqual(await migrate_postgres(self.url), ["001_initial"])
        self.assertEqual(await migrate_postgres(self.url), [])

        async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
            async def one(query, *params):
                return await (await conn.execute(query, params)).fetchone()

            (podcast,) = await one("INSERT INTO podcasts (name) VALUES ('Chips') RETURNING id")
            (episode,) = await one("INSERT INTO episodes (podcast_id, title, source_type, content_hash) "
                                   "VALUES (%s, 'Optics', 'txt', 'abc') RETURNING id", podcast)
            vector = "[" + ",".join(["1"] * 1536) + "]"
            (passage,) = await one("INSERT INTO passages (episode_id, ordinal, text, embedding) "
                                   "VALUES (%s, 0, 'We are power constrained.', %s) RETURNING id",
                                   episode, vector)
            claim = "INSERT INTO claims (passage_id, text, evidence_quote, fingerprint) VALUES (%s, 'x', 'x', 'f')"
            await conn.execute(claim, (passage,))
            self.assertEqual(await one("SELECT embedding <=> %s::vector FROM passages", vector), (0.0,))

            with self.assertRaises(psycopg.errors.UniqueViolation):
                await conn.execute(claim, (passage,))
            with self.assertRaises(psycopg.errors.UniqueViolation):
                await conn.execute("INSERT INTO episodes (podcast_id, title, source_type, content_hash) "
                                   "VALUES (%s, 'Again', 'mp3', 'abc')", (podcast,))
            with self.assertRaises(psycopg.errors.CheckViolation):
                await conn.execute("INSERT INTO episodes (podcast_id, title, source_type) "
                                   "VALUES (%s, 'Bad', 'pdf')", (podcast,))
            with self.assertRaises(psycopg.errors.DataException):
                await conn.execute("INSERT INTO passages (episode_id, ordinal, text, embedding) "
                                   "VALUES (%s, 1, 'y', '[1,2,3]')", (episode,))


if __name__ == "__main__":
    unittest.main()
