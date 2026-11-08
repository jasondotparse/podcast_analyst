# Podcast Analyst — Ingestion Pipeline Design

**Status:** Agreed design for the first slice · **Audience:** the engineer building ingestion

Read first: [database_design.md](database_design.md) (schema and contracts),
[diagrams/etl_pipeline.png](diagrams/etl_pipeline.png) (full vision), and
[local_development.md](local_development.md) (local Postgres and running the backend).

## Scope of the first slice

**In:** a `.txt` transcript plus metadata, end to end: API → screeners → passages →
embeddings → claims/entities → graph. Test with `curl` against localhost, then beta.

**Out (later steps or features):** UI upload form and status view, MP3 (Whisper),
YouTube (yt-dlp), hourly watched sources, thesis evaluation ("reflector"), SES alerts,
and the read-only Postgres role (it arrives with the Codex database tools). Each of
these is designed to slot in without restructuring.

## How it works

```text
POST /api/ingestions   (multipart: metadata JSON + transcript .txt)
  ├─ sha256 of the whitespace-normalized transcript → episodes.content_hash
  ├─ already exists? → 409 {job_id, status} of the existing job
  └─ insert podcast (find-or-create by name), episode, ingestion_job(pending) → 202 {job_id}

Background worker: one in-process asyncio task, one job at a time (oldest pending first)
  screen_topic → screen_injection → chunk → embed → extract_claims → project_graph
       │ a screener rejects → job 'skipped', error = reason
       │ any exception      → job 'failed',  error = message
  each step: read input from Postgres → write output to Postgres (or Ladybug)

GET /api/ingestions/{job_id} → {status, current_step, error}   (the client polls)
```

The runner is a plain list of async functions:

```python
STEPS = [screen_topic, screen_injection, chunk, embed, extract_claims, project_graph]

async def run_job(job):
    for step in STEPS:
        await update_job(job.id, status="running", current_step=step.__name__)
        await step(job.episode_id)   # raises Skip(reason) to end the job as 'skipped'
    await update_job(job.id, status="completed")
```

| Step | Does | Skips its work when |
|---|---|---|
| `screen_topic` | One LLM call on the metadata and transcript: is this relevant to public-equities research (companies, markets, tech, macro)? Include the active theses when any exist. Reject with a reason (e.g., a cooking show). | passages already exist |
| `screen_injection` | One LLM call: does the transcript try to instruct an AI system? Reject with a reason. | passages already exist |
| `chunk` | ~400-word passages with ~80 words of overlap, zero-based `ordinal`; timestamps stay NULL for `.txt`. Insert all passages in one transaction. | passages already exist |
| `embed` | Embed passages `WHERE embedding IS NULL`, in batches. | none are NULL |
| `extract_claims` | One LLM call per passage that has no claims yet; it returns claims (`text`, `evidence_quote`, `speaker`, entities `{name, kind}`). Write each passage's claims, entities, and links in one transaction. | the passage has claims |
| `project_graph` | Deterministic `MERGE`s from Postgres: Episode, Passage, `CONTAINS`, Claim, `EVIDENCED_BY`, Entity, `ABOUT` (Claim→Entity), and `MENTIONS` (Passage→Entity, derived from that passage's claim entities). | never; `MERGE` makes it a safe full rerun |

Retrying a job means running the whole list again; finished work is skipped. On
startup, reset `running` jobs to `pending`. A failed job is retried by setting it
back to `pending` (a small `POST /api/ingestions/{job_id}/retry`, or SQL).
`current_step` is for display only, not recovery. A passage that legitimately
yields zero claims is re-extracted on a retry; that's acceptable.

## Key decisions and trade-offs

1. **No workflow framework (Airflow, n8n, Celery, queues).** It's one host, ~20
   episodes, and one job at a time; the database design forbids extra infrastructure.
   Cost: no parallel jobs, and a backend restart interrupts the running job (it resumes).
2. **Functions, not an `IngestionStep` class hierarchy.** Each step is one call; a
   base class adds nothing yet. Steps run one after another; any concurrency lives
   inside a step (embedding batches, a few passages extracted at once).
3. **Postgres, not memory, carries data between steps.** Every step is idempotent
   against database state, so a crash never repeats expensive LLM work.
4. **Dedupe happens in the request, not in the pipeline.** The unique `content_hash`
   gives an immediate `409`. MP3/YouTube will dedupe on `source_url` or
   `external_episode_id` instead, the same way.
5. **Screeners run in the pipeline, not in the request.** A rejection surfaces only
   through polling (status `skipped`), not as a `400`. This keeps all LLM work in one
   place and works the same for MP3/YouTube, whose transcript exists only after
   transcription. A rejected episode row remains, so resubmitting it gets a `409`
   carrying the old job's `skipped` status.
6. **The LLM extracts; code writes the graph. No LLM-generated Cypher.** Transcripts
   are untrusted input, and the design calls for narrow, idempotent graph operations.
   Keep the glue between the model and the database **minimal**: trust the
   structured output. The model never emits UUIDs; code assigns IDs on insert. The
   one required check is to drop any claim whose `evidence_quote` is not a substring
   of its passage text (a design invariant).
7. **Entities live in Postgres** (migration `002`): `entities(id, name, kind,
   UNIQUE(kind, name))` and `claim_entities(claim_id, entity_id)`. Without them, a
   crash between extraction and projection would lose entities on retry (extraction
   is skipped once claims exist), and the graph could not be rebuilt from Postgres.
   Find-or-create entities on `(kind, name)` after trimming; don't merge aliases (AWS ≠ Amazon).
8. **Screening is defense in depth, not a guarantee.** Transcripts are always data:
   pass them in a clearly delimited data section, never as instructions. The
   extraction call has no tools, and Codex's database tools will be read-only.
9. **Chunk by words, not tokens.** That avoids a tokenizer dependency. Overlap means a
   quote can appear in two passages; that's fine.
10. **Claim `fingerprint`** = sha256 of the lowercased, whitespace-collapsed claim text
    plus evidence quote. With `UNIQUE(passage_id, fingerprint)` and `ON CONFLICT DO
    NOTHING`, retries skip exact duplicates; paraphrases are not caught.

## Models and APIs

- **LLM calls** (both screeners, claim extraction): OpenAI Responses API with
  [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses),
  model `gpt-6-luna`, reasoning effort `high`. Use the existing `OPENAI_API_KEY`.
- **Embeddings:** `text-embedding-3-large` with `dimensions=1536`. Query-time
  embeddings (Codex `vector_search`) must use the same model and `dimensions`.
- **New dependencies:** `openai` (async client) and `python-multipart` (FastAPI form uploads).
- **Ladybug:** the FastAPI process owns the graph (`app.state.graph`); the worker
  runs in the same process. Its API is synchronous; wrap the projection in
  `asyncio.to_thread` if it noticeably stalls chat streaming.

## API

`POST /api/ingestions` takes `multipart/form-data` with two fields:
- `metadata` (JSON): `podcast_name` and `title` are required; `description`,
  `published_at` (date), `guests` (list), and `source_url` are optional.
- `transcript`: a UTF-8 `.txt` file.

It returns `202 {job_id, episode_id}`, `409 {job_id, status}`, or `422` for invalid
input. It uses the same login session as the rest of `/api` (local loopback runs
without login).

```bash
curl -s -F 'metadata={"podcast_name":"Chip Talk","title":"Optical interconnects"}' \
     -F transcript=@episode.txt http://127.0.0.1:8000/api/ingestions
curl -s http://127.0.0.1:8000/api/ingestions/<job_id>
```

## Proposed layout and testing

- `src/backend/ingestion.py`: routes, the worker loop, `STEPS`, and the steps. Split
  out LLM prompts and calls only if the file gets unwieldy.
- `src/backend/migrations/002_entities.sql`: the entity tables.
- **Tests:** unit-test the pure parts (chunker, fingerprint, hash). Run end to end
  with a real short transcript on localhost: check passages, embeddings, claims,
  entities, and graph edges, then rerun the job to prove idempotence. Then deploy to
  beta and repeat.

## Later, without restructuring

- **MP3:** add a `transcribe` step (Whisper; segment the audio and keep offsets so
  passages get timestamps).
- **YouTube:** a `download` step (yt-dlp) before `transcribe`.
- **Watched sources:** an hourly discovery that calls the same API.
- **Reflector:** a step after `project_graph` that evaluates new claims against
  active theses → `SUPPORTS`/`CHALLENGES` edges and `alerts` rows → SES.
