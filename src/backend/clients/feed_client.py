"""Podcast RSS/Atom feeds: fetch and parse them, and download their audio and transcripts.

Basic safety for fetching URLs from the model or a feed: http(s) only, every host (including each
redirect hop) must resolve to public addresses, and requests have timeouts and size caps.
"""

import asyncio
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import ipaddress
import re
import socket
from xml.etree import ElementTree

import httpx2

from ..ingestion.models import Feed, FeedEpisode


ATOM = "{http://www.w3.org/2005/Atom}"
PODCAST = "{https://podcastindex.org/namespace/1.0}"  # Podcasting 2.0, for <podcast:transcript>.
# Transcript formats we turn into plain text, best first. Others (HTML, JSON) fall back to the audio.
TRANSCRIPT_TYPES = ("text/plain", "text/vtt", "application/x-subrip", "application/srt")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav")
MAX_FEED_BYTES = 20 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024
TIMEOUT = httpx2.Timeout(30, connect=10)
USER_AGENT = "PodcastAnalyst/0.1 (podcast feed reader)"


class FeedError(Exception):
    """The URL is unsafe or unreachable, the download is too large, or the document is not a usable feed."""


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def check_public(request: httpx2.Request) -> None:
    """Refuse a request to a non-HTTP(S) URL or a host with any non-public address (private, loopback,
    link-local, ...). httpx runs this for every request, including each redirect."""
    if request.url.scheme not in ("http", "https") or not request.url.host:
        raise FeedError("Only http and https URLs are supported.")
    try:
        addresses = await resolve(request.url.host)
    except OSError as error:
        raise FeedError(f"Cannot resolve {request.url.host}: {error}") from error
    if not all(ipaddress.ip_address(address.split("%")[0]).is_global for address in addresses):
        raise FeedError(f"{request.url.host} is not a public address.")


class FeedClient:
    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None):
        self.transport = transport  # Tests pass a mock transport.

    async def fetch_feed(self, url: str) -> Feed:
        return parse_feed(await self.download(url, MAX_FEED_BYTES))

    async def fetch_transcript(self, url: str, kind: str) -> str:
        return transcript_text(
            (await self.download(url, MAX_TRANSCRIPT_BYTES)).decode("utf-8", errors="replace"), kind)

    async def download(self, url: str, max_bytes: int) -> bytes:
        try:
            async with httpx2.AsyncClient(transport=self.transport, timeout=TIMEOUT, follow_redirects=True,
                                          max_redirects=5, event_hooks={"request": [check_public]},
                                          headers={"User-Agent": USER_AGENT}) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise FeedError(f"{url} is larger than {max_bytes // (1024 * 1024)} MB.")
                    return bytes(body)
        except (httpx2.HTTPError, httpx2.InvalidURL) as error:
            raise FeedError(f"Could not fetch {url}: {error}") from error


def parse_feed(document: bytes) -> Feed:
    """The feed's podcast details and its usable episodes. Raises FeedError if it has none."""
    try:
        root = ElementTree.fromstring(document)  # Expat >= 2.4.1 refuses entity-expansion bombs.
    except ElementTree.ParseError as error:
        raise FeedError(f"Not an XML feed ({error}).") from error
    channel = root.find("channel")
    if root.tag == "rss" and channel is not None:
        title, description, link = text(channel, "title"), text(channel, "description"), text(channel, "link")
        episodes = [rss_episode(item) for item in channel.findall("item")]
    elif root.tag == f"{ATOM}feed":
        title, description, link = text(root, f"{ATOM}title"), text(root, f"{ATOM}subtitle"), atom_link(root)
        episodes = [atom_episode(entry) for entry in root.findall(f"{ATOM}entry")]
    else:
        raise FeedError("Not an RSS or Atom feed.")
    if not title:
        raise FeedError("The feed has no title.")
    episodes = [episode for episode in episodes
                if episode.guid and episode.published_at and (episode.audio_url or episode.transcript_url)]
    if not episodes:
        raise FeedError("The feed has no dated episodes with audio or a supported transcript.")
    # Cite an episode's page only when it is its own (some feeds link every episode to the show's home
    # page); otherwise cite its audio file.
    pages = Counter(episode.source_url for episode in episodes)
    for episode in episodes:
        if not episode.source_url or pages[episode.source_url] > 1 or episode.source_url == link:
            episode.source_url = episode.audio_url
    return Feed(title=title, description=plain(description), episodes=episodes)


def rss_episode(item: ElementTree.Element) -> FeedEpisode:
    enclosure = item.find("enclosure")
    audio_url = audio_file(enclosure.get("url"), enclosure.get("type")) if enclosure is not None else None
    transcript_url, transcript_type = best_transcript(
        (element.get("url"), element.get("type")) for element in item.findall(f"{PODCAST}transcript"))
    return FeedEpisode(guid=text(item, "guid") or audio_url, title=text(item, "title") or "Untitled episode",
                       description=plain(text(item, "description")), published_at=parse_date(text(item, "pubDate")),
                       source_url=text(item, "link"), audio_url=audio_url, transcript_url=transcript_url,
                       transcript_type=transcript_type)


def atom_episode(entry: ElementTree.Element) -> FeedEpisode:
    links = entry.findall(f"{ATOM}link")
    audio_url = next((url for element in links if element.get("rel") == "enclosure"
                      if (url := audio_file(element.get("href"), element.get("type")))), None)
    return FeedEpisode(guid=text(entry, f"{ATOM}id") or audio_url,
                       title=text(entry, f"{ATOM}title") or "Untitled episode",
                       description=plain(text(entry, f"{ATOM}summary")),
                       published_at=parse_date(text(entry, f"{ATOM}published") or text(entry, f"{ATOM}updated")),
                       source_url=atom_link(entry), audio_url=audio_url)


def text(element: ElementTree.Element, tag: str) -> str | None:
    return (element.findtext(tag) or "").strip() or None


def atom_link(element: ElementTree.Element) -> str | None:
    return next((link.get("href") for link in element.findall(f"{ATOM}link")
                 if link.get("rel", "alternate") == "alternate"), None)


def audio_file(url: str | None, kind: str | None) -> str | None:
    """The enclosure's URL if it is audio (by MIME type, or by extension when there is none)."""
    if not url:
        return None
    is_audio = kind.startswith("audio/") if kind else url.split("?")[0].lower().endswith(AUDIO_EXTENSIONS)
    return url if is_audio else None


def best_transcript(candidates) -> tuple[str | None, str | None]:
    found = {kind: url for url, kind in candidates if url and kind}
    return next(((found[kind], kind) for kind in TRANSCRIPT_TYPES if kind in found), (None, None))


def parse_date(value: str | None) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom) as an aware datetime; None if missing or unparseable."""
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def plain(markup: str | None) -> str | None:
    """Show notes without HTML tags and with collapsed whitespace."""
    return markup and " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split()) or None


def transcript_text(raw: str, kind: str) -> str:
    """Plain text from a text, WebVTT, or SRT transcript: cue numbers and timings are dropped, and a
    WebVTT voice tag (<v Speaker>) becomes "Speaker: "."""
    if kind == "text/plain":
        return raw
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.isdigit() or "-->" in line:
            continue
        lines.append(re.sub(r"<[^>]+>", "", re.sub(r"<v(?:\.[^ >]*)? ([^>]+)>", r"\1: ", line)))
    return "\n".join(lines)
