"""Scheduled thesis digests (alpha): one consolidated email per cron occurrence.

See docs/etl_design.md#scheduled-thesis-digests. Off unless PODCAST_ENABLE_SCHEDULED_DIGESTS=1. A digest
reports the supporting and challenging thesis assessments recorded between the previous scheduled occurrence
and this one, read from Postgres at delivery time. Delivery is best effort: next_run_at advances before the
send, a failed send is not retried, and occurrences missed while the app was down are skipped, not backfilled.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from functools import cache
import logging
from uuid import UUID
from zoneinfo import ZoneInfo, available_timezones

from cronsim import CronSim, CronSimError

from ..clients.email_client import EmailClient
from ..clients.postgres_client import PostgresClient
from .models import CronSubscription


log = logging.getLogger(__name__)

TICK_SECONDS = 60  # How often the loop looks for due digests.
MISSED_AFTER = timedelta(minutes=10)  # A due occurrence later than this (the app was down) is skipped.
MAX_DIGEST_CLAIMS = 5  # Detailed claims per thesis; the counts still cover every claim.
RULE = "-" * 40


@cache
def _zones() -> dict[str, str]:
    """Lower-cased IANA names → canonical spelling."""
    return {name.lower(): name for name in available_timezones()}


def parse_schedule(cron: str, tz: str) -> tuple[str, str]:
    """Validate a five-field cron expression and an IANA timezone; return both normalized."""
    cron = " ".join(cron.split())
    zone = _zones().get(tz.strip().lower())
    if zone is None:
        raise ValueError(f"Unknown timezone {tz!r}: use an IANA name such as 'Asia/Dubai' or 'America/Chicago'.")
    if len(cron.split()) != 5:
        raise ValueError(f"Invalid cron expression {cron!r}: use five fields (minute hour day-of-month month "
                         "day-of-week), e.g. '0 14 * * *' for every day at 2 PM.")
    try:
        next(CronSim(cron, datetime.now(ZoneInfo(zone))))
    except (CronSimError, StopIteration) as error:
        raise ValueError(f"Invalid cron expression {cron!r}: {error or 'it never runs'}.") from None
    return cron, zone


def next_run(cron: str, tz: str, after: datetime) -> datetime:
    """The first occurrence strictly after `after`, in UTC. The cron is evaluated in local time, so a daily
    2 PM stays 2 PM across daylight saving changes (a skipped local time runs just after the jump)."""
    return next(CronSim(cron, after.astimezone(ZoneInfo(tz)))).astimezone(timezone.utc)


def previous_run(cron: str, tz: str, before: datetime) -> datetime:
    """The last occurrence strictly before `before`, in UTC: the start of the window ending at `before`."""
    return next(CronSim(cron, before.astimezone(ZoneInfo(tz)), reverse=True)).astimezone(timezone.utc)


def local_time(moment: datetime, tz: str) -> str:
    """'Nov 14, 2026 2:00 PM' in the subscriber's timezone."""
    moment = moment.astimezone(ZoneInfo(tz))
    return (f"{moment:%b} {moment.day}, {moment.year} "
            f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}")


def schedule_label(cron: str) -> str:
    """'Daily' or 'Weekly' for the common shapes, else 'Scheduled'."""
    minute, hour, day, month, weekday = cron.split()
    if not (minute.isdigit() and hour.isdigit() and day == month == "*"):
        return "Scheduled"
    if weekday == "*":
        return "Daily"
    return "Weekly" if weekday.isdigit() or weekday.isalpha() else "Scheduled"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def render_digest(subscription: CronSubscription, theses: list[tuple[UUID, str]], evidence: list[dict],
                  window_start: datetime, window_end: datetime, app_url: str) -> tuple[str, str]:
    """Plain text only: transcript quotes must never become HTML. Every thesis appears; theses with the strongest
    evidence come first, and each lists its strongest claims first (strength, then signal)."""
    tz = subscription.timezone
    by_thesis = {thesis_id: [] for thesis_id, _ in theses}
    for row in evidence:
        by_thesis[row["thesis_id"]].append(row)

    def importance(row):
        return -row["strength"], -(row["signal"] if row["signal"] is not None else -1), row["text"]

    def strongest_first(thesis):  # Stable: theses without evidence keep their order, last.
        rows = by_thesis[thesis[0]]
        return (0, *importance(rows[0])) if rows else (1,)

    for rows in by_thesis.values():
        rows.sort(key=importance)
    ordered = sorted(theses, key=strongest_first)
    label = schedule_label(subscription.cron)
    with_evidence = sum(1 for thesis_id, _ in theses if by_thesis[thesis_id])
    claims = sum(len(by_thesis[thesis_id]) for thesis_id, _ in theses)

    lines = [f"Podcast Analyst — {label} Thesis Digest",
             f"{local_time(window_start, tz)} to {local_time(window_end, tz)} ({tz})", "",
             f"{plural(len(theses), 'thesis', 'theses')} monitored",
             f"{plural(with_evidence, 'thesis', 'theses')} with new evidence ({plural(claims, 'new claim')})", "",
             "Claims are summarized from the quoted podcast transcript and attributed to a speaker only when the "
             "transcript shows one. Stance, strength, and signal are Podcast Analyst's automated assessments, not "
             "the speaker's words.", ""]
    for thesis_id, description in ordered:
        rows = by_thesis[thesis_id]
        lines.extend([RULE, "", f"THESIS: {description}", ""])
        if not rows:
            lines.extend(["No new relevant evidence during this period.", ""])
            continue
        supporting = sum(1 for row in rows if row["stance"] == "supports")
        lines.extend([f"{plural(supporting, 'new supporting claim')}",
                      f"{plural(len(rows) - supporting, 'new challenging claim')}", ""])
        for index, row in enumerate(rows[:MAX_DIGEST_CLAIMS], 1):
            signal = f"{row['signal']}/100" if row["signal"] is not None else "not ranked yet"
            lines.extend([
                f"{index}. {row['text']}",
                f"   Assessment: {'Supports' if row['stance'] == 'supports' else 'Challenges'} · "
                f"strength {row['strength']}/100 · signal {signal}",
                f"   Speaker: {row['speaker'] or 'not identified in the transcript'}",
                f"   Evidence: \"{row['evidence_quote']}\"",
                f"   Source: {row['podcast']} · {row['episode']} · {row['published_at'] or 'date unknown'}"])
            if row["source_url"]:
                lines.append(f"   {row['source_url']}")
            lines.append("")
        if len(rows) > MAX_DIGEST_CLAIMS:
            lines.extend([f"   …and {len(rows) - MAX_DIGEST_CLAIMS} more in Podcast Analyst.", ""])
    lines.extend([RULE, "", "Review the evidence and ask the agent about it:", f"{app_url.rstrip('/')}/app/", "",
                  f"To stop this digest, ask the agent to cancel scheduled digest {subscription.id}."])
    end = window_end.astimezone(ZoneInfo(tz))
    subject = f"Podcast Analyst {label.lower()} thesis digest: {end:%b} {end.day}, {end.year}"
    return subject, "\n".join(lines)


class DigestService:
    def __init__(self, db: PostgresClient, email: EmailClient, app_url: str, now=lambda: datetime.now(timezone.utc)):
        self.db, self.email, self.app_url = db, email, app_url
        self.now = now  # Injected so tests can control the clock.

    async def register(self, email: str, thesis_ids: list[UUID], cron: str, tz: str) -> dict:
        """Validate and save a schedule, or return the identical active one. Also reports this address's
        immediate subscriptions to the same theses, so the agent can offer to remove them."""
        cron, tz = parse_schedule(cron, tz)
        ids = sorted(set(thesis_ids), key=str)
        theses = await self.db.active_theses(ids)
        invalid = set(ids) - {thesis_id for thesis_id, _ in theses}
        if invalid:
            raise ValueError(f"Unknown or inactive thesis IDs: {', '.join(sorted(map(str, invalid)))}")
        subscription, created = await self.db.add_cron_subscription(email, cron, tz, ids,
                                                                    next_run(cron, tz, self.now()))
        if created:
            log.info("Scheduled digest %s registered: %r in %s for %d theses", subscription.id, cron, tz, len(ids))
        first = subscription.next_run_at
        return {
            "status": "registered" if created else "already_registered",
            "subscription_id": subscription.id, "email": email, "cron": cron, "timezone": tz,
            "theses": [{"thesis_id": thesis_id, "description": description} for thesis_id, description in theses],
            "next_run_local": f"{local_time(first, tz)} ({tz})",
            "next_run_at_utc": first.astimezone(timezone.utc).isoformat(),
            "next_digest_covers": f"evidence recorded from {local_time(previous_run(cron, tz, first), tz)} "
                                  f"to {local_time(first, tz)} ({tz})",
            "overlapping_immediate_subscriptions": await self.db.immediate_subscriptions(email, ids),
        }

    async def cancel(self, subscription_id: UUID) -> dict:
        row = await self.db.cancel_cron_subscription(subscription_id)
        if row is None:
            raise ValueError(f"No scheduled digest has ID {subscription_id}; look it up in cron_subscriptions.")
        if row.pop("was_active"):
            log.info("Scheduled digest %s cancelled", subscription_id)
            return {"status": "cancelled", **row}
        return {"status": "already_cancelled", **row}

    async def run(self) -> None:
        """The background loop. A failure is logged and never stops it."""
        log.info("Scheduled thesis digests enabled; checking every %d s", TICK_SECONDS)
        while True:
            try:
                await self.deliver_due()
            except Exception:
                log.exception("Scheduled digest check failed")
            await asyncio.sleep(TICK_SECONDS)

    async def deliver_due(self) -> None:
        now = self.now()
        for subscription in await self.db.due_cron_subscriptions(now):
            try:  # One failed digest must not hold up the others.
                await self.deliver(subscription, now)
            except Exception:
                log.exception("Scheduled digest %s failed", subscription.id)

    async def deliver(self, subscription: CronSubscription, now: datetime) -> None:
        cron, tz = subscription.cron, subscription.timezone
        following = next_run(cron, tz, now)
        due = previous_run(cron, tz, following)  # The latest due occurrence; any earlier ones were missed.
        missed = now - due > MISSED_AFTER
        # Advance before sending, so a failed or repeated send can never repeat this occurrence.
        if not await self.db.advance_cron_subscription(subscription.id, subscription.next_run_at, following,
                                                       "skipped" if missed else None):
            return  # Cancelled or already advanced.
        if missed:
            log.warning("Skipped scheduled digest %s due %s: the app was not running then; next run %s",
                        subscription.id, subscription.next_run_at.isoformat(), following.isoformat())
            return
        theses = await self.db.active_theses(subscription.thesis_ids)
        if not theses:
            await self.db.set_cron_delivery(subscription.id, "skipped")
            log.warning("Skipped scheduled digest %s: none of its theses are active", subscription.id)
            return
        start = previous_run(cron, tz, due)
        evidence = await self.db.digest_evidence([thesis_id for thesis_id, _ in theses], start, due)
        subject, body = render_digest(subscription, theses, evidence, start, due, self.app_url)
        if not self.email.enabled:
            await self.db.set_cron_delivery(subscription.id, "disabled")
            log.info("Email disabled: scheduled digest %s not sent: %s\n%s", subscription.id, subject, body)
            return
        try:
            await self.email.send(subscription.email, subject, body)
        except Exception:
            await self.db.set_cron_delivery(subscription.id, "failed")
            log.exception("Scheduled digest %s could not be sent; this occurrence is not retried", subscription.id)
            return
        await self.db.set_cron_delivery(subscription.id, "sent", self.now())
        log.info("Sent scheduled digest %s: %d claims for %d theses, %s to %s", subscription.id, len(evidence),
                 len(theses), start.isoformat(), due.isoformat())
