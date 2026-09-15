"""Hacker News Viewer.

A Flask web app that lists Hacker News top stories, and checking/unchecking
the box next to a story adds/removes its URL in persistent storage (SQLite).
A separate page (/stored) lets the user view and export all stored URLs.

Checking a story also scrapes the article text in the background (via
scrape_webpage.py). On the /stored page the scraped text can be expanded
inline, downloaded per article as .txt, or exported together as a .zip
archive.
"""
from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import re
import socket
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import (Flask, Response, jsonify, render_template, request,
                   send_file, url_for)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "stored_urls.db"

HN_API = "https://hacker-news.firebaseio.com/v0"
REQUEST_TIMEOUT = 10          # seconds per HTTP request to the HN API
STORY_CACHE_TTL = 60          # seconds to keep the fetched story list
MAX_ITEMS = 100               # hard cap for the "number of items" control

app = Flask(__name__)

# Client allow-list used by --public <IP>: only loopback clients and the
# given address may connect. None means every request is accepted.
ALLOWED_CLIENTS: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] | None = None


@app.before_request
def restrict_remote_access():
    """Enforce the --public <IP> allow-list (loopback is always allowed)."""
    if ALLOWED_CLIENTS is None:
        return None
    try:
        remote = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        remote = None
    if remote is not None and (remote.is_loopback or remote in ALLOWED_CLIENTS):
        return None
    allowed = ", ".join(str(ip) for ip in sorted(ALLOWED_CLIENTS))
    return jsonify(error=f"forbidden: only localhost and {allowed} may connect"), 403


# ---------------------------------------------------------------------------
# Storage (SQLite)
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stored_urls (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                url      TEXT NOT NULL UNIQUE,
                title    TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS article_texts (
                url        TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '',
                text       TEXT,
                status     TEXT NOT NULL DEFAULT 'pending',
                error      TEXT,
                scraped_at TEXT
            )
            """
        )


def list_stored() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT id, url, title, added_at FROM stored_urls ORDER BY id DESC"
        ).fetchall()


def stored_url_set() -> set[str]:
    with get_db() as conn:
        return {row["url"] for row in conn.execute("SELECT url FROM stored_urls")}


def stored_row(url: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT id, url, title, added_at FROM stored_urls WHERE url = ?", (url,)
        ).fetchone()


def stored_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM stored_urls").fetchone()[0]


def set_stored(url: str, title: str, checked: bool) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as conn:
        if checked:
            conn.execute(
                "INSERT INTO stored_urls (url, title, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET title = excluded.title",
                (url, title, now),
            )
        else:
            conn.execute("DELETE FROM stored_urls WHERE url = ?", (url,))
            conn.execute("DELETE FROM article_texts WHERE url = ?", (url,))


def clear_stored() -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM stored_urls")
        conn.execute("DELETE FROM article_texts")


# ---------------------------------------------------------------------------
# Article text storage & background scraping
# ---------------------------------------------------------------------------

_scrape_lock = threading.Lock()
_active_scrapes: set[str] = set()   # URLs with a scrape thread running right now


def get_article(url: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT url, title, text, status, error, scraped_at "
            "FROM article_texts WHERE url = ?",
            (url,),
        ).fetchone()


def article_status_map() -> dict[str, sqlite3.Row]:
    """Map every article_texts row by URL (small table, fetched in full)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT url, title, status, error FROM article_texts"
        ).fetchall()
    return {row["url"]: row for row in rows}


def ensure_article(url: str, title: str = "") -> None:
    """Create the article_texts row for `url` if it does not exist yet."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO article_texts (url, title, status) VALUES (?, ?, 'pending') "
            "ON CONFLICT(url) DO NOTHING",
            (url, title),
        )


def set_article_status(url: str, status: str, error: str | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE article_texts SET status = ?, error = ? WHERE url = ?",
            (status, error, url),
        )


def save_article_text(url: str, title: str, text: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            "UPDATE article_texts SET title = ?, text = ?, status = 'ready', "
            "error = NULL, scraped_at = ? WHERE url = ?",
            (title, text, now, url),
        )


def article_for_stored_id(row_id: int) -> sqlite3.Row | None:
    """Article-text row joined with its stored_urls entry, by stored row id."""
    with get_db() as conn:
        return conn.execute(
            "SELECT a.status, a.title, a.error, a.text, a.scraped_at, "
            "       s.title AS story_title "
            "FROM article_texts a JOIN stored_urls s ON s.url = a.url "
            "WHERE s.id = ?",
            (row_id,),
        ).fetchone()


def list_scraped_articles() -> list[sqlite3.Row]:
    """Successfully scraped texts joined with their stored_urls row."""
    with get_db() as conn:
        return conn.execute(
            "SELECT s.id, s.title AS story_title, a.title, a.text "
            "FROM article_texts a JOIN stored_urls s ON s.url = a.url "
            "WHERE a.status = 'ready' AND TRIM(COALESCE(a.text, '')) != '' "
            "ORDER BY s.id"
        ).fetchall()


def scrape_active(url: str) -> bool:
    with _scrape_lock:
        return url in _active_scrapes


def _safe_filename(title: str, default: str) -> str:
    """Sanitize a title for use in a filename (same rules as scrape_webpage)."""
    name = re.sub(r"[^\w.-]", "_", (title or "").strip())
    return name.strip("._")[:100] or default


def _do_scrape(url: str) -> tuple[str | None, str | None]:
    """Run scrape_webpage.scrape_webpage; imported lazily so the app still
    starts when the scraping dependencies are not installed."""
    from scrape_webpage import scrape_webpage
    return scrape_webpage(url)


def run_scrape(url: str) -> None:
    """Scrape `url` synchronously and record the result (status/text/error)."""
    set_article_status(url, "scraping")
    try:
        text, scraped_title = _do_scrape(url)
    except Exception as exc:
        set_article_status(url, "failed", error=f"{type(exc).__name__}: {exc}")
        return
    if not text or not text.strip():
        set_article_status(url, "failed", error="no text scraped")
        return
    save_article_text(url, (scraped_title or "").strip(), text)


def _scrape_task(url: str) -> None:
    try:
        run_scrape(url)
    except Exception as exc:  # defensive: the thread must never die loudly
        set_article_status(url, "failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        with _scrape_lock:
            _active_scrapes.discard(url)


def enqueue_scrape(url: str, title: str = "") -> bool:
    """Start a background scrape for `url`; False if one is already running."""
    with _scrape_lock:
        if url in _active_scrapes:
            return False
        _active_scrapes.add(url)
    ensure_article(url, title)
    set_article_status(url, "pending", error=None)
    threading.Thread(
        target=_scrape_task, args=(url,), daemon=True, name=f"scrape:{url[:60]}"
    ).start()
    return True


def queue_scrape_if_needed(url: str, title: str = "",
                           retry_failed: bool = False) -> bool:
    """Queue a scrape unless the text is already there / being scraped."""
    art = get_article(url)
    if art is None:
        return enqueue_scrape(url, title)
    if art["status"] == "failed" and retry_failed:
        return enqueue_scrape(url, title)
    if art["status"] in ("pending", "scraping") and not scrape_active(url):
        # Stale row left over from an app restart — try again.
        return enqueue_scrape(url, title)
    return False


# ---------------------------------------------------------------------------
# Hacker News API (server-side fetch with a short-lived cache)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_story_cache: dict[int, tuple[float, list[dict]]] = {}


def _fetch_item(item_id: int) -> dict | None:
    try:
        resp = requests.get(f"{HN_API}/item/{item_id}.json", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def get_top_stories(num_items: int) -> tuple[list[dict], str | None]:
    """Return (stories, error). `error` is None on success."""
    num_items = max(1, min(num_items, MAX_ITEMS))
    now = time.monotonic()

    with _cache_lock:
        cached = _story_cache.get(num_items)
        if cached and now - cached[0] < STORY_CACHE_TTL:
            return cached[1], None

    try:
        resp = requests.get(f"{HN_API}/topstories.json", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        ids = resp.json()[:num_items]
    except (requests.RequestException, ValueError) as exc:
        return [], f"{exc.__class__.__name__}: could not reach the Hacker News API"

    stories: list[dict] = []
    if ids:
        with ThreadPoolExecutor(max_workers=10) as pool:
            items = list(pool.map(_fetch_item, ids))
        for item in items:
            if item and item.get("title") and item.get("url"):
                domain = urlparse(item["url"]).netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                stories.append({
                    "id": item["id"],
                    "title": item["title"],
                    "url": item["url"],
                    "domain": domain,
                    "score": item.get("score", 0),
                    "by": item.get("by", ""),
                    "time": item.get("time", 0),
                    "descendants": item.get("descendants", 0),
                    "kids": item.get("kids", []),
                })

    if stories:
        with _cache_lock:
            _story_cache[num_items] = (time.monotonic(), stories)
    return stories, None


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

@app.template_filter("ago")
def ago_filter(value) -> str:
    """Humanize a unix timestamp or an ISO datetime string."""
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    for size, label in ((86400 * 365, "y"), (86400 * 30, "mo"),
                        (86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{secs // size}{label} ago"
    return f"{secs}s ago"


@app.context_processor
def inject_stored_count():
    return {"stored_count": stored_count()}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    num = request.args.get("num", 30, type=int) or 30
    num = max(1, min(num, MAX_ITEMS))
    stories, error = get_top_stories(num)
    return render_template(
        "index.html",
        stories=stories,
        stored=stored_url_set(),
        num=num,
        error=error,
    )


@app.get("/stored")
def stored():
    rows = list_stored()
    for row in rows:
        # Pick up URLs stored before this feature existed and rows left in a
        # transient state by an app restart.
        queue_scrape_if_needed(row["url"], row["title"])
    statuses = article_status_map()
    view_rows = [
        {
            "id": row["id"],
            "url": row["url"],
            "title": row["title"],
            "added_at": row["added_at"],
            "text_status": statuses.get(row["url"], {"status": "missing"})["status"],
        }
        for row in rows
    ]
    return render_template("stored.html", rows=view_rows)


# ---------------------------------------------------------------------------
# JSON API used by the checkboxes
# ---------------------------------------------------------------------------

@app.post("/api/store")
def api_store():
    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    checked = str(data.get("checked", "")).lower() in ("true", "1", "on", "yes")
    if not url:
        return jsonify(error="url is required"), 400
    set_stored(url, title, checked)
    if checked:
        # Scrape the article text in the background (best effort).
        queue_scrape_if_needed(url, title, retry_failed=True)
    return jsonify(ok=True, checked=checked, count=stored_count())


@app.get("/api/stored")
def api_stored():
    return jsonify(rows=[dict(row) for row in list_stored()])


@app.post("/api/clear")
def api_clear():
    clear_stored()
    return jsonify(ok=True, count=0)


@app.post("/api/scrape")
def api_scrape():
    """(Re)queue background scraping for a stored URL."""
    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(error="url is required"), 400
    row = stored_row(url)
    if row is None:
        return jsonify(error="url is not stored"), 404
    queued = queue_scrape_if_needed(url, row["title"], retry_failed=True)
    if queued:
        status = "pending"   # just (re)queued by this request
    else:
        article = get_article(url)
        status = article["status"] if article else "pending"
    return jsonify(ok=True, queued=queued, status=status)


@app.get("/api/text/<int:row_id>")
def api_text(row_id: int):
    """Scraping status and (when ready) the scraped text for a stored row."""
    article = article_for_stored_id(row_id)
    if article is None:
        return jsonify(error="no such stored article"), 404
    payload = {
        "status": article["status"],
        "title": article["title"] or article["story_title"] or "",
        "error": article["error"],
        "scraped_at": article["scraped_at"],
    }
    if article["status"] == "ready":
        payload["text"] = article["text"]
    return jsonify(payload)


@app.get("/stored/<int:row_id>/download.txt")
def download_text(row_id: int):
    """Download the scraped text of one article as a .txt attachment."""
    article = article_for_stored_id(row_id)
    if article is None or article["status"] != "ready" \
            or not (article["text"] or "").strip():
        return jsonify(error="scraped text is not available"), 404
    name = _safe_filename(article["title"] or article["story_title"],
                          f"article_{row_id}")
    return send_file(
        io.BytesIO(article["text"].encode("utf-8")),
        mimetype="text/plain",
        as_attachment=True,
        download_name=f"{name}_{row_id}.txt",
    )


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------

def _stored_rows():
    return list_stored()


@app.get("/export/txt")
def export_txt():
    body = "\n".join(row["url"] for row in _stored_rows()) + "\n"
    return Response(
        body,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=hn-stored-urls.txt"},
    )


@app.get("/export/csv")
def export_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "url", "added_at"])
    for row in _stored_rows():
        writer.writerow([row["title"], row["url"], row["added_at"]])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=hn-stored-urls.csv"},
    )


@app.get("/export/json")
def export_json():
    body = json.dumps(
        [dict(row) for row in _stored_rows()], indent=2, ensure_ascii=False
    )
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=hn-stored-urls.json"},
    )


@app.get("/export/texts")
def export_texts():
    """Export every successfully scraped article text as a .zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for article in list_scraped_articles():
            row_id = article["id"]
            name = _safe_filename(article["title"] or article["story_title"],
                                  f"article_{row_id}")
            archive.writestr(f"{name}_{row_id}.txt", article["text"])
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="hn-scraped-texts.zip",
    )


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------

def _primary_lan_ip() -> str:
    """Best-effort detection of this machine's primary LAN IPv4 address.

    Opens a UDP socket towards a public address without sending any packets,
    which makes the OS pick the default-route interface. Works on Linux,
    macOS and Windows with no extra dependencies.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python app.py",
        description="Hacker News Viewer — Flask app with persistent URL storage.",
    )
    parser.add_argument(
        "--host", default=None, metavar="HOST",
        help="network interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=5000, metavar="PORT",
        help="port to listen on (default: 5000)",
    )
    parser.add_argument(
        "--public", nargs="?", const="", default=None, metavar="IP",
        help="bind to 0.0.0.0 so other devices can open the app via this "
             "machine's IP address (same as --host 0.0.0.0). Optionally give "
             "an IP address to only accept requests from localhost and that "
             "address; without a value all requests are accepted",
    )
    debug_group = parser.add_mutually_exclusive_group()
    debug_group.add_argument(
        "--debug", dest="debug", action="store_true", default=None,
        help="enable the Flask debugger and auto-reload",
    )
    debug_group.add_argument(
        "--no-debug", dest="debug", action="store_false",
        help="disable the Flask debugger and auto-reload",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    global ALLOWED_CLIENTS
    parser = _build_parser()
    args = parser.parse_args(argv)
    host = "0.0.0.0" if args.public is not None else (args.host or "127.0.0.1")
    loopback = host in ("127.0.0.1", "localhost")

    ALLOWED_CLIENTS = None
    if args.public:
        try:
            allowed_ip = ipaddress.ip_address(args.public)
        except ValueError:
            parser.error(f"--public: {args.public!r} is not a valid IP address")
        ALLOWED_CLIENTS = frozenset({allowed_ip})

    # The interactive debugger allows arbitrary code execution from the
    # browser, so never enable it by default on a non-loopback interface.
    debug = args.debug if args.debug is not None else loopback

    print(f"* HN Viewer — storage: {DB_PATH}")
    print(f"* Local:   http://127.0.0.1:{args.port}")
    if host == "0.0.0.0":
        lan_ip = _primary_lan_ip()
        print(f"* Network: http://{lan_ip}:{args.port}   <- open this on other devices")
    elif not loopback:
        print(f"* Network: http://{host}:{args.port}   <- open this on other devices")
    if ALLOWED_CLIENTS is not None:
        allowed = ", ".join(str(ip) for ip in sorted(ALLOWED_CLIENTS))
        print(f"* Access:   restricted to localhost and {allowed}")
    if not debug:
        print("* Debugger/auto-reload disabled (enable explicitly with --debug)")

    app.run(host=host, port=args.port, debug=debug)


init_db()


if __name__ == "__main__":
    main()
