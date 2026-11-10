"""SQLite schema and helpers.

The database is derived metadata — the markdown documents in ``WIKI_DIR`` are the
source of truth. Only two tables exist: ingested videos and background jobs.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    url               TEXT,
    title             TEXT,
    channel           TEXT,
    channel_id        TEXT,
    duration          INTEGER,
    published         TEXT,
    thumbnail         TEXT,
    wiki_path         TEXT,
    transcript_source TEXT,
    frame_count       INTEGER DEFAULT 0,
    words             INTEGER DEFAULT 0,
    reading_minutes   INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'pending',
    error             TEXT,
    added             TEXT,
    extracted         TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id      TEXT PRIMARY KEY,
    kind    TEXT,
    status  TEXT,
    total   INTEGER DEFAULT 0,
    done    INTEGER DEFAULT 0,
    current TEXT,
    error   TEXT,
    created TEXT,
    updated TEXT
);
"""

VIDEO_COLUMNS = [
    "video_id", "url", "title", "channel", "channel_id", "duration", "published",
    "thumbnail", "wiki_path", "transcript_source", "frame_count", "words",
    "reading_minutes", "status", "error", "added", "extracted",
]

# Tables used by the removed categories / wiki-browser feature.
_OBSOLETE_TABLES = ("categories", "pages", "pages_fts")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ensure_dirs() -> None:
    for path in (config.WIKI_DIR, config.ASSETS_DIR, config.DB_DIR, config.WIKI_DIR / ".forge" / "tmp"):
        path.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


def init_db() -> None:
    ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    migrate_flat_docs()
    backfill_doc_stats()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add newer columns / drop tables from removed features (idempotent)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    for column, ddl in (("words", "INTEGER DEFAULT 0"), ("reading_minutes", "INTEGER DEFAULT 0")):
        if column not in existing:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {column} {ddl}")
    for table in _OBSOLETE_TABLES:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        except sqlite3.Error:
            pass
    # Flat output: drop the old category column from wiki_path values.
    for row in conn.execute("SELECT video_id, wiki_path FROM videos WHERE wiki_path IS NOT NULL"):
        path = str(row["wiki_path"] or "")
        if "/" in path:
            conn.execute(
                "UPDATE videos SET wiki_path = ? WHERE video_id = ?",
                (path.rsplit("/", 1)[-1], row["video_id"]),
            )


def migrate_flat_docs() -> None:
    """Move documents from the old category subfolders into the flat wiki root."""
    if not config.WIKI_DIR.exists():
        return
    for candidate in sorted(config.WIKI_DIR.rglob("*.md")):
        rel = candidate.relative_to(config.WIKI_DIR)
        if any(part.startswith(".") or part == "assets" for part in rel.parts):
            continue
        if len(rel.parts) <= 1:
            continue
        target = config.WIKI_DIR / candidate.name
        counter = 2
        while target.exists():
            target = config.WIKI_DIR / f"{candidate.stem}-{counter}.md"
            counter += 1
        try:
            candidate.rename(target)
        except OSError:
            continue
    # Remove now-empty category folders (including nested ones), except assets/.
    _prune_empty_dirs()
    index = config.WIKI_DIR / "_index.md"
    if index.exists():
        try:
            index.unlink()
        except OSError:
            pass


def _prune_empty_dirs() -> None:
    folders = sorted(
        (path for path in config.WIKI_DIR.rglob("*") if path.is_dir()),
        key=lambda path: len(path.relative_to(config.WIKI_DIR).parts),
        reverse=True,
    )
    for folder in folders:
        rel = folder.relative_to(config.WIKI_DIR)
        if any(part.startswith(".") or part == "assets" for part in rel.parts):
            continue
        try:
            next(folder.iterdir())
        except StopIteration:
            try:
                folder.rmdir()
            except OSError:
                pass
        except OSError:
            pass


def backfill_doc_stats() -> None:
    """Fill words/reading_minutes for rows migrated from older versions."""
    if not config.WIKI_DIR.exists():
        return
    import re as _re

    pattern_words = _re.compile(r"^words:\s*(\d+)", _re.MULTILINE)
    pattern_minutes = _re.compile(r"^reading_minutes:\s*(\d+)", _re.MULTILINE)
    with connect() as conn:
        rows = list(conn.execute(
            "SELECT video_id, wiki_path, words, reading_minutes FROM videos "
            "WHERE wiki_path IS NOT NULL AND (words IS NULL OR words = 0)"
        ))
        for row in rows:
            path = config.WIKI_DIR / str(row["wiki_path"] or "")
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            words = pattern_words.search(head)
            minutes = pattern_minutes.search(head)
            if words:
                conn.execute(
                    "UPDATE videos SET words = ?, reading_minutes = ? WHERE video_id = ?",
                    (
                        int(words.group(1)),
                        int(minutes.group(1)) if minutes else max(1, round(int(words.group(1)) / 220)),
                        row["video_id"],
                    ),
                )


def create_job(job_id: str, kind: str, total: int) -> None:
    stamp = now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, kind, status, total, done, current, created, updated)"
            " VALUES (?, ?, 'queued', ?, 0, NULL, ?, ?)",
            (job_id, kind, total, stamp, stamp),
        )


def update_job(job_id: str, **fields: Any) -> None:
    fields["updated"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", values)


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        found = rows(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)))
    return found[0] if found else None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)))


def video_row(video_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        found = rows(conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)))
    return found[0] if found else None


def upsert_video(**fields: Any) -> None:
    """Insert or update a videos row. ``video_id`` is required."""
    video_id = fields.get("video_id")
    if not video_id:
        raise ValueError("video_id required")
    keys = [key for key in VIDEO_COLUMNS if key in fields]
    values = [fields[key] for key in keys]
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if exists:
            sets = ", ".join(f"{k} = ?" for k in keys if k != "video_id")
            conn.execute(
                f"UPDATE videos SET {sets} WHERE video_id = ?",
                values[keys.index("video_id") + 1:] + [video_id],
            )
        else:
            placeholders = ", ".join("?" for _ in keys)
            conn.execute(f"INSERT INTO videos ({', '.join(keys)}) VALUES ({placeholders})", values)
