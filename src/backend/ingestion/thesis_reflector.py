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
        """One call per claim, with two judgments per missing active thesis/question; Postgres only."""
        claims = await self.db.unassessed_claims(episode_id, limit)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def assess(row):
            claim_episode, claim_id, text, quote, speaker = row
            async with semaphore:
                theses = await self.db.missing_theses(claim_id)
                research = await self.db.missing_questions(claim_id)
                if not theses and not research:
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
                for question_id, question_text, title, our_view, counter_view, company, ticker in research:
                    context = (f"Company: {company} ({ticker})\nDebate: {title}\nOur view: {our_view}\n"
                               f"Counter view: {counter_view}\nResearch question: {question_text}\n")
                    questions.extend([
                        {"name": f"question_stance:{question_id}", "type": "choice", "instructions": context +
                         "If this claim is true, which side of this debate does it favor, as it bears on this "
                         "research question: our view (supports), the counter view (challenges), or neither "
                         "(neutral)? Compare the claim with BOTH parent-debate views from the covered company's "
                         "perspective, not the speaker's or customer's perspective. supports means our_view is "
                         "more likely than counter_view; challenges means counter_view is more likely than our_view. "
                         "Do not label agreement with the research question's premise as supports: a yes answer to "
                         "a question about risks to the company can favor counter_view. For example, if our_view is "
                         "durable supplier pricing/profits and counter_view is buyer bargaining power eroding returns: "
                         "customer discounts, financing credits and flexibility favor counter_view (challenges); "
                         "binding minimum purchases and termination payments protecting the supplier favor our_view "
                         "(supports). Contract size alone does not establish either side. A claim that informs the "
                         "question without favoring either side, or balances both sides, is neutral.",
                         "choices": [{"value": s} for s in ("supports", "challenges", "neutral")]},
                        {"name": f"question_strength:{question_id}", "type": "score", "instructions": context +
                         "How much does this claim help answer the research question, whichever way it points?",
                         "levels": levels("No bearing on the question.",
                             "Tangential: same topic, but it does not help answer the question.",
                             "Indirect: bears on the question through a related company, segment, or trend.",
                             "Direct evidence on the question, but partial or qualitative.",
                             "Directly answers the question with specific evidence: figures, terms, or timelines.")},
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
                if rows:
                    await self.db.save_assessments(rows)
                rows = []
                for question_id, *_ in research:
                    stance = getattr(answers[f"question_stance:{question_id}"], "choice", None)
                    score = getattr(answers[f"question_strength:{question_id}"], "score", None)
                    if stance is None or score is None:
                        log.warning("Decisions refused claim %s / question %s", claim_id, question_id)
                        stance, score = "neutral", 0
                    rows.append((claim_id, question_id, stance,
                                 round(min(max(score, 0), TOP_LEVEL) / TOP_LEVEL * 100)))
                if rows:
                    await self.db.save_question_assessments(rows)
                return claim_id

        return [claim_id for claim_id in await asyncio.gather(*(assess(row) for row in claims)) if claim_id]
