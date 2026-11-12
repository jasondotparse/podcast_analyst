"""Snapshot qualifying challenges and subscribers, then send unsent episode alerts."""

import logging
from uuid import UUID

from ..clients.email_client import EmailClient
from ..clients.postgres_client import PostgresClient


log = logging.getLogger(__name__)
ALERT_MIN_STRENGTH = 75  # Email requires a direct challenge to the thesis's core premise.
ALERT_MIN_SIGNAL = 60  # Placeholder: tune against observed signal distributions.
MAX_EMAIL_CLAIMS = 10  # Keep the email readable; the app holds the remaining evidence.


def render_alert(thesis: str, claims: list[dict], app_url: str) -> tuple[str, str]:
    """Plain text only: transcript quotes must never become HTML."""
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


class Notifier:
    def __init__(self, db: PostgresClient, email: EmailClient, app_url: str):
        self.db, self.email, self.app_url = db, email, app_url

    async def notify(self, episode_id: UUID) -> None:
        await self.db.create_alerts(episode_id, ALERT_MIN_STRENGTH, ALERT_MIN_SIGNAL)
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
