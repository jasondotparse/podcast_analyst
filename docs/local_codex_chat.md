# Local Codex chat: Phase A

This is the local message → Codex → streamed reply slice from
[agent_harness_design.md](agent_harness_design.md). It uses one long-lived
`codex app-server` process per FastAPI worker and a new thread per page session.
Codex owns conversation context; the app keeps only session routing and bounded
event buffers in memory.

## Setup

The tested Codex version is **0.167.0**. Install that exact version if needed:

```bash
npm install -g @openai/codex@0.167.0
codex --version
.venv/bin/python -m pip install -r src/backend/requirements.txt
npm --prefix src/frontend ci
```

Put your key in the repository root `.env`:

```dotenv
OPENAI_API_KEY=<your-api-key>
```

An already-exported environment variable takes precedence over `.env`. The key
stays in the backend and is supplied to Codex through its environment and the
`account/login/start` RPC. Real messages use the model API and incur normal usage.
If you want to select a model, set `CODEX_MODEL` in `.env`; otherwise Codex selects
its default. `CODEX_BIN` can specify the executable if it is not on VS Code's PATH.

The app sets the child's `CODEX_HOME` to `.local/codex/`, including its local
API-key login and persisted Codex thread data. This directory is gitignored and
created with owner-only access. It does not reuse your personal `~/.codex`
configuration or ChatGPT login. To relocate this app's state, set
`PODCAST_CODEX_HOME`; do not point it at your personal Codex home.

Codex's working directory is `.local/agent-workspace/`. Threads use read-only
sandboxing and no approval prompts. Shell tools, multi-agent delegation, and web
search are disabled for this text-chat slice. No podcast tools are advertised.
Unexpected server RPC requests receive an explicit error instead of hanging.

## Run on localhost:8000

In VS Code, select **serve built app** and press **F5**. This builds React before
starting FastAPI and Codex. Open <http://127.0.0.1:8000/app/>, wait for **Ready**,
then type a message and press **Send** or **Enter**. Use **Shift+Enter** for a newline.

Or run from the repository root:

```bash
npm --prefix src/frontend run build
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3
```

For frontend development, **run full stack app** starts FastAPI/Codex and Vite
together. Open <http://127.0.0.1:5173/app/> for HMR. **serve front end** remains
UI-only and uses the running backend. Vite proxies both HTTP requests and the
SSE connection under `/api`.

One page session retains context across messages. Refreshing opens a fresh
conversation; there is no browser-session restoration. Sending a second prompt
while the same conversation is busy returns 409. The UI disables Send until
`turn/completed`, rather than treating HTTP acceptance as completion.

Stop with **Ctrl+C** or **Shift+F5**. The launch configurations allow three
seconds for open SSE streams to finish before cancellation, then the lifespan
handler terminates and reaps Codex. Run one Uvicorn worker.
With a browser's SSE stream still open, Uvicorn may log a cancellation at its
shutdown deadline; the Codex child is still reaped. Close the chat tab first if
you want to avoid that shutdown message.

## Request path

1. React sends `POST /api/sessions`; FastAPI starts a thread and returns an opaque
   app-session ID. React StrictMode reuses this initial request in development.
2. React opens `GET /api/sessions/{session_id}/events` and waits for the SSE
   `ready` event before enabling Send.
3. `POST /api/sessions/{session_id}/turns` with `{"message":"Hello"}` starts a
   Codex turn and returns 202 once accepted.
4. SSE `delta` events append assistant text. The `message` event reconciles the
   completed item's full text by item ID, so the final answer is not duplicated.
5. SSE `completed` releases the busy guard; `fatal` reports a lost runtime/session.

The single stdout reader matches RPC responses by request ID and routes
notifications by thread ID. It never waits for an entire turn to complete before
reading another message. Each session buffers at most 512 events and uses SSE IDs
for short disconnect/reconnect replay. A stream that falls behind this buffer
gets a reload message instead of silently displaying a truncated answer.

`GET /api/health` (also `/health`) is liveness; `GET /ready` checks Codex readiness.
A subprocess disconnect fails outstanding requests and existing sessions. Restart
the backend and reload the page; failed turns are never automatically replayed.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The offline tests cover out-of-order RPC responses, nonblocking rejection of
unsupported server requests, disconnects, early notifications, busy guards,
thread isolation, terminal statuses, and bounded event history.

Live local tests verified initialization/thread creation, progressive text events,
completed-item reconciliation, and a same-thread follow-up that retained context.
The built UI was also checked in Chrome on port 8000 while a reply was streaming.
Shutdown verification confirmed that the backend, Codex child, and Vite processes
exit and release both local ports.

## Scope

This phase is for **local use only**, bound to loopback. Shared-password login,
browser/session ownership enforcement, interruption UI, session eviction, and
automatic runtime recovery belong to Phase B. Dynamic research tools and data
services remain later work. App-session IDs are not an authentication mechanism.

Do not deploy this branch with the existing Docker setup yet: the image does not
package Codex or its persistent runtime storage/secrets. Deployment is a separate
next step after the local happy path is accepted.

Protocol reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
