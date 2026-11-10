"""TubeWiki configuration: paths, ports, and runtime settings.

Settings precedence (highest first):
  settings.json (written by the Settings page)  →  environment / .env  →  defaults
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT = BACKEND_DIR.parent

SETTINGS_FILE = Path(os.environ.get("TUBEWIKI_SETTINGS") or (ROOT / "settings.json"))

# The database is app-level (independent of the output directory) so changing the
# output dir never hides your extraction history.
DATA_DIR = Path(os.environ.get("TUBEWIKI_DATA") or (ROOT / "data")).resolve()
DB_PATH = Path(os.environ.get("TUBEWIKI_DB") or (DATA_DIR / "tubewiki.db"))
TMP_DIR = DATA_DIR / "tmp"

DEFAULT_OUTPUT_DIR = (ROOT / "vault").resolve()
WIKI_DIR = DEFAULT_OUTPUT_DIR
ASSETS_DIR = WIKI_DIR / "assets"
FRONTEND_DIST = ROOT / "frontend" / "dist"

HOST = os.environ.get("TUBEWIKI_HOST", "127.0.0.1")
PORT = int(os.environ.get("TUBEWIKI_PORT", "8787"))
VERSION = "1.1.0"

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DOC_MODES = ("auto", "extractive", "llm")

# Optional OpenAI-compatible LLM. Everything works without it.
LLM_API_KEY = ""
LLM_BASE_URL = DEFAULT_BASE_URL
LLM_MODEL = DEFAULT_MODEL
LLM_VISION_MODEL = DEFAULT_MODEL
DOC_MODE = "auto"


def resolve_output_dir(value: str | None) -> Path:
    """Expand ~ / relative paths and resolve against the repo root."""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_OUTPUT_DIR
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _valid_doc_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in DOC_MODES else "auto"


def apply_env() -> None:
    """Seed settings from environment / .env. Call after load_dotenv()."""
    global WIKI_DIR, ASSETS_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_VISION_MODEL, DOC_MODE
    wiki = os.environ.get("TUBEWIKI_WIKI")
    WIKI_DIR = resolve_output_dir(wiki) if wiki else DEFAULT_OUTPUT_DIR
    ASSETS_DIR = WIKI_DIR / "assets"
    LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("TUBEWIKI_LLM_API_KEY", "")
    LLM_BASE_URL = (
        os.environ.get("OPENAI_BASE_URL", "")
        or os.environ.get("TUBEWIKI_LLM_BASE_URL", "")
        or DEFAULT_BASE_URL
    ).rstrip("/")
    LLM_MODEL = (
        os.environ.get("TUBEWIKI_LLM_MODEL", "")
        or os.environ.get("OPENAI_MODEL", "")
        or DEFAULT_MODEL
    )
    LLM_VISION_MODEL = os.environ.get("TUBEWIKI_VISION_MODEL") or LLM_MODEL
    DOC_MODE = _valid_doc_mode(os.environ.get("TUBEWIKI_DOC_MODE"))


def apply_settings(data: dict[str, Any]) -> None:
    """Apply a settings dict over the current runtime configuration."""
    global WIKI_DIR, ASSETS_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_VISION_MODEL, DOC_MODE

    if "output_dir" in data and data.get("output_dir"):
        WIKI_DIR = resolve_output_dir(data.get("output_dir"))
        ASSETS_DIR = WIKI_DIR / "assets"

    llm = data.get("llm") or {}
    if not isinstance(llm, dict):
        llm = {}
    if "api_key" in llm:
        LLM_API_KEY = str(llm.get("api_key") or "").strip()
    if "base_url" in llm:
        base = str(llm.get("base_url") or "").strip().rstrip("/")
        LLM_BASE_URL = base or DEFAULT_BASE_URL
    if "model" in llm:
        model = str(llm.get("model") or "").strip()
        LLM_MODEL = model or DEFAULT_MODEL
    if "vision_model" in llm:
        vision = str(llm.get("vision_model") or "").strip()
        LLM_VISION_MODEL = vision or LLM_MODEL
    if "doc_mode" in llm:
        DOC_MODE = _valid_doc_mode(llm.get("doc_mode"))


def llm_available() -> bool:
    return bool(LLM_API_KEY)


def load_dotenv() -> None:
    """Tiny .env loader (no dependency). Existing env vars win."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def migrate_storage() -> None:
    """Move the database out of an old vault/.tubewiki/ into the app-level data dir."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    old_db = DEFAULT_OUTPUT_DIR / ".tubewiki" / "tubewiki.db"
    for candidate in (old_db, WIKI_DIR / ".tubewiki" / "tubewiki.db"):
        try:
            if candidate.exists() and not DB_PATH.exists() and candidate != DB_PATH:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                candidate.rename(DB_PATH)
        except OSError:
            continue
    for folder in (DEFAULT_OUTPUT_DIR / ".tubewiki", WIKI_DIR / ".tubewiki"):
        if not folder.exists():
            continue
        try:
            nested = sorted(
                (path for path in folder.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for child in nested:
                child.rmdir()
            folder.rmdir()
        except OSError:
            continue
