"""Handlers for the chat agent's tools (definitions in codex/tools/): three read-only
research tools, add_thesis, add_subscription, and register_rss_to_watcher. Each passes the model's arguments
straight to the clients or services."""

import json
import re
from uuid import UUID

from .clients.embedding_client import EmbeddingClient
from .clients.graph_client import GraphClient
from .clients.postgres_client import PostgresClient


MAX_ROWS, MAX_CHARS = 200, 40_000
CHAT_TOOLS = ("vector_search", "graph_query", "postgres_database_query", "add_thesis", "register_rss_to_watcher",
              "add_subscription")


class AgentTools:
    def __init__(self, db: PostgresClient, graph: GraphClient, embedder: EmbeddingClient):
        self.db = db
        self.graph = graph
        self.embedder = embedder
        self.watcher = None  # A WatcherService, set by main.py once the ingestion pipeline it feeds exists.

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

    async def register_rss_to_watcher(self, feed_url: str):
        return await self.watcher.register(feed_url)

    async def add_subscription(self, email: str, thesis_ids: list[str]):
        email = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError(f"Invalid email address: {email}")
        if not thesis_ids:
            raise ValueError("Choose at least one active thesis ID.")
        ids = []
        for thesis_id in thesis_ids:
            try:
                ids.append(UUID(thesis_id))
            except ValueError:
                raise ValueError(f"Invalid thesis ID: {thesis_id}") from None
        return {"email": email, "subscribed_to": await self.db.add_subscription(email, ids)}
