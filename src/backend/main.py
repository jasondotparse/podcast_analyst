import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat import ChatHarness
from .codex_client import CodexError


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in the repository's .env before starting the backend.")
    home = Path(os.environ.get("PODCAST_CODEX_HOME", ROOT / ".local" / "codex")).expanduser().resolve()
    workspace = (ROOT / ".local" / "agent-workspace").resolve()
    harness = ChatHarness(workspace, model=os.environ.get("CODEX_MODEL"))
    app.state.harness = harness
    await harness.client.start(api_key, home, workspace)
    try:
        yield
    finally:
        await harness.client.close()


app = FastAPI(openapi_url=None, lifespan=lifespan)


@app.exception_handler(CodexError)
async def codex_error(request: Request, error: CodexError):
    return JSONResponse(status_code=502, content={"detail": str(error)})


class TurnInput(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


@app.post("/api/sessions", status_code=201)
async def create_session(request: Request):
    session_id = await request.app.state.harness.create_session()
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/turns", status_code=202)
async def start_turn(session_id: str, body: TurnInput, request: Request):
    if not body.message.strip():
        raise HTTPException(422, "Message cannot be blank.")
    return await request.app.state.harness.start_turn(session_id, body.message.strip())


@app.get("/api/sessions/{session_id}/events")
async def events(session_id: str, request: Request):
    session = request.app.state.harness.session(session_id)
    if session.streaming:
        raise HTTPException(409, "This chat already has an event stream.")
    try:
        cursor = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        raise HTTPException(400, "Invalid Last-Event-ID.")
    session.streaming = True

    async def stream():
        nonlocal cursor
        try:
            yield 'event: ready\ndata: {}\n\n'
            while not await request.is_disconnected():
                if session.events and cursor < session.events[0][0] - 1:
                    yield 'event: fatal\ndata: {"message":"Stream history expired. Reload the page."}\n\n'
                    return
                for sequence, event, data in list(session.events):
                    if sequence > cursor:
                        yield f"id: {sequence}\nevent: {event}\ndata: {json.dumps(data)}\n\n"
                        cursor = sequence
                        if event == "fatal":
                            return
                session.changed.clear()
                if session.sequence > cursor:
                    continue
                try:
                    await asyncio.wait_for(session.changed.wait(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            session.streaming = False

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.get("/ready")
async def ready(request: Request):
    request.app.state.harness.require_ready()
    return {"status": "ready"}


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/app/")


@app.get("/api/health")
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/app/")
async def index() -> FileResponse:
    index_html = FRONTEND_DIST / "index.html"
    if not index_html.is_file():
        raise HTTPException(
            503,
            "Frontend not built. Run npm --prefix src/frontend run build, "
            "or use Vite on port 5173 for development.",
        )
    return FileResponse(index_html, media_type="text/html")


# check_dir=False lets the API run before the first frontend build during dev.
app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False), name="frontend")
