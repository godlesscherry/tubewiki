"""Scene-detected video frames via ffmpeg, with near-duplicate/blank filtering.

Verified against ffmpeg 9.0 (imageio-ffmpeg 9.0), which is much stricter than
older builds:

* ``-vsync vfr`` is gone -> ``-fps_mode vfr``.
* JPEG encoding needs ``-pix_fmt yuvj420p`` (or PNG output).
* ``showinfo`` logs at ``info`` level and its ``pts_time:`` values map 1:1 to
  the output frames in order.
* A scene threshold of 0.35 finds nothing on slide/animation videos; ~0.12 is
  productive and then we cap + dedupe.

Frames are written to ``<wiki>/assets/<video_id>/<hhmmss>.jpg`` and returned as
``{time, path, url, caption}`` dicts.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from .. import config
from . import youtube

log = logging.getLogger(__name__)

SCENE_THRESHOLD = 0.12
SCENE_CANDIDATE_FACTOR = 8  # keep a bigger pool, then dedupe/sample down
MAX_RUNTIME = 115.0  # seconds of wall clock we are willing to spend on frames
FFMPEG_TIMEOUT = 105.0
AVERAGE_HASH_SIZE = 16
HASH_DUPLICATE_DISTANCE = 4
BLANK_STDDEV_THRESHOLD = 8.0
MAX_IMAGE_WIDTH = 960
JPEG_QUALITY = 82

_SHOWINFO_TIME_RE = re.compile(r"pts_time:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".m4v", ".mov", ".avi"}


# ---------------------------------------------------------------------------
# Image fingerprint helpers (pure; unit tested offline)
# ---------------------------------------------------------------------------


def average_hash(image: Image.Image) -> int:
    """64-bit average hash of a Pillow image (16x16 grayscale > mean)."""
    grey = image.convert("L").resize((AVERAGE_HASH_SIZE, AVERAGE_HASH_SIZE), Image.LANCZOS)
    pixels = list(grey.tobytes())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for index, pixel in enumerate(pixels):
        if pixel > mean:
            bits |= 1 << index
    return bits


def hash_distance(left: int, right: int) -> int:
    """Hamming distance between two average hashes."""
    return (left ^ right).bit_count()


def is_near_blank(image: Image.Image, threshold: float = BLANK_STDDEV_THRESHOLD) -> bool:
    """True for frames that are almost uniform (plain background, black, ...)."""
    grey = image.convert("L").resize((64, 64), Image.LANCZOS)
    return float(ImageStat.Stat(grey).stddev[0]) < threshold


def _evenly_spaced(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Pick ``limit`` items spread evenly across the list (keeps first + last)."""
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:1]
    indices = sorted({round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)})
    return [items[index] for index in indices]


def select_unique_frames(
    candidates: list[dict[str, Any]], max_frames: int = 12
) -> list[dict[str, Any]]:
    """Filter *candidates* (``{time, path}``) for blanks + near-duplicates.

    Duplicates are removed first, then the survivors are sampled evenly across
    the timeline so the chosen frames cover the whole video instead of
    clustering in the opening minutes.  Returns at most ``max_frames`` entries
    ``{time, path, hash}`` sorted by time.
    """
    unique: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("time") or 0.0)):
        path = Path(candidate["path"])
        try:
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                if is_near_blank(rgb):
                    continue
                fingerprint = average_hash(rgb)
        except (OSError, ValueError):
            continue
        if any(hash_distance(fingerprint, item["hash"]) <= HASH_DUPLICATE_DISTANCE for item in unique):
            continue
        unique.append(
            {"time": round(max(0.0, float(candidate.get("time") or 0.0)), 3), "path": path, "hash": fingerprint}
        )
    return _evenly_spaced(unique, max_frames)


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _ffmpeg() -> str:
    return youtube._ffmpeg_location() or "ffmpeg"


def _tmp_root() -> Path:
    base = config.WIKI_DIR / ".forge" / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _run_ffmpeg(
    media: Path, outdir: Path, filter_graph: str, frame_cap: int, deadline: float
) -> tuple[list[Path], str]:
    """Run ffmpeg once; fall back from JPEG to PNG if the encoder fails."""
    outdir.mkdir(parents=True, exist_ok=True)
    timeout = max(5.0, min(FFMPEG_TIMEOUT, deadline - time.monotonic()))
    variants: list[tuple[str, list[str]]] = [
        (
            "frame_%04d.jpg",
            ["-pix_fmt", "yuvj420p", "-q:v", "3"],
        ),
        # PNG fallback: works without the full-range YUV flag.
        ("frame_%04d.png", []),
    ]
    last_stderr = ""
    for pattern, extra in variants:
        cmd = [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "info",
            "-y",
            "-i",
            str(media),
            "-vf",
            filter_graph,
            "-fps_mode",
            "vfr",
            *extra,
            "-frames:v",
            str(max(1, frame_cap)),
            str(outdir / pattern),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace", timeout=timeout
            )
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg frame extraction timed out after %.0fs", timeout)
            return [], last_stderr
        last_stderr = proc.stderr or ""
        files = sorted(
            path
            for path in outdir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".png"}
        )
        if files and proc.returncode == 0:
            return files, last_stderr
        if files:  # partial output is still useful
            return files, last_stderr
        log.debug("ffmpeg variant %s failed (rc=%s): %s", pattern, proc.returncode, last_stderr[-600:])
    return [], last_stderr


def _duration_from_stderr(stderr: str) -> float:
    match = _DURATION_RE.search(stderr or "")
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def parse_showinfo_times(stderr: str) -> list[float]:
    """Extract ``pts_time`` values from ffmpeg showinfo output, in order."""
    times: list[float] = []
    for line in (stderr or "").splitlines():
        if "pts_time:" not in line:
            continue
        match = _SHOWINFO_TIME_RE.search(line)
        if match:
            times.append(max(0.0, float(match.group(1))))
    return times


def _candidates_from_files(
    files: list[Path], times: list[float], duration: float
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, path in enumerate(files):
        if index < len(times):
            frame_time = times[index]
        elif duration > 0:
            frame_time = duration * (index + 1) / (len(files) + 1)
        else:
            frame_time = float(index) * 5.0
        candidates.append({"time": max(0.0, float(frame_time)), "path": path})
    candidates.sort(key=lambda item: item["time"])
    return candidates


def _scene_candidates(
    media: Path, tmp: Path, max_frames: int, deadline: float
) -> tuple[list[dict[str, Any]], float]:
    outdir = tmp / "scene"
    filter_graph = f"select='gt(scene,{SCENE_THRESHOLD})',showinfo,scale=640:-1"
    candidate_cap = max(24, max_frames * SCENE_CANDIDATE_FACTOR)
    files, stderr = _run_ffmpeg(media, outdir, filter_graph, candidate_cap, deadline)
    duration = _duration_from_stderr(stderr)
    return _candidates_from_files(files, parse_showinfo_times(stderr), duration), duration


def _uniform_candidates(
    media: Path, tmp: Path, max_frames: int, duration: float, deadline: float
) -> list[dict[str, Any]]:
    outdir = tmp / "uniform"
    interval = max(6.0, duration / max(2, max_frames))
    filter_graph = (
        f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval:.3f})',"
        "showinfo,scale=640:-1"
    )
    files, stderr = _run_ffmpeg(media, outdir, filter_graph, max_frames, deadline)
    return _candidates_from_files(files, parse_showinfo_times(stderr), duration)


# ---------------------------------------------------------------------------
# Download + save
# ---------------------------------------------------------------------------


# Frame extraction only needs the video stream, so prefer a low-res *video-only*
# format: small, fast, and no merging. (Progressive format 18 is served truncated
# for some videos; worst/ storyboard previews are not decodable at all.)
_DOWNLOAD_FORMATS = (
    "bestvideo[height<=360][ext=mp4]/bestvideo[height<=360]/160/134/135/18/best[ext=mp4]/best",
    "18/best[ext=mp4][height<=480]/best[height<=480][ext=mp4]/best[height<=480]/best",
    "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
)
_MIN_MEDIA_BYTES = 200_000


def _valid_media(path: Path | None) -> bool:
    if not path or not path.exists() or not path.is_file():
        return False
    if path.suffix.lower() not in _VIDEO_SUFFIXES:
        return False
    try:
        return path.stat().st_size >= _MIN_MEDIA_BYTES
    except OSError:
        return False


def _download_media(url: str, tmp: Path, deadline: float) -> Path | None:
    """Download a decodable mp4 with yt-dlp (shared options from youtube)."""
    ffmpeg_location = youtube._ffmpeg_location()
    for attempt_index, fmt in enumerate(_DOWNLOAD_FORMATS):
        opts = youtube._base_opts()
        opts.update(
            {
                "format": fmt,
                "skip_download": False,
                "outtmpl": str(tmp / f"media{attempt_index}.%(ext)s"),
                "overwrites": True,
                "merge_output_format": "mp4",
            }
        )
        if ffmpeg_location:
            opts["ffmpeg_location"] = ffmpeg_location
        try:
            with youtube.yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if isinstance(info, dict) and info.get("_type") == "playlist":
                    entries = [entry for entry in (info.get("entries") or []) if entry]
                    info = entries[0] if entries else info
                candidates: list[Path] = []
                if isinstance(info, dict):
                    try:
                        prepared = Path(ydl.prepare_filename(info))
                        candidates.append(prepared)
                        candidates.append(prepared.with_suffix(".mp4"))
                    except Exception:
                        pass
                candidates.extend(
                    sorted(tmp.glob(f"media{attempt_index}.*"))
                )
                for candidate in candidates:
                    if _valid_media(candidate):
                        return candidate
        except Exception as exc:
            log.warning(
                "frame source download attempt %d failed for %s: %s", attempt_index + 1, url, exc
            )
        if time.monotonic() >= deadline:
            break
    # Last resort: any decodable-looking video file left behind.
    for path in sorted(tmp.rglob("*")):
        if _valid_media(path):
            return path
    return None


def _timestamp_name(seconds: float, used: set[str]) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    base = f"{hours:02d}{minutes:02d}{secs:02d}"
    name = base
    counter = 2
    while name in used:
        name = f"{base}-{counter}"
        counter += 1
    used.add(name)
    return name


def _save_frames(selected: list[dict[str, Any]], video_id: str) -> list[dict[str, Any]]:
    assets = config.ASSETS_DIR / video_id
    assets.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for item in selected:
        frame_time = float(item["time"])
        name = _timestamp_name(frame_time, used_names)
        target = assets / f"{name}.jpg"
        try:
            with Image.open(item["path"]) as image:
                rgb = image.convert("RGB")
                if rgb.width > MAX_IMAGE_WIDTH:
                    height = max(1, round(rgb.height * MAX_IMAGE_WIDTH / rgb.width))
                    rgb = rgb.resize((MAX_IMAGE_WIDTH, height), Image.LANCZOS)
                rgb.save(target, "JPEG", quality=JPEG_QUALITY, optimize=True)
        except (OSError, ValueError) as exc:
            log.debug("failed to save frame %s: %s", item["path"], exc)
            continue
        frames.append(
            {
                "time": round(frame_time, 3),
                "path": str(target.resolve()),
                "url": f"assets/{video_id}/{name}.jpg",
                "caption": None,
            }
        )
    return frames


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def caption_frames(
    frames: list[dict[str, Any]],
    prompt: str = "Describe what is shown in this video frame in one short sentence.",
) -> list[dict[str, Any]]:
    """Optionally add vision-model captions when an LLM is configured."""
    try:
        from .. import llm

        if not llm.available():
            return frames
        import asyncio

        for frame in frames:
            try:
                caption = await asyncio.wait_for(
                    llm.describe_image(frame.get("path", ""), prompt), timeout=60.0
                )
            except Exception:
                caption = None
            frame["caption"] = (caption or "").strip() or None
    except Exception:
        pass
    return frames


def _maybe_caption_sync(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Caption in a worker thread (no running loop) when an LLM is configured."""
    try:
        from .. import llm

        if not frames or not llm.available():
            return frames
        import asyncio

        asyncio.run(caption_frames(frames))
    except Exception:
        pass
    return frames


def extract_frames(
    video_path_or_url: str | Path,
    video_id: str,
    max_frames: int = 12,
) -> list[dict[str, Any]]:
    """Extract up to ``max_frames`` scene frames for a video.

    ``video_path_or_url`` may be a local media path or a YouTube URL/id.  Any
    failure returns ``[]`` - frame extraction must never fail an ingest.
    """
    started = time.monotonic()
    deadline = started + MAX_RUNTIME
    tmp = Path(tempfile.mkdtemp(prefix=f"frames-{video_id}-", dir=str(_tmp_root())))
    media: Path | None = None
    owns_media = False
    try:
        source = str(video_path_or_url or "").strip()
        local = Path(source)
        if source and local.exists() and local.is_file():
            media = local
        else:
            url = source
            if not url.startswith("http"):
                resolved = youtube.parse_video_id(url)
                if not resolved:
                    return []
                url = youtube.canonical_url(resolved)
            media = _download_media(url, tmp, deadline)
            owns_media = True
        if media is None or not media.exists():
            return []
        if time.monotonic() >= deadline:
            return []

        candidates, duration = _scene_candidates(media, tmp, max_frames, deadline)
        if not candidates and duration > 2.0 and time.monotonic() < deadline:
            log.debug("scene detection found nothing for %s; sampling uniformly", video_id)
            candidates = _uniform_candidates(media, tmp, max_frames, duration, deadline)
        if not candidates:
            return []

        selected = select_unique_frames(candidates, max_frames=max_frames)
        frames = _save_frames(selected, video_id)
        if frames:
            frames = _maybe_caption_sync(frames)
        log.info(
            "extracted %d frame(s) for %s in %.1fs", len(frames), video_id, time.monotonic() - started
        )
        return frames
    except Exception as exc:
        log.exception("frame extraction failed for %s: %s", video_id, exc)
        return []
    finally:
        if owns_media and media is not None:
            tmp_media = media.resolve()
            try:
                tmp_media.relative_to(tmp.resolve())
            except (ValueError, OSError):
                tmp_media = None
            if tmp_media is not None and tmp_media.exists():
                try:
                    tmp_media.unlink()
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)
