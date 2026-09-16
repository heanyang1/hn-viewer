# HN Viewer

Flask web app (Python 3) listing Hacker News top stories via the official HN API.
Checking a story stores its URL in SQLite and scrapes the article text in the
background (`scrape_webpage.py`); `/stored` views, exports, and downloads stored
URLs and scraped texts. See `README.md` for full feature/API docs.

## Commands

- `pip install -r requirements.txt -r requirements-dev.txt` — install deps (use the `.venv`)
- `python app.py` — run dev server on http://127.0.0.1:5000 (flags: `--host`, `--port`, `--public`, `--debug`/`--no-debug`)
- `python -m pytest -q` — run tests (must run from repo root; `conftest.py` lives there)

## Architecture

Deliberately single-file: `app.py` (~660 lines) holds everything, grouped by
section comments — SQLite storage, article-text (scrape queue via
`ThreadPoolExecutor`), HN API client (60s in-memory story cache), routes/JSON
API, exports, CLI (`main()`). `scrape_webpage.py` is a standalone scraper
module (cloudscraper + html2text). Templates in `templates/` (Jinja), assets in
`static/` (`theme.js` overrides `--accent` CSS vars for the color picker).

## Conventions

- Tests live in `tests/test_*.py`, split by area (api, cli, exports, helpers,
  pages, storage, texts). They import `app as app_module` and rely on root
  `conftest.py` fixtures (`client`, `tmp_db`, `mock_hn`, `mock_hn_down`,
  `mock_scrape_queue`) — DB is throwaway, HN API mocked, scraping stubbed.
- Tests must never touch the network or `data/`; if adding an outbound call,
  add a fixture for it.
- `data/` (the SQLite DB) is gitignored; `init_db()` creates it on startup.
- No formatter/linter configured — match existing style (stdlib `flake8`-ish
  line lengths, `from __future__ import annotations` in `app.py`).
