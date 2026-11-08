"""Handlers for Codex's read-only research tools (definitions in codex/tools/).
Each one passes the model's arguments straight to a client."""

import json

from .clients.embedding_client import EmbeddingClient
from .clients.graph_client import GraphClient
from .clients.postgres_client import PostgresClient


MAX_ROWS, MAX_CHARS = 200, 40_000
RESEARCH_TOOLS = ("vector_search", "graph_query", "postgres_database_query")


class AgentTools:
    def __init__(self, db: PostgresClient, graph: GraphClient, embedder: EmbeddingClient):
        self.db = db
        self.graph = graph
        self.embedder = embedder

    async def call(self, name: str, arguments: dict) -> str:
        """Run a research tool and return its result as (capped) JSON text for the model."""
        if name not in RESEARCH_TOOLS:
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
