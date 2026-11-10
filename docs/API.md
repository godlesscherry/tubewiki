# Forge — API contract (v2, lean)

Forge is now deliberately small: **paste YouTube links → get detailed markdown
documents saved to one flat folder.** No categories, no browsing UI, no search.

```
YouTube links ──▶ extractor ──▶ forge-wiki/<slug>.md
                                 forge-wiki/assets/<video_id>/*.jpg
```

Backend: FastAPI on `127.0.0.1:8787`. Frontend: React + Vite (proxied in dev,
served by FastAPI from `frontend/dist` in production).

## Layout

```
forge/
├── backend/app/
│   ├── main.py            app, SPA serving
│   ├── config.py          paths, env, LLM settings
│   ├── db.py              SQLite: videos + jobs only
│   ├── schemas.py         pydantic models
│   ├── llm.py             optional OpenAI-compatible client
│   ├── routers/
│   │   ├── ingest.py      POST /api/ingest, jobs
│   │   └── sources.py     library, dedupe, delete
│   └── ingest/
│       ├── pipeline.py    run_ingest_job(job_id, urls)
│       ├── youtube.py     metadata, captions
│       ├── frames.py      scene frames
│       ├── document.py    detailed document builder
│       └── writer.py      page writer → WIKI_DIR/<slug>.md
├── frontend/src/          React UI
├── forge-wiki/            flat document folder (source of truth)
│   ├── <slug>.md
│   ├── assets/<video_id>/*.jpg
│   └── .forge/forge.db    derived metadata (deletable)
└── docs/API.md
```

## Endpoints

### Meta
- `GET /api/health` → `{ok, version}`
- `GET /api/config` → `{llm: bool, wiki_root: str, videos: int}`

### Ingest
- `POST /api/ingest` `{urls: string[], force?: bool}` → `{job_id, accepted, duplicates, rejected}`
  - Rejects non-YouTube URLs; duplicates already extracted are skipped unless `force`.
  - Runs one background async job.
- `GET /api/jobs` → `JobOut[]` (newest 20)
- `GET /api/jobs/{id}` → `JobOut`
  - `JobOut = {id, kind, status, total, done, current, error, created, updated}`
  - status: `queued | running | done | error`

### Sources (library)
- `GET /api/sources?q=&status=` → `{videos: VideoOut[], channels: ChannelOut[], total}`
  - `VideoOut`: `video_id, url, title, channel, channel_id, duration, published,
    thumbnail, wiki_path, transcript_source, frame_count, words, reading_minutes,
    status, error, added, extracted`
- `GET /api/sources/check?url=` → `{exists: bool, video: VideoOut | null}`
- `DELETE /api/sources/{video_id}` → `{deleted: true}` (DB row only; markdown file is left on disk)

## Output

Every document is written to `forge-wiki/<slug>.md` (flat). Frontmatter:

```yaml
title, source_type, source_url, video_id, channel, published, extracted,
transcript_source, frames, document (extractive|llm), words, reading_minutes
```

Body: `# Title` → source block → TL;DR → Overview → In this document → timestamped
chapters with prose + inline frames + quotes + highlights → Key takeaways →
transcript appendix.

`writer.write_page(...)` returns `(rel_path, meta)` where `meta` has
`{words, reading_minutes, mode}` so the pipeline can store stats in SQLite.
