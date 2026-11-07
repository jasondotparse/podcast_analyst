from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


app = FastAPI(openapi_url=None)
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/app/")


@app.get("/api/health")
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
