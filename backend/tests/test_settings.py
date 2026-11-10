"""Settings API tests: persistence, masking, validation, live apply."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import config, settings
from backend.app.main import app


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("tubewiki-settings")
    env_names = ("TUBEWIKI_WIKI", "TUBEWIKI_DATA", "TUBEWIKI_DB", "TUBEWIKI_SETTINGS")
    saved_env = {name: os.environ.get(name) for name in env_names}
    saved_key = os.environ.pop("OPENAI_API_KEY", None)
    os.environ["TUBEWIKI_WIKI"] = str(root / "vault")
    os.environ["TUBEWIKI_DATA"] = str(root / "data")
    os.environ["TUBEWIKI_DB"] = str(root / "data" / "tubewiki.db")
    os.environ["TUBEWIKI_SETTINGS"] = str(root / "settings.json")
    saved = {name: getattr(config, name) for name in (
        "SETTINGS_FILE", "WIKI_DIR", "ASSETS_DIR", "DATA_DIR", "DB_PATH", "TMP_DIR",
    )}
    config.SETTINGS_FILE = root / "settings.json"
    config.WIKI_DIR = root / "vault"
    config.ASSETS_DIR = config.WIKI_DIR / "assets"
    config.DATA_DIR = root / "data"
    config.DB_PATH = config.DATA_DIR / "tubewiki.db"
    config.TMP_DIR = config.DATA_DIR / "tmp"
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for name, value in saved.items():
            setattr(config, name, value)
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if saved_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_key


def _llm(client) -> dict:
    return client.get("/api/settings").json()["llm"]


def test_settings_safe_defaults(env) -> None:
    body = env.get("/api/settings").json()
    assert body["llm"]["api_key_set"] is False
    assert body["llm"]["api_key_hint"] == ""
    assert body["llm"]["doc_mode"] == "auto"
    assert body["output_dir"] == str(config.WIKI_DIR)
    assert body["defaults"]["model"]
    assert "settings_file" in body
    assert "sk-" not in env.get("/api/settings").text


def test_update_llm_and_masking(env) -> None:
    response = env.put(
        "/api/settings",
        json={
            "llm": {
                "api_key": "sk-test-abcdefgh1234",
                "base_url": "http://localhost:11434/v1",
                "model": "llama3.1:8b",
                "vision_model": "",
                "doc_mode": "extractive",
            }
        },
    )
    assert response.status_code == 200, response.text
    llm = response.json()["llm"]
    assert llm["available"] is True
    assert llm["api_key_set"] is True
    assert llm["api_key_hint"].endswith("1234")
    assert "sk-test-abcdefgh1234" not in response.text
    assert llm["base_url"] == "http://localhost:11434/v1"
    assert llm["model"] == "llama3.1:8b"
    assert llm["vision_model"] == "llama3.1:8b"  # falls back to model
    assert llm["doc_mode"] == "extractive"
    assert config.DOC_MODE == "extractive"

    stored = config.SETTINGS_FILE.read_text(encoding="utf-8")
    assert "sk-test-abcdefgh1234" in stored
    mode = config.SETTINGS_FILE.stat().st_mode & 0o777
    assert mode == 0o600


def test_update_output_dir(env, tmp_path) -> None:
    target = tmp_path / "my-documents"
    response = env.put("/api/settings", json={"output_dir": str(target)})
    assert response.status_code == 200, response.text
    assert target.is_dir()
    assert response.json()["output_dir"] == str(target.resolve())
    assert config.WIKI_DIR == target.resolve()


def test_invalid_settings_rejected(env) -> None:
    assert env.put("/api/settings", json={"llm": {"base_url": "ftp://nope"}}).status_code == 400
    assert env.put("/api/settings", json={"llm": {"doc_mode": "bogus"}}).status_code == 422
    assert env.put("/api/settings", json={"output_dir": ""}).status_code == 400
    assert env.put("/api/settings", json={"output_dir": "./README.md"}).status_code == 400


def test_clear_api_key(env) -> None:
    response = env.put("/api/settings", json={"llm": {"api_key": ""}})
    assert response.status_code == 200
    llm = response.json()["llm"]
    assert llm["api_key_set"] is False
    assert llm["available"] is False
    assert "api_key" in config.SETTINGS_FILE.read_text(encoding="utf-8")


def test_test_llm_without_key(env) -> None:
    body = env.post("/api/settings/test-llm").json()
    assert body["ok"] is False
    assert "No API key" in body["message"]
    assert body["latency_ms"] == 0


def test_test_llm_unreachable_endpoint(env) -> None:
    env.put(
        "/api/settings",
        json={
            "llm": {
                "api_key": "sk-unreachable",
                "base_url": "http://127.0.0.1:59999/v1",
                "model": "x",
            }
        },
    )
    body = env.post("/api/settings/test-llm").json()
    assert body["ok"] is False
    assert "Could not reach" in body["message"]
    assert body["latency_ms"] >= 0


def test_settings_persist_across_reload(env) -> None:
    settings.load()
    assert config.LLM_API_KEY == "sk-unreachable"
    assert config.LLM_BASE_URL == "http://127.0.0.1:59999/v1"
