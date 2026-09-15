# HN Viewer

> This is a vibe-coded project.

A Flask web app that uses [Hacker News API](https://hacker-news.firebaseio.com/v0/topstories.json)
to list Hacker News top stories. Checking/unchecking the box next to a story adds/removes
its URL in persistent storage (SQLite). A separate page lets you view and export all stored URLs.
Checking a story also scrapes the article text in the background (via `scrape_webpage.py`),
so it can later be read inline or downloaded as a `.txt` file.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
```

### Open from other devices

By default the server listens on `127.0.0.1` only. To view it from phones or
other computers on the same network, pass `--public` (or `--host 0.0.0.0`):

```bash
python app.py --public
# * Network: http://192.168.1.23:5000   <- open this on other devices
```

The app prints the URL to use on other devices (its LAN IP). If it doesn't
show, find the IP with `ip addr` / `ipconfig` and open `http://<ip>:5000`.
Make sure your firewall allows inbound connections on the chosen port.

Options: `--host HOST`, `--port PORT`, `--public`, and `--debug` / `--no-debug`
(debugger + auto-reload; on by default for loopback, auto-disabled for
non-loopback binds because the Werkzeug debugger allows remote code execution).

## Pages

- `/` — Top stories with a checkbox per story.
  - Check a box → the story URL (and title) is stored in SQLite and its page
    text is scraped in the background.
  - Uncheck a box → the URL and its scraped text are removed from storage.
  - "Comments" button lazy-loads the comment tree from the HN API.
  - "Items" control loads 1–100 stories.
- `/stored` — All stored URLs with:
  - a per-row text status (`queued…`, `scraping…`, `text ready`, `failed`),
  - a "Scraped text" button that expands the scraped text inline
    (with a "Retry scraping" button when scraping failed),
  - a per-row "Download .txt" button for the scraped text
    (enabled once scraping succeeded),
  - per-row Remove button and a Clear-all button (both also drop scraped text),
  - export buttons for `.txt` (one URL per line), `.csv` and `.json`,
  - an "Export texts (.zip)" button that archives every successfully scraped
    article text as `{title}_{id}.txt`,
  - a "Copy all" button.

URLs that were stored before the scraping feature existed are scraped
automatically the next time the `/stored` page is opened.

## Theme color

The nav bar has a color picker ("Theme") to change the accent color used
for every orange-ish element: the top bar, the logo, the badge, checkbox
accents, and link hover colors. Choosing a color previews it live, and the
choice is kept across sessions (saved in the browser's `localStorage`, per
browser). "↺ Reset" restores the default Hacker News orange.

Implementation: `static/theme.js` overrides the `--accent` and
`--accent-dark` CSS variables on `:root` and derives the darker variant
from the picked color, so no other markup is needed.

## Tests

Includes a CLI test suite (`tests/test_cli.py`) covering `--public`,
`--host`/`--port`, and the debug auto-detection.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite (60 tests) covers the SQLite storage layer, the JSON API, the index
and `/stored` pages, the export endpoints, the theme color picker, and the
HN API client + `ago` filter. Each test runs against a throwaway SQLite
database (`tmp_path`) with the Hacker News API mocked, so nothing touches
the network or `data/`.

## Storage

SQLite database at `data/stored_urls.db` (created automatically):

```sql
CREATE TABLE stored_urls (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    url      TEXT NOT NULL UNIQUE,
    title    TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);
```

## API

| Method | Path           | Description                                     |
|--------|----------------|-------------------------------------------------|
| POST   | `/api/store`   | JSON `{url, title, checked}` — add/remove a URL |
| GET    | `/api/stored`  | All stored URLs as JSON                         |
| POST   | `/api/clear`   | Remove all stored URLs                          |
| GET    | `/export/txt`  | Download URLs (one per line)                    |
| GET    | `/export/csv`  | Download `title,url,added_at`                   |
| GET    | `/export/json` | Download array of stored rows                   |

Top stories are fetched server-side from the official Hacker News Firebase API
and cached in memory for 60 seconds.
