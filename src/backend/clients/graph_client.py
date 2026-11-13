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

    # Fixed read queries for the visual model. Passage paths become IN_EPISODE.
    def _rows(self, cypher: str, params: dict) -> list:
        return ladybug.Connection(self.database).execute(cypher, params).get_all()

    def thesis_neighborhood(self, thesis_id: str | None, max_claims: int) -> list[str]:
        theses = [row[0] for row in self._rows(
            "MATCH (t:Thesis) WHERE $id IS NULL OR t.id = $id RETURN t.id ORDER BY t.id", {"id": thesis_id})]
        if not theses:
            return []
        entities = self._rows("MATCH (t:Thesis)-[:ABOUT]->(e:Entity) WHERE t.id IN $ids "
                              "RETURN DISTINCT e.id ORDER BY e.id", {"ids": theses})
        claims = self._rows("MATCH (c:Claim)-[r:SUPPORTS|CHALLENGES]->(t:Thesis) WHERE t.id IN $ids "
                            "RETURN c.id, max(r.strength) AS strength ORDER BY strength DESC, c.id LIMIT $n",
                            {"ids": theses, "n": max_claims})
        claim_ids = [row[0] for row in claims]
        episodes = self._rows(
            "MATCH (c:Claim)-[:EVIDENCED_BY]->(:Passage)<-[:CONTAINS]-(e:Episode) "
            "WHERE c.id IN $ids RETURN DISTINCT e.id ORDER BY e.id", {"ids": claim_ids}) if claim_ids else []
        return list(dict.fromkeys(theses + [row[0] for row in entities] + claim_ids + [row[0] for row in episodes]))

    def neighbor_ids(self, node_id: str, limit: int) -> list[str] | None:
        nodes = self.view_nodes([node_id])
        if not nodes or nodes[0][1] == "Passage":
            return None
        rows = []
        # Use typed endpoints here too: untyped lookups can omit thesis links.
        for source, target, relationship in (("Claim", "Entity", "ABOUT"), ("Thesis", "Entity", "ABOUT"),
                                             ("Claim", "Thesis", "SUPPORTS|CHALLENGES")):
            signal = "CASE WHEN a.id = $id THEN NULL ELSE a.signal END" if source == "Claim" else "NULL"
            rows.extend(self._rows(
                f"MATCH (a:{source})-[:{relationship}]->(b:{target}) WHERE a.id = $id OR b.id = $id "
                "RETURN CASE WHEN a.id = $id THEN b.id ELSE a.id END, "
                f"CASE WHEN a.id = $id THEN '{target}' ELSE '{source}' END, "
                f"{signal}", {"id": node_id}))
        rows.extend(self._rows(
            "MATCH (a:Claim)-[:EVIDENCED_BY]->(:Passage)<-[:CONTAINS]-(b:Episode) "
            "WHERE a.id = $id OR b.id = $id "
            "RETURN CASE WHEN a.id = $id THEN b.id ELSE a.id END AS id, "
            "CASE WHEN a.id = $id THEN 'Episode' ELSE 'Claim' END AS type, "
            "CASE WHEN a.id = $id THEN NULL ELSE a.signal END AS signal", {"id": node_id}))
        rows.sort(key=lambda row: (row[1] == "Claim", -(row[2] if row[2] is not None else -1), row[0]))
        return list(dict.fromkeys(row[0] for row in rows))[:limit]

    def view_nodes(self, ids: list[str]) -> list:
        if not ids:
            return []
        # Untyped ID lookups returned nodes from the wrong table on prod (Ladybug 0.15.3).
        # Keep the table explicit; only Entity has name/kind properties.
        rows = []
        for kind in ("Episode", "Passage", "Claim", "Thesis"):
            rows.extend(self._rows(
                f"MATCH (n:{kind}) WHERE n.id IN $ids RETURN n.id, '{kind}', NULL, NULL", {"ids": ids}))
        rows.extend(self._rows("MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id, 'Entity', n.name, n.kind",
                               {"ids": ids}))
        return rows

    def view_edges(self, ids: list[str]) -> list:
        if not ids:
            return []
        params = {"ids": ids}
        stances = self._rows(
            "MATCH (a:Claim)-[r:SUPPORTS|CHALLENGES]->(b:Thesis) WHERE a.id IN $ids AND b.id IN $ids "
            "RETURN a.id, b.id, label(r), r.strength", params)
        about = []
        for kind in ("Claim", "Thesis"):
            about.extend(self._rows(f"MATCH (a:{kind})-[:ABOUT]->(b:Entity) "
                                    "WHERE a.id IN $ids AND b.id IN $ids RETURN a.id, b.id", params))
        episodes = self._rows(
            "MATCH (a:Claim)-[:EVIDENCED_BY]->(:Passage)<-[:CONTAINS]-(b:Episode) "
            "WHERE a.id IN $ids AND b.id IN $ids RETURN DISTINCT a.id, b.id", params)
        return stances + [[a, b, "ABOUT", None] for a, b in about] + [
            [a, b, "IN_EPISODE", None] for a, b in episodes]

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
