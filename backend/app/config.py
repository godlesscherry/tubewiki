"""Forge configuration: paths, ports, optional LLM settings."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT = BACKEND_DIR.parent

WIKI_DIR = Path(os.environ.get("FORGE_WIKI") or (ROOT / "forge-wiki")).resolve()
DB_DIR = WIKI_DIR / ".forge"
DB_PATH = Path(os.environ.get("FORGE_DB") or (DB_DIR / "forge.db"))
ASSETS_DIR = WIKI_DIR / "assets"
FRONTEND_DIST = ROOT / "frontend" / "dist"

HOST = os.environ.get("FORGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("FORGE_PORT", "8787"))
VERSION = "1.0.0"

# Optional OpenAI-compatible LLM. Everything works without it.
LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("FORGE_LLM_API_KEY", "")
LLM_BASE_URL = (
    os.environ.get("OPENAI_BASE_URL", "")
    or os.environ.get("FORGE_LLM_BASE_URL", "")
    or "https://api.openai.com/v1"
).rstrip("/")
LLM_MODEL = (
    os.environ.get("FORGE_LLM_MODEL", "")
    or os.environ.get("OPENAI_MODEL", "")
    or "gpt-4o-mini"
)
LLM_VISION_MODEL = os.environ.get("FORGE_VISION_MODEL") or LLM_MODEL


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


def refresh_llm() -> None:
    """Re-read LLM settings from env (after load_dotenv)."""
    global LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_VISION_MODEL
    LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("FORGE_LLM_API_KEY", "")
    LLM_BASE_URL = (
        os.environ.get("OPENAI_BASE_URL", "")
        or os.environ.get("FORGE_LLM_BASE_URL", "")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    LLM_MODEL = (
        os.environ.get("FORGE_LLM_MODEL", "")
        or os.environ.get("OPENAI_MODEL", "")
        or "gpt-4o-mini"
    )
    LLM_VISION_MODEL = os.environ.get("FORGE_VISION_MODEL") or LLM_MODEL


def llm_available() -> bool:
    return bool(LLM_API_KEY)
