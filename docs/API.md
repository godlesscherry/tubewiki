# TubeWiki — API contract (v2, lean)

TubeWiki is now deliberately small: **paste YouTube links → get detailed markdown
documents saved to one flat folder.** No categories, no browsing UI, no search.

```
YouTube links ──▶ extractor ──▶ vault/<slug>.md
                                 vault/assets/<video_id>/*.jpg
```

Backend: FastAPI on `127.0.0.1:8787`. Frontend: React + Vite (proxied in dev,
served by FastAPI from `frontend/dist` in production).

## Layout

```
tubewiki/
├── backend/app/
│   ├── main.py            app, SPA serving
│   ├── config.py          paths, env, runtime settings
│   ├── settings.py        settings.json load/save/mask/validate
│   ├── db.py              SQLite: videos + jobs only
│   ├── schemas.py         pydantic models
│   ├── llm.py             optional OpenAI-compatible client
│   ├── routers/
│   │   ├── ingest.py      POST /api/ingest, jobs
│   │   ├── sources.py     history, dedupe, delete
│   │   └── settings.py    GET/PUT settings, test LLM
│   └── ingest/
│       ├── pipeline.py    run_ingest_job(job_id, urls)
│       ├── youtube.py     metadata, captions
│       ├── frames.py      scene frames
│       ├── document.py    detailed document builder
│       └── writer.py      page writer → WIKI_DIR/<slug>.md
├── frontend/src/          React UI
├── vault/                 flat document folder (source of truth, configurable)
│   ├── <slug>.md
│   └── assets/<video_id>/*.jpg
├── data/tubewiki.db       derived metadata: videos + jobs (deletable)
├── settings.json          optional runtime settings (git-ignored, 0600)
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

### History (extracted sources)
- `GET /api/sources?q=&status=` → `{videos: VideoOut[], channels: ChannelOut[], total}`
  - `VideoOut`: `video_id, url, title, channel, channel_id, duration, published,
    thumbnail, wiki_path, transcript_source, frame_count, words, reading_minutes,
    status, error, added, extracted`
- `GET /api/sources/check?url=` → `{exists: bool, video: VideoOut | null}`
- `DELETE /api/sources/{video_id}` → `{deleted: true}` (DB row only; markdown file is left on disk)

### Settings
- `GET /api/settings` → safe settings view (never returns the raw API key):
  ```json
  {
    "llm": {
      "available": true, "api_key_set": true, "api_key_hint": "••••1234",
      "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
      "vision_model": "gpt-4o-mini", "doc_mode": "auto"
    },
    "output_dir": "/abs/path/to/vault",
    "defaults": {"output_dir": "...", "base_url": "...", "model": "...", "doc_mode": "auto"},
    "settings_file": "/abs/path/to/settings.json"
  }
  ```
- `PUT /api/settings` — partial update, persisted to `settings.json` (mode 0600) and applied live:
  ```json
  {
    "llm": {
      "api_key": "sk-...",        // omit = keep, "" = clear
      "base_url": "http://localhost:11434/v1",
      "model": "llama3.1:8b",
      "vision_model": "",          // empty = use model
      "doc_mode": "auto"           // auto | extractive | llm
    },
    "output_dir": "vault"          // ~/relative/absolute; created if missing
  }
  ```
  Validation: `base_url` must be http(s), `doc_mode` must be a known mode, `output_dir`
  must be writable and not a file. Returns the same shape as GET.
- `POST /api/settings/test-llm` → `{ok, message, model, latency_ms}` — pings
  `{base_url}/models`; works with OpenAI and OpenAI-compatible servers (Ollama,
  LM Studio, vLLM).

Precedence: `settings.json` → environment/`.env` → defaults. The header shows an
LLM on/off chip and a **Settings** page links these controls to the API.

## Output

Every document is written to `vault/<slug>.md` (flat). Frontmatter:

```yaml
title, source_type, source_url, video_id, channel, published, extracted,
transcript_source, frames, document (extractive|llm), words, reading_minutes
```

Body: `# Title` → source block → TL;DR → Overview → In this document → timestamped
chapters with prose + inline frames + quotes + highlights → Key takeaways →
transcript appendix.

`writer.write_page(...)` returns `(rel_path, meta)` where `meta` has
`{words, reading_minutes, mode}` so the pipeline can store stats in SQLite.
