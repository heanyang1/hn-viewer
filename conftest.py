"""Shared pytest fixtures for the HN Viewer test suite.

Fixtures point the app at a throwaway SQLite database (per test) and fake
the Hacker News API so tests never touch the network or real data.
"""
from datetime import datetime, timezone

import pytest
import requests

import app as app_module

NOW = int(datetime.now(timezone.utc).timestamp())

# Fake HN item payloads. Item 4 has no URL (like Ask HN posts) and must be
# filtered out; item 3 uses a www. prefix to exercise domain stripping.
HN_TOP_IDS = [1, 2, 3, 4]
HN_ITEMS = {
    1: {
        "id": 1,
        "title": "First story",
        "url": "https://example.com/one",
        "score": 10,
        "by": "alice",
        "time": NOW - 3600,
        "descendants": 2,
        "kids": [10, 11],
    },
    2: {
        "id": 2,
        "title": "Second story",
        "url": "https://example.com/two",
        "score": 5,
        "by": "bob",
        "time": NOW - 120,
        "descendants": 0,
    },
    3: {
        "id": 3,
        "title": "Third story",
        "url": "https://www.example.com/three",
        "score": 1,
        "by": "carol",
        "time": NOW,
        "descendants": 0,
    },
    4: {
        "id": 4,
        "title": "Ask HN: no url here",
        "score": 1,
        "by": "dave",
        "time": NOW,
    },
}


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def reset_story_cache():
    """Keep the module-level story cache isolated between tests."""
    app_module._story_cache.clear()
    yield
    app_module._story_cache.clear()


@pytest.fixture(autouse=True)
def mock_scrape_queue(monkeypatch):
    """Stub out background scraping so tests never spawn scrape threads.

    Tests that care about scraping can request this fixture to inspect the
    enqueued URLs, and can drive the pipeline themselves by monkeypatching
    app_module._do_scrape and calling app_module.run_scrape(url) directly.
    """
    queued: list[str] = []
    monkeypatch.setattr(
        app_module, "enqueue_scrape", lambda url, title="": queued.append(url) or True
    )
    yield queued


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point storage at a fresh SQLite DB inside a temp directory."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(app_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_module, "DB_PATH", data_dir / "test.db")
    app_module.init_db()
    return app_module


@pytest.fixture()
def client(tmp_db):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def mock_hn(monkeypatch):
    """Fake a reachable HN API and record which endpoints were called."""
    calls = {"top": 0, "items": []}

    def fake_get(url, timeout=None):
        if url.endswith("topstories.json"):
            calls["top"] += 1
            return FakeResponse(list(HN_TOP_IDS))
        item_id = int(url.rsplit("/", 1)[1].split(".")[0])
        calls["items"].append(item_id)
        return FakeResponse(HN_ITEMS.get(item_id))

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    return calls


@pytest.fixture()
def mock_hn_down(monkeypatch):
    """Fake an unreachable HN API."""

    def fake_get(url, timeout=None):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(app_module.requests, "get", fake_get)
