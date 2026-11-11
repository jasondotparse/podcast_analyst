import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, Json

from .agent_tools import CHAT_TOOLS, AgentTools
from .chat import ChatHarness
from .clients.codex_client import CodexError
from .clients.embedding_client import EmbeddingClient
from .clients.feed_client import FeedClient
from .clients.graph_client import GraphClient
from .clients.postgres_client import PostgresClient
from .clients.transcription_client import TranscriptionClient
from .codex_config import sync_codex_config, tool_specs
from .db.db import migrate_postgres, open_graph
from .ingestion.claim_extractor import ClaimExtractor
from .ingestion.claim_ranker import ClaimRanker
from .ingestion.thesis_reflector import ThesisReflector
from .ingestion.notifier import Notifier
from .clients.email_client import EmailClient
from .ingestion.ingestion_service import IngestionService
from .ingestion.models import EpisodeMetadata
from .ingestion.watcher_service import WatcherService
from .auth import PasswordAuthMiddleware, router as auth_router, valid_login


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(ROOT / ".env")
    # Show the app's own INFO logs (ingestion and watcher progress) alongside Uvicorn's.
    logging.basicConfig(format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("src.backend").setLevel(logging.INFO)
    app.state.app_password = os.environ.get("PODCAST_APP_PASSWORD")
    app.state.public_origin = os.environ.get("PODCAST_PUBLIC_ORIGIN")
    app.state.login_sessions = {}
    if os.environ.get("PODCAST_REQUIRE_AUTH") == "1":
        if not app.state.app_password or not app.state.public_origin:
            raise RuntimeError("Deployment requires PODCAST_APP_PASSWORD and PODCAST_PUBLIC_ORIGIN.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in the repository's .env before starting the backend.")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL; see docs/local_development.md.")
    await migrate_postgres(database_url)
    graph = GraphClient(open_graph(Path(os.environ.get(
        "PODCAST_LADYBUG_PATH", ROOT / ".local" / "ladybug" / "graph.lbug"))))
    db = PostgresClient(database_url)
    openai = AsyncOpenAI(api_key=api_key)
    embedder = EmbeddingClient(openai)
    tools = AgentTools(db, graph, embedder)

    home = Path(os.environ.get("PODCAST_CODEX_HOME", ROOT / ".local" / "codex")).expanduser().resolve()
    sync_codex_config(ROOT / "codex", home)
    workspace = (ROOT / ".local" / "agent-workspace").resolve()
    # Chat threads get the research tools and add_thesis.
    harness = ChatHarness(workspace, model=os.environ.get("CODEX_MODEL"),
                          tools=tool_specs(*CHAT_TOOLS), call_tool=tools.call)
    app.state.harness = harness
    await harness.client.start(api_key, home, workspace)

    # Compose the ingestion pipeline from its components.
    extractor = ClaimExtractor(db, harness, tools, home / "skills" / "extract-claims" / "SKILL.md")
    transcriber = TranscriptionClient(openai)
    ranker = ClaimRanker(db, openai)
    reflector = ThesisReflector(db, openai)
    notifier = Notifier(db, EmailClient(os.environ.get("PODCAST_ALERT_SENDER")),
                        app.state.public_origin or "http://127.0.0.1:8000")
    app.state.ingestion = IngestionService(db, graph, openai, embedder, extractor, transcriber, ranker,
                                           reflector, notifier)
    # RSS watchers queue new episodes into that pipeline; the chat's register_rss_to_watcher tool adds feeds.
    tools.watcher = WatcherService(db, FeedClient(), app.state.ingestion, MAX_AUDIO_BYTES)
    worker = asyncio.create_task(app.state.ingestion.run_worker())
    watcher = asyncio.create_task(tools.watcher.run())
    try:
        yield
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
        await harness.client.close()
        graph.close()


app = FastAPI(openapi_url=None, lifespan=lifespan)
app.add_middleware(PasswordAuthMiddleware)
app.include_router(auth_router)


@app.exception_handler(CodexError)
async def codex_error(request: Request, error: CodexError):
    return JSONResponse(status_code=502, content={"detail": str(error)})


class TurnInput(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


@app.post("/api/sessions", status_code=201)
async def create_session(request: Request):
    session_id = await request.app.state.harness.create_session(request.state.login_id)
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/turns", status_code=202)
async def start_turn(session_id: str, body: TurnInput, request: Request):
    if not body.message.strip():
        raise HTTPException(422, "Message cannot be blank.")
    return await request.app.state.harness.start_turn(session_id, body.message.strip(), request.state.login_id)


@app.get("/api/sessions/{session_id}/events")
async def events(session_id: str, request: Request):
    session = request.app.state.harness.session(session_id, request.state.login_id)
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
                if request.app.state.app_password and not valid_login(request.app.state, request.state.login_id):
                    yield 'event: fatal\ndata: {"message":"Session expired. Sign in again."}\n\n'
                    return
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


def ingestion_service(request: Request) -> IngestionService:
    return request.app.state.ingestion


Ingestion = Annotated[IngestionService, Depends(ingestion_service)]

# An hour at 320 kbps is about 145 MB. The audio is read into memory and stored in
# Postgres on a 4 GiB host, so cap it.
MAX_AUDIO_BYTES = 300 * 1024 * 1024


@app.post("/api/ingestions", status_code=202)
async def create_ingestion(metadata: Annotated[Json[EpisodeMetadata], Form()], ingestion: Ingestion,
                           transcript: UploadFile | None = None, audio: UploadFile | None = None):
    if (transcript is None) == (audio is None):
        raise HTTPException(422, "Send exactly one file: a .txt transcript or .mp3 audio.")
    if transcript is not None:
        job, created = await ingestion.submit(metadata, transcript=(await transcript.read()).decode())
    else:
        if not audio.filename.lower().endswith(".mp3"):
            raise HTTPException(422, "Audio must be an .mp3 file.")
        if audio.size > MAX_AUDIO_BYTES:
            raise HTTPException(413, f"Audio files are limited to {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
        job, created = await ingestion.submit(metadata, audio=await audio.read())
    return job if created else JSONResponse(job.model_dump(mode="json"), status_code=409)


@app.get("/api/ingestions/{job_id}")
async def get_ingestion(job_id: UUID, ingestion: Ingestion):
    job = await ingestion.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown ingestion job.")
    return job


@app.post("/api/ingestions/{job_id}/retry", status_code=202)
async def retry_ingestion(job_id: UUID, ingestion: Ingestion):
    job = await ingestion.retry(job_id)
    if job is None:
        raise HTTPException(404, "Unknown ingestion job.")
    return job


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
