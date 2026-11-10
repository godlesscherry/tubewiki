"""Ingest job orchestration: YouTube URL → metadata → transcript → frames → document.

``run_ingest_job`` is the single entry point used by the ingest router. It is
resilient: one bad video never aborts the job, and every per-video stage
(transcript, frames, document write) is independently guarded.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import db
from . import frames, writer, youtube

log = logging.getLogger(__name__)

TRANSCRIPT_TIMEOUT = 90.0
FRAMES_TIMEOUT = 150.0
MAX_ERROR_TEXT = 500


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    if len(text) > MAX_ERROR_TEXT:
        text = text[: MAX_ERROR_TEXT - 1] + "…"
    return text


def _start_video(video_id: str, url: str) -> dict[str, Any] | None:
    """Create/refresh the videos row for a video that is about to be processed."""
    existing = db.video_row(video_id)
    fields: dict[str, Any] = {
        "video_id": video_id,
        "url": url,
        "status": "extracting",
        "error": None,
    }
    if not existing:
        fields["added"] = db.now()
    db.upsert_video(**fields)
    return existing


def _fail_video(video_id: str, url: str, message: str, had_row: bool) -> None:
    fields: dict[str, Any] = {
        "video_id": video_id,
        "url": url,
        "status": "error",
        "error": message,
    }
    if not had_row:
        fields["added"] = db.now()
    try:
        db.upsert_video(**fields)
    except Exception:
        log.exception("failed to persist error state for video %s", video_id)


async def run_ingest_job(job_id: str, urls: list[str]) -> None:
    """Process a list of YouTube URLs into markdown documents, updating the job row."""
    urls = [str(url or "").strip() for url in (urls or [])]
    total = len(urls)
    processed = 0
    errors: list[str] = []

    db.update_job(job_id, status="running", total=total, done=0, current=None, error=None)

    try:
        for raw_url in urls:
            video_id = youtube.parse_video_id(raw_url)
            if not video_id:
                message = f"not a YouTube video URL: {raw_url!r}"
                errors.append(message)
                log.warning("job %s: %s", job_id, message)
                processed += 1
                db.update_job(job_id, done=processed)
                continue

            had_row = False
            title = raw_url
            canonical = youtube.canonical_url(video_id)
            try:
                # The router owns dedupe: every URL here must be processed
                # (duplicates are filtered unless force=True).
                had_row = _start_video(video_id, canonical) is not None

                # 1) metadata
                video = await youtube.fetch_video(raw_url)
                video["video_id"] = video.get("video_id") or video_id
                title = str(video.get("title") or raw_url)
                db.update_job(job_id, current=title)
                db.upsert_video(
                    video_id=video_id,
                    url=str(video.get("url") or canonical),
                    title=video.get("title") or "",
                    channel=video.get("channel") or "",
                    channel_id=video.get("channel_id") or "",
                    duration=int(video.get("duration") or 0),
                    published=video.get("published") or "",
                    thumbnail=video.get("thumbnail") or "",
                    status="extracting",
                    error=None,
                )

                # 2) transcript (never fatal)
                segments: list[dict[str, Any]] = []
                transcript_source = "none"
                try:
                    segments, transcript_source = await asyncio.wait_for(
                        youtube.fetch_transcript(video), timeout=TRANSCRIPT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    log.warning("transcript stage timed out for %s", title)
                except Exception as exc:
                    log.warning("transcript stage failed for %s: %s", title, exc)

                # 3) frames (never fatal)
                frame_list: list[dict[str, Any]] = []
                media_source = str(video.get("webpage_url") or video.get("url") or canonical)
                try:
                    frame_list = await asyncio.wait_for(
                        asyncio.to_thread(frames.extract_frames, media_source, video_id),
                        timeout=FRAMES_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    log.warning("frame stage timed out for %s", title)
                except Exception as exc:
                    log.warning("frame stage failed for %s: %s", title, exc)
                frame_list = list(frame_list or [])

                # 4) markdown document
                rel_path, meta = await writer.write_page(
                    video, segments, frame_list, transcript_source
                )

                db.upsert_video(
                    video_id=video_id,
                    url=str(video.get("url") or canonical),
                    title=video.get("title") or "",
                    channel=video.get("channel") or "",
                    channel_id=video.get("channel_id") or "",
                    duration=int(video.get("duration") or 0),
                    published=video.get("published") or "",
                    thumbnail=video.get("thumbnail") or "",
                    wiki_path=rel_path,
                    transcript_source=transcript_source,
                    frame_count=len(frame_list),
                    words=int(meta.get("words") or 0),
                    reading_minutes=int(meta.get("reading_minutes") or 0),
                    status="done",
                    error=None,
                    extracted=db.today(),
                )

                processed += 1
                db.update_job(job_id, done=processed, current=title)
                log.info(
                    "job %s: wrote %s (%d segments, %d frames, %d words)",
                    job_id,
                    rel_path,
                    len(segments),
                    len(frame_list),
                    int(meta.get("words") or 0),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = _error_text(exc)
                log.exception("job %s: failed to ingest %s", job_id, raw_url)
                _fail_video(video_id, canonical, message, had_row)
                errors.append(f"{title}: {message}")
                processed += 1
                db.update_job(job_id, done=processed, current=title)
    except asyncio.CancelledError:
        db.update_job(job_id, status="error", error="cancelled", done=processed)
        raise
    except Exception as exc:  # catastrophic (DB unavailable, ...)
        log.exception("job %s crashed", job_id)
        db.update_job(job_id, status="error", error=_error_text(exc), done=processed)
        return

    summary = "; ".join(errors[:5]) or None
    if len(errors) > 5:
        summary = f"{summary}; (+{len(errors) - 5} more)"
    db.update_job(job_id, status="done", done=processed, current=None, error=summary)
