# Podcast Intelligence Agent — High-Level Design

**Status:** Proposed architecture (v1)  

## 1. What we're building

An **AI research coworker for public-equities analysts**. It reads relevant podcasts, organizes attributable evidence against the firm's coverage universe, and helps analysts investigate what that evidence means for their investment theses.

The immediate use case: monitor a defined set of podcast sources and investment theses, process **at least 10 episodes**, and provide an interactive, source-grounded research tool. Analysts must be able to discover episodes and guests, inspect claims with timestamps, and ask questions across the processed material.

**Product direction:** Move beyond a reactive podcast-search tool toward a continuously operating research assistant that detects new, potentially material evidence and **proposes** thesis updates. Analysts—not the agent—decide whether to change a thesis or position. Scheduled monitoring and proactive alerts are **P1**, not required for the initial working slice.

## 2. How it works

```text
Podcast sources / episode URL
          |
          v
Discover & rank episodes (company, guest, debate relevance)
          |
          v
Deterministic ingestion pipeline
  acquire captions/transcript OR transcribe audio
  validate quality; keep failures and provenance
  segment transcript; embed passages
          |
          v
PostgreSQL + pgvector  ──>  Structured claim extraction  ──>  LadybugDB
(authoritative evidence)      (attributed, validated)       (relationships)
          |                                                    |
          +----------------------+-----------------------------+
                                 |
                          Codex research agent
                      graph_search + vector_search
                                 |
                          FastAPI / SSE API
                                 |
                             React UI
                   chat / citations / graph / status
```

1. **Discover:** Collect episode metadata from approved sources, score relevance before expensive processing, and record rejected/deferred episodes.
2. **Ingest:** Use existing timestamped transcripts/captions when usable; otherwise attempt supported audio transcription. Preserve acquisition method, quality warnings, failure states, and source timestamps.
3. **Persist evidence:** Store episode metadata, transcript passages, embeddings, processing state, and immutable source references in PostgreSQL. Stable IDs and idempotent writes make retries safe.
4. **Extract knowledge:** Use structured LLM extraction to identify *attributed claims*, people, companies, debates, and relationships. Validate references to real source passages before writing graph nodes/edges.
5. **Investigate:** The agent follows a `SKILL.md` research procedure: interpret the question, traverse the graph, retrieve supporting passages via vector search, and synthesize an answer with actionable citations. It must distinguish claims/opinions from verified facts and abstain when evidence is insufficient.
6. **Eventually monitor (P1):** Periodically discover new episodes, compare new claims with known debates and claims, and notify analysts of high-value potential thesis impacts. Alerts are suggestions for review, not automatic thesis edits.

## 3. Core system design

| Component | Responsibility |
|---|---|
| **React + Vite** | Browser interface: research chat, company/debate views, episode discovery, processing status, evidence inspection, graph visualization. |
| **FastAPI** | API boundary, authentication integration, pipeline control, database access, and streaming agent responses via SSE. |
| **Codex app-server** | Agent runtime for research and synthesis; uses project `SKILL.md` and bounded `graph_search` / `vector_search` tools. |
| **Ingestion pipeline** | Mechanically repeatable acquisition → validation → chunking → embedding → claim extraction → publication. An agent can initiate a run, but does not freely mutate pipeline state. |
| **PostgreSQL + pgvector** | Source of truth for transcripts, episode/guest metadata, passage embeddings, provenance, and durable processing history. |
| **LadybugDB** | Typed property graph for claims, entities, theses, and their relationships. Can be rebuilt from authoritative evidence and validated extractions. |
| **AWS CDK + EC2** | Minimal infrastructure. Initially one EC2 host, Nginx for HTTPS/static React/reverse proxy, FastAPI service, and Cognito for user authentication. |

**Operational rules:** Pipeline stages have explicit statuses, retry/failure handling, and deduplication by stable episode/source IDs. We'll be super basic - just a beta and prod env. No claim is published without a resolvable passage reference. Agent tools have bounded permissions; credentials remain server-side. The browser never accesses databases or the model API key directly.

**Development order:** **P−1** establish fast local development and one-command application deployment (without rerunning CDK for code changes); **P0** deliver one fully cited episode-to-chat vertical slice, then expand to the required ten episodes and assessment cases; **P1** add scheduled monitoring and notifications.

## 4. Core graph data model

**Central unit: `Claim`.** A podcast guest's statement is an *attributed assertion*, not automatically a fact. Multiple claims may support, challenge, or contradict one another. We preserve their history rather than overwriting old claims with a presumed latest truth.

| Node type | Meaning / key fields |
|---|---|
| `Claim` | `claim_id`, normalized assertion, speaker attribution, extraction confidence, evidence IDs, observed/published time |
| `Person` | Speaker/guest identity, role, organization, confidence in identity |
| `Company` | Coverage company (ticker) or relevant counterparty |
| `Thesis` | Working investment thesis for a company |
| `Debate` | Specific investment question or contention under a thesis |
| `Podcast` | Podcast/show identity and source feed |
| `Episode` | Individual release, date, original URL, ingestion status/reference |
| `Passage` | **Reference** to a timestamped PostgreSQL transcript segment (`passage_id`), not the canonical transcript text |

**Important edges:**

```text
(Person)-[:MADE_CLAIM]->(Claim)
(Claim)-[:ABOUT_COMPANY]->(Company)
(Claim)-[:EVIDENCED_BY]->(Passage)
(Passage)-[:PART_OF]->(Episode)-[:FROM_PODCAST]->(Podcast)
(Company)-[:HAS_THESIS]->(Thesis)-[:HAS_DEBATE]->(Debate)
(Claim)-[:SUPPORTS|CHALLENGES]->(Debate)
(Claim)-[:CORROBORATES|CONTRADICTS]->(Claim)
```

Relationships are **typed and evidence-backed**. `CORROBORATES` and `CONTRADICTS` are interpretations to validate, not ground truth. Questions such as *“Which recent podcast claims challenge Amazon's AWS infrastructure returns?”* become graph traversal plus source-passage retrieval. Vector search catches relevant passages that graph extraction may have missed.

## 5. Why these choices

- **Graph + vector search, not vector-only RAG:** Graph traversal represents explicit investment relationships and competing claims; vectors recover semantically related source text. Both are needed for research questions spanning entities and debates.
- **Postgres remains authoritative:** The graph is an evolving interpretation; source evidence, timestamps, and processing history must remain inspectable and reproducible.
- **LadybugDB over Mem0:** We need explicit node/edge types and predictable traversals, not opaque automatic memory reconciliation. Ladybug is the initial lightweight choice; replace it if integration becomes costly.
- **Codex + `SKILL.md`:** Reuse an agent runtime and encode research behavior as instructions/tools instead of hand-building a complex agent harness. Keep ingestion state transitions deterministic.
- **Single-host AWS/CDK:** Fast iteration and minimal DevOps burden matter more than production-scale infrastructure for this one-week prototype. CDK provisions; a separate deployment command ships app changes.
- **Evidence first:** Timestamped, inspectable citations, transparent failures, and human-reviewed thesis changes are essential for investment workflows.

**Design principle:** Ship the smallest coherent, maintainable system that demonstrates a complete evidence → knowledge → research workflow. Favor explicit contracts and verifiable outputs over unnecessary framework complexity.
