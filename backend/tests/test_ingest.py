"""Offline tests for the ingest pipeline (no network, tmp wiki + tmp DB)."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from backend.app import config, db, llm
from backend.app.ingest import frames, pipeline, writer, youtube

VID_A = "aircAruvnKk"
VID_B = "aqz-KE-bpKQ"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def wiki(tmp_path, monkeypatch):
    """Point config at a throwaway wiki and disable the LLM."""
    root = tmp_path / "vault"
    monkeypatch.setattr(config, "WIKI_DIR", root)
    monkeypatch.setattr(config, "ASSETS_DIR", root / "assets")
    monkeypatch.setattr(config, "DATA_DIR", root / "data")
    monkeypatch.setattr(config, "DB_PATH", root / "data" / "tubewiki.db")
    monkeypatch.setattr(config, "TMP_DIR", root / "data" / "tmp")
    monkeypatch.setattr(llm, "available", lambda: False)
    db.init_db()
    return root


def _video(video_id: str = "vid12345678", **overrides) -> dict:
    base = {
        "video_id": video_id,
        "title": "Test Video Title",
        "channel": "Test Channel",
        "channel_id": "UCtest",
        "duration": 620,
        "published": "2024-01-02",
        "thumbnail": "https://example.com/thumb.jpg",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "description": "A video about building a markdown wiki from YouTube.",
    }
    base.update(overrides)
    return base


def _segments(count: int = 24, step: float = 12.0) -> list[dict]:
    lines = [
        "Position sizing is the most important risk control for every trader.",
        "Never risk more than one percent of your account on a single trade.",
        "A trading journal makes your mistakes visible and fixable over time.",
        "Expectancy combines win rate and payoff ratio into one number.",
        "Backtesting on out of sample data prevents curve fitting your strategy.",
        "Stop losses must be defined before you enter a trade, not after.",
        "Process consistency beats any single brilliant trade idea.",
        "Risk management determines whether you survive long enough to compound.",
    ]
    segments = []
    for index in range(count):
        start = index * step
        segments.append(
            {
                "start": start,
                "end": start + step - 0.5,
                "text": lines[index % len(lines)],
            }
        )
    return segments


def _frame_markdown_for(time: float, video_id: str = "vid12345678") -> dict:
    name = f"{int(time) // 3600:02d}{(int(time) % 3600) // 60:02d}{int(time) % 60:02d}"
    return {
        "time": float(time),
        "path": f"/tmp/{video_id}/{name}.jpg",
        "url": f"assets/{video_id}/{name}.jpg",
        "caption": None,
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), text[:80]
    body = text[4:]
    end = body.find("\n---")
    assert end > 0
    data: dict[str, str] = {}
    for line in body[:end].splitlines():
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _image(seed: int, size: int = 64) -> Image.Image:
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            pixels[x, y] = (
                (x * 7 + seed * 13) % 256,
                (y * 11 + seed * 29) % 256,
                ((x + y) * 5 + seed * 17) % 256,
            )
    return image


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.youtube.com/watch?v={VID_A}", VID_A),
        (f"https://youtu.be/{VID_A}", VID_A),
        (f"https://www.youtube.com/shorts/{VID_A}?feature=share", VID_A),
        (f"https://m.youtube.com/watch?v={VID_A}&t=42s", VID_A),
        (f"https://music.youtube.com/watch?v={VID_A}", VID_A),
        (VID_A, VID_A),
        ("https://example.com/watch?v=" + VID_A, None),
        ("https://www.youtube.com/watch?v=too-short", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_video_id(url, expected):
    assert youtube.parse_video_id(url) == expected


# ---------------------------------------------------------------------------
# Caption parsing / cleaning
# ---------------------------------------------------------------------------


def test_parse_json3_segments():
    payload = {
        "events": [
            {
                "tStartMs": 0,
                "dDurationMs": 1500,
                "segs": [{"utf8": "Hello"}, {"utf8": " world"}],
            },
            {"tStartMs": 1500, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "Next line."}]},
        ]
    }
    segments = youtube.parse_json3(json.dumps(payload))
    assert [segment["text"] for segment in segments] == ["Hello world", "Next line."]
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 1.5
    assert segments[1]["start"] == 2.0


def test_parse_vtt_and_clean_segments():
    payload = "\n".join(
        [
            "WEBVTT",
            "Kind: captions",
            "Language: en",
            "",
            "00:00:00.000 --> 00:00:02.000",
            "Hello <c>world</c>",
            "",
            "00:00:02.000 --> 00:00:04.000",
            "Hello world",
            "",
            "00:00:04.000 --> 00:00:06.000",
            "[Music]",
            "",
            "00:00:06.000 --> 00:00:08.000",
            "Second sentence.",
        ]
    )
    segments = youtube.clean_segments(youtube.parse_vtt(payload))
    assert [segment["text"] for segment in segments] == ["Hello world", "Second sentence."]
    assert segments[0]["end"] == 4.0
    assert segments[1]["start"] == 6.0


def test_clean_segments_drops_empty():
    assert youtube.clean_segments([{"start": 0, "end": 1, "text": "   "}]) == []


# ---------------------------------------------------------------------------
# Windowing / formatting helpers
# ---------------------------------------------------------------------------


def test_format_timestamp():
    assert writer.format_timestamp(0) == "00:00"
    assert writer.format_timestamp(75) == "01:15"
    assert writer.format_timestamp(3675) == "1:01:15"


def test_slugify():
    assert writer.slugify("Hello, World! -- Test") == "hello-world-test"
    assert writer.slugify("  ") == "untitled"
    long_slug = writer.slugify("a" * 100)
    assert len(long_slug) <= 60
    assert writer.slugify("Trading: Risk & Psychology") == "trading-risk-psychology"


# ---------------------------------------------------------------------------
# Frame hashing / dedupe (pure Pillow, no ffmpeg/network)
# ---------------------------------------------------------------------------


def test_average_hash_and_distance():
    first = _image(1)
    same = _image(1)
    different = _image(9)

    assert frames.average_hash(first) == frames.average_hash(same)
    assert frames.hash_distance(frames.average_hash(first), frames.average_hash(same)) == 0

    altered = first.copy()
    altered.putpixel((3, 3), (255, 0, 0))
    assert (
        frames.hash_distance(frames.average_hash(first), frames.average_hash(altered))
        <= frames.HASH_DUPLICATE_DISTANCE
    )
    assert (
        frames.hash_distance(frames.average_hash(first), frames.average_hash(different))
        > frames.HASH_DUPLICATE_DISTANCE
    )


def test_near_blank_detection():
    assert frames.is_near_blank(Image.new("RGB", (64, 64), (255, 255, 255))) is True
    assert frames.is_near_blank(Image.new("RGB", (64, 64), (5, 5, 5))) is True
    assert frames.is_near_blank(_image(2)) is False


def test_select_unique_frames_skips_blank_and_duplicates(tmp_path: Path):
    saved = {}
    for name, image in [
        ("a.jpg", _image(1)),
        ("a-dup.jpg", _image(1)),
        ("blank.jpg", Image.new("RGB", (64, 64), (30, 30, 30))),
        ("b.jpg", _image(7)),
    ]:
        path = tmp_path / name
        image.save(path, "JPEG", quality=95)
        saved[name] = path

    candidates = [
        {"time": 0.0, "path": saved["a.jpg"]},
        {"time": 5.0, "path": saved["a-dup.jpg"]},
        {"time": 8.0, "path": saved["blank.jpg"]},
        {"time": 30.0, "path": saved["b.jpg"]},
    ]
    selected = frames.select_unique_frames(candidates, max_frames=12)
    assert [round(item["time"], 1) for item in selected] == [0.0, 30.0]

    capped = frames.select_unique_frames(candidates, max_frames=1)
    assert len(capped) == 1


def test_parse_showinfo_times():
    stderr = "\n".join(
        [
            "[Parsed_showinfo_1 @ 0x7f] n:   0 pts: 15360 pts_time:1.5 pos: 123",
            "[Parsed_showinfo_1 @ 0x7f] n:   1 pts: 33280 pts_time:3.25 pos: 456",
        ]
    )
    assert frames.parse_showinfo_times(stderr) == [1.5, 3.25]
    assert frames.parse_showinfo_times("") == []


def test_write_page_structure(wiki, monkeypatch):
    video = _video()
    segments = _segments()
    frames_list = [_frame_markdown_for(30.0), _frame_markdown_for(125.0)]

    rel_path, meta = asyncio.run(
        writer.write_page(video, segments, frames_list, "captions")
    )
    assert rel_path.endswith(".md")
    assert "/" not in rel_path
    assert meta["words"] > 50 and meta["reading_minutes"] >= 1

    page_path = wiki / rel_path
    assert page_path.exists()
    text = page_path.read_text(encoding="utf-8")

    frontmatter = _parse_frontmatter(text)
    assert _unquote(frontmatter["title"]) == "Test Video Title"
    assert frontmatter["source_type"] == "youtube"
    assert frontmatter["source_url"] == video["webpage_url"]
    assert frontmatter["video_id"] == "vid12345678"
    assert _unquote(frontmatter["channel"]) == "Test Channel"
    assert frontmatter["published"] == "2024-01-02"
    assert frontmatter["transcript_source"] == "captions"
    assert frontmatter["frames"] == "2"

    assert "# Test Video Title" in text
    assert "> **Source:** [watch](" in text
    assert "## TL;DR" in text
    assert "## Overview" in text
    assert "## In this document" in text
    assert "## Key takeaways" in text
    assert "## Transcript" in text
    assert frontmatter["document"] == "extractive"
    assert int(frontmatter["words"]) > 50
    assert "<details><summary>Full transcript</summary>" in text
    assert "![Frame at 00:30](assets/vid12345678/000030.jpg)" in text
    assert "*Auto-captured frame · 00:30*" in text
    assert "![Frame at 02:05](assets/vid12345678/000205.jpg)" in text
    assert re.search(r"^## \d{2}:\d{2} — .+", text, re.MULTILINE), text[:2000]
    assert "[00:00]" in text
    assert text.rstrip().endswith("</details>")

    tldr = text.split("## TL;DR", 1)[1].split("## Key takeaways", 1)[0]
    assert any(line.startswith("- ") for line in tldr.splitlines())
    takeaways = text.split("## Key takeaways", 1)[1].split("## Transcript", 1)[0]
    assert sum(1 for line in takeaways.splitlines() if line.startswith("- ")) >= 3


def test_write_page_without_transcript(wiki):
    description = (
        "This video explains how to build a personal knowledge wiki from YouTube videos. "
        "It covers metadata extraction, caption parsing, frame capture and markdown writing. "
        "The workflow runs fully offline and degrades gracefully without an LLM key."
    )
    video = _video(description=description)
    rel_path, _meta = asyncio.run(
        writer.write_page(video, [], [_frame_markdown_for(10.0)], "none")
    )
    text = (wiki / rel_path).read_text(encoding="utf-8")
    assert "transcript_source: none" in text
    assert "No captions were available" in text
    assert "## Overview" in text
    assert "written edition" in text
    assert "No transcript was available" in text
    tldr = text.split("## TL;DR", 1)[1].split("## Key takeaways", 1)[0]
    assert any(line.startswith("- ") for line in tldr.splitlines())


def test_write_page_updates_same_video_in_place(wiki):
    video = _video()
    first, _ = asyncio.run(writer.write_page(video, _segments(6), [], "captions"))
    second, _ = asyncio.run(writer.write_page(video, _segments(6), [], "captions"))
    assert first == second
    assert (wiki / first).exists()
    assert not (wiki / first.replace(".md", "-2.md")).exists()

    # A different video with the same title still gets its own page.
    other = _video(video_id="vidOther123")
    third, _ = asyncio.run(writer.write_page(other, _segments(6), [], "captions"))
    assert third != first
    assert third.endswith("-2.md")
    assert (wiki / third).exists()


# ---------------------------------------------------------------------------
# Pipeline integration (offline: yt-dlp/ffmpeg/LLM monkeypatched out)
# ---------------------------------------------------------------------------


async def _fake_fetch_video(url: str) -> dict:
    video_id = youtube.parse_video_id(url)
    return _video(
        video_id=video_id,
        title="Fake Video About Trading",
        url=f"https://www.youtube.com/watch?v={video_id}",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
    )


async def _fake_fetch_transcript(video: dict):
    return (
        [
            {
                "start": 0.0,
                "end": 8.0,
                "text": "Position sizing is the most important risk control for a trader.",
            },
            {
                "start": 95.0,
                "end": 103.0,
                "text": "Never risk more than one percent of your account on a single trade.",
            },
        ],
        "captions",
    )


def _fake_extract_frames(source: str, video_id: str, max_frames: int = 12):
    return [
        {
            "time": 1.0,
            "path": f"/tmp/{video_id}/000001.jpg",
            "url": f"assets/{video_id}/000001.jpg",
            "caption": None,
        }
    ]


def test_pipeline_runs_offline(wiki, monkeypatch):
    db.create_job("job-1", "ingest", 2)
    monkeypatch.setattr(pipeline.youtube, "fetch_video", _fake_fetch_video)
    monkeypatch.setattr(pipeline.youtube, "fetch_transcript", _fake_fetch_transcript)
    monkeypatch.setattr(pipeline.frames, "extract_frames", _fake_extract_frames)

    urls = [
        f"https://www.youtube.com/watch?v={VID_A}",
        f"https://youtu.be/{VID_B}",
    ]
    asyncio.run(pipeline.run_ingest_job("job-1", urls))

    job = db.get_job("job-1")
    assert job["status"] == "done"
    assert job["done"] == 2
    assert job["total"] == 2
    assert job["current"] is None
    assert not job["error"]

    row = db.video_row(VID_A)
    assert row["status"] == "done"
    assert row["wiki_path"].endswith(".md") and "/" not in row["wiki_path"]
    assert row["transcript_source"] == "captions"
    assert row["frame_count"] == 1
    assert row["extracted"]

    page_path = wiki / row["wiki_path"]
    assert page_path.exists()
    content = page_path.read_text(encoding="utf-8")
    assert "Fake Video About Trading" in content
    assert "Position sizing is the most important risk control" in content
    assert f"assets/{VID_A}/000001.jpg" in content
    assert db.video_row(VID_B)["status"] == "done"


def test_pipeline_continues_after_video_failure(wiki, monkeypatch):
    db.create_job("job-2", "ingest", 2)

    async def flaky_fetch_video(url: str) -> dict:
        if VID_A in url:
            raise youtube.YouTubeError("simulated metadata failure")
        return await _fake_fetch_video(url)

    monkeypatch.setattr(pipeline.youtube, "fetch_video", flaky_fetch_video)
    monkeypatch.setattr(pipeline.youtube, "fetch_transcript", _fake_fetch_transcript)
    monkeypatch.setattr(pipeline.frames, "extract_frames", _fake_extract_frames)

    asyncio.run(
        pipeline.run_ingest_job(
            "job-2",
            [f"https://www.youtube.com/watch?v={VID_A}", f"https://youtu.be/{VID_B}"],
        )
    )

    job = db.get_job("job-2")
    assert job["status"] == "done"
    assert job["done"] == 2
    assert "simulated metadata failure" in (job["error"] or "")

    failed = db.video_row(VID_A)
    assert failed["status"] == "error"
    assert "simulated metadata failure" in failed["error"]
    assert db.video_row(VID_B)["status"] == "done"


def test_pipeline_rejects_non_youtube_url(wiki, monkeypatch):
    db.create_job("job-3", "ingest", 1)
    monkeypatch.setattr(
        pipeline.youtube,
        "fetch_video",
        lambda url: pytest.fail("fetch_video must not be called for invalid URLs"),
    )
    asyncio.run(pipeline.run_ingest_job("job-3", ["https://example.com/video"]))
    job = db.get_job("job-3")
    assert job["status"] == "done"
    assert job["done"] == 1
    assert "not a YouTube video URL" in (job["error"] or "")


def test_pipeline_processes_urls_accepted_by_router(wiki, monkeypatch):
    """The router pre-filters duplicates; accepted URLs must always be processed.

    This also covers force re-ingest: the router passes the URL again and the
    pipeline reprocesses it instead of skipping the already-done row.
    """
    db.create_job("job-4", "ingest", 1)
    db.upsert_video(
        video_id=VID_A,
        url=f"https://www.youtube.com/watch?v={VID_A}",
        title="Old title",
        status="done",
    )
    monkeypatch.setattr(pipeline.youtube, "fetch_video", _fake_fetch_video)
    monkeypatch.setattr(pipeline.youtube, "fetch_transcript", _fake_fetch_transcript)
    monkeypatch.setattr(pipeline.frames, "extract_frames", _fake_extract_frames)

    asyncio.run(
        pipeline.run_ingest_job("job-4", [f"https://www.youtube.com/watch?v={VID_A}"])
    )
    job = db.get_job("job-4")
    assert job["status"] == "done"
    assert job["done"] == 1
    row = db.video_row(VID_A)
    assert row["title"] == "Fake Video About Trading"
    assert row["status"] == "done"
    assert row["wiki_path"]


def test_select_unique_frames_spreads_across_timeline(tmp_path: Path):
    candidates = []
    for index in range(20):
        path = tmp_path / f"f{index:02d}.jpg"
        _image(index).save(path, "JPEG", quality=95)
        candidates.append({"time": float(index * 5), "path": path})
    selected = frames.select_unique_frames(candidates, max_frames=4)
    assert len(selected) == 4
    times = [item["time"] for item in selected]
    assert times == sorted(times)
    assert times[0] == 0.0
    assert times[-1] == 95.0
