# Podcast Analyst — Ingestion Pipeline Design

**Status:** `.txt` and `.mp3` ingestion implemented and verified locally and on beta (see [Verification](#verification)); [claim ranking](#claim-ranking) implemented and verified locally; [RSS watchers](#rss-watchers) feed new podcast episodes into it · **Audience:** the engineer building ingestion

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

**Still deferred:** YouTube (yt-dlp). The UI, MP3, watchers, chat tools, reflection, and email alerts have since been implemented.

## How it works

```text
POST /api/ingestions   (multipart: metadata JSON + transcript .txt or audio .mp3)
  ├─ sha256 of the whitespace-normalized transcript, or of the raw MP3 bytes → episodes.content_hash
  ├─ already exists? → 409 with the existing job
  └─ insert podcast (find-or-create by name), episode, episode_audio (MP3 only),
     ingestion_job(pending) in one transaction → 202 with the job

Background worker: one in-process asyncio task, one job at a time (oldest pending first)
  transcribe → screen → chunk → embed → extract_claims → rank → reflect → project_graph → notify
     │ the screen rejects → job 'skipped', error names the failed question
     │ any exception      → job 'failed',  error = message
  each step: read input from Postgres → write output to Postgres (or Ladybug)

GET /api/ingestions/{job_id} → {status, current_step, error, ...}   (the client polls)
GET /api/ingestions/active   → the running job, else the oldest pending one, else null
```

The runner is a plain list of async functions:

```python
STEPS = [transcribe, screen, chunk, embed, extract_claims, rank, reflect, project_graph, notify]

async def run_job(job):
    for step in STEPS:
        await update_job(job.id, status="running", current_step=step.__name__)
        await step(job.episode_id)   # raises Skip(reason) to end the job as 'skipped'
    await update_job(job.id, status="completed")
```

| Step | Does | Skips its work when |
|---|---|---|
| `transcribe` | For an MP3: ffmpeg re-encodes the stored audio to mono 64 kbps and cuts 20-minute segments (~10 MB; the API rejects files over 25 MB). Each segment goes to `gpt-transcribe` in order, with the last ~200 words of the previous segment's text as `prompt`. The texts are joined with newlines and saved to `episodes.transcript_text`; every later step reads that column, so none of them changed. All or nothing: an ffmpeg error (its stderr tail becomes the job error), a failed segment, or empty output fails the job and saves nothing. No timestamps: MP3 passages keep NULL `start_seconds`/`end_seconds`. | `transcript_text` is set (every `.txt` upload, or an MP3 already transcribed) |
| `screen` | One Decisions API call on the metadata and transcript with two `choice` questions, each answered `accept` or `reject`: `on_topic` (relevant to public-equities research: companies, markets, tech, macro?) and `prompt_injection` (does it try to instruct an AI system?). Any `reject` skips the job. No reason is returned. | passages already exist |
| `chunk` | ~750-word passages with ~250 words of overlap, zero-based `ordinal`; timestamps stay NULL for `.txt`. Insert all passages in one transaction. | passages already exist |
| `embed` | Embed passages `WHERE embedding IS NULL`, in batches. | none are NULL |
| `extract_claims` | One Codex thread for the whole episode (below). It reads the transcript with SQL and writes claims one at a time with `write_claim`. When its turn completes, set `episodes.claims_extracted_at`. | `claims_extracted_at` is set; otherwise first delete claims left by an interrupted run |
| `rank` | One Decisions API call per claim (8 at a time) with three `score` questions, each rated against five anchored levels: `specificity`, `materiality`, `source_standing`. Each becomes a 0–100 column on `claims`, and `signal` is their mean ([Claim ranking](#claim-ranking)). | `claims.signal` is set; it scores only `WHERE signal IS NULL` |
| `reflect` | Decisions assesses each claim against missing active theses; saves stance and strength, including unrelated. | the pair already has a `thesis_assessments` row |
| `project_graph` | Deterministic `MERGE`s from Postgres: Episode, Passage, `CONTAINS`, Claim (with `signal`), `EVIDENCED_BY`, Entity, `ABOUT` (Claim→Entity), and `MENTIONS` (Passage→Entity, derived from that passage's claim entities); replace `SUPPORTS`/`CHALLENGES` edges with strength ≥25. | never; `MERGE` makes it a safe full rerun |
| `notify` | Snapshot high-signal challenges and subscribers; send one plain-text SES email per recipient. | alert is already `sent`; disabled email only logs |

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

## Claim ranking

Extracting many claims is not the same as finding investment signal. The `rank` step
(`ingestion/claim_ranker.py`) scores every claim so the chat agent can lead with the ones
that matter, and leaves the rest in place for recall.

**Rubric.** One Decisions API call per claim carries three `score` questions. Each has five
anchored levels (0–4); the API returns the probability-weighted level, which becomes a
0–100 component (`level / 4 × 100`, rounded):

| Axis | What it measures | Level 0 … level 4 |
|---|---|---|
| `specificity` | How checkable the claim is: a measurable quantity, a timeframe, a named subject. Verifiability and falsifiability fold into this axis. | vague sentiment … quantified, time-bound, and about a named subject |
| `materiality` | How much it would move an analyst's estimates or view of an identifiable public company, if true. | anecdote … bears directly on a key debate or earnings driver |
| `source_standing` | How well placed the speaker is to know, and whether the statement is first-hand ("we are seeing") or hearsay ("I read that"). Judged from `speaker`, the `guests` metadata, and the quote's wording. | unknown speaker, no basis … a direct participant on their own business |

**Formula.** `signal = round(mean(specificity, materiality, source_standing))`. A claim with
no speaker is penalized only through `source_standing`, which scores about 0 for an unknown
speaker; an extra discount for `speaker IS NULL` was tried and dropped because it penalized
the same thing twice. All four values are stored, so the formula can change with an
`UPDATE`, without another API call.

**What the scorer sees:** the claim text, its evidence quote, its speaker, and a short
header (podcast, episode title and date, episode description, guests). Not the transcript.
The `guests` field does real work for `source_standing`, so it is worth filling in.

**Failures.** A refused question scores its axis 0 (logged); an API error fails the job,
and a retry scores whatever is still `NULL`. To re-score after changing the rubric, set
`signal` to `NULL` for the claims in question and retry the job. Episodes ingested before
this step have `NULL` scores until their job is retried, which also re-projects the graph.

**At research time.** `claims.signal` and its components are visible to
`postgres_database_query`, and `project_graph` copies `signal` onto the graph's `Claim`
node so a traversal can sort without a second query. The chat instructions tell Codex to
lead with high-signal claims when evidence is plentiful, and that it may run a quick web
search to check whether a high-signal claim has since been confirmed or contradicted,
reporting that separately from the podcast evidence.

**Deferred:** a novelty / non-consensus axis (would a sector analyst already know this?).
It is the most valuable signal and the hardest to score without outside knowledge; the
Decisions API has no web search. Per-speaker credibility as a property of a Person
entity rather than of each claim is also left for later.

## Thesis reflection and alerts

`reflect` uses one `gpt-6-luna` Decisions call per claim, eight concurrently, with two
questions per missing active thesis: stance (`supports`, `challenges`, `unrelated`) and
strength. The five strength anchors are no bearing, tangential, contributing factor,
core driver/premise, and predicted outcome/timeline. The weighted 0–4 score maps to
0–100 exactly like ranking. Refused stance becomes unrelated; refused strength becomes 0.
Every pair is saved once, including unrelated, in migration `007_thesis_assessments`.
Thesis text goes in questions; claim, quote, speaker, and episode context go in input.

`project_graph` replaces stance edges from Postgres; strength ≥25 preserves research
recall. `notify` follows projection and requires an active subscribed thesis, challenges,
strength ≥75, and signal ≥60 (a placeholder to tune; NULL never qualifies). Alerts snapshot
all qualifying `{claim_id, strength, signal}` matches and recipient addresses. Each recipient
gets a separate plain-text email, strongest first, at most ten claims with quotes and sources.
The closing app link invites investigation; the analyst decides whether to revise a thesis.

`add_subscription` normalizes the address and checks every thesis is active. “All my theses”
means currently active theses only; future theses need a new subscription. Email is disabled
when `PODCAST_ALERT_SENDER` is unset: subject/body are logged and alerts stay pending. Retries
may repeat these local logs, but never create a duplicate alert row. SES errors mark failed
and fail the job visibly; retries skip sent alerts. Partial recipient failures may resend
to recipients reached earlier in the same alert. Alpha also offers scheduled digests, one email per
cron occurrence with both supporting and challenging evidence; see
[scheduled thesis digests](#scheduled-thesis-digests). They do not change this alert path.

When idle, the worker assesses at most 50 claims across episodes and projects their stances,
without notifying. This connects new theses to old evidence and backfills existing theses.
Errors are logged and delay the sweep by 60 seconds. **Deferred by owner:** a failure after
assessments commit but before projection can leave missing graph edges; pair idempotency
means the sweep will not repair them automatically. Manually re-project affected episodes.

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
   (or a matching `source_url`) gives an immediate `409`. For an MP3 the hash is the sha256
   of the raw file bytes: re-uploading the same file is caught, but the same episode in a
   different encoding is caught only by `source_url`. YouTube will dedupe on `source_url` or
   `external_episode_id`, the same way.
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
13. **Uploaded audio is stored in Postgres** (`episode_audio`, a `BYTEA` row written in the
    same transaction as the episode and job), following decision 3: the worker reads it
    later, including after a restart, with no file storage to manage. It is kept after
    transcription. `podcast_reader` cannot select it, so Codex's SQL tool never reads audio.
    Cost: a 100 MB upload is held in memory in the request and again in the step, which is why
    uploads are capped at 300 MB.
14. **Ranking is its own step after `extract_claims`, not part of `write_claim`.** It follows
    the `embed` idiom: score `WHERE signal IS NULL`, so retries are idempotent and a rubric change
    is re-scored by nulling `signal`, not by re-running a multi-minute extraction thread. It also
    keeps `write_claim` fast inside the extraction turn's 10-minute budget. Low-signal claims are
    kept, not deleted: they remain recall for vector search, and the threshold will move. Anchored
    `score` levels were chosen over a single 0–100 rating because they calibrate better across
    episodes, and the weighted level still gives fractional resolution.

15. **Reflection persists before projection; notification follows projection.** Postgres remains
    authoritative and readers find the evidence before email arrives.
16. **Low graph threshold, high email threshold.** Keep research connections broad while avoiding
    weak alerts; unrelated rows prevent repeat API work. Plain text preserves quotes as data.
17. **Idle batches, no extra worker.** Handle new theses and backfill with one loop; defer durable
    recovery between assessment and projection for this demo.

## Models and APIs

- **Transcription:** [`gpt-transcribe`](https://developers.openai.com/api/docs/guides/speech-to-text)
  through `client.audio.transcriptions.create` (`model`, `file`, `prompt`; default JSON
  response, read as `.text`). Not `whisper-1`: it is deprecated and shuts down 2027-02-26. The
  pinned `openai` 3.46.0 already lists `gpt-transcribe`. The API takes files up to 25 MB.
  `ffmpeg` must be on the backend's `PATH` (installed in the Docker image).
- **Screen:** OpenAI [Decisions API](https://developers.openai.com/api/docs/guides/decisions)
  (`client.decisions.create`), model `gpt-6-luna` (its only model), with the existing
  `OPENAI_API_KEY`. Input-size limits aren't documented; test with the longest transcript we have.
- **Claim extraction:** a Codex thread, as described above. `gpt-6-luna` at high reasoning
  are already the Git-managed defaults in `codex/config.toml`.
- **Ranking:** the Decisions API again (`gpt-6-luna`), with `score` questions: `instructions`
  plus ordered `levels` (`label`, `description`); the answer's `score` is the
  probability-weighted level index, with a `confidence` we do not store. About 600 input tokens
  per claim at $0.10 per million, so well under a cent per episode.
- **Reflection:** Decisions (`gpt-6-luna`), two questions per missing active thesis, one request per claim.
- **Email:** boto3 `sesv2.send_email` in `asyncio.to_thread`, us-east-1, instance-role credentials.
- **Embeddings:** `text-embedding-3-large` with `dimensions=1536`. Query-time
  embeddings (Codex `vector_search`) must use the same model and `dimensions`.
- **New dependencies:** `openai` (async client, for Decisions and embeddings; it needs a version
  that has `client.decisions`) and `python-multipart` (FastAPI form uploads).
- **Ladybug:** the FastAPI process owns the graph (`app.state.graph`); the worker
  runs in the same process. Its API is synchronous; wrap the projection in
  `asyncio.to_thread` if it noticeably stalls chat streaming.

## API

`POST /api/ingestions` takes `multipart/form-data` with `metadata` and exactly one file:
- `metadata` (JSON): `podcast_name` and `title` are required; `podcast_description`,
  `description` (the episode's), `published_at` (date), `guests` (list), and `source_url`
  are optional. A provided `podcast_description` replaces the podcast's stored description.
- `transcript`: a UTF-8 `.txt` file, **or**
- `audio`: an `.mp3` file of up to 300 MB (`MAX_AUDIO_BYTES` in `main.py`).

Every ingestion endpoint returns the job as
`{job_id, episode_id, status, current_step, error, podcast_name, title}` (the last two
label the episode for the UI). `GET /api/ingestions/active` returns the job the worker is
running, else the next pending one, else `null`, whatever its source: an upload, another
tab, or a watcher. The UI's status bar polls it. Uploads return `202` (new job), `409`
(the episode already exists; the body is its existing job), `413` (audio over the limit), or
`422` (invalid input: missing metadata, both or neither file, or audio not named `.mp3`).
`GET /api/ingestions/{job_id}` and `POST /api/ingestions/{job_id}/retry` return `404` for
an unknown job. It uses the same login session as the rest of `/api` (local loopback runs
without login).

```bash
curl -s -F 'metadata={"podcast_name":"Chip Talk","title":"Optical interconnects"}' \
     -F transcript=@episode.txt http://127.0.0.1:8000/api/ingestions
curl -s -F 'metadata={"podcast_name":"Chip Talk","title":"Memristors"}' \
     -F audio=@episode.mp3 http://127.0.0.1:8000/api/ingestions
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
  ├─▶ ingestion/claim_extractor.py  ClaimExtractor: the Codex thread and write_claim
  │                                 (ChatHarness, PostgresClient, AgentTools injected)
  ├─▶ ingestion/claim_ranker.py   ClaimRanker: the rubric, the Decisions call, signal_score()
  │                                 (PostgresClient, OpenAI client injected)
  ├─▶ ingestion/thesis_reflector.py  ThesisReflector: missing pair assessments (Postgres only)
  ├─▶ ingestion/notifier.py     Notifier: alert snapshots, plain-text rendering, delivery status
  ├─▶ clients/email_client.py   EmailClient: SES (disabled without sender)
  ├─▶ clients/embedding_client.py EmbeddingClient: the one embedding model + dimensions
  ├─▶ clients/transcription_client.py  TranscriptionClient: ffmpeg segments → gpt-transcribe
  ├─▶ clients/postgres_client.py PostgresClient: every SQL statement; returns domain values
  └─▶ clients/graph_client.py    GraphClient: every Cypher statement (MERGE projection)

ingestion/models.py              EpisodeMetadata, IngestionJob, Episode, Claim, Signal, EpisodeGraph, Feed, ...
```

RSS watchers sit beside this stack: `ingestion/watcher_service.py` (`WatcherService`) calls
`IngestionService.submit`, `clients/feed_client.py`, and `PostgresClient`; see [RSS watchers](#rss-watchers).
So do alpha's scheduled digests: `ingestion/digest_service.py` (`DigestService`) reads `PostgresClient` and
sends through the same `EmailClient`; see [scheduled thesis digests](#scheduled-thesis-digests).

- `src/backend/clients/codex_client.py`, `src/backend/chat.py`: the small harness changes above.
- `codex/skills/extract-claims/SKILL.md`: the extraction instructions; tool definitions are in `codex/tools/`.
- `src/backend/db/migrations/002_ingestion.sql`: the entity tables,
  `episodes.claims_extracted_at`, and the SELECT-only role.
- `src/backend/db/migrations/006_claim_signal.sql`: the four score columns on `claims`;
  `graph_schema.cypher` adds `Claim.signal` with `ALTER TABLE … ADD IF NOT EXISTS`.
- **Tests:** unit-test the pure parts (chunker, fingerprint, hash, finding a quote's
  passage, and `signal_score`). The ranker is tested against Postgres with a fake Decisions
  client: it scores only unranked claims, a refusal scores 0, and `project_graph` carries
  `signal` onto the Claim node. Add a check that `postgres_database_query` rejects writes. Run end to end on
  localhost with a real short transcript:
  - Check that claims are sensible and their quotes are verbatim.
  - Check the entities and graph edges.
  - Read the Codex thread to see what it queried and looked up.
  - Rerun the job to prove idempotence.

  Then deploy to beta and repeat.

Reflection tests extend `test_ingestion.py` with fake Decisions/email clients: pair idempotency,
refusals, inactive theses, sweep limits/errors, stance replacement and thresholds, subscription
validation, alert gates, separate recipients, failed/sent retries, and disabled delivery.
`test_db.py` checks migration 007, stance/strength constraints, uniqueness, and cascading deletion.

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
**Beta** (release `3e0d37683c2e-20261108T211604-a65dcd`, public HTTPS API with login):

- EE Times: 9 passages, 41 claims; FT: 13 passages, 35 claims; the cooking transcript was
  skipped by `on_topic`. `202`/`409`/`404`/`422` and `401` without login behaved as locally;
  a retry changed nothing. The graph matched Postgres (76 claims, 103 entities, 249 `ABOUT`).
- The first beta run surfaced `deadlock detected` from `write_claim`: Codex saves claims in
  parallel, and claims sharing entities upserted them in different orders. `save_claim` now
  upserts entities in sorted order; a concurrency test covers it, and the re-run had no deadlocks.
- **Upload form** (release `d07820a980b9-20261108T214740-677d73`): driven in Chrome
  (Playwright) locally and on beta through the login page. A `.json` file was rejected,
  the Bill Gates FT transcript was dropped in and submitted (`202`, modal closed) and then
  completed the pipeline (35 claims locally); re-submitting it showed the `409` message in
  the open modal; Escape closed it.
- Codex sometimes `cat`s `SKILL.md` through `exec` even though the skill body is already
  attached to the turn.

**MP3** (November 9, 2026), with EE Times Current, "Integrating Interface Memristors in the
Back End of Line": a 42-minute, 101 MB, 320 kbps stereo MP3, so 3 segments (20 + 20 + 2.3 min).

- **Locally:** the first 6 minutes (14 MB, cut with `ffmpeg -c copy`) became 768 words,
  2 passages, and 6 sensible claims with verbatim quotes, completing in about a minute.
  The full file uploaded in 4 s; transcription took about a minute (ffmpeg about 14 s), and
  the job completed in under 5 minutes: 6,885 words, 14 passages, 39 claims (36 with speakers,
  thanks to the `guests` metadata), 60 entities, all quotes verbatim, and a graph matching
  Postgres (14 passages, 39 claims, 130 `ABOUT`). The screen accepted the 6,885-word transcript.
- **Beta** (release `c1ff8643ecda-20261110T020829-0310e6`): the full file was dropped into the
  upload modal through the login page (Playwright). The bar showed "Transcribing audio…", and
  the job completed in about 4 minutes: 6,878 words, 14 passages, 45 claims (44 with speakers),
  70 entities, all quotes verbatim.
- **Cut points:** each 20-minute cut can fall mid-sentence; the next segment then starts
  with a capital ("…the kind of question I could send them a follow-up. || Or something for a
  later episode"). At the first cut, the local run dropped one word ("by the— || Of two years"
  for "by the end of two years"); beta's run kept it. No repeated or invented text at either cut.
- **Dedupe and retry:** re-uploading the file returned `409` with the original job: locally
  both with the same `source_url` and with none (hash only); on beta by re-submitting it in the
  modal without a `source_url`, which showed the "already uploaded" message. Retrying the completed job finished in 3–4 s with the transcript
  hash and claims unchanged, so nothing was transcribed again.
- **`.txt` unchanged:** locally, a 768-word `.txt` completed (2 passages, 9 claims, no
  `episode_audio` row) and its re-upload returned `409`; on beta, a cooking transcript passed
  `transcribe` at once and was skipped by `screen` (`on_topic`), and its re-upload returned `409`.
- **Errors:** a text file named `.mp3` failed at `transcribe` with ffmpeg's message ("Invalid
  data found when processing input"). On beta, both files, neither file, and a `.wav` name
  returned `422`; no login returned `401`.
- **Transcripts have no speaker labels**, so Codex attributes speakers only from context and
  metadata. Proper names can come out phonetically: the intro has "Ralph van Kemmen", while a
  later mention reads "Ralph Etienne-Cummings", the name the claims use.

**Claim ranking** (November 10, 2026, locally):

- A new 1,500-word excerpt of "Enabling AI at the Speed of Light" (with `guests`) completed in
  about 2 minutes: 15 claims, signal 32–68. The guest's claims scored source standing 63–100;
  the host's (a journalist) 26–27.
- Retrying the 10 earlier completed jobs ranked their 288 claims in about 25 s, with no refusals
  or errors. The top claims are first-hand executive statements with figures (Axon's CEO on
  Taser's share price and body-camera volumes); the bottom are unattributed technical explanations.
- Unattributed claims (23) first scored 12–31: source standing was about 0 for an unknown speaker,
  and a 0.75 factor applied on top. The factor was then dropped, and one `UPDATE` recomputed
  `signal` from the stored components (no API calls): they now score 16–42, and the 280
  attributed claims were unchanged. All 303 claims: 6 below 20, 63 at 20–40, 142 at 40–60,
  86 at 60–80, 6 at 80+. Most unattributed claims come from test uploads without `guests` metadata.
- The graph matched Postgres: 303 `Claim` nodes, each with the same `signal`.
- Asked about optics and copper, the chat cited claims with their signal scores and led with the
  highest.

- **Open question:** the Codex extraction thread also has Codex's built-in code-mode
  `exec` tool (`unified_exec`, on by default; `--disable shell_tool` does not remove it).
  In the first run it listed the repository and read `docs/` before querying the
  transcript. The read-only sandbox still allows reading files. We have not decided
  whether to disable it.

**Thesis reflection and alerts — local** (November 11, 2026): 57 backend tests passed against
Postgres and four CDK tests passed. The initial idle sweep assessed 303 claims against three
existing theses (909 pairs). A 698-word synthetic executive interview completed in 93 s with
20 claims. Chat saved a new optics thesis, offered email alerts, and subscribed the approved
address. The new thesis got assessments across all 323 claims within 17 s; the first rows
arrived after about 2 s. Chat found 21 CHALLENGES edges and read a linked claim/quote.
A second synthetic interview produced six qualifying challenges, one pending alert and one
plain-text email log with quote, speaker, source context, scores and app link. Retrying the job
left one alert row and logged the email again, as intended in disabled-email mode. By-eye
review of the first episode's 20 assessments found no false positives at the alert gate;
some direct cost and power challenges scored below strength 75, so the gate may miss evidence.
Across 1,364 local assessments: 70 challenges (strength 30–99), 18 supports (30–70),
1,276 unrelated (0–70). The two synthetic episodes together had 12 gate-qualifying claims.

**Thesis reflection and alerts — beta** (November 11, 2026): Startup backfilled 567
existing claims against two theses, then a chat-created optics thesis was assessed against
those claims. The 698-word synthetic interview uploaded through the beta UI produced six
qualifying challenges. The first notify attempt failed because the SESv2 client was used as
a context manager; after the client fix, retry completed and sent the existing alert row.
The approved recipient received one email in the inbox with all six claims, quotes, speakers,
sources, scores, and an app link. A second retry completed with the same single `sent` alert
row and six saved matches; no second message appeared in the Gmail thread. Across 1,749 beta
assessments: 26 challenges (strength 48–98), 21 supports (49–98), and 1,702 unrelated (0–68).

## RSS watchers

**Status:** implemented and verified locally and on beta (November 10, 2026); see [Watcher verification](#watcher-verification).

All 20 podcasts in the case study's starting universe publish RSS feeds. A feed gives each
episode a GUID, title, show notes, publish date, and an audio `<enclosure>`, and some give a
`<podcast:transcript>`. So automated monitoring polls registered feeds instead of
scraping YouTube. The watcher only *acquires* episodes; it queues them through the same
`IngestionService.submit` as uploads, and the pipeline above runs unchanged.

```text
chat: "Start watching the Dwarkesh Podcast"
  └─ skill register-watcher-on-podcast: web search → official feed URL
       └─ tool register_rss_to_watcher(feed_url)
            ├─ fetch + validate the feed (RSS or Atom; ≥1 dated episode with audio or a supported transcript)
            ├─ invalid → {status: "invalid", error}
            └─ INSERT watch_sources(url, description = feed title, ingest_since = newest episode's date)
                 → {status: "registered" | "already_registered", ...}

WatcherService.run: one in-process asyncio task, every 60 s
  ├─ an ingestion job is pending or running? → do nothing this tick
  └─ for each feed not checked for an hour (least recently checked first):
       set last_checked_at; fetch the feed (an error → log, next feed)
       for each episode published ≥ ingest_since, newest first:
         already an episode with this GUID (same podcast) or source_url? → next
         <podcast:transcript> in text/plain, WebVTT, or SRT? → submit(transcript=…)
         otherwise download the audio enclosure → submit(audio=…)
         a download error → log, next episode;  queued → stop (one episode per tick)
```

- **No backfill.** `ingest_since` is the newest episode's publish time at registration, so
  the first check queues only that episode; later checks queue episodes published after it.
  Older episodes are never ingested.
- **Dedupe:** the watcher sets `episodes.external_episode_id` to the GUID (falling back to the
  enclosure URL), and `UNIQUE(podcast_id, external_episode_id)` already exists. Every check
  compares the feed's episodes against those rows, so several new episodes are picked up
  across several checks, newest first. `submit`'s own `content_hash`/`source_url` dedupe
  still applies. A job that fails or is skipped still counts as seen; retry it with
  `POST /api/ingestions/{job_id}/retry`.
- **Strictly serial:** a tick queues at most one episode, and only while no job is pending
  or running. The existing worker runs it. No queue, lock, or extra worker was added. A manual
  upload that arrives while a tick is downloading is queued as usual, and the worker still runs
  jobs one at a time.
- **Failures** (unreachable or malformed feeds, failed downloads, a missing transcript) are
  logged as warnings and skipped. A failed feed is retried at its next hourly check, and a
  failed download is retried at that feed's next check. An unexpected exception is logged,
  and the loop continues on the next tick.
- **Metadata:** podcast name and description come from the channel, and the episode title,
  show notes (HTML stripped), and publish date come from the item. `source_url` is the
  episode's own page (`<link>`), or its audio URL when the feed links every episode to the
  same page.
- **Fetching safety (basic):** http(s) only; the host and every redirect hop must resolve
  only to public addresses (no private, loopback, link-local, or metadata IPs); 10 s connect
  and 30 s read timeouts; caps of 20 MB for a feed, 5 MB for a transcript, and
  `MAX_AUDIO_BYTES` (300 MB) for audio. XML is parsed with the standard library (expat
  ≥ 2.4.1 refuses entity-expansion bombs). This is not DNS-rebinding-proof.
- **Code:** `clients/feed_client.py` (`FeedClient`: fetch, parse, download),
  `ingestion/watcher_service.py` (`WatcherService`: `register`, `run`, `check_feeds`), and
  migration `005_rss_watchers.sql` (`watch_sources.ingest_since`). The skill is
  `codex/skills/register-watcher-on-podcast/SKILL.md`, and the tool is
  `codex/tools/register_rss_to_watcher.json`, a chat tool handled by `AgentTools`.
  `httpx2` (already installed for `openai`) does the HTTP.

**Limitations (deliberately deferred):**
- Watcher jobs show in the UI's status bar like uploads, but there is no UI or API to list,
  pause, or remove feeds. Ask Codex (it can read `watch_sources`),
  or set `is_active = false` in SQL.
- Episodes that leave a feed before they are queued are missed. An episode whose
  `pubDate` is earlier than `ingest_since` is never ingested, and undated episodes are ignored.
- Video-only enclosures and HTML/JSON transcripts are not supported. A broken
  enclosure is retried every hour, with a warning each time.
- A long episode can exceed the extraction step's existing 10-minute Codex timeout.
- A burst of new episodes across many feeds drains at one episode per tick, once the previous
  job finishes.

### Watcher verification

**Prod, November 12, 2026 (Central):** a Chrome follow request registered No Priors,
queued its newest Max Jaderberg episode within 18 seconds, and completed MP3 ingestion
in about 3 minutes 36 seconds: 15 embedded passages, 39 ranked claims, 49 entities.
Quotes, graph claim IDs, and two evidence-based research answers were verified.
See [the test record](prod_no_priors_e2e.md), including an independent graph overview defect.

Local, November 10, 2026 (unit tests: `tests/test_watcher.py`, mocked feeds and HTTP):

- **Real feeds** parsed by `FeedClient`: Dwarkesh (145 episodes, 78 with WebVTT
  transcripts), Acquired (218), All-In (444), and Latent Space (297, 247 with WebVTT).
  Wrong feed URLs returned `invalid` with the HTTP error.
- **Chat:** "Start watching the Dwarkesh Podcast for new episodes." Codex applied the skill,
  found the feed through Apple's directory, and registered it. "Please also keep an eye on the
  All-In podcast" registered its Libsyn feed. Asking for Dwarkesh again returned
  `already_registered` ("already being watched").
- **Transcript path:** within a minute, the watcher queued Dwarkesh's newest episode from its
  WebVTT transcript (9,001 words, `source_type` `txt`). The screen skipped it as `on_topic`;
  it is a Robert Caro episode about political power.
- **Audio path:** the next tick queued All-In's newest episode, the Axon CEO interview: a
  76 MB MP3. It was transcribed (11,031 words) and completed in about 7 minutes with 22
  passages and 66 claims, with speakers attributed (Rick Smith, David Friedberg).
  `external_episode_id` held the GUID and `source_url` the episode's Libsyn page.
- **Hourly re-check:** with `last_checked_at` moved back two hours, both feeds were fetched
  again and nothing was queued.

Beta (release `c257b8e9de91-20261111T005745-2a80e0`), confirmed from `docker compose logs app`:
startup applied `005_rss_watchers`. In chat (with a password login), "Start watching the Latent
Space podcast for new episodes." registered `https://podcast.latent.space/feed.xml`. The
watcher logged `Watcher queued 'Designing for Agents — Zeno Rocha, Resend' …` about 30 s later.
The episode went through the **transcript path** (its WebVTT transcript, `source_type` `txt`)
and completed in about 5 minutes with 34 passages and 75 claims. With `last_checked_at` moved
back two hours, the next tick logged `Watcher checked 1 feed(s); no new episodes`.

## Scheduled thesis digests

**Status:** alpha-only experiment, off unless `PODCAST_ENABLE_SCHEDULED_DIGESTS=1`. Implemented and
verified locally (November 13, 2026); not yet deployed.

Immediate alerts (above) email each strong challenge as an episode finishes. A digest is the
quiet alternative: **one email per scheduled time** covering every new supporting *and*
challenging claim for a chosen set of theses, e.g. "a daily digest at 2 PM Dubai time for my
AI infrastructure theses". The two are independent, and an address can have both.

```text
chat: "Send me a daily thesis digest at 2 PM Dubai time, covering all my AI infrastructure theses."
  └─ tool register_scheduled_thesis_digest(email, thesis_ids, cron "0 14 * * *", timezone "Asia/Dubai")
       ├─ validate the address, the cron, the IANA timezone, and that every thesis is active
       ├─ the same address + cron + timezone + set of theses already active? → return it ('already_registered')
       ├─ INSERT cron_subscriptions(..., next_run_at = next occurrence, in UTC)
       └─ return the schedule in local time + this address's immediate subscriptions to the same theses
            → the agent offers to remove those (remove_subscription), only on explicit confirmation

DigestService.run: one in-process asyncio task, every 60 s
  for each active schedule with next_run_at <= now:
    due = the latest occurrence <= now; next = the first occurrence > now
    UPDATE next_run_at = next (only if still unchanged: each occurrence is claimed once)
    due more than 10 minutes ago? → the app was down: 'skipped', nothing sent (no backfill)
    no active theses left?        → 'skipped'
    evidence = thesis_assessments recorded in [previous occurrence, due), stance supports|challenges
    render one plain-text email → SES → 'sent' (+ last_delivered_at) | 'failed' | 'disabled'
```

- **Cron and timezone:** standard five-field cron (`minute hour day-of-month month day-of-week`),
  parsed with [`cronsim`](https://pypi.org/project/cronsim/), evaluated in the subscriber's IANA
  timezone (`zoneinfo`, with the `tzdata` package as a fallback), and stored with `next_run_at` in
  UTC. Six-field (seconds) and `@daily`-style expressions, impossible dates (`0 0 30 2 *`), and
  unknown zones are rejected; zone names are matched case-insensitively and saved in canonical form.
  Daylight saving follows local time, like Debian cron: a daily 9 AM stays 9 AM (that day's window
  is 23 or 25 hours), a local time skipped by spring-forward runs just after the jump, and a
  repeated time runs once.
- **Reporting window:** `[previous scheduled occurrence, this occurrence)`, computed from the cron,
  never from `last_delivered_at`, so a late or failed send does not change what a period covers.
  The first digest after registration starts at the previous scheduled time, even if that is
  before registration. Weekly schedules use the previous weekly occurrence.
- **Evidence:** `thesis_assessments.created_at` decides "new", not the episode's publish date, so an
  old claim newly assessed against a new thesis counts. No strength or signal floor (unlike
  alerts): every supports/challenges row in the window is counted, inactive or deleted theses are
  left out, and `unrelated` is ignored. No pending-claim cache and no LLM call: the query runs at
  delivery time against existing rows.
- **Email:** `render_digest` is deterministic plain text, like `render_alert`. A header gives the
  window in local time and counts; each selected active thesis follows, theses with the strongest
  evidence first and "No new relevant evidence during this period." for the rest. Per thesis:
  exact supporting/challenging counts, then at most five claims, strongest first (strength, then
  signal), each with the app's assessment on its own line, apart from the speaker, verbatim quote,
  podcast, episode, publish date, and source URL. It ends with the app link and the digest ID to
  cancel. Sender and link come from `PODCAST_ALERT_SENDER` and `PODCAST_PUBLIC_ORIGIN`, like alerts.
- **Cancelling:** "Stop sending me my daily Dubai digest." Codex finds the row through
  `postgres_database_query` on `cron_subscriptions` (asking if several match) and calls
  `remove_scheduled_thesis_digest`, which sets `is_active = false`. Rows are never deleted.
- **Code:** `ingestion/digest_service.py` (`DigestService`, `render_digest`, and the cron helpers),
  migration `008_cron_subscriptions.sql`, the three tools in `codex/tools/`, and their handlers in
  `AgentTools`. Tests: `tests/test_digests.py` (mocked email, injected clock).

**Enabling it (alpha only).** `main.py` creates the `DigestService`, starts its task beside the
ingestion worker and watcher, and advertises the three tools only when
`PODCAST_ENABLE_SCHEDULED_DIGESTS=1`. Otherwise nothing starts, chat threads never see the
tools, and a call to one is refused ("Scheduled digests are not enabled"). The migration runs
everywhere and leaves an empty table. In alpha, `./scripts/configure_runtime.py alpha` adds the
flag to `/podcast-analyst/alpha/runtime`; rerun it, then deploy alpha (`./scripts/deploy.py alpha`)
for the app to pick it up. Beta and prod runtime parameters never include it. Locally, add
`PODCAST_ENABLE_SCHEDULED_DIGESTS=1` to `.env` or the environment; without `PODCAST_ALERT_SENDER`,
each digest is logged and recorded as `disabled`.

**Limitations (deliberate MVP choices):**
- Best effort, at most one attempt per occurrence: `next_run_at` moves before the send, a failed
  SES call is logged and marked `failed` but not retried, and a crash mid-send loses that email.
- No backfill: occurrences more than 10 minutes late (the app was down) are skipped; only the next
  future one is scheduled. No delivery history: `last_delivery_status`/`last_delivered_at` hold
  only the latest outcome.
- Thesis IDs are a plain `UUID[]` (no foreign key), and duplicates are prevented in the app, not by
  a constraint. "All my AI theses" is resolved once, at registration; later theses need a new digest.
- No minimum interval: an every-minute cron would send an email every minute. No UI; ask Codex.

### Digest verification

Local, November 13, 2026 (Central): real Codex chat and pipeline against a scratch database, with
the flag on and no SES sender, so the digest was rendered to the log and recorded as `disabled`.

- Saved two AI theses and a Costco thesis, set immediate alerts on both AI theses, and watched
  Latent Space. Its newest episode (47 claims) was assessed `unrelated` to all three theses.
- "Send me a daily thesis digest at 2 PM Dubai time, covering all my AI infrastructure theses."
  Codex chose the two AI theses (not Costco) and registered `0 14 * * *` / `Asia/Dubai` (next run
  10:00 UTC). It confirmed 2 PM Dubai and offered to remove the two overlapping immediate alerts.
  After "yes", `remove_subscription` deleted exactly those two rows.
- A synthetic test-fixture transcript was uploaded to create relevant evidence (9 supporting or
  challenging assessments), and a second digest was registered for 10:42 PM Chicago. It fired at
  22:42:31 with window Nov 12 10:42 PM to Nov 13 10:42 PM, both theses, all 9 claims strongest
  first, quotes and source links, and no Latent Space or Costco rows. `next_run_at` advanced a day.
- "Stop sending me my daily Dubai digest." Codex queried `cron_subscriptions` and cancelled only the
  Dubai row (`is_active = false`); the Chicago digest stayed active.
- Restarted without the flag: no digest worker log line, and Codex reported it had no tool to
  schedule a digest.

Local, November 14, 2026, with real SES (`PODCAST_ALERT_SENDER=alerts@alpha.podcastanalystagent.com`):

- Saved a co-packaged-optics thesis with an immediate challenge alert, then asked for a daily digest
  at 2:15 PM Chicago (`15 14 * * *` / `America/Chicago`) and kept the immediate alert.
- Uploaded a synthetic "copper comeback" transcript at 2:07 PM; by 2:09 PM it had 18 challenging and
  1 supporting assessment, and the immediate alert email was sent.
- The digest fired at 14:15:26 with 19 claims (window Nov 13 2:15 PM to Nov 14 2:15 PM), status
  `sent`, and `next_run_at` advanced to Nov 15 2:15 PM. SES accepted both emails independently.

Alpha, November 14, 2026: deployed this branch with the flag in `/podcast-analyst/alpha/runtime`.
Migration 008 applied and the worker logged that digests were enabled. The same chat flow saved the
thesis, kept the immediate alert, and registered `40 14 * * *` / `America/Chicago`. The uploaded
transcript produced 19 challenging assessments by 2:29 PM (immediate alert sent), and the digest was
sent at 14:40:54 with 19 claims and a one-day window; `next_run_at` advanced to Nov 15 2:40 PM.

## Later, without restructuring

- **YouTube:** a `download` step (yt-dlp) before `transcribe`.
- Durable sweep recovery if graph projection fails after assessments commit.
