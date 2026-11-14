# Codex chat: local development and beta

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
The Git-managed default is `gpt-6-luna` with high reasoning. Set `CODEX_MODEL`
in `.env` only when you want a model override. `CODEX_BIN` can specify the executable if it is not on VS Code's PATH.

The app sets the child's `CODEX_HOME` to `.local/codex/`, including its local
API-key login and persisted Codex thread data. This directory is gitignored and
created with owner-only access. It does not reuse your personal `~/.codex`
configuration or ChatGPT login. To relocate this app's state, set
`PODCAST_CODEX_HOME`; do not point it at your personal Codex home.

## Git-managed Codex configuration

Edit `codex/config.toml` for defaults and `codex/AGENTS.md` for the agent's global
instructions. Skills belong in `codex/skills/<skill-name>/SKILL.md`: `extract-claims` is
attached to the ingestion pipeline's extraction turns, and chat threads discover
`register-watcher-on-podcast` on their own when a user asks to watch a podcast, and
`youtube-link-to-rss` when a user asks to follow a podcast from a YouTube link (it finds the
show's RSS feed and offers to watch that instead).

Each FastAPI startup copies those two files into the selected Codex home,
overwriting the installed versions, and replaces its entire `skills/` directory.
The app exclusively owns that skills directory, so removing a skill from Git
also removes it from the home on the next startup. All other files, including
authentication, session history, and SQLite databases, are left untouched.
Keep secrets and runtime state out of the repository's `codex/` directory.

Restart FastAPI after editing configuration or adding/removing skills; the
Python reloader does not watch these files. Edit the Git-managed sources rather
than the installed copies. The same startup sync runs in Docker, with sources
packaged at `/app/codex/` and state stored in the host bind mount described in
[AWS deployment](aws_deployment.md#codex-home-and-configuration).

The defaults are `sandbox_mode = "read-only"`, `approval_policy = "never"`, and
`web_search = "live"`; see the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Existing CLI flags and `chat.py` thread settings/developer instructions still
apply and take precedence where they overlap. Configuration consolidation is
separate work.

## Runtime restrictions

Codex's working directory is `.local/agent-workspace/`. Threads use read-only
sandboxing and no approval prompts. The legacy shell tool and multi-agent delegation are
disabled. Codex's code-mode `exec` tool (`unified_exec`) is still enabled and can run
read-only commands anywhere the sandbox allows reading; see
[etl_design.md](etl_design.md#verification). Live web search is enabled through `codex/config.toml`; no podcast-specific
tools are advertised.
Chat threads get three read-only research tools (`vector_search`, `graph_query`,
`postgres_database_query`) and three write tools, `add_thesis`, `add_subscription`, and `register_rss_to_watcher`; ingestion extraction threads get `postgres_database_query`,
`write_claim`, and the `extract-claims` skill. Tool definitions live in `codex/tools/`.
Other server RPC requests receive an explicit error instead of hanging.

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

Chat developer instructions require clarification questions to be normal final
chat responses that end the turn immediately. The agent must not use
`request_user_input`, `request_user_input_async`, or sleep/wait tools to wait for a
reply: users can only send their answer after the current turn finishes. Restart
the backend and reload the page to apply instruction changes to a new chat.

For "how do you work?" questions, the instructions point the agent at this public
GitHub repo: it reads the README, then the linked `docs/`, as raw files over live web
search, and cites them. It answers from `main` on GitHub, not from local
or unmerged changes.

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
   Assistant text renders as GitHub-flavored Markdown (`react-markdown` + `remark-gfm`)
   with raw HTML skipped, images dropped, and unsafe link URLs stripped; user text stays plain.
5. SSE `tool` events show tool activity. `item/started` and `item/completed`
   notifications for `dynamicToolCall`, `mcpToolCall`, `webSearch`, and
   `commandExecution` items become a snapshot keyed by `item_id`: `name`,
   `arguments`, `result` (completion only; `null` when Codex exposes none, as is
   common for web search), `status` (`running`, `succeeded`, `failed`, `declined`),
   Codex's `started_at_ms`/`completed_at_ms`/`duration_ms`, and `truncated`. The UI
   upserts one collapsed **Using tools** row per call; expanding it shows the exact
   arguments and payload. Payloads are redacted (the API key and obvious credential
   keys) and native output is capped at 40,000 characters for display only; nothing
   is logged or stored. Reasoning items and other item types are never forwarded.
   While a turn is busy and neither a tool nor answer text is active, the UI shows a
   plain **Thinking…** status. A tool still running when its turn ends is shown as
   interrupted, failed, or ended without completion. The chat auto-scrolls only while
   the reader is near the bottom (or just sent a message), so expanded rows can be read.
6. SSE `completed` releases the busy guard; `fatal` reports a lost runtime/session.

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
thread isolation, terminal statuses, bounded event history, and tool-activity
projection (lifecycle, isolation, reasoning exclusion, web search without results,
redaction, and display truncation).

Live local tests verified initialization/thread creation, progressive text events,
completed-item reconciliation, and a same-thread follow-up that retained context.
The built UI was also checked in Chrome on port 8000 while a reply was streaming.
Tool rows, expansion, web search without results, Markdown rendering, and the
no-jump scroll were checked in Chrome on a live turn.
Shutdown verification confirmed that the backend, Codex child, and Vite processes
exit and release both local ports.

## Scope

Local development stays on loopback and does not require a password by default.
Set `PODCAST_APP_PASSWORD` and `PODCAST_PUBLIC_ORIGIN=http://127.0.0.1:8000` to
exercise login locally. Keep the beta password in `PODCAST_BETA_PASSWORD`, which
is read only by the secret-setup script and does not enable local auth.

Prod uses its own `PODCAST_PROD_PASSWORD` in the gitignored root `.env` (at least
8 characters on one line). Upload it with `configure_runtime.py prod`, then
release with `./scripts/deploy.py prod`; see the [prod runbook](aws_deployment.md#prod-deployment).
Never put either stage's password in Git. Rotate the prod demo password after the demo.

Cloud Compose requires password login and an explicit public origin. UI and API
requests use a server-side login session; chat IDs are checked against its owner.
An expired or logged-out SSE stream closes on its next check (at most the
15-second idle keepalive interval). Authentication state is in memory, so restarting
FastAPI signs users out even though Codex's filesystem history is retained. The
app still creates a fresh conversation after reload; there is no history UI.

Deploy through [AWS deployment](aws_deployment.md), including the one-time
runtime-secret setup. Dynamic research tools, databases, an interruption UI,
conversation eviction, and automatic runtime recovery remain later work.

Protocol reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
