# Podcast Analyst — Ingestion Pipeline Design

**Status:** `.txt` slice implemented and verified locally (see [Verification](#verification)) · **Audience:** the engineer building ingestion

Read first: [database_design.md](database_design.md) (schema, contracts, Codex tools),
[agent_harness_design.md](agent_harness_design.md) (the Codex app-server harness),
[diagrams/etl_pipeline.png](diagrams/etl_pipeline.png) (full vision), and
[local_development.md](local_development.md) (local Postgres and running the backend).

## Scope of the first slice

**In:**
- A `.txt` transcript plus metadata, end to end: API → screen → passages → embeddings →
  claims/entities → graph.
- The read-only `postgres_database_query` Codex tool and its role. Only extraction threads
  get the tool for now.

Test with `curl` against localhost, then on beta.

**Out (later steps or features):** UI upload form and status view, MP3 (Whisper),
YouTube (yt-dlp), hourly watched sources, thesis evaluation ("reflector"), SES alerts,
and database tools in chat threads (they arrive with the research skill). Each of these is
designed to slot in without restructuring.

## How it works

```text
POST /api/ingestions   (multipart: metadata JSON + transcript .txt)
  ├─ sha256 of the whitespace-normalized transcript → episodes.content_hash
  ├─ already exists? → 409 with the existing job
  └─ insert podcast (find-or-create by name), episode, ingestion_job(pending) → 202 with the job

Background worker: one in-process asyncio task, one job at a time (oldest pending first)
  screen → chunk → embed → extract_claims → project_graph
     │ the screen rejects → job 'skipped', error names the failed question
     │ any exception      → job 'failed',  error = message
  each step: read input from Postgres → write output to Postgres (or Ladybug)

GET /api/ingestions/{job_id} → {status, current_step, error}   (the client polls)
```

The runner is a plain list of async functions:

```python
STEPS = [screen, chunk, embed, extract_claims, project_graph]

async def run_job(job):
    for step in STEPS:
        await update_job(job.id, status="running", current_step=step.__name__)
        await step(job.episode_id)   # raises Skip(reason) to end the job as 'skipped'
    await update_job(job.id, status="completed")
```

| Step | Does | Skips its work when |
|---|---|---|
| `screen` | One Decisions API call on the metadata and transcript with two `choice` questions, each answered `accept` or `reject`: `on_topic` (relevant to public-equities research: companies, markets, tech, macro?) and `prompt_injection` (does it try to instruct an AI system?). Any `reject` skips the job. No reason is returned. | passages already exist |
| `chunk` | ~750-word passages with ~250 words of overlap, zero-based `ordinal`; timestamps stay NULL for `.txt`. Insert all passages in one transaction. | passages already exist |
| `embed` | Embed passages `WHERE embedding IS NULL`, in batches. | none are NULL |
| `extract_claims` | One Codex thread for the whole episode (below). It reads the transcript with SQL and writes claims one at a time with `write_claim`. When its turn completes, set `episodes.claims_extracted_at`. | `claims_extracted_at` is set; otherwise first delete claims left by an interrupted run |
| `project_graph` | Deterministic `MERGE`s from Postgres: Episode, Passage, `CONTAINS`, Claim, `EVIDENCED_BY`, Entity, `ABOUT` (Claim→Entity), and `MENTIONS` (Passage→Entity, derived from that passage's claim entities). | never; `MERGE` makes it a safe full rerun |

Retrying a job means running the whole list again; finished work is skipped. On
startup, reset `running` jobs to `pending`. An interrupted extraction thread is abandoned,
and its partial claims are deleted when the step reruns. A failed job is retried by setting
it back to `pending` (a small `POST /api/ingestions/{job_id}/retry`, or SQL).
`current_step` is for display only, not recovery.

## Claim extraction with Codex

The app's Codex harness is not only the user's research coworker; it is also a
pipeline component. `extract_claims` starts a dedicated thread on the same long-lived
`codex app-server` process the chat uses:

```text
thread/start  gpt-6-luna, high reasoning, memories off (thread config), read-only sandbox,
              shell disabled, live web search,
              dynamicTools = [postgres_database_query, write_claim]
turn/start    [skill: extract-claims] + "Extract every claim from episode <episode_id>."
   Codex ⇄ postgres_database_query(sql) → transcript, metadata, anything else it needs
   Codex ⇄ web search (optional)        → who a guest is, what a product or company is
   Codex ⇄ write_claim(text, evidence_quote, speaker, entities[{name, kind}]) → ok | error
turn/completed  status completed → set claims_extracted_at; anything else or >10 min → job failed
```

- **`postgres_database_query(sql)`** is the read-only SQL tool from the database design. It
  runs one statement at a time as a SELECT-only role (migration `002`), inside a `READ ONLY`
  transaction. It has a statement timeout and a cap on result size. Because of that cap, the
  skill tells Codex to read long transcripts in slices: with `substr(transcript_text, …)`, or
  passages by `ordinal`.
- **`write_claim` is all the glue there is.** FastAPI maps the thread ID to its
  `episode_id`, so the tool takes no episode ID and cannot write to another episode. It finds
  the lowest-ordinal passage of that episode containing `evidence_quote`, comparing
  whitespace-collapsed text. If none does, it returns an error asking for a verbatim quote, and
  Codex retries. Otherwise, in one transaction, it inserts the claim (`fingerprint`, `ON
  CONFLICT DO NOTHING`), finds or creates its entities, and links `claim_entities`.
  Chat threads never receive `write_claim`.
- **Skill:** `codex/skills/extract-claims/SKILL.md`, synced into `CODEX_HOME` at
  startup like other skills. It is attached explicitly as a `skill` input item on `turn/start`,
  so it does not depend on Codex choosing it. It defines:
  - The relevant schema, and how to read the transcript in slices.
  - What counts as a claim: an attributable assertion relevant to equities research, such as
    a forecast, a figure, or a causal or competitive statement.
  - One atomic, self-contained claim per call, with pronouns resolved and timeframes kept.
    Claims must not overlap; each one has to earn its place.
  - A verbatim quote of up to 250 words.
  - The speaker only when the transcript supports it.
  - Entity kinds `company`, `person`, `technology`, and `concept`, with no alias merging.
  - Web search is for context only, never a claim source.
  - Read the whole transcript, then end with a one-line count.
- **Harness changes:**
  - `codex_client.py` currently declines every server request. It must route `item/tool/call` for
    extraction threads to the two handlers and keep declining everything else.
  - `chat.py` routes notifications only to chat sessions. It must also resolve a per-thread future on
    `turn/completed`.
  - The turn times out after 10 minutes; the step then sends `turn/interrupt` and fails the job.
  - The thread is kept (not ephemeral), so a developer can inspect what Codex did. Log
    its ID with the job.

## Key decisions and trade-offs

1. **No workflow framework (Airflow, n8n, Celery, queues).** It's one host, ~20
   episodes, and one job at a time; the database design forbids extra infrastructure.
   Cost: no parallel jobs, and a backend restart interrupts the running job (it resumes).
2. **Functions, not an `IngestionStep` class hierarchy.** Each step is one call; a
   base class adds nothing yet. Steps run one after another; any concurrency lives
   inside a step (embedding batches).
3. **Postgres, not memory, carries data between steps.** Every step is idempotent
   against database state, so a crash rarely repeats expensive work.
4. **Dedupe happens in the request, not in the pipeline.** The unique `content_hash`
   gives an immediate `409`. MP3/YouTube will dedupe on `source_url` or
   `external_episode_id` instead, the same way.
5. **Screening runs in the pipeline, not in the request.** A rejection surfaces only
   through polling (status `skipped`), not as a `400`. This keeps all model work in one
   place and works the same for MP3/YouTube, whose transcript exists only after
   transcription. A rejected episode row remains, so resubmitting it gets a `409`
   carrying the old job's `skipped` status.
6. **One Decisions API call screens for both topic and injection.** The questions share
   one input, so it's one request and one input charge. Each answer is typed, with nothing to
   parse, and OpenAI documents the API as about 10× faster than Responses. No reason is
   stored; the job error names the question that rejected. A `refusal` answer counts as
   `reject` (fail closed).
7. **Claim extraction is one Codex thread per episode, not one LLM call per passage.**
   A single passage is too little context: speakers, pronouns, and references span the
   episode, and a guest or company may need a quick web lookup. The agent sees the whole
   episode and gathers what it needs with SQL and search, and the code stays small because
   the harness and the SQL tool are needed anyway. Costs:
   - It is slower and pricier per episode: one long high-reasoning turn with many tool calls.
   - It is less deterministic; the skill text and a rerun are the levers.
   - Retry is all-or-nothing per episode.
   - Ingestion depends on the Codex process and on dynamic tools, which are experimental.
8. **Codex reads with read-only SQL and writes through one narrow tool; code writes the
   graph. No LLM-generated writes in SQL or Cypher.** Transcripts are untrusted input, and
   the design calls for narrow, idempotent mutations. Keep the glue **minimal** and trust the
   tool arguments. Codex never passes UUIDs to `write_claim`: the tool locates the passage
   from the quote. The one required check is that the quote appears verbatim in the episode
   (a design invariant). A failure goes back to Codex to fix, rather than being silently dropped.
9. **Entities live in Postgres** (migration `002`): `entities(id, name, kind,
   UNIQUE(kind, name))` and `claim_entities(claim_id, entity_id)`. Without them, a
   crash between extraction and projection would lose entities, and the graph could not be
   rebuilt from Postgres. Find-or-create entities on `(kind, name)` after
   trimming; don't merge aliases (AWS ≠ Amazon).
10. **Prompt-injection safety is the screen's job, not the skill's.** For single
    responsibility, the extraction skill does not repeat injection guidance; it relies on the
    `prompt_injection` screen. Screening is not a guarantee, so the blast radius stays small.
    The transcript reaches Codex only as query output, never as the prompt. The extraction
    thread has:
    - no legacy shell tool (Codex's code-mode `exec` remains; see [Verification](#verification));
    - a read-only sandbox;
    - read-only SQL;
    - one write tool, bound to its own episode.

    At worst, an injected transcript writes bad claims for its own episode, or steers a web
    search or a read query.
11. **Chunk by words, not tokens.** That avoids a tokenizer dependency. With 250 words
    of overlap, any quote of up to 250 words fits inside some passage; a quote found in
    two passages attaches to the first.
12. **Claim `fingerprint`** = sha256 of the lowercased, whitespace-collapsed claim text
    plus evidence quote. With `UNIQUE(passage_id, fingerprint)` and `ON CONFLICT DO
    NOTHING`, a repeated `write_claim` is a no-op; paraphrases are not caught.

## Models and APIs

- **Screen:** OpenAI [Decisions API](https://developers.openai.com/api/docs/guides/decisions)
  (`client.decisions.create`), model `gpt-6-luna` (its only model), with the existing
  `OPENAI_API_KEY`. Input-size limits aren't documented; test with the longest transcript we have.
- **Claim extraction:** a Codex thread, as described above. `gpt-6-luna` at high reasoning
  are already the Git-managed defaults in `codex/config.toml`.
- **Embeddings:** `text-embedding-3-large` with `dimensions=1536`. Query-time
  embeddings (Codex `vector_search`) must use the same model and `dimensions`.
- **New dependencies:** `openai` (async client, for Decisions and embeddings; it needs a version
  that has `client.decisions`) and `python-multipart` (FastAPI form uploads).
- **Ladybug:** the FastAPI process owns the graph (`app.state.graph`); the worker
  runs in the same process. Its API is synchronous; wrap the projection in
  `asyncio.to_thread` if it noticeably stalls chat streaming.

## API

`POST /api/ingestions` takes `multipart/form-data` with two fields:
- `metadata` (JSON): `podcast_name` and `title` are required; `description`,
  `published_at` (date), `guests` (list), and `source_url` are optional.
- `transcript`: a UTF-8 `.txt` file.

Every ingestion endpoint returns the job as
`{job_id, episode_id, status, current_step, error}`. Uploads return `202` (new job), `409`
(the episode already exists; the body is its existing job), or `422` (invalid input).
`GET /api/ingestions/{job_id}` and `POST /api/ingestions/{job_id}/retry` return `404` for
an unknown job. It uses the same login session as the rest of `/api` (local loopback runs
without login).

```bash
curl -s -F 'metadata={"podcast_name":"Chip Talk","title":"Optical interconnects"}' \
     -F transcript=@episode.txt http://127.0.0.1:8000/api/ingestions
curl -s http://127.0.0.1:8000/api/ingestions/<job_id>
```

## Code layout and testing

Each layer only talks to the one below it, and dependencies are passed in through
constructors (`main.py` wires them together at startup):

```text
main.py                          HTTP: endpoints, request parsing, status codes; gets the
  │                              IngestionService through FastAPI Depends
  ▼
ingestion/ingestion_service.py   IngestionService: submit/get/retry, the worker, the steps,
  │                              screening and embedding calls (OpenAI client injected)
  ├─▶ ingestion/claim_extractor.py  ClaimExtractor: the Codex thread and its two tools
  │                                 (ChatHarness and PostgresClient injected)
  ├─▶ clients/postgres_client.py PostgresClient: every SQL statement; returns domain values
  └─▶ clients/graph_client.py    GraphClient: every Cypher statement (MERGE projection)

ingestion/models.py              EpisodeMetadata, IngestionJob, Episode, EpisodeGraph
```

- `src/backend/clients/codex_client.py`, `src/backend/chat.py`: the small harness changes above.
- `codex/skills/extract-claims/SKILL.md`: the extraction instructions.
- `src/backend/db/migrations/002_ingestion.sql`: the entity tables,
  `episodes.claims_extracted_at`, and the SELECT-only role.
- **Tests:** unit-test the pure parts (chunker, fingerprint, hash, and finding a quote's
  passage). Add a check that `postgres_database_query` rejects writes. Run end to end on
  localhost with a real short transcript:
  - Check that claims are sensible and their quotes are verbatim.
  - Check the entities and graph edges.
  - Read the Codex thread to see what it queried and looked up.
  - Rerun the job to prove idempotence.

  Then deploy to beta and repeat.

## Verification

Local runs (`curl` against the backend on port 8000), November 2026:

- **EE Times, "Enabling AI at the Speed of Light"** (4,520 words): 9 passages, 82 claims
  (all with speakers, longest quote 85 words), 90 entities, completed in about 3.5 minutes.
  Retrying the completed job re-projected the graph in seconds and changed nothing else.
- **FT, "How AI is really changing jobs"** (6,359 words): 13 passages, 74 claims, completed.
- A short cooking-show transcript ended `skipped` with `Rejected by screen: on_topic`.
- The graph matched Postgres exactly: 2 episodes, 22 passages, 156 claims, 148 entities,
  533 `ABOUT` and 260 `MENTIONS` edges.
- A duplicate upload returned `409` with the original job; missing metadata returned `422`.
- After splitting the code into service and client layers, and tightening the skill to avoid
  overlapping claims, a clean re-run of EE Times gave 31 denser claims (down from 82),
  45 entities, and a graph matching Postgres. The `202`/`409`/`404`/`422` responses, the
  screen rejection, and the idempotent retry were re-checked.
- **Open question:** the Codex extraction thread also has Codex's built-in code-mode
  `exec` tool (`unified_exec`, on by default; `--disable shell_tool` does not remove it).
  In the first run it listed the repository and read `docs/` before querying the
  transcript. The read-only sandbox still allows reading files. We have not decided
  whether to disable it.

## Later, without restructuring

- **MP3:** add a `transcribe` step (Whisper; segment the audio and keep offsets so
  passages get timestamps).
- **YouTube:** a `download` step (yt-dlp) before `transcribe`.
- **Watched sources:** an hourly discovery that calls the same API.
- **Reflector:** a step after `project_graph` that evaluates new claims against
  active theses → `SUPPORTS`/`CHALLENGES` edges and `alerts` rows → SES. This is likely
  another Codex thread with its own skill and tools.
