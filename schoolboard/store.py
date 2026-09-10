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
    """Work that is new, or whose deadline moved, since you last looked.

    "New" is measured against an explicit baseline rather than inferred from the
    data: the first sync imports everything at once, and there is no reliable way
    to tell that batch apart afterwards (mail and Canvas seeded 14 minutes apart,
    which defeated a time-window guess). The baseline is stamped once, so the
    strip starts empty and only ever reports genuine change.

    A moved deadline is always reported — that is a change whenever it happens.
    """
    baseline = get_meta(conn, "changed_baseline")
    if baseline is None:
        baseline = datetime.now(timezone.utc).isoformat()
        set_meta(conn, "changed_baseline", baseline)
    floor = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    new_floor = max(floor, baseline)
    holes = ",".join("?" * len(NON_WORK_KINDS))
    return conn.execute(
        f"SELECT *, (first_seen > ?) AS is_new FROM items "
        f"WHERE done=0 AND kind NOT IN ({holes}) "
        f"AND (first_seen > ? OR due_changed_at > ?) "
        f"ORDER BY COALESCE(due_changed_at, first_seen) DESC LIMIT ?",
        (new_floor, *NON_WORK_KINDS, new_floor, floor, limit)).fetchall()


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


def on_day(conn, day_start_utc, day_end_utc, include_done=True):
    """Everything owed on one calendar day, past or future."""
    holes = ",".join("?" * len(NON_WORK_KINDS))
    done_clause = "" if include_done else " AND done=0"
    return conn.execute(
        f"SELECT * FROM items WHERE kind NOT IN ({holes})"
        f" AND due_utc >= ? AND due_utc < ?{done_clause} ORDER BY due_utc",
        (*NON_WORK_KINDS, day_start_utc, day_end_utc)).fetchall()


def completed(conn, limit=40):
    """What has actually been finished — the past the board never showed."""
    holes = ",".join("?" * len(NON_WORK_KINDS))
    return conn.execute(
        f"SELECT * FROM items WHERE done=1 AND kind NOT IN ({holes})"
        f" AND due_utc IS NOT NULL ORDER BY due_utc DESC LIMIT ?",
        (*NON_WORK_KINDS, limit)).fetchall()


def workload(conn):
    """{date -> [total, done]} for every dated item, for the term strip."""
    holes = ",".join("?" * len(NON_WORK_KINDS))
    rows = conn.execute(
        f"SELECT substr(due_utc,1,10) d, COUNT(*) n, SUM(done) f FROM items "
        f"WHERE kind NOT IN ({holes}) AND due_utc IS NOT NULL GROUP BY d",
        NON_WORK_KINDS).fetchall()
    return {r["d"]: (r["n"], r["f"] or 0) for r in rows}


def personal(conn, include_done=False, limit=40):
    """Items he added himself.

    The board otherwise only knows what instructors put in Canvas, and CS 1800
    runs its announcements through Discord — so a real part of his term can
    never arrive automatically.
    """
    clause = "" if include_done else " AND done=0"
    return conn.execute(
        f"SELECT * FROM items WHERE source='local'{clause} "
        f"ORDER BY (due_utc IS NULL), due_utc LIMIT ?", (limit,)).fetchall()


def add_personal(conn, title, due_utc=None, course="Mine"):
    import uuid
    item = {"id": f"local:{uuid.uuid4().hex[:12]}", "source": "local", "kind": "task",
            "course": course, "title": title.strip(), "due_utc": due_utc,
            "url": None, "done": 0, "body": None}
    upsert_items(conn, [item])
    return item["id"]


def delete_item(conn, item_id):
    """Only ever removes something he added. Canvas rows come back on next sync
    anyway, so deleting one would be a lie that lasts fifteen minutes."""
    cur = conn.execute("DELETE FROM items WHERE id=? AND source='local'", (item_id,))
    conn.commit()
    return cur.rowcount > 0


def announcements(conn, limit=8):
    return conn.execute(
        "SELECT * FROM items WHERE kind='announcement' "
        "ORDER BY COALESCE(due_utc, first_seen) DESC LIMIT ?", (limit,)).fetchall()
