"""Unit tests for the SQLite storage layer."""
from datetime import datetime

import app as app_module


def test_add_and_list(tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    rows = app_module.list_stored()
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://a.example"
    assert row["title"] == "A"
    # added_at must be a parseable ISO timestamp
    datetime.fromisoformat(row["added_at"])


def test_upsert_updates_title_without_duplicating(tmp_db):
    app_module.set_stored("https://a.example", "Old title", True)
    app_module.set_stored("https://a.example", "New title", True)
    rows = app_module.list_stored()
    assert len(rows) == 1
    assert rows[0]["title"] == "New title"


def test_remove(tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    app_module.set_stored("https://a.example", "A", False)
    assert app_module.list_stored() == []


def test_remove_missing_url_is_noop(tmp_db):
    app_module.set_stored("https://missing.example", "M", False)
    assert app_module.stored_count() == 0


def test_newest_first_ordering(tmp_db):
    app_module.set_stored("https://first.example", "First", True)
    app_module.set_stored("https://second.example", "Second", True)
    urls = [row["url"] for row in app_module.list_stored()]
    assert urls == ["https://second.example", "https://first.example"]


def test_stored_url_set(tmp_db):
    assert app_module.stored_url_set() == set()
    app_module.set_stored("https://a.example", "A", True)
    app_module.set_stored("https://b.example", "B", True)
    assert app_module.stored_url_set() == {
        "https://a.example",
        "https://b.example",
    }


def test_stored_count(tmp_db):
    assert app_module.stored_count() == 0
    app_module.set_stored("https://a.example", "A", True)
    app_module.set_stored("https://b.example", "B", True)
    assert app_module.stored_count() == 2


def test_clear_stored(tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    app_module.clear_stored()
    assert app_module.stored_count() == 0
    assert app_module.list_stored() == []
