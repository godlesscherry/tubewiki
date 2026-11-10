"""YouTube metadata + transcript helpers built on yt-dlp.

Design goals:

* ``fetch_video`` raises :class:`YouTubeError` when metadata cannot be fetched;
  the pipeline catches it, marks that video as failed and keeps going.
* ``fetch_transcript`` never raises: it returns ``([], "none")`` on any problem
  (no captions, network error, timeout, ...) so a video without captions still
  produces a wiki page from its description.
* All temporary download state lives under ``<wiki>/.forge/tmp``.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp

from .. import config

log = logging.getLogger(__name__)

METADATA_TIMEOUT = 100.0
SUBTITLE_TIMEOUT = 80.0

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_ID_RE = re.compile(r"/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})(?:[/?#]|$)")

_VTT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
_SRV3_P_RE = re.compile(r'<p\s+t="(\d+)"(?:\s+d="(\d+)")?[^>]*>(.*?)</p>', re.DOTALL)
_YT_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
}


class YouTubeError(RuntimeError):
    """Raised when video metadata cannot be retrieved."""


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def parse_video_id(url: str | None) -> str | None:
    """Return the 11-char YouTube id for any common YouTube URL, else None."""
    if not url:
        return None
    value = str(url).strip()
    if _VIDEO_ID_RE.match(value):
        return value
    try:
        parsed = urlparse(value if "://" in value else "https://" + value)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host not in _YT_HOSTS and not host.endswith(".youtube.com"):
        return None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0] if parsed.path.strip("/") else ""
        return candidate if _VIDEO_ID_RE.match(candidate) else None
    query_ids = parse_qs(parsed.query).get("v")
    if query_ids and _VIDEO_ID_RE.match(query_ids[0]):
        return query_ids[0]
    match = _PATH_ID_RE.search(parsed.path)
    if match:
        return match.group(1)
    return None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# yt-dlp plumbing
# ---------------------------------------------------------------------------


def _tmp_dir() -> Path:
    base = config.WIKI_DIR / ".forge" / "tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="yt-", dir=str(base)))


def _ffmpeg_location() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - imageio-ffmpeg is a hard requirement
        return None


def _base_opts() -> dict[str, Any]:
    """Shared yt-dlp options.

    YouTube now requires a JS runtime to expose media/caption formats.  Without
    ``js_runtimes`` + ``remote_components`` the format list comes back empty.
    These were verified working on this machine (yt-dlp 2026.10.26 / Node).
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "cachedir": False,
        # YouTube's player challenge needs a JS runtime + challenge solver.
        "js_runtimes": {"node": {}},
        "remote_components": {"ejs:github"},
    }
    ffmpeg = _ffmpeg_location()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return opts


def _format_upload_date(value: Any) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _thumb_url(info: dict[str, Any]) -> str:
    thumb = info.get("thumbnail")
    if thumb:
        return str(thumb)
    thumbnails = info.get("thumbnails") or []
    for entry in reversed(thumbnails):
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"])
    return ""


def _subtitle_map(raw: Any) -> dict[str, list[dict[str, str]]]:
    """Reduce yt-dlp's subtitle metadata to ``{lang: [{url, ext}, ...]}``."""
    out: dict[str, list[dict[str, str]]] = {}
    if not isinstance(raw, dict):
        return out
    for lang, entries in raw.items():
        if not isinstance(entries, list):
            continue
        clean: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            clean.append({"url": str(url), "ext": str(entry.get("ext") or "")})
        if clean:
            out[str(lang)] = clean
    return out


def _extract_info(target: str) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(_base_opts()) as ydl:
        info = ydl.extract_info(target, download=False)
    if not info:
        raise YouTubeError("yt-dlp returned no metadata")
    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            raise YouTubeError("playlist had no playable entries")
        info = entries[0]
    if not isinstance(info, dict):
        raise YouTubeError("unexpected yt-dlp metadata payload")
    return info


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


async def fetch_video(url: str) -> dict[str, Any]:
    """Fetch metadata for ``url``. Raises :class:`YouTubeError` on failure."""
    video_id = parse_video_id(url)
    if not video_id:
        raise YouTubeError(f"not a YouTube video URL: {url!r}")
    target = canonical_url(video_id)
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract_info, target), timeout=METADATA_TIMEOUT
        )
    except asyncio.TimeoutError as exc:
        raise YouTubeError(
            f"yt-dlp timed out after {METADATA_TIMEOUT:.0f}s fetching {target}"
        ) from exc
    except YouTubeError:
        raise
    except Exception as exc:  # yt-dlp raises DownloadError and friends
        raise YouTubeError(f"yt-dlp failed for {target}: {exc}") from exc

    raw_id = str(info.get("id") or "")
    resolved_id = raw_id if _VIDEO_ID_RE.match(raw_id) else video_id
    webpage = str(info.get("webpage_url") or canonical_url(resolved_id))
    duration = info.get("duration")
    try:
        duration_int = int(duration) if duration not in (None, "") else 0
    except (TypeError, ValueError):
        duration_int = 0

    video = {
        "video_id": resolved_id,
        "title": str(info.get("title") or "").strip() or "Untitled video",
        "channel": str(info.get("channel") or info.get("uploader") or "").strip(),
        "channel_id": str(info.get("channel_id") or info.get("uploader_id") or "").strip(),
        "duration": duration_int,
        "published": _format_upload_date(info.get("upload_date")),
        "thumbnail": _thumb_url(info),
        "url": webpage,
        "webpage_url": webpage,
        "description": str(info.get("description") or "").strip(),
        # private hints used by fetch_transcript (kept small + JSON-safe)
        "_manual_subtitles": _subtitle_map(info.get("subtitles")),
        "_automatic_captions": _subtitle_map(info.get("automatic_captions")),
    }
    return video


# ---------------------------------------------------------------------------
# Subtitle parsing
# ---------------------------------------------------------------------------


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _seconds(groups: tuple[str | None, ...], offset: int = 0) -> float:
    hours = int(groups[offset] or 0)
    minutes = int(groups[offset + 1] or 0)
    seconds = int(groups[offset + 2] or 0)
    millis = int(str(groups[offset + 3] or "0").ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_json3(text: str) -> list[dict[str, Any]]:
    """Parse YouTube ``.json3`` caption payload text into segments."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    segments: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        parts: list[str] = []
        for seg in event.get("segs") or []:
            if isinstance(seg, dict):
                parts.append(str(seg.get("utf8") or ""))
        segment_text = _clean_text("".join(parts))
        if not segment_text:
            continue
        start = max(0.0, float(event.get("tStartMs") or 0) / 1000.0)
        duration = float(event.get("dDurationMs") or 0) / 1000.0
        segments.append({"start": start, "end": start + duration, "text": segment_text})
    return _fix_ends(segments)


def parse_vtt(text: str) -> list[dict[str, Any]]:
    """Parse WebVTT caption text into segments."""
    cleaned = str(text or "").replace("\ufeff", "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = cleaned.split("\n")
    segments: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        match = _VTT_TIME_RE.search(line)
        if not match:
            index += 1
            continue
        start = _seconds(match.groups(), 0)
        end = _seconds(match.groups(), 4)
        index += 1
        parts: list[str] = []
        while index < len(lines) and lines[index].strip():
            if _VTT_TIME_RE.search(lines[index]):
                break
            parts.append(_clean_text(re.sub(r"<[^>]+>", " ", lines[index])))
            index += 1
        segment_text = _clean_text(" ".join(part for part in parts if part))
        if segment_text:
            segments.append({"start": start, "end": end, "text": segment_text})
    return _fix_ends(segments)


def parse_srv3(text: str) -> list[dict[str, Any]]:
    """Parse YouTube ``.srv3`` (XML) caption payloads. Best-effort."""
    segments: list[dict[str, Any]] = []
    for match in _SRV3_P_RE.finditer(str(text or "")):
        start_ms = int(match.group(1))
        dur_ms = int(match.group(2) or 0)
        segment_text = _clean_text(match.group(3))
        if segment_text:
            segments.append(
                {
                    "start": start_ms / 1000.0,
                    "end": (start_ms + dur_ms) / 1000.0,
                    "text": segment_text,
                }
            )
    return _fix_ends(segments)


def parse_subtitle_file(path: Path) -> list[dict[str, Any]]:
    """Parse a downloaded subtitle file based on its extension."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = Path(path).suffix.lower()
    if suffix == ".json3":
        return parse_json3(text)
    if suffix == ".vtt":
        return parse_vtt(text)
    if suffix == ".srv3":
        return parse_srv3(text)
    if suffix == ".ttml":
        return parse_srv3(text)
    return []


def _fix_ends(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make sure every segment has a sane end time (json3 often omits it)."""
    ordered = sorted(segments, key=lambda seg: float(seg["start"]))
    for index, segment in enumerate(ordered):
        start = float(segment["start"])
        end = float(segment.get("end") or 0.0)
        if end <= start:
            if index + 1 < len(ordered):
                end = float(ordered[index + 1]["start"])
            if end <= start:
                end = start + max(1.0, len(segment["text"]) / 15.0)
        segment["start"] = round(start, 3)
        segment["end"] = round(end, 3)
    return ordered


def clean_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise caption segments and collapse duplicated rolling captions."""
    prepared: list[dict[str, Any]] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = _clean_text(segment.get("text"))
        if not text or re.fullmatch(r"\[[^\]]{0,60}\]", text):
            continue
        try:
            start = max(0.0, float(segment.get("start") or 0.0))
            end = float(segment.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if end <= start:
            end = start + max(1.0, len(text) / 15.0)
        prepared.append({"start": start, "end": end, "text": text})
    prepared.sort(key=lambda seg: seg["start"])

    merged: list[dict[str, Any]] = []
    for segment in prepared:
        if merged:
            previous = merged[-1]
            close = segment["start"] <= previous["end"] + 0.4
            if segment["text"] == previous["text"]:
                previous["end"] = max(previous["end"], segment["end"])
                continue
            if close and (
                segment["text"] in previous["text"] or previous["text"] in segment["text"]
            ):
                if len(segment["text"]) > len(previous["text"]):
                    previous["text"] = segment["text"]
                previous["end"] = max(previous["end"], segment["end"])
                continue
        merged.append(dict(segment))
    for segment in merged:
        segment["start"] = round(segment["start"], 3)
        segment["end"] = round(segment["end"], 3)
    return merged


# ---------------------------------------------------------------------------
# Subtitle downloading
# ---------------------------------------------------------------------------


def _preferred_langs(langs: list[str]) -> list[str]:
    """Order subtitle language codes: exact ``en`` first, then en-*, then rest."""
    values = [str(lang) for lang in langs if isinstance(lang, str) and lang]
    exact_en = [lang for lang in values if lang.lower() == "en"]
    english = [lang for lang in values if lang.lower().startswith("en") and lang not in exact_en]
    other = [lang for lang in values if not lang.lower().startswith("en")]
    ordered = exact_en + english + other[:2]
    return ordered[:3]


def _scan_subtitle_files(root: Path) -> list[Path]:
    suffixes = {".json3", ".vtt", ".srv3", ".ttml"}
    found: list[Path] = []
    try:
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and path.suffix.lower() in suffixes:
                found.append(path)
    except OSError:
        return []
    return found


def _subtitle_sort_key(path: Path) -> tuple[int, int, str]:
    parts = Path(path).name.split(".")
    lang = parts[-2].lower() if len(parts) >= 3 else ""
    if lang == "en":
        lang_rank = 0
    elif lang.startswith("en"):
        lang_rank = 1
    else:
        lang_rank = 2
    suffix = Path(path).suffix.lower()
    ext_rank = 0 if suffix == ".json3" else 1 if suffix == ".vtt" else 2
    return (lang_rank, ext_rank, Path(path).name)


def _download_subs_sync(target: str, tmp: Path, attempt: dict[str, Any]) -> list[Path]:
    opts = _base_opts()
    opts.update(
        {
            "skip_download": True,
            "writesubtitles": bool(attempt.get("writesubtitles")),
            "writeautomaticsub": bool(attempt.get("writeautomaticsub")),
            "subtitleslangs": list(attempt.get("langs") or ["en.*", "en"]),
            "subtitlesformat": "json3/vtt/best",
            "outtmpl": str(tmp / "%(id)s.%(ext)s"),
        }
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([target])
    return _scan_subtitle_files(tmp)


async def _download_subtitle_files(
    target: str, tmp: Path, video: dict[str, Any]
) -> list[Path]:
    manual = video.get("_manual_subtitles") if isinstance(video, dict) else None
    automatic = video.get("_automatic_captions") if isinstance(video, dict) else None
    manual_langs = _preferred_langs(list(manual)) if isinstance(manual, dict) else []
    auto_langs = _preferred_langs(list(automatic)) if isinstance(automatic, dict) else []

    attempts: list[dict[str, Any]] = []
    if manual_langs:
        attempts.append(
            {"writesubtitles": True, "writeautomaticsub": False, "langs": manual_langs}
        )
    if auto_langs:
        attempts.append(
            {"writesubtitles": False, "writeautomaticsub": True, "langs": auto_langs}
        )
    if not attempts:
        # No subtitle metadata hints (e.g. video dict reloaded from the DB):
        # try English manual+auto first, then any language at all.
        attempts.append(
            {"writesubtitles": True, "writeautomaticsub": True, "langs": ["en.*", "en"]}
        )
        attempts.append(
            {"writesubtitles": False, "writeautomaticsub": True, "langs": ["all"]}
        )

    deadline = time.monotonic() + SUBTITLE_TIMEOUT
    for attempt in attempts:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        try:
            found = await asyncio.wait_for(
                asyncio.to_thread(_download_subs_sync, target, tmp, attempt),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            log.warning("subtitle download timed out for %s", target)
            break
        except Exception as exc:  # missing captions, network hiccups, ...
            log.debug("subtitle download attempt failed for %s: %s", target, exc)
            found = []
        if found:
            return sorted(found, key=_subtitle_sort_key)
    return _scan_subtitle_files(tmp)


async def _fetch_direct_subtitles(video: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback: fetch caption payloads directly from URLs stored in metadata."""
    if not isinstance(video, dict):
        return []
    manual = video.get("_manual_subtitles")
    automatic = video.get("_automatic_captions")
    candidates: list[tuple[str, str]] = []
    for source in (manual, automatic):
        if not isinstance(source, dict):
            continue
        for lang in _preferred_langs(list(source)):
            for entry in source.get(lang) or []:
                if not isinstance(entry, dict):
                    continue
                url = entry.get("url")
                ext = str(entry.get("ext") or "").lower()
                if url and ext in {"json3", "vtt", "srv3", "ttml"}:
                    candidates.append((ext, str(url)))
    if not candidates:
        return []
    candidates.sort(key=lambda item: 0 if item[0] == "json3" else 1)
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is a hard dependency
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for ext, url in candidates[:6]:
                try:
                    response = await client.get(url)
                except Exception:
                    continue
                if response.status_code != 200 or not response.text:
                    continue
                if ext == "json3":
                    segments = parse_json3(response.text)
                elif ext == "vtt":
                    segments = parse_vtt(response.text)
                else:
                    segments = parse_srv3(response.text)
                if segments:
                    return segments
    except Exception as exc:
        log.debug("direct subtitle fetch failed: %s", exc)
    return []


async def fetch_transcript(
    video: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Return ``(segments, source)`` where source is ``"captions"`` or ``"none"``.

    Never raises: any failure degrades to ``([], "none")``.
    """
    if not isinstance(video, dict):
        return [], "none"
    video_id = video.get("video_id") or parse_video_id(
        video.get("url") or video.get("webpage_url")
    )
    if not video_id:
        return [], "none"
    target = str(video.get("webpage_url") or video.get("url") or "").strip()
    if not target.startswith("http"):
        target = canonical_url(str(video_id))

    tmp = _tmp_dir()
    try:
        # Preferred path: the same extract_info() call that produced metadata
        # already gave us signed caption URLs (vtt/json3/srt). Fetch those
        # directly - no reliance on yt-dlp writing files.
        segments = clean_segments(await _fetch_direct_subtitles(video))
        if not segments:
            # Fallback: ask yt-dlp to download subtitle files (works for video
            # dicts reloaded from the DB, where metadata hints are absent).
            files = await _download_subtitle_files(target, tmp, video)
            for path in files:
                try:
                    segments = clean_segments(parse_subtitle_file(path))
                except Exception as exc:
                    log.debug("failed to parse subtitle file %s: %s", path, exc)
                    segments = []
                if segments:
                    break
        if not segments:
            return [], "none"
        return segments, "captions"
    except asyncio.TimeoutError:
        log.warning("transcript fetch timed out for %s", target)
        return [], "none"
    except Exception as exc:
        log.warning("transcript fetch failed for %s: %s", target, exc)
        return [], "none"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
