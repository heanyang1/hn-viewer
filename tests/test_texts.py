"""Tests for article scraping, text viewing and text export."""
import io
import zipfile

import app as app_module


def store(url, title="T"):
    app_module.set_stored(url, title, True)


def test_store_check_enqueues_scrape(client, tmp_db, mock_scrape_queue):
    client.post(
        "/api/store", json={"url": "https://a.example", "title": "A", "checked": True}
    )
    assert mock_scrape_queue == ["https://a.example"]


def test_uncheck_does_not_enqueue(client, tmp_db, mock_scrape_queue):
    client.post("/api/store", json={"url": "https://a.example", "checked": False})
    assert mock_scrape_queue == []


def test_uncheck_removes_scraped_text(client, tmp_db):
    store("https://a.example", "A")
    app_module.ensure_article("https://a.example", "A")
    app_module.save_article_text("https://a.example", "A", "body")
    client.post("/api/store", json={"url": "https://a.example", "checked": False})
    assert app_module.get_article("https://a.example") is None


def test_clear_all_removes_scraped_text(client, tmp_db):
    store("https://a.example", "A")
    app_module.ensure_article("https://a.example")
    app_module.save_article_text("https://a.example", "A", "body")
    client.post("/api/clear")
    assert app_module.get_article("https://a.example") is None


def test_run_scrape_success(tmp_db, monkeypatch):
    store("https://a.example", "Story A")
    app_module.ensure_article("https://a.example", "Story A")
    monkeypatch.setattr(
        app_module, "_do_scrape", lambda url: ("Page text", "Page title")
    )
    app_module.run_scrape("https://a.example")
    art = app_module.get_article("https://a.example")
    assert art["status"] == "ready"
    assert art["text"] == "Page text"
    assert art["title"] == "Page title"
    assert art["error"] is None
    assert art["scraped_at"]


def test_run_scrape_no_text(tmp_db, monkeypatch):
    store("https://a.example")
    app_module.ensure_article("https://a.example")
    monkeypatch.setattr(app_module, "_do_scrape", lambda url: (None, None))
    app_module.run_scrape("https://a.example")
    art = app_module.get_article("https://a.example")
    assert art["status"] == "failed"
    assert "no text" in art["error"]


def test_run_scrape_exception(tmp_db, monkeypatch):
    store("https://a.example")
    app_module.ensure_article("https://a.example")

    def boom(url):
        raise ValueError("bad page")

    monkeypatch.setattr(app_module, "_do_scrape", boom)
    app_module.run_scrape("https://a.example")
    art = app_module.get_article("https://a.example")
    assert art["status"] == "failed"
    assert "ValueError" in art["error"]


def test_api_text_by_row_id(client, tmp_db):
    store("https://a.example", "A")
    app_module.ensure_article("https://a.example", "A")
    row_id = app_module.stored_row("https://a.example")["id"]
    assert client.get(f"/api/text/{row_id}").get_json()["status"] == "pending"
    app_module.save_article_text("https://a.example", "T", "Hello")
    body = client.get(f"/api/text/{row_id}").get_json()
    assert body["status"] == "ready"
    assert body["text"] == "Hello"
    assert client.get("/api/text/9999").status_code == 404


def test_download_txt(client, tmp_db):
    store("https://a.example", "Story A")
    app_module.ensure_article("https://a.example", "Story A")
    row_id = app_module.stored_row("https://a.example")["id"]
    # not scraped yet -> 404
    assert client.get(f"/stored/{row_id}/download.txt").status_code == 404
    app_module.save_article_text("https://a.example", "A nice title!", "Hello")
    res = client.get(f"/stored/{row_id}/download.txt")
    assert res.status_code == 200
    assert res.mimetype == "text/plain"
    assert "attachment" in res.headers["Content-Disposition"]
    assert "A_nice_title_1.txt" in res.headers["Content-Disposition"]
    assert res.get_data(as_text=True) == "Hello"


def test_export_texts_zip(client, tmp_db):
    store("https://a.example", "A")
    store("https://b.example", "B")
    app_module.ensure_article("https://a.example")
    app_module.ensure_article("https://b.example")
    app_module.save_article_text("https://a.example", "First", "one")
    res = client.get("/export/texts")
    assert res.status_code == 200
    assert res.mimetype == "application/zip"
    assert "attachment" in res.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
        assert zf.namelist() == ["First_1.txt"]
        assert zf.read("First_1.txt") == b"one"


def test_api_scrape_retry(client, tmp_db, mock_scrape_queue):
    store("https://a.example", "A")
    app_module.ensure_article("https://a.example")
    app_module.set_article_status("https://a.example", "failed", error="boom")
    res = client.post("/api/scrape", json={"url": "https://a.example"})
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is True
    assert body["status"] == "pending"
    assert mock_scrape_queue == ["https://a.example"]
    assert client.post(
        "/api/scrape", json={"url": "https://not-stored.example"}
    ).status_code == 404


def test_stored_page_shows_status_and_enqueues_legacy(
    client, tmp_db, mock_scrape_queue
):
    store("https://legacy.example", "Legacy")   # no article_texts row yet
    html = client.get("/stored").get_data(as_text=True)
    assert mock_scrape_queue == ["https://legacy.example"]
    assert "queued…" in html
    assert "Download .txt" in html


def test_safe_filename():
    assert app_module._safe_filename("Hello, world! (x)", "d") == "Hello__world___x"
    assert app_module._safe_filename("", "d") == "d"
    assert app_module._safe_filename("   ", "d") == "d"
    assert app_module._safe_filename("...", "d") == "d"
    assert len(app_module._safe_filename("x" * 500, "d")) == 100
