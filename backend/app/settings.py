"""Persisted runtime settings (settings.json) and the public settings API view."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import config, llm

MASK = "••••"


def _read_file() -> dict[str, Any]:
    path = Path(config.SETTINGS_FILE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> None:
    """Apply settings.json over the environment-derived configuration."""
    config.apply_settings(_read_file())
    _ensure_dirs()


def _ensure_dirs() -> None:
    for folder in (config.WIKI_DIR, config.ASSETS_DIR, config.DATA_DIR, config.TMP_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{MASK}{key[-4:]}" if len(key) >= 8 else MASK


def public() -> dict[str, Any]:
    """Safe view of the current settings (never exposes the raw API key)."""
    key = config.LLM_API_KEY
    return {
        "llm": {
            "available": config.llm_available(),
            "api_key_set": bool(key),
            "api_key_hint": _mask(key),
            "base_url": config.LLM_BASE_URL,
            "model": config.LLM_MODEL,
            "vision_model": config.LLM_VISION_MODEL,
            "doc_mode": config.DOC_MODE,
        },
        "output_dir": str(config.WIKI_DIR),
        "defaults": {
            "output_dir": str(config.DEFAULT_OUTPUT_DIR),
            "base_url": config.DEFAULT_BASE_URL,
            "model": config.DEFAULT_MODEL,
            "doc_mode": "auto",
        },
        "settings_file": str(config.SETTINGS_FILE),
    }


def _validate_llm(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("llm must be an object.")
    out: dict[str, Any] = {}
    if "api_key" in raw:
        value = raw.get("api_key")
        if value is None:
            pass  # keep the existing key
        elif not isinstance(value, str):
            raise ValueError("api_key must be a string.")
        else:
            out["api_key"] = value.strip()
    if "base_url" in raw:
        base = raw.get("base_url")
        if not isinstance(base, str):
            raise ValueError("base_url must be a string.")
        base = base.strip().rstrip("/")
        if base and not (base.startswith("http://") or base.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        out["base_url"] = base or config.DEFAULT_BASE_URL
    if "model" in raw:
        model = raw.get("model")
        if not isinstance(model, str):
            raise ValueError("model must be a string.")
        model = model.strip()
        if len(model) > 120:
            raise ValueError("model name is too long.")
        out["model"] = model or config.DEFAULT_MODEL
    if "vision_model" in raw:
        vision = raw.get("vision_model")
        if not isinstance(vision, str):
            raise ValueError("vision_model must be a string.")
        if len(vision.strip()) > 120:
            raise ValueError("vision_model name is too long.")
        out["vision_model"] = vision.strip()
    if "doc_mode" in raw:
        mode = raw.get("doc_mode")
        if mode not in config.DOC_MODES:
            raise ValueError(f"doc_mode must be one of {', '.join(config.DOC_MODES)}.")
        out["doc_mode"] = mode
    return out


def _validate_output_dir(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("output_dir must be a non-empty path.")
    path = config.resolve_output_dir(raw)
    if path.exists() and not path.is_dir():
        raise ValueError("output_dir points to a file, not a directory.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".tubewiki-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError(f"output_dir is not writable: {exc}") from exc
    return str(path)


def update(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist and apply a partial settings update."""
    if not isinstance(payload, dict):
        raise ValueError("settings payload must be an object.")

    current = _read_file()
    merged: dict[str, Any] = {
        "llm": dict(current.get("llm") or {}),
        "output_dir": current.get("output_dir") or str(config.WIKI_DIR),
    }

    llm_update = _validate_llm(payload.get("llm"))
    merged["llm"].update(llm_update)

    if "output_dir" in payload:
        merged["output_dir"] = _validate_output_dir(payload.get("output_dir"))

    # Persist atomically with owner-only permissions (the file can hold a key).
    path = Path(config.SETTINGS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)

    config.apply_settings(merged)
    _ensure_dirs()
    return public()


async def test_llm() -> dict[str, Any]:
    """Ping the configured LLM endpoint and report a human-readable result."""
    if not config.LLM_API_KEY:
        return {
            "ok": False,
            "message": "No API key configured. Add one here or point the base URL at a local server.",
            "model": config.LLM_MODEL,
            "latency_ms": 0,
        }
    ok, message, latency = await llm.probe()
    return {"ok": ok, "message": message, "model": config.LLM_MODEL, "latency_ms": latency}
