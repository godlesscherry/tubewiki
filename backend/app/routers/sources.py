"""Source library: every extracted document, channels, dedupe checks."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..schemas import DedupeOut, SourcesOut, VideoOut
from .ingest import parse_youtube_id

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _video_out(row: dict) -> VideoOut:
    return VideoOut(**{key: row.get(key) for key in VideoOut.model_fields if key in row})


@router.get("", response_model=SourcesOut)
def list_sources(q: str | None = None, status: str | None = None) -> SourcesOut:
    clauses: list[str] = []
    params: list[object] = []
    if q:
        clauses.append("(title LIKE ? OR channel LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.connect() as conn:
        video_rows = db.rows(
            conn.execute(
                f"""
                SELECT * FROM videos {where}
                ORDER BY COALESCE(extracted, added) DESC, title ASC
                """,
                params,
            )
        )
        channel_rows = db.rows(
            conn.execute(
                """
                SELECT channel, channel_id, COUNT(*) AS video_count,
                       MAX(COALESCE(extracted, added)) AS last_added
                FROM videos
                GROUP BY channel
                ORDER BY video_count DESC, channel ASC
                LIMIT 100
                """
            )
        )
    return SourcesOut(
        videos=[_video_out(row) for row in video_rows],
        channels=[dict(row) for row in channel_rows],
        total=len(video_rows),
    )


@router.get("/check", response_model=DedupeOut)
def check_source(url: str) -> DedupeOut:
    video_id = parse_youtube_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Not a valid YouTube video URL.")
    row = db.video_row(video_id)
    return DedupeOut(exists=bool(row), video=_video_out(row) if row else None)


@router.delete("/{video_id}")
def delete_source(video_id: str) -> dict:
    with db.connect() as conn:
        found = conn.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if not found:
            raise HTTPException(status_code=404, detail="Source not found.")
        conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
    return {"deleted": True}
