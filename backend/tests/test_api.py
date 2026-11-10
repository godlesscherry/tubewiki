"""API surface tests (Lead-owned). No network required."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

TMP_WIKI = Path(tempfile.mkdtemp(prefix="tubewiki-test-wiki-"))
TMP_DATA = Path(tempfile.mkdtemp(prefix="tubewiki-test-data-"))
os.environ["TUBEWIKI_WIKI"] = str(TMP_WIKI)
os.environ["TUBEWIKI_DATA"] = str(TMP_DATA)
os.environ["TUBEWIKI_DB"] = str(TMP_DATA / "tubewiki.db")
os.environ["TUBEWIKI_SETTINGS"] = str(TMP_DATA / "settings.json")
os.environ.pop("OPENAI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import config, db  # noqa: E402

# Pin every path explicitly, independent of import order.
config.WIKI_DIR = TMP_WIKI
config.ASSETS_DIR = TMP_WIKI / "assets"
config.DATA_DIR = TMP_DATA
config.DB_PATH = TMP_DATA / "tubewiki.db"
config.TMP_DIR = TMP_DATA / "tmp"
config.SETTINGS_FILE = TMP_DATA / "settings.json"

from backend.app.main import app  # noqa: E402
from backend.app.routers.ingest import parse_youtube_id  # noqa: E402


@pytest.fixture(scope="module")
def client():
    config.load_dotenv()
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client
    shutil.rmtree(TMP_WIKI, ignore_errors=True)
    shutil.rmtree(TMP_DATA, ignore_errors=True)


@pytest.mark.parametrize(
    "url,video_id",
    [
        ("https://www.youtube.com/watch?v=aircAruvnKk", "aircAruvnKk"),
        ("https://youtu.be/aircAruvnKk?t=10", "aircAruvnKk"),
        ("https://www.youtube.com/shorts/aircAruvnKk", "aircAruvnKk"),
        ("https://m.youtube.com/watch?v=aircAruvnKk&list=PL1", "aircAruvnKk"),
        ("https://www.youtube.com/live/aircAruvnKk", "aircAruvnKk"),
        ("https://example.com/watch?v=aircAruvnKk", None),
        ("not a url", None),
        ("https://www.youtube.com/watch?v=short", None),
    ],
)
def test_parse_youtube_id(url: str, video_id: str | None) -> None:
    assert parse_youtube_id(url) == video_id


def test_health(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_config(client) -> None:
    body = client.get("/api/config").json()
    assert body["llm"] is False
    assert Path(body["wiki_root"]).resolve() == TMP_WIKI.resolve()
    assert body["videos"] == 0


def test_removed_routes_are_gone(client) -> None:
    assert client.get("/api/categories").status_code == 404
    assert client.get("/api/wiki/tree").status_code == 404
    assert client.get("/api/search", params={"q": "x"}).status_code == 404
    assert client.post("/api/ask", json={"question": "x"}).status_code in (404, 405)


def test_ingest_rejects_non_youtube(client) -> None:
    response = client.post("/api/ingest", json={"urls": ["https://example.com/nope"]})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 0
    assert body["rejected"] == ["https://example.com/nope"]


def test_sources_empty(client) -> None:
    body = client.get("/api/sources").json()
    assert body["videos"] == []
    assert body["total"] == 0


def test_source_check_invalid(client) -> None:
    assert client.get("/api/sources/check", params={"url": "https://example.com"}).status_code == 400


def test_jobs_empty(client) -> None:
    assert client.get("/api/jobs").json() == []
