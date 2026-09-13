"""Tests for template helpers and the HN API client (get_top_stories)."""
import time

import requests

import app as app_module


# ---------------------------------------------------------------------------
# ago filter
# ---------------------------------------------------------------------------

def ts(seconds_ago):
    return time.time() - seconds_ago


def test_ago_seconds():
    assert app_module.ago_filter(ts(30)) == "30s ago"


def test_ago_minutes():
    assert app_module.ago_filter(ts(90)) == "1m ago"


def test_ago_hours():
    assert app_module.ago_filter(ts(7300)) == "2h ago"


def test_ago_days():
    assert app_module.ago_filter(ts(3 * 86400 + 100)) == "3d ago"


def test_ago_iso_string():
    from datetime import datetime, timedelta, timezone
    value = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(
        timespec="seconds"
    )
    assert app_module.ago_filter(value) == "1m ago"


def test_ago_naive_iso_string():
    from datetime import datetime, timedelta, timezone
    value = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=90)
    ).isoformat(timespec="seconds")
    assert app_module.ago_filter(value) == "1m ago"


def test_ago_invalid_string_returns_input():
    assert app_module.ago_filter("not-a-date") == "not-a-date"


def test_ago_future_clamped_to_zero():
    assert app_module.ago_filter(ts(-3600)) == "0s ago"


# ---------------------------------------------------------------------------
# get_top_stories
# ---------------------------------------------------------------------------

def test_get_top_stories_filters_and_maps(mock_hn):
    stories, error = app_module.get_top_stories(3)
    assert error is None
    # item 4 has no URL and is filtered out
    assert [s["id"] for s in stories] == [1, 2, 3]
    first = stories[0]
    assert first["title"] == "First story"
    assert first["url"] == "https://example.com/one"
    assert first["domain"] == "example.com"
    assert first["kids"] == [10, 11]
    # story without a kids key gets an empty list
    assert stories[1]["kids"] == []
    # www. prefix is stripped from the domain
    assert stories[2]["domain"] == "example.com"


def test_get_top_stories_caches_within_ttl(mock_hn):
    app_module.get_top_stories(3)
    app_module.get_top_stories(3)
    app_module.get_top_stories(3)
    assert mock_hn["top"] == 1


def test_get_top_stories_different_counts_cached_separately(mock_hn):
    app_module.get_top_stories(2)
    app_module.get_top_stories(3)
    assert mock_hn["top"] == 2


def test_get_top_stories_api_down(mock_hn_down):
    stories, error = app_module.get_top_stories(3)
    assert stories == []
    assert error is not None


def test_failed_result_is_not_cached(mock_hn_down, monkeypatch):
    app_module.get_top_stories(3)
    assert app_module._story_cache == {}

    # once the API recovers the result must be fetched again
    def recovering_get(url, timeout=None):
        if url.endswith("topstories.json"):
            from conftest import FakeResponse, HN_TOP_IDS
            return FakeResponse(list(HN_TOP_IDS))
        from conftest import FakeResponse, HN_ITEMS
        item_id = int(url.rsplit("/", 1)[1].split(".")[0])
        return FakeResponse(HN_ITEMS.get(item_id))

    monkeypatch.setattr(app_module.requests, "get", recovering_get)
    stories, error = app_module.get_top_stories(3)
    assert error is None
    assert len(stories) == 3


def test_fetch_item_returns_none_on_request_error(monkeypatch):
    def failing_get(url, timeout=None):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(app_module.requests, "get", failing_get)
    assert app_module._fetch_item(1) is None


def test_fetch_item_returns_none_on_bad_json(monkeypatch):
    def failing_get(url, timeout=None):
        raise ValueError("not json")

    monkeypatch.setattr(app_module.requests, "get", failing_get)
    assert app_module._fetch_item(1) is None
