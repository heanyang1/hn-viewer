"""Hacker News Viewer.

A Flask web app that lists Hacker News top stories, and checking/unchecking
the box next to a story adds/removes its URL in persistent storage (SQLite).
A separate page (/stored) lets the user view and export all stored URLs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import (Flask, Response, jsonify, render_template, request,
                   url_for)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "stored_urls.db"

HN_API = "https://hacker-news.firebaseio.com/v0"
REQUEST_TIMEOUT = 10          # seconds per HTTP request to the HN API
STORY_CACHE_TTL = 60          # seconds to keep the fetched story list
MAX_ITEMS = 100               # hard cap for the "number of items" control

app = Flask(__name__)


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


def list_stored() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT id, url, title, added_at FROM stored_urls ORDER BY id DESC"
        ).fetchall()


def stored_url_set() -> set[str]:
    with get_db() as conn:
        return {row["url"] for row in conn.execute("SELECT url FROM stored_urls")}


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


def clear_stored() -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM stored_urls")


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
    return render_template("stored.html", rows=list_stored())


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
    return jsonify(ok=True, checked=checked, count=stored_count())


@app.get("/api/stored")
def api_stored():
    return jsonify(rows=[dict(row) for row in list_stored()])


@app.post("/api/clear")
def api_clear():
    clear_stored()
    return jsonify(ok=True, count=0)


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
        "--public", action="store_true",
        help="bind to 0.0.0.0 so other devices can open the app via this "
             "machine's IP address (same as --host 0.0.0.0)",
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
    args = _build_parser().parse_args(argv)
    host = "0.0.0.0" if args.public else (args.host or "127.0.0.1")
    loopback = host in ("127.0.0.1", "localhost")

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
    if not debug:
        print("* Debugger/auto-reload disabled (enable explicitly with --debug)")

    app.run(host=host, port=args.port, debug=debug)


init_db()


if __name__ == "__main__":
    main()
