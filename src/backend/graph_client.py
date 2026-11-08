"""LadybugDB access. All Cypher lives here; writes are idempotent MERGEs."""

import real_ladybug as ladybug

from .ingestion.models import EpisodeGraph


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
        for claim_id, passage_id in graph.claims:
            conn.execute("MATCH (p:Passage {id: $p}) MERGE (c:Claim {id: $c}) MERGE (c)-[:EVIDENCED_BY]->(p)",
                         {"p": str(passage_id), "c": str(claim_id)})
        for link in graph.entity_links:
            conn.execute("MATCH (c:Claim {id: $c}), (p:Passage {id: $p}) "
                         "MERGE (n:Entity {id: $n}) ON CREATE SET n.name = $name, n.kind = $kind "
                         "MERGE (c)-[:ABOUT]->(n) MERGE (p)-[:MENTIONS]->(n)",
                         {"c": str(link.claim_id), "p": str(link.passage_id), "n": str(link.entity_id),
                          "name": link.name, "kind": link.kind})

    def close(self) -> None:
        self.database.close()
