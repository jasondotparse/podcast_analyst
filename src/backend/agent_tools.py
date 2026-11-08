"""Handlers for the chat agent's tools (definitions in codex/tools/): three read-only
research tools and add_thesis. Each passes the model's arguments straight to the clients."""

import json

from .clients.embedding_client import EmbeddingClient
from .clients.graph_client import GraphClient
from .clients.postgres_client import PostgresClient


MAX_ROWS, MAX_CHARS = 200, 40_000
CHAT_TOOLS = ("vector_search", "graph_query", "postgres_database_query", "add_thesis")


class AgentTools:
    def __init__(self, db: PostgresClient, graph: GraphClient, embedder: EmbeddingClient):
        self.db = db
        self.graph = graph
        self.embedder = embedder

    async def call(self, name: str, arguments: dict) -> str:
        """Run a tool and return its result as (capped) JSON text for the model."""
        if name not in CHAT_TOOLS:
            raise ValueError(f"Unknown tool {name}")
        output = json.dumps(await getattr(self, name)(**arguments), default=str)
        return output if len(output) <= MAX_CHARS else output[:MAX_CHARS] + "\n[output cut off; ask for less]"

    async def vector_search(self, query: str, limit: int = 5):
        [embedding] = await self.embedder.embed([query])
        return await self.db.vector_search(embedding, max(1, min(limit, 20)))

    async def graph_query(self, cypher: str):
        return self.graph.read_only_query(cypher, MAX_ROWS)

    async def postgres_database_query(self, sql: str):
        return await self.db.read_only_query(sql, MAX_ROWS)

    async def add_thesis(self, description: str, entities: list[dict]):
        """Save the thesis in Postgres (the source of truth), then mirror it into the graph."""
        if not description.strip() or not entities:  # Seen in testing: an empty "probe" call.
            raise ValueError("Nothing saved: a thesis needs a description and at least one entity. If the user's "
                             "view is not specific yet, ask them follow-up questions instead of saving.")
        thesis_id, linked = await self.db.add_thesis(
            description.strip(), [(entity["name"].strip(), entity["kind"]) for entity in entities])
        self.graph.project_thesis(thesis_id, linked)
        return {"thesis_id": thesis_id, "saved": description.strip(),
                "about": [f"{name} ({kind})" for _, name, kind in linked]}
