"""Assess missing claim/thesis pairs with Decisions; Postgres holds every answer, including unrelated."""

import asyncio
import logging
from uuid import UUID

from openai import AsyncOpenAI

from ..clients.postgres_client import PostgresClient
from .claim_ranker import CONCURRENCY, MODEL, TOP_LEVEL, levels, scorer_input
from .models import Claim


log = logging.getLogger(__name__)


class ThesisReflector:
    def __init__(self, db: PostgresClient, openai: AsyncOpenAI):
        self.db = db
        self.openai = openai

    async def reflect(self, episode_id: UUID | None = None, limit: int | None = None) -> list[UUID]:
        """One call per claim, with two questions per missing active thesis; never writes the graph."""
        claims = await self.db.unassessed_claims(episode_id, limit)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def assess(row):
            claim_episode, claim_id, text, quote, speaker = row
            async with semaphore:
                theses = await self.db.missing_theses(claim_id)
                if not theses:
                    return
                questions = []
                for thesis_id, description in theses:
                    questions.extend([
                        {"name": f"stance:{thesis_id}", "type": "choice", "instructions":
                         f"Thesis: {description}\nIf this claim is true, does it make the thesis more likely "
                         "(supports), less likely (challenges), or neither (unrelated)? It challenges the thesis "
                         "if it contradicts the expected outcome, undermines the stated driver, or pushes out "
                         "the timeline. A claim on the same topic that neither helps nor hurts is unrelated.",
                         "choices": [{"value": s} for s in ("supports", "challenges", "unrelated")]},
                        {"name": f"strength:{thesis_id}", "type": "score", "instructions":
                         f"Thesis: {description}\nHow strongly does the claim bear on this thesis, "
                         "in whichever direction?", "levels": levels(
                             "No bearing.", "Tangential: same industry or topic, indirect.",
                             "Bears on a contributing factor.", "Bears directly on the core driver or premise.",
                             "Directly addresses its predicted outcome or timeline; the analyst must revisit it.")},
                    ])
                episode = await self.db.get_episode(claim_episode)
                decision = await self.openai.decisions.create(model=MODEL, questions=questions,
                    input=scorer_input(episode, Claim(claim_id, text, quote, speaker)))
                answers = {answer.name: answer for answer in decision.answers}
                rows = []
                for thesis_id, _ in theses:
                    stance = getattr(answers[f"stance:{thesis_id}"], "choice", None)
                    score = getattr(answers[f"strength:{thesis_id}"], "score", None)
                    if stance is None or score is None:
                        log.warning("Decisions refused an assessment for claim %s / thesis %s", claim_id, thesis_id)
                    rows.append((claim_id, thesis_id, stance or "unrelated",
                                 round(min(max(score or 0, 0), TOP_LEVEL) / TOP_LEVEL * 100)))
                await self.db.save_assessments(rows)
                return claim_id

        return [claim_id for claim_id in await asyncio.gather(*(assess(row) for row in claims)) if claim_id]
