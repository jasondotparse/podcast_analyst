# Podcast Analyst

> User: "Keep an eye on semiconductor podcasts. I'm particularly bullish on optical interconnects."

`Agent: "Got it. I'll monitor those sources and let you know if I encounter evidence that challenges your thesis."`

> Two days later...

`Agent (email): "I ingested a new semiconductor podcast. The guest made two claims that could undermine your thesis about optical interconnect adoption. Here's the evidence."`

> User: "Interesting. How does that compare to what other executives have said?"

`Agent: Retrieves related evidence, traverses relationships between companies, claims, and speakers, and produces a cited synthesis.`

React + Vite chat UI with HMR, a FastAPI API, and a local Codex App Server harness
streaming replies over SSE. Use **serve built app** on port 8000, **run full stack
app** for HMR development, or **serve front end** when the backend is already running.

## Current progress

- [x] Local React/FastAPI app with VS Code launch configurations and frontend HMR.
- [x] Codex chat streams replies and uses live web search; config and agent instructions live in Git, with an app-owned `CODEX_HOME`.
- [x] Beta deployed over HTTPS with password login, server-side sessions, persistent Codex state, and the ECR/SSM deploy workflow.
- [x] PostgreSQL + pgvector and LadybugDB schemas on beta, with host-persisted storage and forward-only migrations ([database design](docs/database_design.md)).
- [ ] **Next:** build the ingestion pipeline that can ingest one episode end-to-end: transcript → passages/embeddings → grounded claims → graph projection. (docs/diagrams/etl_pipeline.png)
  - [x] handle .txt file transcript + podcast metadata ([ingestion design](docs/etl_design.md))
  - [x] handle .mp3 file + podcast metadata (a `transcribe` step: ffmpeg segments → OpenAI `gpt-transcribe`)
  - [x] watch podcasts via RSS: the `register-watcher-on-podcast` skill finds a feed, the `register_rss_to_watcher` tool validates and saves it, and an hourly watcher queues new episodes (transcript or audio) into the pipeline ([below](#watching-podcasts-rss))
- [x] Claim ranking: a `rank` pipeline stage between claim extraction and graph projection scores each claim on specificity, materiality, and source standing (Decisions API, one call per claim) and derives `claims.signal` (0–100, their mean). Codex is told to lead with high-signal claims and may web-search to substantiate them ([design](docs/etl_design.md#claim-ranking)).
  - [x] verify on beta with a real episode; retry older jobs to backfill scores
- [x] Connect Codex database tools (`vector_search`, `graph_query`, `postgres_database_query`); cited answers verified locally.
- [x] Conversational thesis capture: Codex helps the user sharpen a thesis, then saves it with `add_thesis` (Postgres + graph).
- [x] watched sources / scheduled discovery & ingestion (RSS watchers)
- [x] Thesis reflection and alerts: `rank → reflect → project_graph → notify`; Decisions assessments, stance edges, chat subscriptions, and SES email for challenges with strength ≥75 and signal ≥60 ([design](docs/etl_design.md#thesis-reflection-and-alerts)).
- [x] support reflection in both directions, not just ingestion:
  	- New podcast ingested: Evaluate its claims against existing theses. (Done) 
  	- New thesis created: Evaluate existing claims against the new thesis.
- [ ] Process 10 real episodes
- [ ] update readme.md & docs with your product vision, tradeoffs made, etc. 

XXX PO REQUIREMENTS COMPLETE AT THIS POINT XXX

- [x] build "suggested prompts" new users can use these UI elements to auto-populate a prompt into the chat box. The same as 'suggested prompts' in ChatGPT, but hardcoded.
- [x] Left-hand cabinet lists theses, watched podcasts, email subscriptions, and recent ingestion status.
- [x] [Knowledge graph](docs/knowledge_graph_design.md): thesis overview, source evidence, and connection expansion.
- [ ] build a "architecture overview" tool to allow the agent to read its own code on GitHub - this will be the best way to explain to folks how it works under the hood. Literally just ask it! One of the suggested prompts can just be a 'Explain how you work at a high level'. Will require the creation of a github service account / API key. Readonly, scoped to the podcast_analyst repo. 
- [ ] in the chat history, it should show more than just user input and agent final_response. It should also display thinking / using tools events as UI elements so the user isn't just sitting there waiting for the agent to reply
- [ ] clean up the UI to make it more polished. The buttons on the top of the view should go on a sidebar on the left. 
- [ ] later: a novelty / non-consensus axis on the 'signal' measure - guest credibility as a property of the speaker rather than each claim
- [ ] a 'new user' onboarding screen (can just be 'new session' onboarding screen for demo purposes, if we boost session lifespan to like 1 day). This should be a few pages that teach the user how to use the agent - can be a single index.html deployed to newuser.podcastanalyst.com
- [ ] exact same thing for actually describing *how it works under the hood*. deployed to howitworks.podcastanalyst.com
- [ ] Prod env deployment.

## Watching podcasts (RSS)

Ask the chat to *"Start watching the Dwarkesh Podcast."* Codex follows the
`register-watcher-on-podcast` skill: it web-searches for the official RSS feed and calls
`register_rss_to_watcher`. The backend then fetches and validates the feed (http(s) only, public
hosts only, timeouts and size caps) and saves it in `watch_sources`.
- **First run:** only the newest episode at registration is ingested; the archive is not backfilled.
- **Polling:** every minute the watcher checks whether the pipeline is idle (no pending or running job).
  When it is, it fetches the feeds not checked for an hour and queues **one** new episode, compared by
  RSS GUID against `episodes`. It uses the feed's transcript (plain text, WebVTT, or SRT) if there is
  one, otherwise the audio enclosure, through the existing ingestion path.
- **Limitations:** no UI to remove feeds (the left-hand cabinet lists them); episodes that drop out of a feed before they are
  queued are missed; video-only feeds and HTML/JSON transcripts are not supported; and a failed
  job is not retried automatically.

Details: [RSS watchers](docs/etl_design.md#rss-watchers).

## Documentation

- [Local setup and VS Code run configuration](docs/local_development.md)
- [Local Codex chat setup](docs/local_codex_chat.md)
- [Docker and AWS deployment](docs/aws_deployment.md)
- [Longer-term deployment plan](docs/devops.md)
- [High-level design](docs/high_level_design.md)
- [Database design](docs/database_design.md)
- [Ingestion pipeline design](docs/etl_design.md)
