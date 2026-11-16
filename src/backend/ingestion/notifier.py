"""Snapshot qualifying thesis challenges/question answers and subscribers, then send episode alerts."""

import logging
from uuid import UUID

from ..clients.email_client import EmailClient
from ..clients.postgres_client import PostgresClient


log = logging.getLogger(__name__)
ALERT_MIN_STRENGTH = 75  # Email requires a direct challenge to the thesis's core premise.
ALERT_MIN_SIGNAL = 60  # Placeholder: tune against observed signal distributions.
QUESTION_ALERT_MIN_STRENGTH = 75  # Level 3 is direct evidence on an open question, whichever side it favors.
MAX_EMAIL_CLAIMS = 10  # Keep the email readable; the app holds the remaining evidence.


def render_alert(thesis: str, claims: list[dict], app_url: str) -> tuple[str, str]:
    """Plain text only: transcript quotes must never become HTML."""
    if any(c.get('question_id') for c in claims):
        return render_coverage_alert(thesis, claims, app_url)
    lines = ["New podcast evidence may challenge your thesis:", thesis, ""]
    for index, claim in enumerate(claims[:MAX_EMAIL_CLAIMS], 1):
        lines.extend([f"{index}. {claim['text']}", f"Speaker: {claim['speaker'] or 'unknown'}",
                      f"Quote: \"{claim['evidence_quote']}\"",
                      f"{claim['podcast']} · {claim['episode']} · {claim['published_at'] or 'date unknown'}"])
        if claim['source_url']:
            lines.append(claim['source_url'])
        lines.extend([f"Signal: {claim['signal']}/100; challenge strength: {claim['strength']}/100", ""])
    lines.append(f"Review the evidence and ask the agent about it: {app_url.rstrip('/')}/app/")
    return "Podcast Analyst: new evidence challenges your thesis", "\n".join(lines)


def render_coverage_alert(thesis: str, claims: list[dict], app_url: str) -> tuple[str, str]:
    """Arrange stored facts; assign each claim to its strongest question before thesis-only hits."""
    ordered = sorted(claims, key=lambda c: (-c['strength'], -c['signal'], str(c['claim_id']),
                                           str(c.get('question_id') or '')))
    selected = {}
    for claim in [c for c in ordered if c.get('question_id')] + [c for c in ordered if not c.get('question_id')]:
        if len(selected) < MAX_EMAIL_CLAIMS:
            selected.setdefault(claim['claim_id'], claim)
    groups, challenges = {}, []
    for claim in selected.values():
        if claim.get('question_id'):
            groups.setdefault((claim['debate_id'], claim['debate_label'], claim['debate_title']), {}).setdefault(
                claim['question_id'], []).append(claim)
        else:
            challenges.append(claim)
    ticker = next(c['ticker'] for c in claims if c.get('question_id'))
    lines = [f"New podcast evidence on your {ticker} coverage:", "Working thesis:", thesis, ""]
    for (_, label, title), questions in groups.items():
        lines.extend([f"{label} · {title}", ""])
        for evidence in questions.values():
            lines.extend([f"Question: {evidence[0]['question']}",
                          f"Model drivers: {', '.join(evidence[0]['drivers'])}", ""])
            for claim in evidence:
                lines.extend([claim['text'], f"Speaker: {claim['speaker'] or 'unknown'}",
                              f"Quote: \"{claim['evidence_quote']}\"",
                              f"{claim['podcast']} · {claim['episode']} · {claim['published_at'] or 'date unknown'}"])
                if claim['source_url']:
                    lines.append(claim['source_url'])
                lean = {'supports': 'our view', 'challenges': 'the counter view', 'neutral': 'neither side'}[claim['stance']]
                lines.extend([f"Signal: {claim['signal']}/100; question strength: {claim['strength']}/100",
                              f"Leans toward: {lean}", ""])
    if challenges:
        # Reuse today's exact thesis-challenge layout, with a single closing app link.
        _, body = render_alert(thesis, challenges, app_url)
        lines.extend(body.splitlines()[:-1])
    lines.append(f"Review the evidence and ask the agent about it: {app_url.rstrip('/')}/app/")
    return f"Podcast Analyst: new evidence on your {ticker} coverage", "\n".join(lines)


class Notifier:
    def __init__(self, db: PostgresClient, email: EmailClient, app_url: str):
        self.db, self.email, self.app_url = db, email, app_url

    async def notify(self, episode_id: UUID) -> None:
        await self.db.create_alerts(episode_id, ALERT_MIN_STRENGTH, ALERT_MIN_SIGNAL, QUESTION_ALERT_MIN_STRENGTH)
        for alert_id, thesis, recipients, matches in await self.db.unsent_alerts(episode_id):
            subject, body = render_alert(thesis, await self.db.alert_claims(matches), self.app_url)
            if not self.email.enabled:
                log.info("Email disabled: %s\n%s", subject, body)
                continue
            try:
                for recipient in recipients:
                    await self.email.send(recipient, subject, body)
            except Exception:
                await self.db.set_alert_status(alert_id, "failed")
                raise
            await self.db.set_alert_status(alert_id, "sent")
