"""SQLite store for collected items.

One row per obligation, keyed by "<source>:<native id>" so re-syncing updates in
place instead of duplicating. Items that vanish upstream are kept but marked
stale via last_seen, so a Canvas outage can't silently empty the board.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "schoolboard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    course      TEXT,
    title       TEXT NOT NULL,
    due_utc     TEXT,
    url         TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    body        TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_due ON items(due_utc);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def upsert_items(conn, items):
    """Insert or update. Returns (new, updated)."""
    now = _now()
    new = updated = 0
    for it in items:
        cur = conn.execute("SELECT id FROM items WHERE id = ?", (it["id"],))
        exists = cur.fetchone() is not None
        if exists:
            conn.execute(
                """UPDATE items SET source=?, kind=?, course=?, title=?, due_utc=?,
                   url=?, done=?, body=?, last_seen=? WHERE id=?""",
                (it["source"], it["kind"], it.get("course"), it["title"], it.get("due_utc"),
                 it.get("url"), int(it.get("done", 0)), it.get("body"), now, it["id"]),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO items (id, source, kind, course, title, due_utc, url,
                   done, body, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (it["id"], it["source"], it["kind"], it.get("course"), it["title"],
                 it.get("due_utc"), it.get("url"), int(it.get("done", 0)), it.get("body"),
                 now, now),
            )
            new += 1
    conn.commit()
    return new, updated


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
    conn.commit()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def upcoming(conn, limit=40, stale_days=14):
    """Dated, unfinished work. Announcements are excluded on purpose: their
    timestamp is when they were posted, not something owed, and treating the two
    alike renders every announcement as overdue."""
    floor = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
    return conn.execute(
        "SELECT * FROM items WHERE done=0 AND due_utc IS NOT NULL "
        "AND kind != 'announcement' AND due_utc > ? "
        "ORDER BY due_utc ASC LIMIT ?", (floor, limit)).fetchall()


def undated(conn, limit=20):
    return conn.execute(
        "SELECT * FROM items WHERE done=0 AND due_utc IS NULL AND kind!='announcement' "
        "ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()


def announcements(conn, limit=8):
    return conn.execute(
        "SELECT * FROM items WHERE kind='announcement' "
        "ORDER BY COALESCE(due_utc, first_seen) DESC LIMIT ?", (limit,)).fetchall()
