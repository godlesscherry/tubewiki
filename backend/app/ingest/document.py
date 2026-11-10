"""Detailed document builder: turns a transcript into a real long-form document.

The goal is a *reading edition* of a video, not a bullet list:

    Overview (prose)  →  Chapters (headings, paragraphs, quotes, highlights,
    captured slide frames)  →  Key takeaways  →  full transcript appendix

Two modes:

* ``extractive`` (default offline) — topic-shift chapterisation, filler-cleaned
  sentence reconstruction, per-chapter prose paragraphs, pull quotes and
  highlights. Works with no API key and no network.
* ``llm`` (when an OpenAI-compatible endpoint is configured) — the transcript is
  rewritten in chunks into polished article sections, then an executive overview
  and takeaways are generated. Falls back to the extractive path on any failure.

Selection: ``TUBEWIKI_DOC_MODE=auto|extractive|llm`` (default ``auto``).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from typing import Any

from .. import config, llm

log = logging.getLogger(__name__)

MAX_CHAPTERS = 9
MIN_CHAPTER_SENTENCES = 5
PARAGRAPH_TARGET_WORDS = 110
PARAGRAPH_MAX_SENTENCES = 5
LLM_CHUNK_CHARS = 7000
LLM_MAX_CHUNKS = 12
LLM_TIMEOUT = 240.0
LLM_DOC_MAX_TOKENS = 2200
MAX_SUMMARY_CHARS = 60_000

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are aren't as at be
    because been before being below between both but by can can't cannot could
    couldn't did didn't do does doesn't doing don't down during each few for from
    further get got had hadn't has hasn't have haven't having he he'd he'll he's
    her here here's hers herself him himself his how how's i i'd i'll i'm i've if
    in into is isn't it it's its itself just know like let's me more most much
    mustn't my myself no nor not now of off on once only or other ought our ours
    ourselves out over own really right same say says shall shan't she she'd
    she'll she's should shouldn't so some something still such than that that's
    the their theirs them themselves then there there's these they they'd they'll
    they're they've thing things think this those through to too under until up
    use used using very want was wasn't way we we'd we'll we're we've well were
    weren't what what's when when's where where's which while who who's whom why
    why's will with won't would wouldn't yeah yes you you'd you'll you're you've
    your yours yourself yourselves actually basically going gonna kinda lot lots
    okay ok hey guys welcome back today video channel subscribe
    """.split()
)

_FILLER_RES = (
    re.compile(r"\b(?:um|uh|erm|hmm|ah)\b[,.]?\s*", re.I),
    re.compile(r"\byou know\b[,.]?\s*", re.I),
    re.compile(r"\bi mean\b[,.]?\s*", re.I),
    re.compile(r"\bkind of\b", re.I),
    re.compile(r"\bsort of\b", re.I),
    re.compile(r"\bokay so\b[,.]?\s*", re.I),
    re.compile(r"\bso,?\s+(?=[A-Z])", re.I),
    re.compile(r"\band so\b", re.I),
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")
_SOFT_BREAK_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9'-]{2,}")
_WS_RE = re.compile(r"\s+")
_QUOTE_CUES = re.compile(
    r"\b(the key is|the important thing|most important|remember|the rule|"
    r"never|always|the mistake|the secret|what matters|the point is|"
    r"the trick|you have to|the real)\b",
    re.I,
)

_CHAPTER_HEADING_STOPWORDS = STOPWORDS | {"part", "section", "video", "actually"}


def doc_mode() -> str:
    return config.DOC_MODE


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def clean_space(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def strip_filler(text: str) -> str:
    cleaned = clean_space(text)
    for pattern in _FILLER_RES:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = clean_space(cleaned)
    cleaned = cleaned.replace(" ,", ",").replace(" .", ".")
    return cleaned


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


def content_tokens(text: str) -> list[str]:
    return [token for token in _tokens(text) if token not in STOPWORDS and len(token) > 2]


def title_case(text: str) -> str:
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
    words = text.split()
    out = []
    for index, word in enumerate(words):
        if index and word.lower() in small:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def _finish_sentence(text: str) -> str:
    cleaned = strip_filler(text)
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    if cleaned[-1] not in ".!?…\"')":
        cleaned = cleaned.rstrip(",;:") + "."
    return cleaned


def _truncate(text: str, limit: int) -> str:
    text = clean_space(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " …"


# ---------------------------------------------------------------------------
# Sentence reconstruction
# ---------------------------------------------------------------------------


def build_sentences(segments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Reconstruct readable sentences from caption segments.

    Captions arrive as short rolling fragments. This joins them, splits at
    sentence punctuation (or at a soft cap for unpunctuated streams) and keeps
    the timestamp of the first segment of every sentence.
    """
    ordered: list[dict[str, Any]] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = strip_filler(segment.get("text"))
        if not text:
            continue
        try:
            start = float(segment.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        ordered.append({"text": text, "start": start})
    ordered.sort(key=lambda seg: seg["start"])
    if not ordered:
        return []

    # Character offset -> segment start time, so a sentence can inherit a time.
    joined_parts: list[str] = []
    spans: list[tuple[int, int, float]] = []
    cursor = 0
    for seg in ordered:
        chunk = seg["text"]
        if joined_parts:
            joined_parts.append(" ")
            cursor += 1
        start_char = cursor
        joined_parts.append(chunk)
        cursor += len(chunk)
        spans.append((start_char, cursor, seg["start"]))
    joined = "".join(joined_parts)

    starts = [span[0] for span in spans]

    def time_at(char_offset: int) -> float:
        index = max(0, min(len(spans) - 1, bisect_right(starts, char_offset) - 1))
        return spans[index][2]

    boundaries = [0]
    for match in _SENTENCE_END_RE.finditer(joined):
        boundaries.append(match.end())
    boundaries.append(len(joined))

    sentences: list[dict[str, Any]] = []
    for index in range(len(boundaries) - 1):
        raw = joined[boundaries[index]:boundaries[index + 1]].strip()
        if not raw:
            continue
        # Split unpunctuated run-ons into ~200 char readable chunks.
        pieces = _soft_split(raw)
        for piece in pieces:
            text = _finish_sentence(piece)
            if len(text) < 12:
                continue
            sentences.append({"text": text, "start": time_at(boundaries[index])})
    return sentences


def _soft_split(text: str, limit: int = 220) -> list[str]:
    if len(text) <= limit:
        return [text]
    words = text.split()
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        if len(" ".join(current)) >= limit and word[-1:] in ".!?…":
            pieces.append(" ".join(current))
            current = []
        elif len(" ".join(current)) >= limit * 1.4:
            pieces.append(" ".join(current))
            current = []
    if current:
        pieces.append(" ".join(current))
    return pieces


def _bisect_right(values: list[int], value: int) -> int:
    # tiny binary search (avoids importing bisect for one call)
    low, high = 0, len(values)
    while low < high:
        mid = (low + high) // 2
        if values[mid] <= value:
            low = mid + 1
        else:
            high = mid
    return low


bisect_right = _bisect_right


# ---------------------------------------------------------------------------
# Chapterisation
# ---------------------------------------------------------------------------


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def chapterize(sentences: list[dict[str, Any]], duration: float) -> list[list[dict[str, Any]]]:
    """Split sentences into chapters at topic shifts, respecting time spacing."""
    total = len(sentences)
    if total < 8:
        return [sentences] if sentences else []

    desired = int(round(duration / 240.0)) if duration else total // 12
    desired = max(2, min(MAX_CHAPTERS, desired or 2))
    desired = min(desired, max(1, total // MIN_CHAPTER_SENTENCES))
    if desired <= 1:
        return [sentences]

    token_sets = [set(content_tokens(sentence["text"])) for sentence in sentences]
    window = 7
    candidates: list[tuple[float, int]] = []
    for index in range(MIN_CHAPTER_SENTENCES, total - MIN_CHAPTER_SENTENCES + 1):
        before: set[str] = set()
        for item in token_sets[max(0, index - window):index]:
            before |= item
        after: set[str] = set()
        for item in token_sets[index:index + window]:
            after |= item
        candidates.append((_similarity(before, after), index))
    candidates.sort()

    min_gap = max(MIN_CHAPTER_SENTENCES, total // (desired * 3))
    min_time_gap = 75.0
    chosen: list[int] = []

    def far_enough(index: int) -> bool:
        for existing in chosen:
            if abs(existing - index) < min_gap:
                return False
            if abs(sentences[existing]["start"] - sentences[index]["start"]) < min_time_gap:
                return False
        return True

    for _score, index in candidates:
        if len(chosen) >= desired - 1:
            break
        if far_enough(index):
            chosen.append(index)

    if not chosen:
        # Even time split fallback.
        step = total / desired
        chosen = [int(round(step * i)) for i in range(1, desired)]
    chosen = sorted(set(index for index in chosen if 0 < index < total))

    chapters: list[list[dict[str, Any]]] = []
    start = 0
    for boundary in chosen:
        if boundary > start:
            chapters.append(sentences[start:boundary])
        start = boundary
    if start < total:
        chapters.append(sentences[start:])
    return [chapter for chapter in chapters if chapter] or [sentences]


# ---------------------------------------------------------------------------
# Chapter pieces
# ---------------------------------------------------------------------------


def _chapter_heading(chapter: list[dict[str, Any]], index: int, used: set[str]) -> str:
    bigrams: Counter[tuple[str, str]] = Counter()
    unigrams: Counter[str] = Counter()
    for sentence in chapter:
        words = content_tokens(sentence["text"])
        unigrams.update(words)
        for left, right in zip(words, words[1:]):
            if len(left) > 2 and len(right) > 2:
                bigrams[(left, right)] += 1

    # Rank candidates: repeated bigrams first, then any bigram, then repeated
    # single words. A one-off word ("Miss", "Okay") makes a poor section title.
    candidates: list[str] = []
    for (left, right), count in bigrams.most_common(24):
        if count >= 2:
            candidates.append(f"{left} {right}")
    for (left, right), count in bigrams.most_common(24):
        if count < 2:
            candidates.append(f"{left} {right}")
    for word, count in unigrams.most_common(24):
        if count >= 2 and word not in _CHAPTER_HEADING_STOPWORDS:
            candidates.append(word)

    for candidate in candidates:
        heading = _truncate(title_case(candidate).title(), 64)
        if heading.lower() not in used:
            used.add(heading.lower())
            return heading

    if index > 0 and len(chapter) <= 4:
        fallback = "Closing Thoughts"
    else:
        fallback = f"Part {index + 1}"
    candidate_fallback = fallback
    counter = 2
    while candidate_fallback.lower() in used:
        candidate_fallback = f"{fallback} {counter}"
        counter += 1
    used.add(candidate_fallback.lower())
    return candidate_fallback


def _paragraphs(chapter: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []
    words = 0
    for sentence in chapter:
        buffer.append(sentence)
        words += len(sentence["text"].split())
        done = words >= PARAGRAPH_TARGET_WORDS or len(buffer) >= PARAGRAPH_MAX_SENTENCES
        if done:
            paragraphs.append(
                {
                    "text": " ".join(item["text"] for item in buffer),
                    "start": buffer[0]["start"],
                    "end": buffer[-1]["start"],
                }
            )
            buffer = []
            words = 0
    if buffer:
        if paragraphs and len(buffer) <= 1:
            previous = paragraphs[-1]
            previous["text"] = previous["text"] + " " + buffer[0]["text"]
            previous["end"] = buffer[0]["start"]
        else:
            paragraphs.append(
                {
                    "text": " ".join(item["text"] for item in buffer),
                    "start": buffer[0]["start"],
                    "end": buffer[-1]["start"],
                }
            )
    return paragraphs


def _scored(pool: list[dict[str, Any]], counts: Counter[str]) -> list[tuple[float, dict[str, Any]]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for sentence in pool:
        tokens = [token for token in _tokens(sentence["text"]) if token in counts]
        if len(tokens) < 3:
            continue
        score = 0.0
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            score += math.log1p(counts[token])
        scored.append((score / math.sqrt(len(sentence["text"].split()) or 1), sentence))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _pick(
    scored: list[tuple[float, dict[str, Any]]],
    limit: int,
    used: list[set[str]],
    max_chars: int = 300,
    min_chars: int = 0,
) -> list[dict[str, Any]]:
    """Pick distinct, high-signal sentences.

    ``min_chars`` is a soft preference (complete sentences read better than
    fragments); if there are not enough long candidates we fill from shorter ones.
    """
    picked: list[dict[str, Any]] = []

    def absorb(enforce_min: bool) -> None:
        for _score, sentence in scored:
            if len(picked) >= limit:
                return
            if enforce_min and min_chars and len(sentence["text"]) < min_chars:
                continue
            tokens = set(_tokens(sentence["text"]))
            if len(tokens) < 4:
                continue
            if any(_similarity(tokens, other) > 0.6 for other in used):
                continue
            used.append(tokens)
            picked.append(
                {"text": _truncate(sentence["text"], max_chars), "start": sentence["start"]}
            )

    absorb(True)
    absorb(False)
    picked.sort(key=lambda item: item["start"])
    return picked


def _highlights(chapter: list[dict[str, Any]], counts: Counter[str], limit: int = 3) -> list[str]:
    picked = _pick(_scored(chapter, counts), limit, [])
    return [item["text"] for item in picked]


def _quote(chapter: list[dict[str, Any]]) -> dict[str, Any] | None:
    for sentence in chapter:
        text = sentence["text"]
        if 80 <= len(text) <= 260 and _QUOTE_CUES.search(text):
            return {"text": text, "start": sentence["start"]}
    for sentence in chapter:
        if 90 <= len(sentence["text"]) <= 260:
            return {"text": sentence["text"], "start": sentence["start"]}
    return None


def _assign_frames(
    chapters: list[list[dict[str, Any]]], frames: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    assigned: list[list[dict[str, Any]]] = [[] for _ in chapters]
    if not chapters:
        return assigned
    starts = [chapter[0]["start"] for chapter in chapters]
    for frame in frames:
        try:
            time = float(frame.get("time") or 0.0)
        except (TypeError, ValueError):
            time = 0.0
        index = max(0, bisect_right(starts, time) - 1)
        assigned[min(index, len(chapters) - 1)].append(frame)
    return assigned


def _overview(
    title: str, channel: str, duration: float, chapters_md: list[dict[str, Any]], pool: list[dict[str, Any]], counts: Counter[str]
) -> list[str]:
    scored = _scored(pool, counts)
    # Prefer complete, readable sentences over truncated fragments.
    readable = [
        item for item in scored if 110 <= len(item[1]["text"]) <= 340
    ] or scored
    picked = _pick(readable, 3, [], max_chars=340)
    intro_sentences = [item["text"] for item in picked[:3]]
    minutes = max(1, int(round((duration or 0) / 60.0)))
    lead_bits = []
    if channel:
        lead_bits.append(f"by {channel}")
    if minutes:
        article = "an" if str(minutes).startswith(("8", "11", "18")) else "a"
        lead_bits.append(f"{article} {minutes}-minute video")
    lead = "This document is a written edition of “" + title + "”"
    if lead_bits:
        lead += " — " + ", ".join(lead_bits)
    lead += "."
    paragraph_one = lead
    if intro_sentences:
        paragraph_one += " " + " ".join(intro_sentences)
    if chapters_md:
        walk = "; ".join(
            f"{chapter['timestamp']} {chapter['heading']}" for chapter in chapters_md[:10]
        )
        paragraph_two = (
            "The document walks through the material in the same order it appears in the video: "
            + walk
            + ". Captured slide and diagram frames are placed next to the passages they illustrate, "
            "and the full transcript is kept in the appendix for reference."
        )
    else:
        paragraph_two = (
            "No spoken transcript was available, so this page is built from the video's description "
            "and metadata."
        )
    return [clean_space(paragraph_one), clean_space(paragraph_two)]


def _duration(segments: list[dict[str, Any]], video: dict[str, Any]) -> float:
    try:
        from_video = float(video.get("duration") or 0.0)
    except (TypeError, ValueError):
        from_video = 0.0
    if from_video > 0:
        return from_video
    for segment in reversed(segments or []):
        try:
            return float(segment.get("end") or segment.get("start") or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def build_extractive_document(
    video: dict[str, Any],
    sentences: list[dict[str, Any]],
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the offline document: overview, chapters (prose), takeaways."""
    title = clean_space(video.get("title")) or "Untitled video"
    channel = clean_space(video.get("channel"))
    description = clean_space(video.get("description"))
    duration = _duration(video.get("_segments") or [], video)

    if not sentences and description:
        description_sentences = [
            {"text": _finish_sentence(piece), "start": 0.0}
            for piece in re.split(r"(?<=[.!?])\s+", description)
            if len(piece.strip()) > 24
        ]
        counts = Counter(
            token for sentence in description_sentences for token in content_tokens(sentence["text"])
        )
        pool = description_sentences
    else:
        counts = Counter(
            token for sentence in sentences for token in content_tokens(sentence["text"])
        )
        pool = sentences

    chapters_raw = chapterize(sentences, duration) if sentences else []
    frame_groups = _assign_frames(chapters_raw, frames)

    used_headings: set[str] = set()
    chapters: list[dict[str, Any]] = []
    total_chapters = len(chapters_raw)
    for index, chapter in enumerate(chapters_raw):
        heading = _chapter_heading(chapter, index, used_headings)
        # A short final chapter is an outro, not a topic of its own.
        if index == total_chapters - 1 and index >= 2:
            span = _duration(video.get("_segments") or [], video) - chapter[0]["start"]
            if span < 120:
                heading = "Closing Thoughts"
        chapters.append(
            {
                "heading": heading,
                "timestamp": "",
                "start": chapter[0]["start"],
                "end": chapter[-1]["start"] if chapter else 0.0,
                "paragraphs": _paragraphs(chapter),
                "highlights": _highlights(chapter, counts),
                "quote": _quote(chapter),
                "frames": frame_groups[index] if index < len(frame_groups) else [],
            }
        )

    # Human-readable timestamps filled by the caller (needs writer's formatter);
    # expose raw starts and let the writer format.
    chapters_md = [
        {
            "heading": chapter["heading"],
            "timestamp": _format_ts(chapter["start"]),
        }
        for chapter in chapters
    ]

    takeaways = [
        item["text"]
        for item in _pick(_scored(pool, counts), 6, [], max_chars=260, min_chars=80)
    ]
    tldr = [
        item["text"]
        for item in _pick(_scored(pool, counts), 3, [], max_chars=260, min_chars=80)
    ]

    overview = _overview(title, channel, duration, chapters_md, pool, counts)
    words = _word_count(overview, chapters, takeaways)

    return {
        "mode": "extractive",
        "title": title,
        "overview": overview,
        "chapters": chapters,
        "takeaways": takeaways or ["No takeaways could be extracted automatically."],
        "tldr": tldr or ["No summary could be generated automatically."],
        "words": words,
        "reading_minutes": max(1, round(words / 220)) if words else 1,
    }


def _format_ts(seconds: float | int | None) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _word_count(
    overview: list[str], chapters: list[dict[str, Any]], takeaways: list[str]
) -> int:
    text_parts = list(overview)
    for chapter in chapters:
        text_parts.extend(paragraph["text"] for paragraph in chapter.get("paragraphs", []))
        text_parts.extend(chapter.get("highlights", []))
        quote = chapter.get("quote")
        if quote:
            text_parts.append(quote.get("text", ""))
    text_parts.extend(takeaways)
    return len(" ".join(text_parts).split())


# ---------------------------------------------------------------------------
# LLM document mode
# ---------------------------------------------------------------------------


def _chunk_sentences(sentences: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not sentences:
        return []
    total_chars = sum(len(sentence["text"]) for sentence in sentences)
    target_chunks = max(1, min(LLM_MAX_CHUNKS, math.ceil(total_chars / LLM_CHUNK_CHARS)))
    target_size = max(1, math.ceil(len(sentences) / target_chunks))
    return [sentences[i:i + target_size] for i in range(0, len(sentences), target_size)]


def _strip_fences(text: str) -> str:
    cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _split_section_markdown(raw: str, index: int) -> tuple[str, list[str]]:
    text = _strip_fences(raw)
    heading = ""
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        heading = re.sub(r"^#+\s*", "", lines[0]).strip()
        body = "\n".join(lines[1:]).strip()
    else:
        body = text
    if not heading:
        heading = f"Part {index + 1}"
    paragraphs = [
        clean_space(block)
        for block in re.split(r"\n\s*\n", body)
        if clean_space(block)
    ]
    return heading, paragraphs


async def _llm_document(
    video: dict[str, Any],
    sentences: list[dict[str, Any]],
    frames: list[dict[str, Any]],
) -> dict[str, Any] | None:
    title = clean_space(video.get("title")) or "Untitled video"
    channel = clean_space(video.get("channel"))
    profile = f"Video: {title}\nChannel: {channel or 'unknown'}\n"

    chunks = _chunk_sentences(sentences)
    if not chunks:
        return None

    system_prompt = (
        "You write long-form, detailed explanatory documents from YouTube transcripts. "
        "Your job is to turn spoken explanation into a genuine written article: coherent "
        "prose, real section headings, definitions, examples, numbers, and step-by-step "
        "explanations, exactly as a strong technical writer would. Do not invent facts or "
        "numbers that are not in the transcript. Do not reduce the material to bullet points. "
        "Write in the present tense, third person, and keep the speaker's meaning intact. "
        "Return Markdown only, starting with a single '## Section title' heading."
    )

    chapters: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        transcript = " ".join(sentence["text"] for sentence in chunk)
        if len(transcript) > 16000:
            transcript = transcript[:16000] + " …"
        user_prompt = (
            profile
            + f"This is part {index + 1} of {len(chunks)} of the transcript, starting near "
            + _format_ts(chunk[0]["start"])
            + ". Rewrite it as one section of the document: a '## ' heading plus 3-6 "
            "substantial paragraphs of flowing prose. Preserve concrete details, definitions, "
            "examples and any reasoning steps. Where useful, include short block quotes of "
            "notable lines with their [mm:ss] timestamp. Do not add a summary or bullet list "
            "at the end of the section.\n\nTRANSCRIPT:\n"
            + transcript
        )
        raw = await asyncio.wait_for(
            llm.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.35,
                max_tokens=LLM_DOC_MAX_TOKENS,
            ),
            timeout=LLM_TIMEOUT,
        )
        if not raw:
            return None
        heading, paragraphs = _split_section_markdown(raw, index)
        chapters.append(
            {
                "heading": heading,
                "start": chunk[0]["start"],
                "end": chunk[-1]["start"],
                "paragraphs": [{"text": paragraph, "start": chunk[0]["start"]} for paragraph in paragraphs],
                "highlights": [],
                "quote": None,
                "frames": [],
            }
        )

    # Attach captured frames to the nearest chapter.
    chapters_raw = [[sentence] for sentence in sentences]
    frame_groups = _assign_frames(chapters_raw, frames)  # per-sentence, then bucket
    frame_buckets: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(chapters))}
    for sentence_index, frame_list in enumerate(frame_groups):
        if not frame_list:
            continue
        time = sentences[sentence_index]["start"] if sentence_index < len(sentences) else 0.0
        target = 0
        for index, chapter in enumerate(chapters):
            if chapter["start"] <= time:
                target = index
        frame_buckets[target].extend(frame_list)
    for index, chapter in enumerate(chapters):
        chapter["frames"] = frame_buckets.get(index, [])

    # Executive overview from the generated section headings + ledes.
    headings = [chapter["heading"] for chapter in chapters]
    ledes = "\n".join(
        f"- {heading}: {clean_space(chapter['paragraphs'][0]['text'])[:400] if chapter['paragraphs'] else ''}"
        for heading, chapter in zip(headings, chapters)
    )
    overview_raw = await asyncio.wait_for(
        llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Write a tight two-paragraph executive overview for a long-form document. "
                        "Paragraph one: what the material is about and the core argument. "
                        "Paragraph two: what the reader will be able to do or understand after "
                        "reading it. Plain prose, no headings, no bullets, no invented facts."
                    ),
                },
                {"role": "user", "content": f"{profile}\nSections:\n{ledes}"},
            ],
            temperature=0.3,
            max_tokens=700,
        ),
        timeout=LLM_TIMEOUT,
    )
    if overview_raw:
        overview = [
            clean_space(block)
            for block in re.split(r"\n\s*\n", _strip_fences(overview_raw))
            if clean_space(block)
        ][:2]
    else:
        overview = [f"This document covers “{title}” in detail."]

    takeaways_raw = await asyncio.wait_for(
        llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Extract the most useful takeaways from a long-form document. Return "
                        "5-6 plain sentences, one per line, no markdown, no numbering, each "
                        "self-contained and specific (a rule, a number, a method, a warning)."
                    ),
                },
                {"role": "user", "content": f"{profile}\nSections:\n{ledes}"},
            ],
            temperature=0.25,
            max_tokens=500,
        ),
        timeout=LLM_TIMEOUT,
    )
    takeaways: list[str] = []
    if takeaways_raw:
        for line in _strip_fences(takeaways_raw).splitlines():
            cleaned = re.sub(r"^[-*•\d.\s]+", "", line).strip()
            if len(cleaned) > 12:
                takeaways.append(_truncate(_finish_sentence(cleaned), 240))
    if not takeaways:
        takeaways = ["See the document sections above for the detailed material."]

    words = _word_count(overview, chapters, takeaways)
    return {
        "mode": "llm",
        "title": title,
        "overview": overview,
        "chapters": chapters,
        "takeaways": takeaways[:6],
        "tldr": takeaways[:3],
        "words": words,
        "reading_minutes": max(1, round(words / 220)) if words else 1,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def build_document(
    video: dict[str, Any],
    segments: list[dict[str, Any]] | None,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the detailed document (LLM when configured, extractive otherwise)."""
    video = dict(video or {})
    sentences = build_sentences(segments)
    mode = doc_mode()

    if sentences and mode in {"auto", "llm"}:
        try:
            if llm.available():
                document = await _llm_document(video, sentences, frames)
                if document and document.get("chapters"):
                    log.info("document: llm mode, %d sections", len(document["chapters"]))
                    return document
        except Exception as exc:  # any provider/parse failure -> offline document
            log.warning("document: LLM mode failed, falling back to extractive: %s", exc)

    # Extractive needs duration from the video, and the raw segments for fallback.
    video["_segments"] = segments or []
    document = build_extractive_document(video, sentences, frames)
    return document


__all__ = [
    "build_document",
    "build_extractive_document",
    "build_sentences",
    "chapterize",
    "clean_space",
    "doc_mode",
    "strip_filler",
    "title_case",
]
