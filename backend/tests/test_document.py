"""Offline tests for the detailed document builder (extractive + LLM paths)."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from backend.app import config, db, llm
from backend.app.ingest import document, writer


@pytest.fixture()
def wiki(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    monkeypatch.setattr(config, "WIKI_DIR", root)
    monkeypatch.setattr(config, "ASSETS_DIR", root / "assets")
    monkeypatch.setattr(config, "DATA_DIR", root / "data")
    monkeypatch.setattr(config, "DB_PATH", root / "data" / "tubewiki.db")
    monkeypatch.setattr(config, "TMP_DIR", root / "data" / "tmp")
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(document, "doc_mode", lambda: "extractive")
    db.init_db()
    return root


def _video(**overrides) -> dict:
    base = {
        "video_id": "vidDoc12345",
        "title": "The Complete Risk Management Playbook",
        "channel": "Test Channel",
        "duration": 720,
        "published": "2024-01-02",
        "url": "https://www.youtube.com/watch?v=vidDoc12345",
        "webpage_url": "https://www.youtube.com/watch?v=vidDoc12345",
        "description": "A deep dive into position sizing, expectancy and trading psychology.",
    }
    base.update(overrides)
    return base


TOPICS = [
    [
        "Position sizing is the single most important risk control in trading.",
        "Never risk more than one percent of your account on any single trade.",
        "A fixed fractional model keeps drawdowns survivable across bad streaks.",
        "Volatility adjusted sizing shrinks exposure when the market gets wild.",
        "The stop loss must be defined before the entry, never after it.",
        "Risk per trade multiplied by losing streak length gives your drawdown.",
        "Small consistent position sizes let you compound without blowing up.",
        "A trader who survives the streak eventually meets the opportunity.",
        "Sizing discipline is boring, and boring is exactly the point.",
        "Every blown account traces back to a position size that was too large.",
        "You can be right about direction and still lose by sizing wrong.",
        "Capital preservation comes before any profit target.",
    ],
    [
        "Trading psychology is mostly about managing the stories you tell yourself.",
        "Revenge trading turns one loss into a losing streak within minutes.",
        "A journal makes your emotional mistakes visible and therefore fixable.",
        "Boredom is a bigger enemy than fear for most intraday traders.",
        "You need a written rule for what happens after two consecutive losses.",
        "FOMO entries have the worst expectancy of any trade you can take.",
        "Detaching from any single outcome is the core psychological skill.",
        "Process consistency beats a brilliant one-off trade every time.",
        "Sitting flat is a valid position and often the most profitable one.",
        "Your identity should not be tied to whether today was green.",
        "The best traders review losses with curiosity instead of shame.",
        "Sleep and stress management are part of the trading system.",
    ],
    [
        "A strategy is only real once you can state its entry, exit and invalidation.",
        "Backtesting must include out of sample data to avoid curve fitting.",
        "Expectancy combines win rate and payoff ratio into a single number.",
        "Walk forward testing is the closest thing to honest validation.",
        "Every strategy has regimes where it stops working.",
        "A trading journal should record the setup name and the reason for entry.",
        "Sample size matters: twenty trades cannot validate a strategy.",
        "Transaction costs and slippage quietly destroy fragile edges.",
        "You need a process for shelving a strategy that has stopped working.",
        "Rules reduce decision fatigue and make results measurable.",
        "A strategy you cannot explain to someone else is not a strategy.",
        "Position sizing, psychology and strategy are one system, not three.",
    ],
]


def _segments() -> list[dict]:
    segments = []
    step = 15.0
    index = 0
    for topic in TOPICS:
        for line in topic:
            start = index * step
            segments.append({"start": start, "end": start + step - 0.5, "text": line})
            index += 1
    return segments


def test_build_sentences_merges_fragments_and_strips_filler():
    segments = [
        {"start": 0.0, "end": 2.0, "text": "um, the key is"},
        {"start": 2.0, "end": 4.0, "text": "risk management."},
        {"start": 4.0, "end": 6.0, "text": "you know, never size too big."},
    ]
    sentences = document.build_sentences(segments)
    text = " ".join(sentence["text"] for sentence in sentences)
    assert "um," not in text.lower()
    assert "you know," not in text.lower()
    assert "risk management." in text
    assert sentences[0]["text"].startswith("The key is")
    assert all(sentence["start"] >= 0 for sentence in sentences)


def test_chapterize_finds_topic_boundaries():
    sentences = document.build_sentences(_segments())
    chapters = document.chapterize(sentences, 720)
    assert 2 <= len(chapters) <= 4
    assert sum(len(chapter) for chapter in chapters) == len(sentences)


def test_extractive_document_is_prose_not_bullets():
    video = _video()
    sentences = document.build_sentences(_segments())
    doc = document.build_extractive_document(video, sentences, [])
    assert doc["mode"] == "extractive"
    assert len(doc["overview"]) >= 2
    assert len(doc["chapters"]) >= 2
    for chapter in doc["chapters"]:
        assert chapter["heading"]
        prose = " ".join(paragraph["text"] for paragraph in chapter["paragraphs"])
        assert len(prose.split()) >= 40, prose
        assert chapter["paragraphs"][0]["text"][0].isupper()
    assert len(doc["takeaways"]) >= 3
    assert doc["words"] > 300


def test_write_page_contains_detailed_document(wiki):
    video = _video()
    rel_path, meta = asyncio.run(writer.write_page(video, _segments(), [], "captions"))
    text = (wiki / rel_path).read_text(encoding="utf-8")

    assert "document: extractive" in text
    assert "## Overview" in text
    assert "## In this document" in text
    assert "## TL;DR" in text
    assert "## Key takeaways" in text
    assert re.search(r"^## \d{2}:\d{2} — .+", text, re.MULTILINE)

    # The body should be substantial prose, not a handful of bullets.
    body = text.split("## Overview", 1)[1].split("## Transcript", 1)[0]
    paragraphs = [p for p in body.split("\n\n") if len(p.split()) > 25]
    assert len(paragraphs) >= 4
    assert "**Highlights**" in body


async def _fake_section_chat(messages, **kwargs):
    system = messages[0]["content"]
    if "executive overview" in system:
        return "The material explains risk control end to end. It treats sizing as the core edge.\n\nAfter reading you can size trades, journal mistakes and validate a strategy."
    if "most useful takeaways" in system:
        return "Risk one percent per trade.\nJournal every loss.\nValidate with out of sample data."
    return "## Sizing And Survival\n\nRisk is the first problem to solve. " + ("Position size decides whether a streak kills you. " * 12)


def test_llm_mode_generates_sections_and_overview(wiki, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", _fake_section_chat)
    monkeypatch.setattr(document, "doc_mode", lambda: "auto")

    doc = asyncio.run(document.build_document(_video(), _segments(), []))
    assert doc["mode"] == "llm"
    assert doc["chapters"]
    assert all(chapter["paragraphs"] for chapter in doc["chapters"])
    assert "risk control" in " ".join(doc["overview"]).lower()
    assert len(doc["takeaways"]) >= 3


def test_llm_failure_falls_back_to_extractive(wiki, monkeypatch):
    async def broken_chat(messages, **kwargs):
        return None

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "chat", broken_chat)
    monkeypatch.setattr(document, "doc_mode", lambda: "auto")

    doc = asyncio.run(document.build_document(_video(), _segments(), []))
    assert doc["mode"] == "extractive"
    assert doc["chapters"]


def test_no_transcript_uses_description():
    doc = asyncio.run(document.build_document(_video(), [], []))
    assert doc["overview"]
    assert "written edition" in doc["overview"][0]
    assert doc["chapters"] == []
    assert doc["tldr"]
