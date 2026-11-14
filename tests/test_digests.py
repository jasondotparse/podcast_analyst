"""Scheduled thesis digests (alpha): schedules, rendering, the delivery worker, and the chat tools."""

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

from src.backend.agent_tools import CHAT_TOOLS, DIGEST_TOOLS, AgentTools
from src.backend.clients.postgres_client import PostgresClient
from src.backend.codex_config import tool_specs
from src.backend.db.db import migrate_postgres
from src.backend.ingestion.digest_service import (MAX_DIGEST_CLAIMS, DigestService, next_run, parse_schedule,
                                                  previous_run, render_digest, schedule_label)
from src.backend.ingestion.models import CronSubscription
from tests.test_db import DATABASE_URL


UTC = timezone.utc


def at(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class ScheduleTests(unittest.TestCase):
    def test_daily_dubai_schedule_runs_at_2_pm_local_and_is_normalized(self):
        self.assertEqual(parse_schedule(" 0  14 * * * ", "asia/dubai"), ("0 14 * * *", "Asia/Dubai"))
        self.assertEqual(next_run("0 14 * * *", "Asia/Dubai", at(2026, 11, 13, 9)), at(2026, 11, 13, 10))
        self.assertEqual(next_run("0 14 * * *", "Asia/Dubai", at(2026, 11, 13, 10)), at(2026, 11, 14, 10))
        self.assertEqual(previous_run("0 14 * * *", "Asia/Dubai", at(2026, 11, 14, 10)), at(2026, 11, 13, 10))

    def test_weekly_schedule_uses_the_previous_weekly_occurrence(self):
        # Mondays at 9 AM Chicago (CST, UTC-6); November 13, 2026 is a Friday.
        self.assertEqual(next_run("0 9 * * 1", "America/Chicago", at(2026, 11, 13, 12)), at(2026, 11, 16, 15))
        self.assertEqual(previous_run("0 9 * * 1", "America/Chicago", at(2026, 11, 16, 15)), at(2026, 11, 9, 15))

    def test_invalid_cron_expressions_and_timezones_are_rejected(self):
        for cron in ("61 * * * *", "0 0 30 2 *", "0 0 * * * *", "@daily", "", "every day"):
            with self.assertRaisesRegex(ValueError, "Invalid cron expression"):
                parse_schedule(cron, "UTC")
        for tz in ("Mars/Olympus", "Dubai", "", "../etc/passwd"):
            with self.assertRaisesRegex(ValueError, "Unknown timezone"):
                parse_schedule("0 14 * * *", tz)

    def test_daylight_saving_transitions_keep_local_time_and_windows(self):
        chicago = "America/Chicago"
        # Fall back (Nov 1, 2026): 9 AM is 14:00 UTC in CDT and 15:00 UTC in CST, so that window is 25 hours.
        self.assertEqual(next_run("0 9 * * *", chicago, at(2026, 10, 31, 20)), at(2026, 11, 1, 15))
        self.assertEqual(previous_run("0 9 * * *", chicago, at(2026, 11, 1, 15)), at(2026, 10, 31, 14))
        # Spring forward (Mar 14, 2027): 2:30 AM does not exist, so it runs at 3:00 AM CDT, then 2:30 again.
        self.assertEqual(next_run("30 2 * * *", chicago, at(2027, 3, 13, 18)), at(2027, 3, 14, 8))
        self.assertEqual(next_run("30 2 * * *", chicago, at(2027, 3, 14, 8)), at(2027, 3, 15, 7, 30))
        self.assertEqual(previous_run("30 2 * * *", chicago, at(2027, 3, 15, 7, 30)), at(2027, 3, 14, 8))

    def test_schedule_labels(self):
        self.assertEqual([schedule_label(c) for c in ("0 14 * * *", "0 9 * * 1", "0 9 * * MON", "0 9 * * 1-5",
                                                      "0 */6 * * *", "0 9 1 * *")],
                         ["Daily", "Weekly", "Weekly", "Scheduled", "Scheduled", "Scheduled"])


def evidence(thesis_id, text, stance="supports", strength=80, signal=70, **overrides):
    return {"thesis_id": thesis_id, "stance": stance, "strength": strength, "claim_id": uuid4(), "text": text,
            "evidence_quote": f"quote: {text}", "speaker": "Jane Doe", "signal": signal, "podcast": "Chips",
            "episode": "Optics", "published_at": date(2026, 11, 10), "source_url": "https://example.com/ep",
            **overrides}


class RenderDigestTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c = uuid4(), uuid4(), uuid4()
        self.theses = [(self.a, "Thesis A"), (self.b, "Thesis B"), (self.c, "Thesis C")]
        self.subscription = CronSubscription(uuid4(), "analyst@example.com", "0 14 * * *", "Asia/Dubai",
                                             [self.a, self.b, self.c], at(2026, 11, 14, 10))

    def render(self, rows):
        return render_digest(self.subscription, self.theses, rows, at(2026, 11, 13, 10), at(2026, 11, 14, 10),
                             "https://app.example.com/")

    def test_every_thesis_appears_strongest_evidence_first_with_capped_details_and_exact_counts(self):
        rows = ([evidence(self.b, f"B support {i}", strength=50 + i) for i in range(4)]
                + [evidence(self.b, f"B challenge {i}", "challenges", strength=40 + i) for i in range(3)]
                + [evidence(self.a, "A top", "challenges", strength=95, signal=None)])
        subject, body = self.render(rows)
        self.assertEqual(subject, "Podcast Analyst daily thesis digest: Nov 14, 2026")
        self.assertIn("Nov 13, 2026 2:00 PM to Nov 14, 2026 2:00 PM (Asia/Dubai)", body)
        self.assertIn("3 theses monitored\n2 theses with new evidence (8 new claims)", body)
        # Theses with the strongest evidence first; the one without evidence last, explicitly marked.
        self.assertLess(body.index("THESIS: Thesis A"), body.index("THESIS: Thesis B"))
        self.assertLess(body.index("THESIS: Thesis B"), body.index("THESIS: Thesis C"))
        self.assertIn("THESIS: Thesis C\n\nNo new relevant evidence during this period.", body)
        # Counts cover all seven of B's claims; only five are detailed, strongest first.
        self.assertIn("4 new supporting claims\n3 new challenging claims", body)
        self.assertIn("0 new supporting claims\n1 new challenging claim\n", body)
        b_section = body[body.index("THESIS: Thesis B"):body.index("THESIS: Thesis C")]
        self.assertEqual(sum(f"\n{i}. " in b_section for i in range(1, 8)), MAX_DIGEST_CLAIMS)
        self.assertLess(b_section.index("B support 3"), b_section.index("B support 0"))
        self.assertNotIn("B challenge 0", b_section)
        self.assertIn("…and 2 more in Podcast Analyst.", b_section)
        self.assertIn("Assessment: Challenges · strength 95/100 · signal not ranked yet", body)

    def test_claims_carry_attribution_citations_and_untrusted_quotes_as_plain_text(self):
        rows = [evidence(self.a, "Cited", speaker=None, evidence_quote="<script>x</script>", published_at=None,
                         source_url=None),
                evidence(self.a, "Linked", strength=90, signal=88)]
        _, body = self.render(rows)
        self.assertIn("1. Linked\n   Assessment: Supports · strength 90/100 · signal 88/100\n   Speaker: Jane Doe\n"
                      "   Evidence: \"quote: Linked\"\n   Source: Chips · Optics · 2026-11-10\n"
                      "   https://example.com/ep\n", body)
        self.assertIn("Speaker: not identified in the transcript\n   Evidence: \"<script>x</script>\"\n"
                      "   Source: Chips · Optics · date unknown\n\n", body)
        self.assertIn("automated assessments, not the speaker's words", body)
        self.assertIn("https://app.example.com/app/", body)
        self.assertIn(f"cancel scheduled digest {self.subscription.id}", body)

    def test_an_empty_digest_still_lists_every_thesis(self):
        _, body = self.render([])
        self.assertIn("3 theses monitored\n0 theses with new evidence (0 new claims)", body)
        self.assertEqual(body.count("No new relevant evidence during this period."), 3)


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL is not set")
class DigestDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.name = f"podcast_test_{secrets.token_hex(4)}"
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"CREATE DATABASE {self.name}")
        self.db = PostgresClient(make_conninfo(DATABASE_URL, dbname=self.name))
        await migrate_postgres(self.db.url)
        self.clock = at(2026, 11, 13, 9)  # 1 PM in Dubai; the first daily 2 PM digest is due at 10:00 UTC.
        self.email = SimpleNamespace(enabled=True, send=AsyncMock())
        self.service = DigestService(self.db, self.email, "https://app.example.com", now=lambda: self.clock)
        self.tools = AgentTools(self.db, graph=None, embedder=None)
        self.tools.digests = self.service
        async with await self.db.connect() as conn:
            async def insert(sql, *params):
                return (await (await conn.execute(sql + " RETURNING id", params)).fetchone())[0]
            podcast = await insert("INSERT INTO podcasts (name) VALUES ('Chips')")
            episode = await insert("INSERT INTO episodes (podcast_id, title, source_type, published_at, source_url) "
                                   "VALUES (%s, 'Optics', 'txt', '2026-11-10', 'https://example.com/optics')", podcast)
            old = await insert("INSERT INTO episodes (podcast_id, title, source_type, published_at) "
                               "VALUES (%s, 'Archive', 'txt', '2019-05-01')", podcast)
            self.passage = await insert("INSERT INTO passages (episode_id, ordinal, text) VALUES (%s, 0, 'x')", episode)
            self.old_passage = await insert("INSERT INTO passages (episode_id, ordinal, text) VALUES (%s, 0, 'x')", old)
        self.a = (await self.db.add_thesis("AI inference costs will decline", []))[0]
        self.b = (await self.db.add_thesis("Enterprise SaaS margins will expand", []))[0]
        self.c = (await self.db.add_thesis("Data center demand remains strong", []))[0]

    async def asyncTearDown(self):
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"DROP DATABASE {self.name} WITH (FORCE)")

    async def claim(self, text, signal=70, passage=None):
        (claim_id,) = await self.db.one(
            "INSERT INTO claims (passage_id, text, evidence_quote, speaker, fingerprint, signal) "
            "VALUES (%s, %s, %s, 'Jane Doe', %s, %s) RETURNING id",
            passage or self.passage, text, f"quote: {text}", text, signal)
        return claim_id

    async def assess(self, claim_id, thesis_id, stance, strength, recorded_at):
        await self.db.execute("INSERT INTO thesis_assessments (claim_id, thesis_id, stance, strength, created_at) "
                              "VALUES (%s, %s, %s, %s, %s)", claim_id, thesis_id, stance, strength, recorded_at)

    async def register(self, thesis_ids, cron="0 14 * * *", tz="Asia/Dubai", email="analyst@example.com"):
        return json.loads(await self.tools.call("register_scheduled_thesis_digest", {
            "email": email, "thesis_ids": [str(thesis_id) for thesis_id in thesis_ids], "cron": cron,
            "timezone": tz}))

    async def schedule(self, subscription_id):
        return await self.db.one("SELECT is_active, next_run_at, last_delivered_at, last_delivery_status "
                                 "FROM cron_subscriptions WHERE id = %s", subscription_id)

    async def test_register_validates_dedupes_and_reports_overlapping_immediate_subscriptions(self):
        await self.tools.add_subscription("analyst@example.com", [str(self.a)])
        await self.tools.add_subscription("other@example.com", [str(self.b)])
        result = await self.register([self.a, self.b], email=" Analyst@Example.COM ")
        self.assertEqual(result["status"], "registered")
        self.assertEqual((result["email"], result["cron"], result["timezone"]),
                         ("analyst@example.com", "0 14 * * *", "Asia/Dubai"))
        self.assertEqual(result["next_run_at_utc"], "2026-11-13T10:00:00+00:00")
        self.assertEqual(result["next_run_local"], "Nov 13, 2026 2:00 PM (Asia/Dubai)")
        self.assertEqual(result["next_digest_covers"],
                         "evidence recorded from Nov 12, 2026 2:00 PM to Nov 13, 2026 2:00 PM (Asia/Dubai)")
        self.assertEqual({thesis["thesis_id"] for thesis in result["theses"]}, {str(self.a), str(self.b)})
        overlap = result["overlapping_immediate_subscriptions"]
        self.assertEqual([(row["thesis_id"], row["thesis"]) for row in overlap],
                         [(str(self.a), "AI inference costs will decline")])

        # The same address, schedule, timezone, and set of theses: the existing digest, not a second one.
        again = await self.register([self.b, self.a, self.a], cron="0  14 * * *", tz="asia/dubai")
        self.assertEqual((again["status"], again["subscription_id"]), ("already_registered", result["subscription_id"]))
        weekly = await self.register([self.a, self.b], cron="0 9 * * 1")
        self.assertEqual(weekly["status"], "registered")
        self.assertEqual(weekly["next_run_local"], "Nov 16, 2026 9:00 AM (Asia/Dubai)")
        self.assertEqual(await self.db.one("SELECT count(*) FROM cron_subscriptions"), (2,))

        await self.db.execute("UPDATE theses SET is_active = false WHERE id = %s", self.c)
        for thesis_ids, error in (([self.a, self.c], f"inactive thesis IDs: {self.c}"),
                                  ([self.a, uuid4()], "Unknown or inactive"), ([], "at least one thesis"),
                                  ([self.a, "not-a-uuid"], "Invalid thesis ID: not-a-uuid")):
            with self.assertRaisesRegex(ValueError, error):
                await self.register(thesis_ids)
        for arguments, error in (({"cron": "0 25 * * *"}, "Invalid cron"), ({"tz": "Dubai"}, "Unknown timezone"),
                                 ({"email": "not-an-address"}, "Invalid email")):
            with self.assertRaisesRegex(ValueError, error):
                await self.register([self.a], **arguments)
        self.assertEqual(await self.db.one("SELECT count(*) FROM cron_subscriptions"), (2,))
        # The chat's read-only SQL tool can find schedules to cancel; the table refuses an empty thesis list.
        rows = json.loads(await self.tools.call("postgres_database_query", {
            "sql": "SELECT cron, timezone, thesis_ids FROM cron_subscriptions WHERE is_active ORDER BY cron"}))
        self.assertEqual([row["cron"] for row in rows], ["0 14 * * *", "0 9 * * 1"])
        with self.assertRaises(psycopg.errors.CheckViolation):
            await self.db.execute("INSERT INTO cron_subscriptions (email, cron, timezone, thesis_ids, next_run_at) "
                                  "VALUES ('a@b.co', '0 14 * * *', 'UTC', '{}', now())")

    async def test_remove_subscription_removes_only_the_requested_immediate_subscriptions(self):
        await self.tools.add_subscription("analyst@example.com", [str(self.a), str(self.b)])
        await self.tools.add_subscription("other@example.com", [str(self.a)])
        digest = await self.register([self.a])
        removed = json.loads(await self.tools.call("remove_subscription", {
            "email": "ANALYST@example.com", "thesis_ids": [str(self.a), str(uuid4())]}))
        self.assertEqual((removed["email"], removed["removed_count"]), ("analyst@example.com", 1))
        self.assertEqual([(row["thesis_id"], row["thesis"]) for row in removed["removed"]],
                         [(str(self.a), "AI inference costs will decline")])
        self.assertEqual(await self.db.all("SELECT email, thesis_id FROM subscriptions ORDER BY email"),
                         [("analyst@example.com", self.b), ("other@example.com", self.a)])
        again = json.loads(await self.tools.call("remove_subscription", {
            "email": "analyst@example.com", "thesis_ids": [str(self.a)]}))
        self.assertEqual(again["removed_count"], 0)
        self.assertEqual((await self.schedule(digest["subscription_id"]))[0], True)  # Digests are untouched.
        with self.assertRaisesRegex(ValueError, "Invalid email"):
            await self.tools.call("remove_subscription", {"email": "nope", "thesis_ids": [str(self.a)]})

    async def test_cancel_disables_only_that_digest_and_is_idempotent(self):
        daily = await self.register([self.a])
        weekly = await self.register([self.a], cron="0 9 * * 1")
        cancel = {"subscription_id": daily["subscription_id"]}
        result = json.loads(await self.tools.call("remove_scheduled_thesis_digest", cancel))
        self.assertEqual((result["status"], result["cron"], result["timezone"]),
                         ("cancelled", "0 14 * * *", "Asia/Dubai"))
        again = json.loads(await self.tools.call("remove_scheduled_thesis_digest", cancel))
        self.assertEqual(again["status"], "already_cancelled")
        self.assertEqual((await self.schedule(daily["subscription_id"]))[0], False)
        self.assertEqual((await self.schedule(weekly["subscription_id"]))[0], True)
        with self.assertRaisesRegex(ValueError, "No scheduled digest"):
            await self.tools.call("remove_scheduled_thesis_digest", {"subscription_id": str(uuid4())})
        with self.assertRaisesRegex(ValueError, "Invalid scheduled digest ID"):
            await self.tools.call("remove_scheduled_thesis_digest", {"subscription_id": "daily"})
        # A cancelled digest is never delivered, and re-registering it creates a new one.
        self.clock = at(2026, 11, 13, 10, 0, 30)
        await self.service.deliver_due()
        self.email.send.assert_not_awaited()
        self.assertEqual((await self.register([self.a]))["status"], "registered")

    async def test_tools_are_hidden_and_refused_unless_digests_are_enabled(self):
        disabled = AgentTools(self.db, graph=None, embedder=None)
        self.assertEqual(disabled.chat_tools(), CHAT_TOOLS)
        for name in DIGEST_TOOLS:
            with self.assertRaisesRegex(ValueError, "not enabled"):
                await disabled.call(name, {"subscription_id": str(uuid4())})
        self.assertEqual(await self.db.one("SELECT count(*) FROM cron_subscriptions"), (0,))
        self.assertEqual(self.tools.chat_tools(), CHAT_TOOLS + DIGEST_TOOLS)
        self.assertEqual([spec["name"] for spec in tool_specs(*self.tools.chat_tools())], list(CHAT_TOOLS + DIGEST_TOOLS))

    async def test_due_digest_is_sent_once_with_the_scheduled_window_and_evidence(self):
        digest = await self.register([self.a, self.b, self.c])
        start, end = at(2026, 11, 12, 10), at(2026, 11, 13, 10)  # 2 PM to 2 PM Dubai.
        at_start = await self.claim("At the window start", signal=60)
        await self.assess(at_start, self.a, "supports", 90, start)
        old = await self.claim("Old episode, newly assessed", signal=95, passage=self.old_passage)
        await self.assess(old, self.b, "challenges", 85, end - timedelta(seconds=1))
        unrelated = await self.claim("Unrelated claim")
        await self.assess(unrelated, self.a, "unrelated", 0, end - timedelta(hours=1))
        before = await self.claim("Before the window")
        await self.assess(before, self.a, "supports", 99, start - timedelta(seconds=1))
        at_end = await self.claim("At the window end")
        await self.assess(at_end, self.a, "supports", 99, end)
        other_thesis, _ = await self.db.add_thesis("Not in this digest", [])
        await self.assess(await self.claim("Other thesis"), other_thesis, "supports", 99, end - timedelta(hours=1))

        self.clock = at(2026, 11, 13, 9, 59)
        await self.service.deliver_due()
        self.email.send.assert_not_awaited()  # Not due yet.

        self.clock = at(2026, 11, 13, 10, 0, 40)
        await self.service.deliver_due()
        await self.service.deliver_due()  # A second poll must not resend this occurrence.
        self.email.send.assert_awaited_once()
        to, subject, body = self.email.send.call_args.args
        self.assertEqual((to, subject), ("analyst@example.com", "Podcast Analyst daily thesis digest: Nov 13, 2026"))
        self.assertIn("Nov 12, 2026 2:00 PM to Nov 13, 2026 2:00 PM (Asia/Dubai)", body)
        for text in ("At the window start", "Old episode, newly assessed", "Archive · 2019-05-01",
                     "Chips · Optics · 2026-11-10", "https://example.com/optics", "Speaker: Jane Doe",
                     "THESIS: Data center demand remains strong\n\nNo new relevant evidence"):
            self.assertIn(text, body)
        for text in ("Unrelated claim", "Before the window", "At the window end", "Other thesis"):
            self.assertNotIn(text, body)
        # Theses ordered by their strongest new evidence (strength 90, then 85), then the one without any.
        self.assertLess(body.index("THESIS: AI inference costs"), body.index("THESIS: Enterprise SaaS margins"))
        self.assertLess(body.index("THESIS: Enterprise SaaS margins"), body.index("THESIS: Data center demand"))
        self.assertEqual(await self.schedule(digest["subscription_id"]),
                         (True, at(2026, 11, 14, 10), self.clock, "sent"))

        # The next day's digest covers the next window, which starts where this one ended.
        self.clock = at(2026, 11, 14, 10, 0, 20)
        await self.service.deliver_due()
        body = self.email.send.call_args.args[2]
        self.assertEqual(self.email.send.await_count, 2)
        self.assertIn("At the window end", body)
        self.assertNotIn("At the window start", body)

    async def test_a_failed_send_is_recorded_not_retried_and_does_not_stop_other_digests(self):
        failing = await self.register([self.a], email="bad@example.com")
        working = await self.register([self.a], email="good@example.com")

        async def send(to, subject, body):
            if to == "bad@example.com":
                raise RuntimeError("SES denied")
        self.email.send.side_effect = send
        self.clock = at(2026, 11, 13, 10, 1)
        with self.assertLogs("src.backend.ingestion.digest_service", level="ERROR"):
            await self.service.deliver_due()
        await self.service.deliver_due()
        self.assertEqual(sorted(call.args[0] for call in self.email.send.call_args_list),
                         ["bad@example.com", "good@example.com"])
        self.assertEqual(await self.schedule(failing["subscription_id"]), (True, at(2026, 11, 14, 10), None, "failed"))
        self.assertEqual((await self.schedule(working["subscription_id"]))[3], "sent")

        # An unexpected error in a whole tick is logged, and the loop keeps polling.
        self.service.deliver_due = AsyncMock(side_effect=[RuntimeError("database down"), None])
        with patch("src.backend.ingestion.digest_service.asyncio.sleep",
                   AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertLogs("src.backend.ingestion.digest_service", level="ERROR"), \
                    self.assertRaises(asyncio.CancelledError):
                await self.service.run()
        self.assertEqual(self.service.deliver_due.await_count, 2)

    async def test_missed_occurrences_are_skipped_after_a_restart(self):
        digest = await self.register([self.a])
        await self.assess(await self.claim("Recent"), self.a, "supports", 80, at(2026, 11, 13, 8))
        self.clock = at(2026, 11, 16, 12)  # The app was down for three days.
        with self.assertLogs("src.backend.ingestion.digest_service", level="WARNING"):
            await self.service.deliver_due()
        self.email.send.assert_not_awaited()
        self.assertEqual(await self.schedule(digest["subscription_id"]), (True, at(2026, 11, 17, 10), None, "skipped"))

        # Back within a few minutes of an occurrence (e.g. a deploy): only that latest occurrence is sent.
        await self.assess(await self.claim("Yesterday"), self.a, "challenges", 70, at(2026, 11, 17, 12))
        await self.db.execute("UPDATE cron_subscriptions SET next_run_at = %s", at(2026, 11, 15, 10))
        self.clock = at(2026, 11, 18, 10, 5)
        await self.service.deliver_due()
        self.email.send.assert_awaited_once()
        body = self.email.send.call_args.args[2]
        self.assertIn("Nov 17, 2026 2:00 PM to Nov 18, 2026 2:00 PM", body)
        self.assertIn("Yesterday", body)
        self.assertEqual((await self.schedule(digest["subscription_id"]))[1:], (at(2026, 11, 19, 10), self.clock, "sent"))

    async def test_disabled_email_and_inactive_theses_are_not_recorded_as_sent(self):
        disabled = await self.register([self.a, self.b])
        inactive = await self.register([self.c])
        await self.db.execute("UPDATE theses SET is_active = false WHERE id IN (%s, %s)", self.b, self.c)
        self.email.enabled = False
        self.clock = at(2026, 11, 13, 10, 0, 30)
        with self.assertLogs("src.backend.ingestion.digest_service", level="INFO") as logs:
            await self.service.deliver_due()
        self.email.send.assert_not_awaited()
        self.assertEqual(await self.schedule(disabled["subscription_id"]), (True, at(2026, 11, 14, 10), None, "disabled"))
        self.assertEqual(await self.schedule(inactive["subscription_id"]), (True, at(2026, 11, 14, 10), None, "skipped"))
        logged = "\n".join(logs.output)
        self.assertIn("1 thesis monitored", logged)  # The inactive thesis is left out of the digest.
        self.assertNotIn("Enterprise SaaS margins", logged)

    async def test_window_spans_a_daylight_saving_change(self):
        self.clock = at(2026, 10, 31, 20)  # 3 PM CDT; 9 AM on Nov 1 is CST (UTC-6).
        digest = await self.register([self.a], cron="0 9 * * *", tz="America/Chicago")
        self.assertEqual(digest["next_run_at_utc"], "2026-11-01T15:00:00+00:00")
        # 9:30 AM CDT on Oct 31: inside the 25-hour window, but outside a naive 24-hour one.
        await self.assess(await self.claim("Before the change"), self.a, "supports", 80, at(2026, 10, 31, 14, 30))
        self.clock = at(2026, 11, 1, 15, 0, 10)
        await self.service.deliver_due()
        body = self.email.send.call_args.args[2]
        self.assertIn("Oct 31, 2026 9:00 AM to Nov 1, 2026 9:00 AM (America/Chicago)", body)
        self.assertIn("Before the change", body)
        self.assertEqual((await self.schedule(digest["subscription_id"]))[1], at(2026, 11, 2, 15))


if __name__ == "__main__":
    unittest.main()
