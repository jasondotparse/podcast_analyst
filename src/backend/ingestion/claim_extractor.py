"""Claim extraction as a Codex thread: it reads the episode with read-only SQL and
saves claims through write_claim. See docs/etl_design.md#claim-extraction-with-codex."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

from ..chat import ChatHarness
from ..clients.postgres_client import PostgresClient


QUERY_ROWS, QUERY_CHARS = 200, 40_000

TOOLS = [
    {"type": "function", "name": "postgres_database_query",
     "description": f"Run one read-only PostgreSQL query against the podcast database. Returns JSON rows "
                    f"(at most {QUERY_ROWS} rows and {QUERY_CHARS} characters; longer output is cut off).",
     "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}},
                     "required": ["sql"], "additionalProperties": False}},
    {"type": "function", "name": "write_claim",
     "description": "Save one claim from the episode you are extracting. evidence_quote must be copied verbatim "
                    "from the transcript.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "required": ["text", "evidence_quote", "speaker", "entities"],
                     "properties": {
                         "text": {"type": "string", "description": "The claim, self-contained."},
                         "evidence_quote": {"type": "string", "description": "Verbatim transcript text, at most 250 words."},
                         "speaker": {"type": ["string", "null"], "description": "Who made the claim, if the transcript shows it."},
                         "entities": {"type": "array", "items": {
                             "type": "object", "additionalProperties": False, "required": ["name", "kind"],
                             "properties": {"name": {"type": "string"},
                                            "kind": {"type": "string", "enum": ["company", "person", "technology", "concept"]}}}},
                     }}},
]


def fingerprint(text: str, quote: str) -> str:
    return hashlib.sha256(" ".join(f"{text} {quote}".lower().split()).encode()).hexdigest()


class ClaimExtractor:
    def __init__(self, db: PostgresClient, harness: ChatHarness, skill: Path, timeout: float = 10 * 60):
        self.db = db
        self.harness = harness
        self.skill = skill
        self.timeout = timeout

    async def extract(self, episode_id: UUID) -> str:
        """Run one Codex turn over the episode; return its thread ID for inspection."""
        async def call_tool(name: str, arguments: dict) -> str:
            if name == "postgres_database_query":
                return await self.query(arguments["sql"])
            if name == "write_claim":
                return await self.write_claim(episode_id, arguments)
            raise ValueError(f"Unknown tool {name}")

        return await self.harness.run_task(f"Extract every claim from episode {episode_id}.",
                                           self.skill, TOOLS, call_tool, self.timeout)

    async def query(self, sql: str) -> str:
        result = await self.db.read_only_query(sql, QUERY_ROWS)
        output = result if isinstance(result, str) else json.dumps(result, default=str)
        return output if len(output) <= QUERY_CHARS else output[:QUERY_CHARS] + "\n[output cut off; select less]"

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
