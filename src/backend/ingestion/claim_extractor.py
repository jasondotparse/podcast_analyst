"""Claim extraction as a Codex thread: it reads the episode with read-only SQL and
saves claims through write_claim. See docs/etl_design.md#claim-extraction-with-codex."""

import hashlib
from pathlib import Path
from uuid import UUID

from ..agent_tools import AgentTools
from ..chat import ChatHarness
from ..clients.postgres_client import PostgresClient
from ..codex_config import tool_specs


def fingerprint(text: str, quote: str) -> str:
    return hashlib.sha256(" ".join(f"{text} {quote}".lower().split()).encode()).hexdigest()


class ClaimExtractor:
    def __init__(self, db: PostgresClient, harness: ChatHarness, tools: AgentTools, skill: Path,
                 timeout: float = 10 * 60):
        self.db = db
        self.harness = harness
        self.tools = tools
        self.skill = skill
        self.timeout = timeout

    async def extract(self, episode_id: UUID) -> str:
        """Run one Codex turn over the episode; return its thread ID for inspection."""
        async def call_tool(name: str, arguments: dict) -> str:
            if name == "write_claim":
                return await self.write_claim(episode_id, arguments)
            return await self.tools.call(name, arguments)

        return await self.harness.run_task(f"Extract every claim from episode {episode_id}.", self.skill,
                                           tool_specs("postgres_database_query", "write_claim"),
                                           call_tool, self.timeout)

    async def write_claim(self, episode_id: UUID, claim: dict) -> str:
        """Attach the claim to the first passage that contains its quote (whitespace-collapsed)."""
        quote = " ".join(claim["evidence_quote"].split())
        passage_id = await self.db.find_passage_containing(episode_id, quote)
        if passage_id is None:
            raise ValueError("evidence_quote does not appear verbatim in this episode's transcript. Copy it exactly.")
        saved = await self.db.save_claim(
            passage_id, claim["text"], quote, claim.get("speaker"), fingerprint(claim["text"], quote),
            [(entity["name"].strip(), entity["kind"]) for entity in claim["entities"]])
        return "Saved." if saved else "Already saved."
