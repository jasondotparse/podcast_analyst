"""RSS watchers: register podcast feeds, then queue their new episodes for the ingestion pipeline.

See docs/etl_design.md#rss-watchers. Feeds are checked hourly. A check runs only while no ingestion job
is pending or running, and it queues at most one episode, so watcher ingestions run one at a time.
"""

import asyncio
from datetime import timedelta
import logging

from ..clients.feed_client import FeedClient, FeedError
from ..clients.postgres_client import PostgresClient
from .ingestion_service import IngestionService
from .models import EpisodeMetadata, Feed, FeedEpisode, IngestionJob, WatchSource


log = logging.getLogger(__name__)

CHECK_INTERVAL = timedelta(hours=1)  # How often each feed is fetched.
TICK_SECONDS = 60  # How often the loop looks for due feeds and an idle pipeline.


class WatcherService:
    def __init__(self, db: PostgresClient, feeds: FeedClient, ingestion: IngestionService, max_audio_bytes: int):
        self.db = db
        self.feeds = feeds
        self.ingestion = ingestion
        self.max_audio_bytes = max_audio_bytes

    async def register(self, feed_url: str) -> dict:
        """Validate the feed and start watching it. Only its newest episode and later ones get ingested."""
        feed_url = feed_url.strip()
        try:
            feed = await self.feeds.fetch_feed(feed_url)
        except FeedError as error:
            return {"status": "invalid", "feed_url": feed_url, "error": str(error)}
        newest = max(feed.episodes, key=lambda episode: episode.published_at)
        if not await self.db.add_watch_source(feed_url, feed.title, newest.published_at):
            return {"status": "already_registered", "feed_url": feed_url, "podcast": feed.title}
        log.info("Watching %s (%s); newest episode %r", feed.title, feed_url, newest.title)
        return {"status": "registered", "feed_url": feed_url, "podcast": feed.title,
                "newest_episode": newest.title, "published": newest.published_at.date(),
                "next": "The newest episode is ingested within minutes, once no other ingestion is running. "
                        "The feed is checked hourly for later episodes; older episodes are not ingested."}

    async def run(self) -> None:
        """The background loop. A failure is logged and never stops it."""
        while True:
            try:
                await self.check_feeds()
            except Exception:
                log.exception("Watcher check failed")
            await asyncio.sleep(TICK_SECONDS)

    async def check_feeds(self) -> None:
        """Fetch the feeds due for a check, least recently checked first, until one episode is queued."""
        if await self.db.has_active_job():
            return  # One ingestion at a time: look again on a later tick.
        due = await self.db.due_watch_sources(CHECK_INTERVAL)
        for source in due:
            await self.db.mark_watch_source_checked(source.id)  # Even if it fails: retry in an hour, not a minute.
            try:
                feed = await self.feeds.fetch_feed(source.url)
            except FeedError as error:
                log.warning("Watcher could not read %s: %s", source.url, error)
                continue
            if await self.queue_new_episode(source, feed):
                return
        if due:
            log.info("Watcher checked %d feed(s); no new episodes", len(due))

    async def queue_new_episode(self, source: WatchSource, feed: Feed) -> bool:
        """Queue the newest episode published since registration that is not ingested yet."""
        for episode in sorted(feed.episodes, key=lambda episode: episode.published_at, reverse=True):
            if episode.published_at < source.ingest_since:
                break
            if await self.db.has_episode(feed.title, episode.guid, episode.source_url):
                continue
            metadata = EpisodeMetadata(
                podcast_name=feed.title, podcast_description=feed.description, title=episode.title,
                description=episode.description, published_at=episode.published_at.date(),
                source_url=episode.source_url, external_episode_id=episode.guid)
            try:
                job, created = await self.submit(metadata, episode)
            except FeedError as error:  # Try the next episode; this one is retried on the next check.
                log.warning("Watcher could not download %r from %s: %s", episode.title, source.url, error)
                continue
            if created:
                log.info("Watcher queued %r from %s as ingestion job %s", episode.title, feed.title, job.job_id)
                return True
            log.info("Watcher skipped %r: same content as ingestion job %s", episode.title, job.job_id)
        return False

    async def submit(self, metadata: EpisodeMetadata, episode: FeedEpisode) -> tuple[IngestionJob, bool]:
        """Hand the episode to the existing pipeline: its transcript if the feed has a supported one,
        otherwise its audio."""
        if episode.transcript_url:
            try:
                transcript = await self.feeds.fetch_transcript(episode.transcript_url, episode.transcript_type)
                if transcript.strip():
                    return await self.ingestion.submit(metadata, transcript=transcript)
                log.warning("Transcript for %r is empty; using the audio", episode.title)
            except FeedError as error:
                log.warning("Could not fetch the transcript for %r (%s); using the audio", episode.title, error)
        if not episode.audio_url:
            raise FeedError("no transcript and no audio enclosure")
        return await self.ingestion.submit(metadata, audio=await self.feeds.download(episode.audio_url,
                                                                                     self.max_audio_bytes))
