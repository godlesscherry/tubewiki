"""Markdown document writer.

``ingest/document.py`` builds the detailed document; this module formats it into
the page layout (frontmatter → TL;DR → overview → chapters → takeaways →
transcript) and writes it as ``vault/<slug>.md``.

Everything is flat: there are no categories and no index. The markdown file is
the permanent output; SQLite only tracks what was ingested.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from .. import config
from . import document as document_builder

log = logging.getLogger(__name__)

MAX_SLUG_LEN = 60
_WHITESPACE_RE = re.compile(r"\s+")


def format_timestamp(seconds: float | int | None) -> str:
    """Format seconds as ``mm:ss`` (or ``h:mm:ss`` when needed)."""
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", str(title or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        return "untitled"
    if len(text) > MAX_SLUG_LEN:
        text = text[:MAX_SLUG_LEN].rsplit("-", 1)[0].strip("-") or text[:MAX_SLUG_LEN]
    return text


def _yaml_quote(value: Any) -> str:
    text = str(value or "").replace('"', '\\"')
    return f'"{text}"'


def _clean_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _normalize_segments(segments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = _clean_text(segment.get("text"))
        if not text:
            continue
        try:
            start = max(0.0, float(segment.get("start") or 0.0))
            end = float(segment.get("end") or 0.0)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        if end <= start:
            end = start + max(1.0, len(text) / 15.0)
        out.append({"start": start, "end": end, "text": text})
    out.sort(key=lambda item: item["start"])
    return out


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------


def _frontmatter(
    video: dict[str, Any],
    transcript_source: str,
    frames: list[dict[str, Any]],
    document: dict[str, Any] | None = None,
) -> str:
    published = _clean_text(video.get("published")) or "unknown"
    extracted = _clean_text(video.get("extracted")) or date.today().isoformat()
    source_url = _clean_text(video.get("webpage_url") or video.get("url"))
    document = document or {}
    lines = [
        "---",
        f"title: {_yaml_quote(video.get('title') or 'Untitled video')}",
        "source_type: youtube",
        f"source_url: {source_url}",
        f"video_id: {_clean_text(video.get('video_id'))}",
        f"channel: {_yaml_quote(video.get('channel') or '')}",
        f"published: {published}",
        f"extracted: {extracted}",
        f"transcript_source: {transcript_source or 'none'}",
        f"frames: {len(frames)}",
        f"document: {document.get('mode') or 'extractive'}",
        f"words: {int(document.get('words') or 0)}",
        f"reading_minutes: {int(document.get('reading_minutes') or 1)}",
        "---",
    ]
    return "\n".join(lines)


def _source_block(video: dict[str, Any], transcript_source: str) -> str:
    source_url = _clean_text(video.get("webpage_url") or video.get("url"))
    channel = _clean_text(video.get("channel")) or "Unknown channel"
    extracted = _clean_text(video.get("extracted")) or date.today().isoformat()
    lines = [
        f"> **Source:** [watch]({source_url}) · **Channel:** {channel}",
        f"> **Extracted:** {extracted} · **Transcript:** {transcript_source or 'none'}",
    ]
    if transcript_source != "captions":
        lines.append(
            "> _No captions were available; this page was generated from the video description._"
        )
    return "\n".join(lines)


def _transcript_block(segments: list[dict[str, Any]]) -> str:
    if segments:
        body = "\n".join(
            f"[{format_timestamp(segment['start'])}] {segment['text']}" for segment in segments
        )
    else:
        body = "_No transcript was available for this video._"
    return (
        "## Transcript\n"
        "<details><summary>Full transcript</summary>\n\n"
        f"{body}\n\n"
        "</details>"
    )


def _frame_markdown(frame: dict[str, Any]) -> str:
    timestamp = format_timestamp(frame.get("time") or 0)
    url = str(frame.get("url") or "").strip()
    if not url:
        return ""
    caption = _clean_text(frame.get("caption")) or "Auto-captured frame"
    return f"![Frame at {timestamp}]({url})\n*{caption} · {timestamp}*"


def _chapter_block(chapter: dict[str, Any]) -> str:
    timestamp = format_timestamp(chapter.get("start") or 0)
    heading = _clean_text(chapter.get("heading")) or "Section"
    lines: list[str] = [f"## {timestamp} — {heading}", ""]

    paragraphs = [
        paragraph
        for paragraph in (chapter.get("paragraphs") or [])
        if isinstance(paragraph, dict) and _clean_text(paragraph.get("text"))
    ]
    frames = sorted(
        [
            frame
            for frame in (chapter.get("frames") or [])
            if isinstance(frame, dict) and frame.get("url")
        ],
        key=lambda frame: float(frame.get("time") or 0.0),
    )
    paragraph_starts = [float(paragraph.get("start") or 0.0) for paragraph in paragraphs]
    buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(paragraphs))}
    for frame in frames:
        frame_time = float(frame.get("time") or 0.0)
        target = 0
        for index, start in enumerate(paragraph_starts):
            if start <= frame_time:
                target = index
        if buckets:
            buckets[target].append(frame)

    for index, paragraph in enumerate(paragraphs):
        lines.append(_clean_text(paragraph.get("text")))
        lines.append("")
        for frame in buckets.get(index, []):
            markdown = _frame_markdown(frame)
            if markdown:
                lines.append(markdown)
                lines.append("")

    quote = chapter.get("quote") or {}
    quote_text = _clean_text(quote.get("text"))
    if quote_text:
        lines.append(f'> "{quote_text}"')
        lines.append(f"> — {format_timestamp(quote.get('start'))}")
        lines.append("")

    highlights = [_clean_text(item) for item in (chapter.get("highlights") or []) if _clean_text(item)]
    if highlights:
        lines.append("**Highlights**")
        lines.append("")
        lines.extend(f"- {item}" for item in highlights)
        lines.append("")

    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _document_blocks(document: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    overview = [
        _clean_text(paragraph)
        for paragraph in (document.get("overview") or [])
        if _clean_text(paragraph)
    ]
    if overview:
        blocks.append("## Overview\n\n" + "\n\n".join(overview))

    chapters = [chapter for chapter in (document.get("chapters") or []) if isinstance(chapter, dict)]
    if chapters:
        contents = [
            f"- **{format_timestamp(chapter.get('start') or 0)}** — "
            f"{_clean_text(chapter.get('heading')) or 'Section'}"
            for chapter in chapters
        ]
        blocks.append("## In this document\n\n" + "\n".join(contents))
        blocks.extend(_chapter_block(chapter) for chapter in chapters)

    takeaways = [
        _clean_text(item) for item in (document.get("takeaways") or []) if _clean_text(item)
    ]
    if takeaways:
        blocks.append("## Key takeaways\n\n" + "\n".join(f"- {item}" for item in takeaways))
    return blocks


def _existing_page_for_video(video_id: str | None) -> Path | None:
    """Find an already-written document for this video (flat folder)."""
    video_id = _clean_text(video_id)
    if not video_id or not config.WIKI_DIR.exists():
        return None
    needle = f"video_id: {video_id}"
    for candidate in sorted(config.WIKI_DIR.glob("*.md")):
        try:
            head = candidate.read_text(encoding="utf-8", errors="replace")[:1600]
        except OSError:
            continue
        if needle in head:
            return candidate
    return None


async def write_page(
    video: dict[str, Any],
    segments: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    transcript_source: str,
) -> tuple[str, dict[str, Any]]:
    """Build + persist the markdown document.

    Returns ``(filename, meta)`` where meta carries ``words``, ``reading_minutes``
    and ``mode`` for the pipeline to store in SQLite.
    """
    video = dict(video or {})
    normalized_segments = _normalize_segments(segments)
    clean_frames = sorted(
        [frame for frame in (frames or []) if isinstance(frame, dict)],
        key=lambda frame: float(frame.get("time") or 0.0),
    )

    title = _clean_text(video.get("title")) or "Untitled video"
    document = await document_builder.build_document(video, normalized_segments, clean_frames)
    tldr = [
        _clean_text(bullet) for bullet in (document.get("tldr") or []) if _clean_text(bullet)
    ] or ["No summary could be generated automatically."]

    blocks: list[str] = [
        _frontmatter(video, transcript_source, clean_frames, document),
        f"# {title}",
        _source_block(video, transcript_source),
        "## TL;DR\n" + "\n".join(f"- {bullet}" for bullet in tldr[:3]),
    ]
    blocks.extend(_document_blocks(document))
    blocks.append(_transcript_block(normalized_segments))

    markdown = "\n\n".join(block.strip() for block in blocks if block and block.strip()) + "\n"

    config.WIKI_DIR.mkdir(parents=True, exist_ok=True)
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    path = _existing_page_for_video(video.get("video_id"))
    if path is None:
        base_slug = slugify(title)
        slug = base_slug
        counter = 2
        while (config.WIKI_DIR / f"{slug}.md").exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        path = config.WIKI_DIR / f"{slug}.md"

    path.write_text(markdown, encoding="utf-8")
    meta = {
        "words": int(document.get("words") or 0),
        "reading_minutes": int(document.get("reading_minutes") or 1),
        "mode": document.get("mode") or "extractive",
    }
    log.info(
        "wrote document %s (%d chars, %s, %d words)",
        path.name,
        len(markdown),
        meta["mode"],
        meta["words"],
    )
    return path.name, meta


__all__ = [
    "format_timestamp",
    "slugify",
    "write_page",
]
