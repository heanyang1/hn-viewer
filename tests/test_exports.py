"""Tests for the export endpoints."""
import csv
import io
import json

import app as app_module


def test_export_txt_empty(client, tmp_db):
    res = client.get("/export/txt")
    assert res.status_code == 200
    assert res.mimetype == "text/plain"
    assert "attachment" in res.headers["Content-Disposition"]
    assert "hn-stored-urls.txt" in res.headers["Content-Disposition"]
    assert res.get_data(as_text=True).strip() == ""


def test_export_txt_one_url_per_line_newest_first(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    app_module.set_stored("https://b.example", "B", True)
    body = client.get("/export/txt").get_data(as_text=True)
    assert body.splitlines() == ["https://b.example", "https://a.example"]


def test_export_csv(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    res = client.get("/export/csv")
    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    assert "hn-stored-urls.csv" in res.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(res.get_data(as_text=True))))
    assert rows[0] == ["title", "url", "added_at"]
    assert rows[1][0] == "A"
    assert rows[1][1] == "https://a.example"
    assert rows[1][2]  # added_at present


def test_export_json(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    res = client.get("/export/json")
    assert res.status_code == 200
    assert res.mimetype == "application/json"
    assert "hn-stored-urls.json" in res.headers["Content-Disposition"]
    rows = json.loads(res.get_data(as_text=True))
    assert len(rows) == 1
    assert rows[0]["url"] == "https://a.example"
    assert rows[0]["title"] == "A"
    assert "added_at" in rows[0]


def test_export_unknown_format_is_404(client, tmp_db):
    assert client.get("/export/xml").status_code == 404
