"""Ingest endpoints: accept YouTube URLs, run background extraction jobs."""
from __future__ import annotations

import asyncio
import re
import uuid
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException

from .. import db
from ..schemas import IngestRequest, IngestResponse, JobOut

router = APIRouter(prefix="/api", tags=["ingest"])

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_youtube_id(url: str) -> str | None:
    """Extract an 11-char video id from common YouTube URL shapes."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if ID_RE.match(candidate) else None
    if parsed.path in ("/watch", "/watch/"):
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        return candidate if ID_RE.match(candidate) else None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        candidate = parts[1]
        return candidate if ID_RE.match(candidate) else None
    return None


def _job_out(row: dict) -> JobOut:
    return JobOut(**{key: row.get(key) for key in JobOut.model_fields if key in row})


@router.post("/ingest", response_model=IngestResponse)
async def ingest(payload: IngestRequest) -> IngestResponse:
    accepted: list[str] = []
    duplicates: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for raw in payload.urls:
        url = raw.strip()
        if not url:
            continue
        video_id = parse_youtube_id(url)
        if not video_id:
            rejected.append(url)
            continue
        if video_id in seen:
            duplicates.append(url)
            continue
        seen.add(video_id)
        existing = db.video_row(video_id)
        if existing and existing.get("status") == "done" and not payload.force:
            duplicates.append(url)
            continue
        accepted.append(url)

    if not accepted:
        return IngestResponse(job_id="", accepted=0, duplicates=duplicates, rejected=rejected)

    job_id = uuid.uuid4().hex[:12]
    db.create_job(job_id, "ingest", len(accepted))

    # Import lazily so the API can boot while the pipeline module is assembled.
    from ..ingest.pipeline import run_ingest_job

    asyncio.create_task(run_ingest_job(job_id, accepted))
    return IngestResponse(
        job_id=job_id, accepted=len(accepted), duplicates=duplicates, rejected=rejected
    )


@router.get("/jobs", response_model=list[JobOut])
def list_jobs() -> list[JobOut]:
    return [_job_out(row) for row in db.list_jobs(limit=20)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    row = db.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_out(row)
