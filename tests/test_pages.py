"""Tests for the HTML pages (index and /stored) with a mocked HN API."""
import app as app_module


def test_index_renders_stories(client, mock_hn):
    res = client.get("/?num=3")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "First story" in html
    assert "Second story" in html
    assert 'href="https://example.com/one"' in html
    assert 'class="story-checkbox"' in html
    # item without a URL (Ask HN) is filtered out
    assert "Ask HN: no url here" not in html


def test_index_checkboxes_default_unchecked(client, mock_hn):
    html = client.get("/?num=3").get_data(as_text=True)
    assert html.count("checked>") == 0


def test_index_reflects_stored_state(client, mock_hn, tmp_db):
    app_module.set_stored("https://example.com/two", "Second story", True)
    html = client.get("/?num=3").get_data(as_text=True)
    assert html.count("checked>") == 1


def test_index_num_input_roundtrip(client, mock_hn):
    html = client.get("/?num=7").get_data(as_text=True)
    assert 'name="num" min="1" max="100" value="7"' in html


def test_index_num_clamped_to_max(client, mock_hn, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_ITEMS", 2)
    client.get("/?num=50")
    assert mock_hn["items"] == [1, 2]


def test_index_negative_num_clamped_to_one(client, mock_hn):
    client.get("/?num=-5")
    assert mock_hn["items"] == [1]


def test_index_shows_error_when_api_down(client, mock_hn_down):
    res = client.get("/")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "Could not reach the Hacker News API" in html
    assert 'class="story-checkbox"' not in html


def test_stored_page_empty_state(client, tmp_db):
    res = client.get("/stored")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "Nothing stored yet" in html


def test_stored_page_lists_rows(client, tmp_db):
    app_module.set_stored("https://a.example", "A title", True)
    res = client.get("/stored")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "A title" in html
    assert "https://a.example" in html
    assert "remove-btn" in html
    assert 'href="/export/txt"' in html
    assert 'href="/export/csv"' in html
    assert 'href="/export/json"' in html


def test_stored_page_nav_badge_count(client, tmp_db):
    app_module.set_stored("https://a.example", "A", True)
    html = client.get("/stored").get_data(as_text=True)
    assert 'id="stored-count-badge">1</span>' in html
