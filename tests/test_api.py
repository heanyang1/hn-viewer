"""Tests for the JSON API endpoints used by the checkboxes."""
import app as app_module


def test_store_add_json(client, tmp_db):
    res = client.post(
        "/api/store",
        json={"url": "https://a.example", "title": "A", "checked": True},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body == {"ok": True, "checked": True, "count": 1}
    assert app_module.stored_url_set() == {"https://a.example"}


def test_store_remove_json(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    res = client.post("/api/store", json={"url": "https://a.example", "checked": False})
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "checked": False, "count": 0}
    assert app_module.stored_count() == 0


def test_store_accepts_form_data(client, tmp_db):
    """Non-JSON clients fall back to form-encoded bodies."""
    res = client.post(
        "/api/store",
        data={"url": "https://f.example", "title": "F", "checked": "on"},
    )
    assert res.status_code == 200
    assert app_module.stored_count() == 1


def test_store_checked_string_variants(client, tmp_db):
    res = client.post("/api/store", json={"url": "https://a.example", "checked": "true"})
    assert res.status_code == 200
    assert app_module.stored_count() == 1

    res = client.post("/api/store", json={"url": "https://b.example", "checked": "false"})
    assert res.status_code == 200
    assert app_module.stored_count() == 1  # "false" must not add anything


def test_store_requires_url(client, tmp_db):
    res = client.post("/api/store", json={"checked": True})
    assert res.status_code == 400
    assert "error" in res.get_json()
    assert app_module.stored_count() == 0


def test_api_stored_empty(client, tmp_db):
    assert client.get("/api/stored").get_json() == {"rows": []}


def test_api_stored_returns_rows(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    body = client.get("/api/stored").get_json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["url"] == "https://a.example"
    assert row["title"] == "A"
    assert "added_at" in row


def test_api_clear(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    app_module.set_stored("https://b.example", "B", True)
    res = client.post("/api/clear")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "count": 0}
    assert app_module.stored_count() == 0
