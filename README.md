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
  - [ ] handle .mp3 file + podcast metadata
  - [ ] handle a youtube link + podcast metadata
  - [ ] update the UI with fields where the above 3 can be provided by the user (.txt upload form done)
- [ ] Connect Codex database tools and a research skill; verify cited answers across the initial episode set.
- [ ] watched sources / scheduled discovery & ingestion
- [ ] email alerts. 
- [ ] Prod env deployment.

- [Local setup and VS Code run configuration](docs/local_development.md)
- [Local Codex chat setup](docs/local_codex_chat.md)
- [Docker and AWS deployment](docs/aws_deployment.md)
- [Longer-term deployment plan](docs/devops.md)
- [High-level design](docs/high_level_design.md)
- [Database design](docs/database_design.md)
- [Ingestion pipeline design](docs/etl_design.md)
