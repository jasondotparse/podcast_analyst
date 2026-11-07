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
```

No AWS credentials or API keys are needed. Install Microsoft's **Python** and
**Python Debugger** VS Code extensions; the JavaScript debugger is built into
VS Code. The frontend configuration reads only its optional
`src/frontend/.env.local`, rather than the backend's root `.env`.

## VS Code run configurations

Open **Run and Debug**, choose a configuration, and press **F5**:

| Configuration | Starts | Open |
|---|---|---|
| **run full stack app** | FastAPI with Python reload + Vite with React HMR | <http://127.0.0.1:5173/app/> |
| **run backend** | FastAPI only, with Python reload/debugging | <http://127.0.0.1:8000/api/health> |
| **serve front end** | Vite only, with React HMR; uses the running backend | <http://127.0.0.1:5173/app/> |
| **serve built app** | Builds React, then starts FastAPI serving the compiled UI | <http://127.0.0.1:8000/app/> |

For everyday UI work, use **run full stack app**, or start **run backend** once
and use **serve front end** independently. Saving `src/frontend/src/App.jsx` or
CSS updates the browser without a manual build or refresh. Python edits restart
FastAPI; breakpoints in `src/backend/main.py` work in the backend debugger.

**serve front end** does not start the backend. If the page says **Backend:
Unavailable**, start FastAPI and click **Check backend**. The full-stack compound
starts both servers in parallel, so this can also happen briefly at startup.

Stop with **Shift+F5**. Stopping a session launched by the full-stack compound
stops both sessions. Stop existing servers before switching launch modes: the
backend uses port 8000, and Vite uses port 5173. Vite fails visibly if 5173 is
occupied, rather than silently choosing another port.

## How dev requests work

```text
Browser → 127.0.0.1:5173/app/ → Vite → React + HMR
Browser → 127.0.0.1:5173/api/health → Vite proxy → 127.0.0.1:8000/api/health
```

React uses `fetch('/api/health')`, so the same code works in both modes. In dev,
Vite forwards `/api` requests to FastAPI. In the built app, FastAPI handles them
directly on port 8000. No CORS configuration or environment-specific API URL is
needed.

## Run dev mode from terminals

From the repository root, start these in separate terminals:

```bash
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir src/backend
```

```bash
npm --prefix src/frontend run dev
```

Open <http://127.0.0.1:5173/app/>. Both servers stay local to your machine.

## Build and serve through FastAPI

**serve built app** performs this build automatically. To do it manually:

```bash
npm --prefix src/frontend run build
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000
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

The React page and `Backend: ok` were checked in Chrome through both Vite and
FastAPI. Editing `App.jsx` changed the visible heading through HMR without a
browser refresh. The VS Code frontend launch and full-stack compound were run;
stopping the compound freed both ports. HTTP checks covered redirects, API proxying,
built assets, and the missing-build response. Docker builds passed for native ARM
and the EC2 host's amd64 architecture; the runtime serves the compiled UI without
Node or `node_modules`.

References: [Vite server options](https://vite.dev/config/server-options.html),
[VS Code compound launches](https://code.visualstudio.com/docs/debugtest/debugging-configuration#_compound-launch-configurations),
[FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/).
