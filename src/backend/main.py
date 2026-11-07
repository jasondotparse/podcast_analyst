from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse


app = FastAPI(openapi_url=None)
INDEX_HTML = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML, media_type="text/html")
