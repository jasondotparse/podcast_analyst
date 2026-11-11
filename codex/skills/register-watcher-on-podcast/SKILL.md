---
name: register-watcher-on-podcast
description: Start watching a podcast for new episodes (e.g. "Start watching the Dwarkesh Podcast", "keep an eye on Acquired"). Find the podcast's official RSS feed and register it with the register_rss_to_watcher tool, so new episodes are ingested automatically.
---

# Watch a podcast for new episodes

Use this when the user asks you to watch, monitor, follow, or keep an eye on a podcast.

1. **Identify the podcast.** Work out which show the user means. If several shows
   could match, ask which one.
2. **Find its RSS feed URL** with web search. Good ways to find it:
   - Apple's podcast directory lists each show's feed as `feedUrl`:
     `https://itunes.apple.com/search?media=podcast&term=<podcast name>`.
   - The podcast's official website, which often links its RSS feed.
   - Its hosting provider (for example Megaphone, Simplecast, Libsyn, Art19,
     Transistor, Buzzsprout, Substack, or Spotify for Creators/Anchor).

   Prefer the publisher's official feed over third-party mirrors and aggregators.
   A feed URL returns XML, not a web page or a YouTube channel. Never invent a URL.
3. **Register it.** Call `register_rss_to_watcher` with the feed URL. Don't ask the
   user to confirm first; they asked you to watch the show. The backend validates
   the feed itself.
4. **Report the result.**
   - `registered`: say you're now watching the podcast. Name its newest episode,
     which will be ingested within minutes. Explain that later episodes are picked
     up automatically (feeds are checked hourly) and that older episodes are not ingested.
   - `already_registered`: say the podcast is already being watched.
   - `invalid`: give the reason. If another credible feed URL exists, try it (at
     most two more attempts). Otherwise ask the user for the podcast's RSS feed URL.

If you can't search the web or find a credible feed, ask the user for the RSS feed URL.
