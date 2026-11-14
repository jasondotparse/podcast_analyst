import os
import secrets
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
import psycopg
from psycopg.conninfo import make_conninfo
import real_ladybug as ladybug

from src.backend.clients.graph_client import GraphClient
from src.backend.clients.postgres_client import PostgresClient
from src.backend.db.db import migrate_postgres, open_graph


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")


class GraphSchemaTests(unittest.TestCase):
    def test_node_lookup_preserves_types_and_ids_after_deletions_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.lbug"
            graph = GraphClient(open_graph(path))
            conn = ladybug.Connection(graph.database)
            expected = []
            for count, kind in enumerate(("Episode", "Passage", "Claim", "Thesis", "Entity"), start=2):
                ids = [str(uuid4()) for _ in range(count)]
                for node_id in ids:
                    properties = ", name: 'Amazon', kind: 'company'" if kind == "Entity" else ""
                    conn.execute(f"CREATE (:{kind} {{id: $id{properties}}})", {"id": node_id})
                conn.execute(f"MATCH (n:{kind} {{id: $id}}) DELETE n", {"id": ids[0]})
                expected.append((ids[-1], kind, "Amazon" if kind == "Entity" else None,
                                 "company" if kind == "Entity" else None))
            selected = {kind: node_id for node_id, kind, _, _ in expected}
            conn.execute("MATCH (c:Claim {id: $c}), (t:Thesis {id: $t}), (e:Entity {id: $e}) "
                         "CREATE (c)-[:SUPPORTS {strength: 80}]->(t), (c)-[:ABOUT]->(e), (t)-[:ABOUT]->(e)",
                         {"c": selected["Claim"], "t": selected["Thesis"], "e": selected["Entity"]})
            graph.close()

            graph = GraphClient(open_graph(path))
            self.addCleanup(graph.close)
            for row in expected:
                self.assertEqual(graph.view_nodes([row[0]]), [list(row)])
            selected = [row[0] for row in expected]
            self.assertEqual({tuple(row) for row in graph.view_nodes([*selected, selected[0], str(uuid4())])},
                             set(expected))
            self.assertEqual(graph.view_nodes([str(uuid4())]), [])
            by_type = {kind: node_id for node_id, kind, _, _ in expected}
            self.assertEqual({tuple(row) for row in graph.view_edges(selected)}, {
                (by_type["Claim"], by_type["Thesis"], "SUPPORTS", 80),
                (by_type["Claim"], by_type["Entity"], "ABOUT", None),
                (by_type["Thesis"], by_type["Entity"], "ABOUT", None),
            })
            for kind in ("Claim", "Thesis", "Entity"):
                self.assertEqual(set(graph.neighbor_ids(by_type[kind], 60)),
                                 {by_type[other] for other in ("Claim", "Thesis", "Entity") if other != kind})

    def test_visual_queries_order_cap_and_derive_episode_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = GraphClient(open_graph(Path(directory) / "graph.lbug"))
            self.addCleanup(graph.close)
            conn = ladybug.Connection(graph.database)
            for cypher in (
                "CREATE (:Thesis {id: 't'}), (:Entity {id: 'e', name: 'Optics', kind: 'technology'}), "
                "(:Episode {id: 'ep'}), (:Passage {id: 'p'})",
                "MATCH (t:Thesis), (e:Entity) CREATE (t)-[:ABOUT]->(e)",
                "MATCH (ep:Episode), (p:Passage) CREATE (ep)-[:CONTAINS]->(p)",
            ):
                conn.execute(cypher)
            for claim, signal, strength in [('low', 20, 30), ('high', 80, 90), ('null', None, 50)]:
                conn.execute("MATCH (t:Thesis), (p:Passage), (e:Entity) "
                             "CREATE (c:Claim {id: $id, signal: $signal})-[:SUPPORTS {strength: $strength}]->(t), "
                             "(c)-[:EVIDENCED_BY]->(p), (c)-[:ABOUT]->(e)",
                             {"id": claim, "signal": signal, "strength": strength})
            self.assertEqual(graph.thesis_neighborhood(None, 2), ['t', 'e', 'high', 'null', 'ep'])
            self.assertEqual(graph.thesis_neighborhood('t', 1), ['t', 'e', 'high', 'ep'])
            self.assertEqual(graph.thesis_neighborhood('missing', 2), [])
            self.assertEqual(graph.neighbor_ids('t', 60), ['e', 'high', 'low', 'null'])
            self.assertEqual(graph.neighbor_ids('e', 2), ['t', 'high'])
            self.assertEqual(graph.neighbor_ids('ep', 60), ['high', 'low', 'null'])
            self.assertEqual(graph.neighbor_ids('high', 60), ['e', 'ep', 't'])
            for missing in ['p', 'unknown']:
                self.assertIsNone(graph.neighbor_ids(missing, 60))
            self.assertEqual(graph.view_nodes(['e']), [['e', 'Entity', 'Optics', 'technology']])
            self.assertEqual({tuple(row) for row in graph.view_edges(['high', 'ep', 't'])},
                             {('high', 't', 'SUPPORTS', 90), ('high', 'ep', 'IN_EPISODE', None)})
            self.assertEqual(graph.view_edges([]), [])
            self.assertEqual(graph.view_nodes([]), [])

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


    def test_read_only_query_reads_and_refuses_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = GraphClient(open_graph(Path(directory) / "graph.lbug"))
            ladybug.Connection(graph.database).execute(
                "CREATE (:Claim {id: 'c1'})-[:ABOUT]->(:Entity {id: 'e1', name: 'Amazon', kind: 'company'})")
            self.assertEqual(graph.read_only_query(
                "MATCH (c:Claim)-[:ABOUT]->(e:Entity) RETURN c.id AS claim, e.name AS entity", 10),
                [{"claim": "c1", "entity": "Amazon"}])
            for cypher in ("CREATE (:Entity {id: 'e2'})", "COMMIT; CREATE (:Entity {id: 'e3'})",
                           "MATCH (n:Missing) RETURN n"):
                with self.assertRaises(RuntimeError):
                    graph.read_only_query(cypher, 10)
            self.assertEqual(graph.read_only_query("MATCH (e:Entity) RETURN count(e) AS n", 10), [{"n": 1}])
            graph.close()


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
        self.assertEqual(await migrate_postgres(self.url), ["001_initial", "002_ingestion", "003_thesis_entities",
                                                            "004_episode_audio", "005_rss_watchers",
                                                            "006_claim_signal", "007_thesis_assessments",
                                                            "008_cron_subscriptions"])
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
            with self.assertRaises(psycopg.errors.CheckViolation):
                await conn.execute("UPDATE claims SET signal = 101")
            self.assertEqual(await one("SELECT signal FROM claims"), (None,))  # Unranked until the rank step.
            (claim_id,) = await one("SELECT id FROM claims")
            (thesis,) = await one("INSERT INTO theses (description) VALUES ('Optics') RETURNING id")
            for stance, strength in [('invalid', 50), ('supports', -1), ('challenges', 101)]:
                with self.assertRaises(psycopg.errors.CheckViolation):
                    await conn.execute("INSERT INTO thesis_assessments VALUES (%s, %s, %s, %s)",
                                       (claim_id, thesis, stance, strength))
            await conn.execute("INSERT INTO thesis_assessments VALUES (%s, %s, 'unrelated', 0)", (claim_id, thesis))
            with self.assertRaises(psycopg.errors.UniqueViolation):
                await conn.execute("INSERT INTO thesis_assessments VALUES (%s, %s, 'supports', 25)", (claim_id, thesis))
            await conn.execute("DELETE FROM claims")
            self.assertEqual(await one("SELECT count(*) FROM thesis_assessments"), (0,))

    async def test_catalog_lists_what_the_cabinet_shows(self):
        await migrate_postgres(self.url)
        db = PostgresClient(self.url)
        async with await psycopg.AsyncConnection.connect(self.url, autocommit=True) as conn:
            async def one(query, *params):
                return await (await conn.execute(query, params)).fetchone()

            (thesis,) = await one("INSERT INTO theses (description) VALUES ('Optics win') RETURNING id")
            await conn.execute("INSERT INTO theses (description, is_active) VALUES ('Old idea', false)")
            await conn.execute("INSERT INTO subscriptions (thesis_id, email) VALUES (%s, 'a@example.com')", (thesis,))
            await conn.execute("INSERT INTO watch_sources (url, description, ingest_since) VALUES "
                               "('https://b.example/rss', 'Beta Pod', now()), ('https://a.example/rss', NULL, now()), "
                               "('https://c.example/rss', 'Gone', now())")
            await conn.execute("UPDATE watch_sources SET is_active = false WHERE description = 'Gone'")
            (podcast,) = await one("INSERT INTO podcasts (name) VALUES ('Chips') RETURNING id")
            for title, status, age in [('Old', 'completed', '2 hours'), ('New', 'failed', '1 minute')]:
                (episode,) = await one("INSERT INTO episodes (podcast_id, title, source_type) "
                                       "VALUES (%s, %s, 'txt') RETURNING id", podcast, title)
                await conn.execute("INSERT INTO ingestion_jobs (episode_id, status, error, updated_at) "
                                   "VALUES (%s, %s, %s, now() - %s::interval)",
                                   (episode, status, 'boom' if status == 'failed' else None, age))

        theses = await db.list_theses()
        self.assertEqual({(row["description"], row["is_active"]) for row in theses},
                         {("Optics win", True), ("Old idea", False)})
        self.assertEqual([row["name"] for row in await db.list_watched_podcasts()],
                         ["Beta Pod", "https://a.example/rss"])  # Falls back to the URL; inactive feeds hidden.
        self.assertEqual([(row["email"], row["thesis"]) for row in await db.list_subscriptions()],
                         [("a@example.com", "Optics win")])
        jobs = await db.recent_ingestions(1)
        self.assertEqual([(row["podcast"], row["title"], row["status"], row["error"]) for row in jobs],
                         [("Chips", "New", "failed", "boom")])



if __name__ == "__main__":
    unittest.main()
