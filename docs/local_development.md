# React and FastAPI development

The UI lives entirely in `src/frontend`. React + Vite provide the dev server and
HMR (hot module replacement): save a UI file and the browser updates automatically.
FastAPI provides `/api/*` and serves the compiled UI under `/app/` after a build.

## First-time setup

From the repository root, using Python 3.11+ and Node.js 22.12+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r src/backend/requirements.txt
npm --prefix src/frontend ci
brew install ffmpeg   # MP3 ingestion splits audio with it; any ffmpeg on PATH works
```

The chat harness now also needs Codex **0.167.0** and a backend-only
`OPENAI_API_KEY` in the root `.env`. Follow [local Codex chat](local_codex_chat.md)
for setup and runtime details. No AWS credentials are needed. Install Microsoft's **Python** and
**Python Debugger** VS Code extensions; the JavaScript debugger is built into
VS Code. The frontend configuration reads only its optional
`src/frontend/.env.local`, rather than the backend's root `.env`.

### Local databases

The backend requires PostgreSQL + pgvector. Start a loopback-only dev container
once; it restarts with Docker Desktop and keeps data in the `podcast-postgres` volume:

```bash
docker run -d --name podcast-postgres --restart unless-stopped -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=podcast -e POSTGRES_PASSWORD=podcast -e POSTGRES_DB=podcast \
  -v podcast-postgres:/var/lib/postgresql pgvector/pgvector:0.8.7-pg18-bookworm
echo 'DATABASE_URL=postgresql://podcast:podcast@127.0.0.1:5432/podcast' >> .env
```

On startup FastAPI applies pending migrations and opens the embedded LadybugDB
graph at `.local/ladybug/graph.lbug`. The backend process holds the graph's file
lock, so stop it before opening the graph from another script. Inspect Postgres
with `docker exec -it podcast-postgres psql -U podcast`. To reset local data, stop
the backend, then `docker rm -f podcast-postgres && docker volume rm podcast-postgres`
and delete `.local/ladybug/`. See [the database design](database_design.md#implementation).

### Ingest a transcript or MP3

In the UI, **Upload podcast** opens a form: drop (or choose) a `.txt` transcript or an
`.mp3` recording (up to 300 MB), fill in the podcast and episode fields, and submit. An
MP3 is first transcribed with OpenAI `gpt-transcribe` (about a minute for a 42-minute
episode), and the status bar shows "Transcribing audio…" meanwhile. Optional fields include
the episode's original URL (`source_url`, which gives claims a linkable source and also
dedupes re-uploads) and comma-separated guests. The form closes once the server accepts the
upload (`202`) and shows the server's error otherwise, for example a duplicate (`409`).
A green bar at the top of the page then tracks that upload: the `usePipelineStatus` hook
(`src/frontend/src/hooks/`) polls `GET /api/ingestions/{job_id}` every 2 s and fills one
segment per pipeline step. The bar disappears after success; a rejected or failed job
stays visible, in red with its reason, until dismissed. It tracks only the latest upload
and is not restored after a page reload.
Components live in `src/frontend/src/components/`; API calls in `src/frontend/src/api.js`.

With curl, upload a `.txt` transcript (field `transcript`) or an `.mp3` (field `audio`) and
poll its job; see the [ingestion design](etl_design.md#api). Extraction uses real OpenAI
calls and takes a few minutes.

```bash
curl -s -F 'metadata={"podcast_name":"Chip Talk","title":"Optical interconnects"}' \
     -F transcript=@episode.txt http://127.0.0.1:8000/api/ingestions
curl -s -F 'metadata={"podcast_name":"Chip Talk","title":"Memristors"}' \
     -F audio=@episode.mp3 http://127.0.0.1:8000/api/ingestions
curl -s http://127.0.0.1:8000/api/ingestions/<job_id>
```

Run tests with `.venv/bin/python -m unittest discover -s tests -v`. The Postgres
test creates and drops a temporary database through `DATABASE_URL`; without it
set, that test is skipped. The `TranscriptionClient` tests are skipped without ffmpeg.

Leave `PODCAST_ALERT_SENDER` unset locally: alerts stay pending and their plain-text email is
logged at INFO. Retrying may log again but cannot create a duplicate alert.

### Watch a podcast (RSS)

Ask the chat, for example, "Start watching the Dwarkesh Podcast for new episodes." Codex finds
the feed and registers it with `register_rss_to_watcher`. Within about a minute (once no other
job is pending or running), the watcher queues the podcast's newest episode, and the status
appears in the backend log (`src.backend.ingestion.watcher_service: Watcher queued ...`) and in
`ingestion_jobs`. Feeds are rechecked hourly. To force a recheck, run
`UPDATE watch_sources SET last_checked_at = NULL`. See the
[watcher design](etl_design.md#rss-watchers).

### Browse the database (cabinet)

The rail on the left of the page (`DbCabinet`) lists what the database holds. Each button opens
a drawer, with **Close** at its top, which fetches its list fresh every time it opens:

| Button | Shows | Endpoint |
|---|---|---|
| Theses | Every thesis, newest first; inactive ones are marked | `GET /api/catalog/theses` |
| Podcasts | Names of the active RSS feeds being watched | `GET /api/catalog/podcasts` |
| Subscriptions | One line per alert subscription: email → thesis | `GET /api/catalog/subscriptions` |
| Ingestions | The 20 most recently updated jobs: podcast, episode, status, time, and any error | `GET /api/catalog/ingestions` |
| Show knowledge graph | Theses and linked evidence; expandable connections | `GET /api/graph` |

The list endpoints are read-only and go through `CatalogService` (`src/backend/catalog_service.py`);
the SQL is in `PostgresClient`'s catalog section. The buttons and how each one renders a row are
defined in `src/frontend/src/components/cabinetViews.jsx`.

Verified locally and on beta in headless Chromium: each list matched the database's row counts
(beta: 3 theses, 3 feeds, 1 subscription, 14 jobs), **Close** closed the drawer, there were no
console errors, and the endpoints return 401 without a login.

### Knowledge graph

**Show knowledge graph** in the left cabinet opens a panel covering 75% of the
screen (full width below 700 px). It starts with every thesis, its entities,
stance claims, and source episodes. Pick one thesis, select a node for its quote,
speaker, source, and signal, or use **Show connections** to add neighbors. **Reset**
reloads the current view. **Escape**, **Close**, or the cabinet button closes it
and restores focus; chat and its event stream stay mounted.

Components live in `src/frontend/src/components/graph/`; only `GraphCanvas.jsx`
imports the lazy-loaded, exactly pinned `react-force-graph-2d` library. Evidence
appears as plain text, and only http(s) source URLs become links. Keyboard users
can select from the node list and walk the connection buttons.

Verification checklist: compare stance-edge counts with a chat graph query; inspect
a quote against Postgres; expand and reset; try literal markup in evidence; open
while ingestion runs; check at 375 px; ask chat for thesis challenges and open its
card. See [graph design](knowledge_graph_design.md) for the recorded checks.

Verified November 11, 2026 in Chrome: overview, claim details, expansion, Reset,
and the full-width 375 x 800 layout with stacked details. Beta's authenticated
API, graph counts, 401 protection, and final frontend assets were checked over
HTTPS; the beta browser remained at sign-in. Full notes and remaining manual
checks are in the graph design document.

## VS Code run configurations

Open **Run and Debug**, choose a configuration, and press **F5**:

| Configuration | Starts | Open |
|---|---|---|
| **run full stack app** | FastAPI/Codex with Python reload + Vite with React HMR | <http://127.0.0.1:5173/app/> |
| **run backend** | FastAPI/Codex, with Python reload/debugging | <http://127.0.0.1:8000/api/health> |
| **serve front end** | Vite only, with React HMR; uses the running backend | <http://127.0.0.1:5173/app/> |
| **serve built app** | Builds React, then starts FastAPI/Codex serving the compiled UI | <http://127.0.0.1:8000/app/> |

For everyday UI work, use **run full stack app**, or start **run backend** once
and use **serve front end** independently. Saving `src/frontend/src/App.jsx` or
CSS updates the browser without a manual build or refresh. Python edits restart
FastAPI; breakpoints in `src/backend/main.py` work in the backend debugger.

**serve front end** does not start the backend. Start FastAPI/Codex first, then
open the UI. The chat creates a fresh session when the page opens; reload the
page after restarting the backend. Wait for **Ready** before sending a message.

An empty chat centers the message input beneath a welcome heading, with three
hardcoded suggested prompts below it: save a Microsoft thesis, request email
alerts for that thesis, and follow the a16z podcast. Each card shows a label and
a shortened preview; selecting it fills and focuses the input with the full prompt
without sending, so it can be edited first. The alert example intentionally refers
to an existing Microsoft thesis. After the first message is submitted, the cards
and welcome heading disappear and the input moves to the bottom of the chat.
The cards stack vertically on narrow screens.

Stop with **Shift+F5**. Stopping a session launched by the full-stack compound
stops both sessions. Stop existing servers before switching launch modes: the
backend uses port 8000, and Vite uses port 5173. Vite fails visibly if 5173 is
occupied, rather than silently choosing another port.

## How dev requests work

```text
Browser → 127.0.0.1:5173/app/ → Vite → React + HMR
Browser → 127.0.0.1:5173/api/sessions → Vite proxy → 127.0.0.1:8000/api/sessions
```

React uses `/api/...` URLs for session creation, message submission, and SSE, so
the same code works in both modes. In dev, Vite forwards `/api` requests to
FastAPI. In the built app, FastAPI handles them
directly on port 8000. No CORS configuration or environment-specific API URL is
needed.

## Run dev mode from terminals

From the repository root, start these in separate terminals:

```bash
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir src/backend --timeout-graceful-shutdown 3
```

```bash
npm --prefix src/frontend run dev
```

Open <http://127.0.0.1:5173/app/>. Both servers stay local to your machine.

## Build and serve through FastAPI

**serve built app** performs this build automatically. To do it manually:

```bash
npm --prefix src/frontend run build
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3
```

Open <http://127.0.0.1:8000/app/>. The generated HTML, JavaScript, and CSS are in
`src/frontend/dist/`, which is ignored by Git. This mode serves a build snapshot;
UI edits appear after another build and browser refresh. HMR belongs to Vite on
5173. FastAPI can start for API development without a frontend build; `/app/`
returns a clear 503 response until the build exists.

| FastAPI path | Behavior |
|---|---|
| `/` and `/app` | Redirect to `/app/` |
| `/app/` | Built React page |
| `/app/assets/*` | Built JavaScript/CSS |
| `/api/health` | `{"status":"ok"}` |

Unknown API paths and missing assets return 404. API documentation endpoints
remain disabled. There is no client-side page router in this initial UI.

## Docker and deployment

The Dockerfile builds React in a Node stage and copies only `dist/` into the
Python runtime image. The container serves the UI and API on port 8000; Node and
the Vite dev server are not runtime services. See [AWS deployment](aws_deployment.md)
for Docker checks and `./scripts/deploy.py beta`.

## Verified behavior

The original React page and `Backend: ok` were checked in Chrome through both Vite and
FastAPI. Editing `App.jsx` changed the visible heading through HMR without a
browser refresh. The VS Code frontend launch and full-stack compound were run;
stopping the compound freed both ports. HTTP checks covered redirects, API proxying,
built assets, and the missing-build response. Docker builds passed for native ARM
and the EC2 host's amd64 architecture; the runtime serves the compiled UI without
Node or `node_modules`.

The current chat harness's live streaming tests are recorded in
[local_codex_chat.md](local_codex_chat.md). For the password-protected beta release, follow [AWS deployment](aws_deployment.md).

Suggested prompts were checked locally in Chrome on November 11, 2026: all three
cards filled and focused the input without sending; the cards stacked at 390 px
width; editing and sending a test message hid the welcome area, moved the input
to the bottom, and received a live reply. The frontend build passed.

References: [Vite server options](https://vite.dev/config/server-options.html),
[VS Code compound launches](https://code.visualstudio.com/docs/debugtest/debugging-configuration#_compound-launch-configurations),
[FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/).
