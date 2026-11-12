import asyncio
from datetime import date, datetime, timezone
import secrets
import socket
import unittest
from unittest.mock import patch

import httpx2
import psycopg
from psycopg.conninfo import make_conninfo

from src.backend.clients import feed_client
from src.backend.clients.feed_client import FeedClient, FeedError, parse_feed, transcript_text
from src.backend.clients.postgres_client import PostgresClient
from src.backend.db.db import migrate_postgres
from src.backend.ingestion import watcher_service
from src.backend.ingestion.ingestion_service import IngestionService
from src.backend.ingestion.models import EpisodeMetadata
from src.backend.ingestion.watcher_service import WatcherService
from tests.test_db import DATABASE_URL


FEED_URL = "https://feeds.example.com/chips.rss"
OTHER_FEED_URL = "https://feeds.example.com/macro.rss"
ADDRESSES = {"feeds.example.com": ["93.184.215.14"], "cdn.example.com": ["93.184.215.15"],
             "chips.example.com": ["93.184.215.16"], "intranet.example.com": ["10.0.0.7"]}
real_resolve = feed_client.resolve


async def fake_resolve(host: str) -> list[str]:
    """Made-up public and private hosts; IP literals and localhost resolve for real (no network needed)."""
    if host.endswith(".example.com"):
        if host not in ADDRESSES:
            raise socket.gaierror(f"unknown host {host}")
        return ADDRESSES[host]
    return await real_resolve(host)


def item(guid: str, day: int, extra: str = "") -> str:
    return (f"<item><guid>{guid}</guid><title>Episode {guid}</title><pubDate>{day} Nov 2026 09:00:00 GMT</pubDate>"
            f"<link>https://chips.example.com/{guid}</link><description>&lt;p&gt;About {guid}&lt;/p&gt;</description>"
            f'<enclosure url="https://cdn.example.com/{guid}.mp3" type="audio/mpeg" length="9"/>{extra}</item>')


def rss(*items: str, title: str = "Chip Talk") -> bytes:
    return ('<?xml version="1.0"?><rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">'
            f"<channel><title>{title}</title><link>https://chips.example.com/</link>"
            f'<description>&lt;b&gt;Semiconductor&lt;/b&gt; news</description>{"".join(items)}</channel></rss>').encode()


class FakeWeb:
    """A mock transport's handler: URL -> body (200) or Response; anything else is a 404. Records requests."""
    def __init__(self, pages: dict):
        self.pages = pages
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(str(request.url))
        page = self.pages.get(str(request.url))
        if isinstance(page, httpx2.Response):
            return page
        return httpx2.Response(404) if page is None else httpx2.Response(200, content=page)


class ParseFeedTests(unittest.TestCase):
    def test_rss_items_become_episodes_with_their_audio_transcript_and_plain_show_notes(self):
        feed = parse_feed(rss(
            item("ep2", 9, '<podcast:transcript url="https://cdn.example.com/ep2.html" type="text/html"/>'
                           '<podcast:transcript url="https://cdn.example.com/ep2.vtt" type="text/vtt"/>'),
            item("ep1", 2)))
        self.assertEqual((feed.title, feed.description), ("Chip Talk", "Semiconductor news"))
        newest, oldest = feed.episodes
        self.assertEqual((newest.guid, newest.title, newest.description, newest.source_url, newest.audio_url),
                         ("ep2", "Episode ep2", "About ep2", "https://chips.example.com/ep2",
                          "https://cdn.example.com/ep2.mp3"))
        self.assertEqual(newest.published_at, datetime(2026, 11, 9, 9, tzinfo=timezone.utc))
        self.assertEqual((newest.transcript_url, newest.transcript_type), ("https://cdn.example.com/ep2.vtt", "text/vtt"))
        self.assertIsNone(oldest.transcript_url)

    def test_episodes_sharing_the_show_page_cite_their_audio_instead(self):
        shared = rss(item("ep1", 2).replace("https://chips.example.com/ep1", "https://chips.example.com/"))
        self.assertEqual(parse_feed(shared).episodes[0].source_url, "https://cdn.example.com/ep1.mp3")

    def test_atom_entries_with_audio_enclosures_are_supported(self):
        feed = parse_feed(
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Chip Talk</title>'
            b'<entry><id>urn:optics</id><title>Optics</title><updated>2026-11-09T09:00:00Z</updated>'
            b'<link href="https://chips.example.com/optics"/>'
            b'<link rel="enclosure" type="audio/mpeg" href="https://cdn.example.com/optics.mp3"/></entry>'
            b'<entry><id>urn:post</id><title>A blog post</title><updated>2026-11-08T09:00:00Z</updated></entry></feed>')
        [episode] = feed.episodes
        self.assertEqual((episode.guid, episode.source_url, episode.audio_url, episode.published_at),
                         ("urn:optics", "https://chips.example.com/optics", "https://cdn.example.com/optics.mp3",
                          datetime(2026, 11, 9, 9, tzinfo=timezone.utc)))

    def test_documents_that_are_not_podcast_feeds_are_rejected(self):
        video_only = item("ep1", 2).replace('ep1.mp3" type="audio/mpeg"', 'ep1.mp4" type="video/mp4"')
        undated = item("ep1", 2).replace("<pubDate>2 Nov 2026 09:00:00 GMT</pubDate>", "")
        for document in (b"not xml", b"<html><body>Chip Talk</body></html>", rss(), rss(video_only), rss(undated),
                         rss(item("ep1", 2), title="")):
            with self.assertRaises(FeedError, msg=document):
                parse_feed(document)

    def test_vtt_and_srt_transcripts_become_plain_text(self):
        vtt = ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n<v Dylan Patel>Lasers ship next year.\n\n"
               "2\n00:00:02.000 --> 00:00:04.000\n<v Host>Really?</v>\n")
        self.assertEqual(transcript_text(vtt, "text/vtt"), "Dylan Patel: Lasers ship next year.\nHost: Really?")
        srt = "1\n00:00:00,000 --> 00:00:02,000\nLasers ship next year.\n\n2\n00:00:02,000 --> 00:00:04,000\nReally?\n"
        self.assertEqual(transcript_text(srt, "application/x-subrip"), "Lasers ship next year.\nReally?")


class FeedClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(feed_client, "resolve", fake_resolve))

    async def test_refuses_non_public_destinations_including_redirect_targets(self):
        web = FakeWeb({"https://feeds.example.com/moved.rss": httpx2.Response(
            302, headers={"Location": "http://intranet.example.com/feed.rss"})})
        client = FeedClient(httpx2.MockTransport(web))
        for url in ("http://127.0.0.1:8000/api/health", "http://localhost/feed", "http://[::1]/feed",
                    "http://169.254.169.254/latest/meta-data/", "http://intranet.example.com/feed.rss",
                    "https://feeds.example.com/moved.rss", "file:///etc/passwd", "not a url"):
            with self.assertRaises(FeedError, msg=url):
                await client.download(url, 1000)
        self.assertEqual(web.requests, ["https://feeds.example.com/moved.rss"])  # Nothing reached a private host.

    async def test_downloads_over_the_size_limit_are_refused(self):
        client = FeedClient(httpx2.MockTransport(FakeWeb({"https://cdn.example.com/ep1.mp3": b"x" * 100})))
        with self.assertRaisesRegex(FeedError, "larger than"):
            await client.download("https://cdn.example.com/ep1.mp3", 99)
        self.assertEqual(await client.download("https://cdn.example.com/ep1.mp3", 100), b"x" * 100)


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL is not set")
class WatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.name = f"podcast_test_{secrets.token_hex(4)}"
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"CREATE DATABASE {self.name}")
        self.db = PostgresClient(make_conninfo(DATABASE_URL, dbname=self.name))
        await migrate_postgres(self.db.url)
        self.enterContext(patch.object(feed_client, "resolve", fake_resolve))
        self.web = FakeWeb({f"https://cdn.example.com/ep{n}.mp3": f"ID3 ep{n}".encode() for n in range(1, 10)})
        self.ingestion = IngestionService(self.db, graph=None, openai=None, embedder=None, extractor=None,
                                          transcriber=None, ranker=None, reflector=None, notifier=None)
        self.watcher = WatcherService(self.db, FeedClient(httpx2.MockTransport(self.web)), self.ingestion,
                                      max_audio_bytes=1000)

    async def asyncTearDown(self):
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute(f"DROP DATABASE {self.name} WITH (FORCE)")

    async def queued(self) -> list[str]:
        """The GUIDs of queued episodes, in the order they were queued."""
        rows = await self.db.all("SELECT e.external_episode_id FROM episodes e "
                                 "JOIN ingestion_jobs j ON j.episode_id = e.id ORDER BY j.created_at")
        return [guid for (guid,) in rows]

    async def next_check(self, hour_later: bool = True) -> None:
        """Finish the queued jobs, let an hour pass if asked, and run a watcher check."""
        await self.db.execute("UPDATE ingestion_jobs SET status = 'completed'")
        if hour_later:
            await self.db.execute("UPDATE watch_sources SET last_checked_at = now() - interval '2 hours'")
        await self.watcher.check_feeds()

    async def test_registers_a_valid_feed_once_and_reports_invalid_ones(self):
        self.web.pages[FEED_URL] = rss(item("ep2", 9), item("ep1", 2))
        self.web.pages["https://chips.example.com/"] = b"<html><body>Chip Talk</body></html>"
        result = await self.watcher.register(f" {FEED_URL} ")
        self.assertEqual((result["status"], result["podcast"], result["newest_episode"], result["published"]),
                         ("registered", "Chip Talk", "Episode ep2", date(2026, 11, 9)))
        self.assertEqual((await self.watcher.register(FEED_URL))["status"], "already_registered")
        for url in ("https://chips.example.com/", "https://feeds.example.com/missing.rss",
                    "http://intranet.example.com/feed.rss", "not a url"):
            result = await self.watcher.register(url)
            self.assertEqual(result["status"], "invalid", url)
            self.assertTrue(result["error"], url)
        self.assertEqual(await self.db.all("SELECT url, description, ingest_since, last_checked_at FROM watch_sources"),
                         [(FEED_URL, "Chip Talk", datetime(2026, 11, 9, 9, tzinfo=timezone.utc), None)])
        self.assertEqual(await self.queued(), [])  # Registration itself queues nothing.

    async def test_queues_only_the_newest_episode_at_first_then_each_new_one_once(self):
        self.web.pages[FEED_URL] = rss(item("ep2", 9), item("ep1", 2))
        await self.watcher.register(FEED_URL)
        await self.watcher.check_feeds()
        self.assertEqual(await self.queued(), ["ep2"])
        self.assertEqual(await self.db.all(
            "SELECT p.name, p.description, e.title, e.description, e.published_at, e.source_url, e.source_type "
            "FROM episodes e JOIN podcasts p ON p.id = e.podcast_id"),
            [("Chip Talk", "Semiconductor news", "Episode ep2", "About ep2", date(2026, 11, 9),
              "https://chips.example.com/ep2", "mp3")])

        fetches = self.web.requests.count(FEED_URL)
        await self.next_check(hour_later=False)  # Checked less than an hour ago: not fetched again.
        self.assertEqual(self.web.requests.count(FEED_URL), fetches)
        with self.assertLogs(watcher_service.log, "INFO") as logs:
            await self.next_check()  # ep2 is already ingested, and ep1 predates registration.
        self.assertEqual(logs.output, [f"INFO:{watcher_service.log.name}:Watcher checked 1 feed(s); no new episodes"])
        self.assertEqual(await self.queued(), ["ep2"])

        self.web.pages[FEED_URL] = rss(item("ep4", 11), item("ep3", 10), item("ep2", 9), item("ep1", 2))
        await self.next_check()
        await self.next_check()
        await self.next_check()
        self.assertEqual(await self.queued(), ["ep2", "ep4", "ep3"])
        self.assertNotIn("https://cdn.example.com/ep1.mp3", self.web.requests)

    async def test_nothing_is_fetched_or_queued_while_an_ingestion_is_active(self):
        self.web.pages[FEED_URL] = rss(item("ep1", 2))
        await self.watcher.register(FEED_URL)
        upload, _ = await self.ingestion.submit(EpisodeMetadata(podcast_name="Other", title="Upload"),
                                                transcript="Lasers ship next year.")
        requests = len(self.web.requests)
        for status in ("pending", "running"):
            await self.db.update_job(upload.job_id, status)
            await self.watcher.check_feeds()
            self.assertEqual(len(self.web.requests), requests)
        await self.db.update_job(upload.job_id, "failed", error="Some error")
        await self.watcher.check_feeds()
        self.assertEqual(await self.queued(), [None, "ep1"])

    async def test_hands_a_supported_transcript_or_else_the_audio_to_the_pipeline(self):
        vtt, srt = "https://cdn.example.com/ep2.vtt", "https://cdn.example.com/ep3.srt"
        self.web.pages[vtt] = b"WEBVTT\n\n00:00.000 --> 00:02.000\n<v Dylan>Lasers ship next year.\n"
        self.web.pages[FEED_URL] = rss(item("ep2", 9, f'<podcast:transcript url="{vtt}" type="text/vtt"/>'))
        await self.watcher.register(FEED_URL)
        await self.watcher.check_feeds()
        # The SRT transcript is missing (404), so its episode falls back to the audio.
        self.web.pages[FEED_URL] = rss(item("ep3", 10, f'<podcast:transcript url="{srt}" type="application/x-subrip"/>'),
                                       item("ep2", 9, f'<podcast:transcript url="{vtt}" type="text/vtt"/>'))
        with self.assertLogs(watcher_service.log, "WARNING") as logs:
            await self.next_check()
        self.assertIn("using the audio", logs.output[0])

        rows = await self.db.all("SELECT e.id, e.external_episode_id, e.source_type, e.transcript_text FROM episodes e "
                                 "ORDER BY e.external_episode_id")
        self.assertEqual([row[1:] for row in rows], [("ep2", "txt", "Dylan: Lasers ship next year."),
                                                     ("ep3", "mp3", None)])
        self.assertEqual(await self.db.get_audio(rows[1][0]), b"ID3 ep3")
        self.assertNotIn("https://cdn.example.com/ep2.mp3", self.web.requests)

    async def test_feed_and_download_failures_are_logged_and_skipped(self):
        self.web.pages[FEED_URL] = rss(item("ep1", 2))
        self.web.pages[OTHER_FEED_URL] = rss(item("ep9", 3), title="Macro Hour")
        await self.watcher.register(FEED_URL)
        await self.watcher.register(OTHER_FEED_URL)
        self.web.pages[FEED_URL] = b"<html><body>Service unavailable</body></html>"
        del self.web.pages["https://cdn.example.com/ep9.mp3"]
        with self.assertLogs(watcher_service.log, "WARNING") as logs:
            await self.watcher.check_feeds()
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(await self.queued(), [])
        self.assertEqual(await self.db.all("SELECT count(*) FROM watch_sources WHERE last_checked_at IS NULL"), [(0,)])

        # Both recover. Each check queues one episode; the second feed waits for the next check.
        self.web.pages[FEED_URL] = rss(item("ep1", 2))
        self.web.pages["https://cdn.example.com/ep9.mp3"] = b"ID3 ep9"
        await self.next_check()
        self.assertEqual(len(await self.queued()), 1)
        await self.next_check(hour_later=False)
        self.assertEqual(sorted(await self.queued()), ["ep1", "ep9"])


class WatcherLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_loop_keeps_running_after_a_failed_check(self):
        watcher = WatcherService(db=None, feeds=None, ingestion=None, max_audio_bytes=0)
        calls, third_call = [], asyncio.Event()  # One entry per check.

        async def check_feeds():
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("Postgres is down")
            if len(calls) == 3:
                third_call.set()

        watcher.check_feeds = check_feeds
        with patch.object(watcher_service, "TICK_SECONDS", 0), self.assertLogs(watcher_service.log, "ERROR"):
            loop = asyncio.create_task(watcher.run())
            await asyncio.wait_for(third_call.wait(), 5)  # Checks continued after the first one failed.
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
