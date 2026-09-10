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
CREATE TABLE IF NOT EXISTS notifications (
    key      TEXT PRIMARY KEY,
    sent_at  TEXT NOT NULL,
    text     TEXT
);
"""

# Added after the first release, so they arrive by migration rather than in SCHEMA.
MIGRATIONS = [
    ("items", "prev_due_utc", "TEXT"),
    ("items", "due_changed_at", "TEXT"),
]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for table, column, coltype in MIGRATIONS:
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


# Kinds whose timestamp is an arrival time, not a deadline. Mixing these into
# "Due soon" renders every one of them as overdue — it happened once with
# announcements, so the rule is now explicit rather than a special case.
NON_WORK_KINDS = ("announcement", "mail", "appointment")


def upsert_items(conn, items):
    """Insert or update. Returns (new, updated)."""
    now = _now()
    new = updated = 0
    for it in items:
        cur = conn.execute("SELECT due_utc FROM items WHERE id = ?", (it["id"],))
        row = cur.fetchone()
        if row is not None:
            moved = row["due_utc"] != it.get("due_utc") and row["due_utc"] and it.get("due_utc")
            conn.execute(
                """UPDATE items SET source=?, kind=?, course=?, title=?, due_utc=?,
                   url=?, done=?, body=?, last_seen=? WHERE id=?""",
                (it["source"], it["kind"], it.get("course"), it["title"], it.get("due_utc"),
                 it.get("url"), int(it.get("done", 0)), it.get("body"), now, it["id"]),
            )
            if moved:
                # A deadline that quietly shifted looks identical to one already
                # read, so record the old value rather than overwriting silently.
                conn.execute(
                    "UPDATE items SET prev_due_utc=?, due_changed_at=? WHERE id=?",
                    (row["due_utc"], now, it["id"]))
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
    holes = ",".join("?" * len(NON_WORK_KINDS))
    return conn.execute(
        f"SELECT * FROM items WHERE done=0 AND due_utc IS NOT NULL "
        f"AND kind NOT IN ({holes}) AND due_utc > ? "
        f"ORDER BY due_utc ASC LIMIT ?", (*NON_WORK_KINDS, floor, limit)).fetchall()


def undated(conn, limit=20):
    return conn.execute(
        "SELECT * FROM items WHERE done=0 AND due_utc IS NULL AND kind!='announcement' "
        "ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()


def recently_changed(conn, hours=36, limit=6):
    """Work that is new or whose deadline moved, for the "what changed" strip."""
    floor = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    holes = ",".join("?" * len(NON_WORK_KINDS))
    return conn.execute(
        f"SELECT *, (first_seen > ?) AS is_new FROM items "
        f"WHERE done=0 AND kind NOT IN ({holes}) "
        f"AND (first_seen > ? OR due_changed_at > ?) "
        f"ORDER BY COALESCE(due_changed_at, first_seen) DESC LIMIT ?",
        (floor, *NON_WORK_KINDS, floor, floor, limit)).fetchall()


def appointments(conn, limit=6):
    """Upcoming calendar events from .ics feeds."""
    now = datetime.now(timezone.utc).isoformat()
    return conn.execute(
        "SELECT * FROM items WHERE kind='appointment' AND due_utc > ? "
        "ORDER BY due_utc ASC LIMIT ?", (now, limit)).fetchall()


def mail(conn, limit=8):
    """Unread first, then most recent — the unread ones are the actionable set."""
    return conn.execute(
        "SELECT * FROM items WHERE kind='mail' "
        "ORDER BY done ASC, due_utc DESC LIMIT ?", (limit,)).fetchall()


def announcements(conn, limit=8):
    return conn.execute(
        "SELECT * FROM items WHERE kind='announcement' "
        "ORDER BY COALESCE(due_utc, first_seen) DESC LIMIT ?", (limit,)).fetchall()
