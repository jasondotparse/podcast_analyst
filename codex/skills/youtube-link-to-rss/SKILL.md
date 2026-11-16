---
name: youtube-link-to-rss
description: If a user provides you with a youtube link and tells you to follow the podcast, use this.
---

# Find the RSS feed of a podcast whose youtube link you received

Context - sometimes users will see a podcast on youtube and want you to subscribe to it. They don't realize what they really need is the RSS feed of the podcast. 

That's fine - all we need to do is find the RSS feed which corresponds to the podcast in the youtube link they provided. Once you do that, we'll just ask them if they wanna subscribe to the RSS feed instead.

## Tools

Use the available web tool:

- **Open a page:** `open` with the full YouTube URL. In this session, that was `web__run({open: [{ref_id: URL}]})`.
- **Search the web:** `search_query` with the exact playlist ID or video ID. In this session, that was `web__run({search_query: [{q: QUERY}]})`.
- **Open corroborating results:** use `open` on relevant search results. Use `click` only when the page exposes a useful link.

## Procedure

1. **Preserve and parse the URL.**
   - Keep the complete URL.
   - For a playlist URL, extract the value of the `list` parameter.
   - For a video URL, extract the `v` parameter.
   - If the URL contains both, retain both IDs.

2. **Open the original YouTube URL.**
   Inspect whatever the page actually exposes:
   - playlist or video title
   - channel name
   - video titles, descriptions, and visible links

   Record when the page gives only a generic title or limited metadata. Do not treat “Full Episodes,” a channel name, or a single guest name as sufficient proof of the podcast identity.

3. **Search for the exact ID.**
   If the YouTube page does not identify the show, search the full playlist or video ID in quotes. You can also try the ID with terms such as `podcast`, `episode`, or a distinctive video title.

   Prefer results that contain the same ID and explicitly name a show or episode. Do not rely on a search result that merely discusses a similar topic.

4. **Corroborate the show identity.**
   Check an authoritative source, such as:
   - the podcast’s official website
   - its official YouTube channel
   - an official podcast platform page

   Confirm that the show name, hosts, or episode title match the YouTube evidence. A search result can establish a useful lead; official show information helps verify what the name refers to.

5. **Reflect on the evidence level.**
   Distinguish:
   - **Direct page evidence:** the YouTube page itself names the show or channel.
   - **Search-result association:** an indexed result links the exact YouTube ID to a show or episode.
   - **Corroboration:** an official show source confirms the name or hosts.

   If the evidence is indirect, it “appears to be” or “likely.” If the sources do not establish a reliable match, say you could not identify it from the available evidence.

   **Keep searching using your web tools as necessary until you find the RSS Feed which corresponds to the youtube link**

6. **Once you find the RSS feed, offer the user to subscribe to the RSS feed instead**
   If you were able to find the RSS feed for the podcast they want to subscribe to, tell them that you cannot subscribe to Youtube or ingest their videos directly as it's against their ToS. However, 
   you CAN subscribe to the podcast's RSS feed, which is better because it's the official channel, more reliable, and simpler, while getting them the same result. Ask if you can do that instead. 
   If they answer yes, all you need to do is use your register-watcher-on-podcast skill to register it.

## Example

For playlist ID `PL6Jwmvh8DLmc9WZsrCIgsW_5IDWncrqV5`:

- Opening the playlist exposed the generic title “Full Episodes,” which did not identify the show.
- Searching the exact playlist ID surfaced a link to a Satya Nadella video titled “Satya Nadella | BG2 w/ Bill Gurley & Brad Gerstner” associated with that playlist.
- BG2’s official site confirmed the show name and hosts.

Conclusion: **The playlist appears to be BG2 Pod.** The conclusion is supported by the exact-ID association and official-site corroboration, rather than by the generic playlist title alone.