# How it works walkthrough

**How it works** in the left cabinet opens a seven-page technical walkthrough in a native
modal dialog. It is the only entry point: there is no route, first-visit redirect, or
automatic launch. Opening and closing it leaves chat and its event stream mounted and
creates no new chat session. It does not read or change onboarding's `localStorage` key.

`src/frontend/src/components/HowItWorks.jsx` holds the chapters, explanatory traces, and
interactive diagrams; `HowItWorks.css` supplies their layout. `DbCabinet.jsx` owns opening,
closing, lazy loading, and focus restoration. The dialog supports Escape and Close; its final action is
**Back to research**. Onboarding and this walkthrough share `WalkthroughNav.jsx` for
Previous/Next, progress dots, and the accessible step announcement.

Each chapter has a diagram with selectable nodes and Play/Pause, previous/next diagram
step, and Reset controls. Chapter buttons allow direct jumps. The diagrams illustrate the
mechanism without making API calls, querying the database, or sending email. They are
explanations of implemented behavior, not live execution traces or service-health views.

## Maintaining the seven chapters

Check source when changing the architecture or copy; older proposed designs in `/docs`
may describe deferred features.

- **Agent harness:** `src/backend/chat.py`, `codex/config.toml`, `codex/tools/`, and
  `codex/skills/` define the Codex thread/turn boundary, dynamic tools, and research procedures.
- **Data layer:** `src/backend/db/migrations/`, `src/backend/db/graph_schema.cypher`, and
  `src/backend/ingestion/claim_extractor.py` define canonical evidence and graph relationships.
- **Ingestion:** `src/backend/ingestion/ingestion_service.py`, `watcher_service.py`, and
  `src/backend/clients/transcription_client.py` define stage order, retries, feeds, and audio.
- **Models + Decisions:** `codex/config.toml`, `src/backend/ingestion/claim_ranker.py`,
  `thesis_reflector.py`, and `ingestion_service.py` define GPT-6-luna use and typed judgments.
- **Retrieval / RAG:** `src/backend/agent_tools.py`, `src/backend/clients/embedding_client.py`,
  `postgres_client.py`, and `graph_client.py` define vector, SQL, and Cypher access.
- **Infrastructure:** `infra/lib/podcast-analyst-stack.ts`, `compose.yaml`, and
  `src/backend/clients/email_client.py` define the single AWS host, storage, permissions, and SES.
- **Delivery / CI/CD:** `scripts/deploy.py`, `Dockerfile`, and `infra/` define developer-triggered
  releases and separate CDK provisioning. There is no GitHub Actions CI or push-triggered deployment.

## Accuracy boundaries

GPT-6-luna handles research/extraction through Codex and screening/ranking/reflection through
the OpenAI Decisions API. Scores and supports/challenges edges are model judgments, not
truth probabilities. Claims retain attribution and a quote validated against a real passage
after whitespace normalization. The current pipeline does not generate passage timestamps;
citations use exact quotes and available episode metadata/URLs. The RAG trace is one possible
agent strategy, not a mandatory sequence of tools.

The infrastructure chapter gives Azure equivalents for AWS services. Beta is a single-host
demo; host files survive app releases but the root disk is deleted on instance termination.
Application deployment is scripted and manually triggered, with manual rollback to a previous
image tag. Tests run separately from deployment. See [AWS deployment](aws_deployment.md).

## Verification

Checked locally in Chrome on November 12, 2026 through Vite and the built app served by
FastAPI. All seven chapters, progress counts, chapter jumps, diagram selection, playback,
pause, stepping, and Reset worked. Returning preserved an unsent chat draft; Escape restored
focus to the cabinet button. The final built walkthrough was inspected at 1280 × 800 with
the diagram, copy, and navigation visible together. No console errors were observed.
The frontend production build and `git diff --check` passed. Beta deployment was not part
of this change; desktop is the intended demo layout.
