"""LadybugDB access. All Cypher lives here; writes are idempotent MERGEs."""

import real_ladybug as ladybug

from ..ingestion.models import EpisodeGraph


EDGE_MIN_STRENGTH = 25  # Keep weak relevant connections available for research recall.


class GraphClient:
    def __init__(self, database: ladybug.Database):
        self.database = database  # Keep a reference; Ladybug loses writes if the Database is collected.

    def project_episode(self, graph: EpisodeGraph) -> None:
        """Mirror an episode's passages, claims, and entities from Postgres. Safe to rerun."""
        conn = ladybug.Connection(self.database)
        episode = str(graph.episode_id)
        conn.execute("MERGE (:Episode {id: $id})", {"id": episode})
        for passage_id in graph.passage_ids:
            conn.execute("MATCH (e:Episode {id: $e}) MERGE (p:Passage {id: $p}) MERGE (e)-[:CONTAINS]->(p)",
                         {"e": episode, "p": str(passage_id)})
        for claim_id, passage_id, signal in graph.claims:
            conn.execute("MATCH (p:Passage {id: $p}) MERGE (c:Claim {id: $c}) SET c.signal = $signal "
                         "MERGE (c)-[:EVIDENCED_BY]->(p)",
                         {"p": str(passage_id), "c": str(claim_id), "signal": signal})
        for link in graph.entity_links:
            conn.execute("MATCH (c:Claim {id: $c}), (p:Passage {id: $p}) "
                         "MERGE (n:Entity {id: $n}) ON CREATE SET n.name = $name, n.kind = $kind "
                         "MERGE (c)-[:ABOUT]->(n) MERGE (p)-[:MENTIONS]->(n)",
                         {"c": str(link.claim_id), "p": str(link.passage_id), "n": str(link.entity_id),
                          "name": link.name, "kind": link.kind})
        self.project_stances([row[0] for row in graph.claims], graph.stances)

    def project_stances(self, claim_ids: list, edges: list) -> None:
        """Replace stance edges from Postgres, so a changed stance never leaves its opposite behind."""
        if not claim_ids:
            return
        conn = ladybug.Connection(self.database)
        conn.execute("MATCH (c:Claim)-[r:SUPPORTS|CHALLENGES]->(:Thesis) WHERE c.id IN $ids DELETE r",
                     {"ids": list(map(str, claim_ids))})
        for claim_id, thesis_id, stance, strength in edges:
            if stance in ("supports", "challenges") and strength >= EDGE_MIN_STRENGTH:
                conn.execute(f"MERGE (c:Claim {{id: $c}}) MERGE (t:Thesis {{id: $t}}) "
                             f"MERGE (c)-[r:{stance.upper()}]->(t) SET r.strength = $strength",
                             {"c": str(claim_id), "t": str(thesis_id), "strength": strength})

    def project_thesis(self, thesis_id, entities: list[tuple]) -> None:
        """Mirror a thesis and its (id, name, kind) entities as (Thesis)-[:ABOUT]->(Entity)."""
        conn = ladybug.Connection(self.database)
        conn.execute("MERGE (:Thesis {id: $id})", {"id": str(thesis_id)})
        for entity_id, name, kind in entities:
            conn.execute("MATCH (t:Thesis {id: $t}) MERGE (n:Entity {id: $n}) ON CREATE SET n.name = $name, "
                         "n.kind = $kind MERGE (t)-[:ABOUT]->(n)",
                         {"t": str(thesis_id), "n": str(entity_id), "name": name, "kind": kind})

    def read_only_query(self, cypher: str, max_rows: int) -> list[dict]:
        """Run one statement in a read-only transaction. Preparing it rejects multi-statement
        input, which could otherwise COMMIT out of the read-only transaction."""
        conn = ladybug.Connection(self.database)
        conn.execute("BEGIN TRANSACTION READ ONLY")
        result = conn.execute(conn.prepare(cypher))  # A failing query ends the transaction itself.
        columns = result.get_column_names()
        rows = []
        while result.has_next() and len(rows) < max_rows:
            rows.append(dict(zip(columns, result.get_next())))
        conn.execute("ROLLBACK")
        return rows

    def close(self) -> None:
        self.database.close()
