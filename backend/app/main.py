"""Forge — YouTube → detailed markdown documents (lean API).

Run:  ./forge api       (from the repo root)
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import ingest, sources

STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.load_dotenv()
    config.refresh_llm()
    db.init_db()
    yield


app = FastAPI(title="Forge", version=config.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(sources.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": config.VERSION, "uptime_s": round(time.time() - STARTED_AT, 1)}


@app.get("/api/config")
def get_config() -> dict:
    try:
        with db.connect() as conn:
            videos = conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]
    except Exception:
        videos = 0
    return {
        "llm": config.llm_available(),
        "wiki_root": str(config.WIKI_DIR),
        "videos": videos,
    }


# Serve the built SPA (frontend/dist) when present.
if config.FRONTEND_DIST.exists() and (config.FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIST), html=True), name="spa")
else:

    @app.get("/")
    def dev_root() -> JSONResponse:
        return JSONResponse(
            {
                "message": "Forge API is running. Frontend is not built yet.",
                "next": "run `./forge dev` (Vite) or `./forge build` then reload",
                "docs": "/docs",
            }
        )
