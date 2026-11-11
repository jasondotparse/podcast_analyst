"""Claim ranking: score each claim's investment signal with one Decisions API call.
See docs/etl_design.md#claim-ranking.

Three `score` questions rate the claim against anchored 0-4 levels; each becomes a 0-100
component, and `signal` is their mean, discounted when no speaker is attributed. The
components are stored too, so the formula can change without calling the API again.
"""

import asyncio
import logging
from uuid import UUID

from openai import AsyncOpenAI

from ..clients.postgres_client import PostgresClient
from .models import Claim, Episode, Signal


log = logging.getLogger(__name__)

MODEL = "gpt-6-luna"
CONCURRENCY = 8  # Claims scored at once; the Decisions API is fast, so this is per-episode seconds.
UNATTRIBUTED_FACTOR = 0.75  # Applied to the signal of a claim with no speaker.


def levels(*descriptions: str) -> list[dict]:
    """Five anchored levels, index 0-4; the API returns the probability-weighted index."""
    return [{"label": str(index), "description": text} for index, text in enumerate(descriptions)]


RUBRIC = [
    {"name": "specificity", "type": "score", "instructions":
        "How specific, and therefore checkable, is the claim? A specific claim names a measurable "
        "quantity, a timeframe, and the particular product, metric, company, or counterparty it is "
        "about, so it can be verified now or falsified later.",
     "levels": levels(
        "Vague: sentiment or a generality with no measurable content (\"demand is strong\").",
        "Directional: a direction or comparison, but no magnitude, timeframe, or named subject.",
        "Partly specific: one of a magnitude or figure, a timeframe, or a named product, metric, or counterparty.",
        "Mostly specific: two of those three.",
        "Fully specific: quantified, time-bound, and about a named subject; checkable against a datapoint.")},
    {"name": "materiality", "type": "score", "instructions":
        "If the claim were true, how much would it matter to a public-equities analyst's estimates or "
        "view of an identifiable company or sector? Judge the bearing on revenue, cost, margin, share, "
        "capital allocation, or valuation, not how interesting the claim is.",
     "levels": levels(
        "Immaterial: no bearing on any company's results (anecdote, trivia, pleasantry).",
        "Background: industry or technology context an analyst already assumes; would not change an estimate.",
        "Relevant: touches a driver (demand, pricing, capacity, competition, regulation, adoption) for "
        "identifiable public companies, but indirectly or at small magnitude.",
        "Material: would plausibly change an estimate or view on an identifiable public company or sector.",
        "Highly material: directly bears on a key debate or earnings driver for a public company, such as a "
        "specific forecast, order, price change, share shift, or capital decision.")},
    {"name": "source_standing", "type": "score", "instructions":
        "How well positioned is the speaker to know this, and is the statement first-hand? Use the "
        "Speaker and Guests fields and the wording of the evidence quote (\"we are seeing\" is first-hand; "
        "\"I read that\" is hearsay). Score 0 when the speaker is unknown and the quote gives no basis.",
     "levels": levels(
        "No standing: unknown speaker, or relaying something read or heard with no stated basis.",
        "Commentator: host, journalist, or generalist giving an opinion without direct exposure to the subject.",
        "Informed observer: analyst, academic, or adjacent industry participant with domain expertise but "
        "second-hand visibility.",
        "Direct participant: operator, executive, engineer, customer, or supplier with first-hand exposure, "
        "speaking generally or about others.",
        "First-hand on their own business: a direct participant reporting what their own organization is "
        "seeing, doing, or expects.")},
]
AXES = [question["name"] for question in RUBRIC]
TOP_LEVEL = len(RUBRIC[0]["levels"]) - 1


def signal_score(components: dict[str, int], attributed: bool) -> Signal:
    """The mean of the three 0-100 components, times UNATTRIBUTED_FACTOR when no speaker is known."""
    mean = sum(components[axis] for axis in AXES) / len(AXES)
    return Signal(**components, signal=round(mean * (1 if attributed else UNATTRIBUTED_FACTOR)))


def scorer_input(episode: Episode, claim: Claim) -> str:
    return "\n".join([
        f"Podcast: {episode.podcast_name}",
        f"Episode: {episode.title}" + (f" ({episode.published_at})" if episode.published_at else ""),
        f"Episode description: {episode.description or ''}",
        f"Guests: {', '.join(episode.guests) if episode.guests else 'not listed'}",
        f"Speaker: {claim.speaker or 'unknown'}",
        f"Claim: {claim.text}",
        f"Evidence quote: \"{claim.evidence_quote}\"",
    ])


class ClaimRanker:
    def __init__(self, db: PostgresClient, openai: AsyncOpenAI):
        self.db = db
        self.openai = openai

    async def rank(self, episode_id: UUID) -> None:
        """Score this episode's unranked claims (signal IS NULL) and save their scores."""
        claims = await self.db.unranked_claims(episode_id)
        if not claims:
            return
        episode = await self.db.get_episode(episode_id)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def rank_one(claim: Claim) -> None:
            async with semaphore:
                await self.db.set_claim_signal(claim.claim_id, await self.score(episode, claim))

        await asyncio.gather(*(rank_one(claim) for claim in claims))

    async def score(self, episode: Episode, claim: Claim) -> Signal:
        """One Decisions call answering every rubric question about the claim."""
        decision = await self.openai.decisions.create(model=MODEL, questions=RUBRIC,
                                                      input=scorer_input(episode, claim))
        components = {}
        for question, answer in zip(RUBRIC, decision.answers):
            score = getattr(answer, "score", None)
            if score is None:  # A refusal scores the axis 0 rather than failing the episode.
                log.warning("Decisions refused %s for claim %s", question["name"], claim.claim_id)
                score = 0
            components[question["name"]] = round(min(max(score, 0), TOP_LEVEL) / TOP_LEVEL * 100)
        return signal_score(components, attributed=bool(claim.speaker))
