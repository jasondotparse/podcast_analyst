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
  - [ ] handle .mp3 file + podcast metadata (will require a new ingestion step: OpenAI whisper API integration)
  - [ ] handle a youtube link + podcast metadata (will require a new step: use the yt-dlp package to create a .mp3 from the link, and then let it flow through the pipeline as normal)
  - [ ] update the UI with fields where the above 3 can be provided by the user (.txt upload form done)
- [ ] Discovery/ranking, guest relevance, evaluation
  - it's important that we don't confuse extracting lots of claims with extracting useful investment signals. The next step should be a new 'ranking' pipeline stage between claim extraction and graph projection - it can be a simplel system that ranks the claims based upon: attributable, genuinely material, verifiable, guest credibility. This will probably require a few new attribtues on the claims table. It can be implemented cheaply using the Decisions API - just one call per claim prior to postgres write. Now when Codex is researching a question, it can retrieve claims using graph/vector search and prioritize those with higher materiality, stronger source evidence, and more relevant speakers. We should use the combination of the attributes we extract from the ranking stage to create a 'signal_score'. This formula could start as a very basic average of all the 0-100 scores it got on all the attributes mentioned above. See "signal score design" chatGPT thread.
- [ ] Process 10 real episodes
- [x] Connect Codex database tools (`vector_search`, `graph_query`, `postgres_database_query`); cited answers verified locally.
- [x] Conversational thesis capture: Codex helps the user sharpen a thesis, then saves it with `add_thesis` (Postgres + graph).
- [ ] add a new, final step to the current ingestion pipeline: "reflect". reflect() would:
    1. Retrieve active theses from Postgres.
    2. Compare newly extracted claims against those theses using an LLM.
    3. Identify claims that meaningfully support or challenge each thesis.
    4. Persist the assessments in Postgres and create corresponding SUPPORTS or CHALLENGES relationships in Ladybug.
    5. Trigger an SES notification when sufficiently strong contradictory evidence is found.
      * this requires new tool add_subscription() which codex can use to update its subscriptions table. That way, it'll know who to email post-reflect stage when a thesis contradiction is detected and its signal score is let's say >90.
- [ ] watched sources / scheduled discovery & ingestion
- [ ] build "suggested prompts" new users can use these UI elements to auto-populate a prompt into the chat box. The same as 'suggested prompts' in ChatGPT, but hardcoded.
- [ ] build a "architecture overview" tool to allow the agent to read its own code on GitHub - this will be the best way to explain to folks how it works under the hood. Literally just ask it! One of the suggested prompts can just be a 'Explain how you work at a high level'. Will require the creation of a github service account / API key. Readonly, scoped to the podcast_analyst repo. 
- [ ] UI nice-to-haves
	 - in the chat history, it should show more than just user input and agent final_response. It should also display thinking / using tools events as UI elements so the user isn't just sitting there waiting for the agent to reply
	 - graph visualization - ladybugDB already has a graph visualizer tool. We should just deploy and iframe this within our web app. 
	 - onboarding modal when a new browser is detected that explains how the app works
   - clean up the UI to make it more polished. The buttons on the top of the view should go on a sidebar on the left. 
- [ ] support reflection in both directions, not just ingestion:
  	- New podcast ingested: Evaluate its claims against existing theses. (Done) 
  	- New thesis created: Evaluate existing claims against the new thesis.
- [ ] Prod env deployment.

- [Local setup and VS Code run configuration](docs/local_development.md)
- [Local Codex chat setup](docs/local_codex_chat.md)
- [Docker and AWS deployment](docs/aws_deployment.md)
- [Longer-term deployment plan](docs/devops.md)
- [High-level design](docs/high_level_design.md)
- [Database design](docs/database_design.md)
- [Ingestion pipeline design](docs/etl_design.md)
