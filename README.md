# HN Viewer

> This is a vibe-coded project.

A Flask web app that uses [Hacker News API](https://hacker-news.firebaseio.com/v0/topstories.json)
to list Hacker News top stories. Checking/unchecking the box next to a story adds/removes
its URL in persistent storage (SQLite). A separate page lets you view and export all stored URLs.

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
  - Check a box → the story URL (and title) is stored in SQLite.
  - Uncheck a box → the URL is removed from storage.
  - "Comments" button lazy-loads the comment tree from the HN API.
  - "Items" control loads 1–100 stories.
- `/stored` — All stored URLs with:
  - per-row Remove button and a Clear-all button,
  - export buttons for `.txt` (one URL per line), `.csv` and `.json`,
  - a "Copy all" button.

## Tests

Includes a CLI test suite (`tests/test_cli.py`) covering `--public`,
`--host`/`--port`, and the debug auto-detection.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite (46 tests) covers the SQLite storage layer, the JSON API, the index
and `/stored` pages, the export endpoints, and the HN API client + `ago`
filter. Each test runs against a throwaway SQLite database (`tmp_path`) with
the Hacker News API mocked, so nothing touches the network or `data/`.

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
