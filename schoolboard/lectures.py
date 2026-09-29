"""Lectures recorded by scribe: deadlines said in class, and each course's last
lecture for the pre-class recap and the Sunday weekly.

scribe (on the laptop) writes the vault's .scribe/index.json, and Syncthing
carries it into this machine's ~/Notes. It's read in place and never copied
here or committed: it holds what was said in class.

A deadline said in class is usually also a Canvas item ("read one of the
articles for Thursday" is the PHIL reading already on Canvas). Listing both
would mean two rows and two sets of warnings for one piece of work, so a
deadline that matches a Canvas item of the same course, due the same day
(give or take one: several courses' Canvas clocks are on Eastern time), is
attached to that item as "also said in class". Only unmatched ones become
items of their own, kind "said in class".
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import store

KIND = "said in class"
SOURCE = "scribe"
EXPORT_VERSION = 1
LINKS_META = "scribe_links"

_STOP = set("""a an and are as at be by class classes do due for from in into is it its next of on one or our
plus the their this to two three your you we before after about why how what which""".split())
_SAME = {"hw": "homework", "readings": "read", "reading": "read", "reread": "read", "articles": "article",
         "chapters": "chapter", "reflections": "reflection", "posts": "post", "problems": "problem",
         "questions": "question", "drafts": "draft", "quizzes": "quiz", "exams": "exam"}


def index_path(cfg):
    return Path(cfg.get("scribe_index") or Path.home() / "Notes" / ".scribe" / "index.json").expanduser()


def _usable(check):
    try:
        return bool(check())
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def load(cfg):
    """The scribe index, or None if it's missing, unreadable or a version this
    code doesn't know. Lectures and deadlines too malformed to use are dropped
    here, so one bad entry can't stop the sync or every class reminder."""
    try:
        index = json.loads(index_path(cfg).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict) or index.get("version") != EXPORT_VERSION:
        return None
    lectures = []
    for lec in index.get("lectures") or []:
        if not _usable(lambda: lec["course"] and date.fromisoformat(lec["date"]) and _utc(lec["start"])):
            continue
        lec["deadlines"] = [d for d in lec.get("deadlines") or []
                            if _usable(lambda: d["id"] and d["what"] and _utc(d["due"]))]
        lectures.append(lec)
    index["lectures"] = lectures
    return index


def _words(text):
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        w = _SAME.get(w, w)
        if w not in _STOP and len(w) > 2:
            out.add(w)
    return out


def _numbers(text):
    return set(re.findall(r"\d+", text))


def _utc(value):
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when.astimezone(timezone.utc)


def match(deadline, course, rows, tz):
    """The Canvas row a lecture deadline is, or None."""
    due = _utc(deadline["due"]).astimezone(tz).date()
    said = _words(deadline["what"] + " " + (deadline.get("quote") or ""))
    said_numbers = _numbers(deadline["what"])
    best, best_score = None, 0
    for row in rows:
        if row["course"] != course or not row["due_utc"] or not str(row["id"]).startswith("canvas:"):
            continue
        if abs((_utc(row["due_utc"]).astimezone(tz).date() - due).days) > 1:
            continue
        numbers = _numbers(row["title"])
        if said_numbers and numbers and not said_numbers & numbers:
            continue  # "homework 3" isn't "Homework 2"
        score = len(said & _words(row["title"]))
        if score > best_score:
            best, best_score = row, score
    return best


def collect(conn, cfg, tz):
    """Sync the lecture deadlines into the store. Returns a short note, or None
    if there's no index yet. Unmatched deadlines become items (keeping their
    done state across syncs, and removed if the lecture's notes no longer have
    them); matched ones are recorded in meta[scribe_links] by Canvas item id."""
    index = load(cfg)
    if index is None:
        return None
    rows = conn.execute("SELECT id, course, title, due_utc FROM items WHERE source='canvas'").fetchall()
    items, links = [], {}
    for lec in index.get("lectures", []):
        for d in lec.get("deadlines", []):
            said = {"lecture": lec.get("lecture"), "date": lec["date"], "quote": d.get("quote") or "",
                    "what": d["what"], "t": d.get("t"), "notes_note": lec.get("notes_note")}
            canvas_row = match(d, lec["course"], rows, tz)
            if canvas_row is not None:
                links.setdefault(canvas_row["id"], []).append(said)
                continue
            old = conn.execute("SELECT done FROM items WHERE id=?", (d["id"],)).fetchone()
            items.append({"id": d["id"], "source": SOURCE, "kind": KIND, "course": lec["course"],
                          "title": d["what"], "due_utc": _utc(d["due"]).isoformat(), "url": None,
                          "done": old["done"] if old else 0, "body": json.dumps(said)})
    store.upsert_items(conn, items)
    keep = {it["id"] for it in items}
    gone = [r["id"] for r in conn.execute("SELECT id FROM items WHERE source=?", (SOURCE,))
            if r["id"] not in keep]
    conn.executemany("DELETE FROM items WHERE id=?", [(i,) for i in gone])
    conn.commit()
    store.set_meta(conn, LINKS_META, links)
    return (f"lectures: {len(index.get('lectures', []))} recorded, {len(items)} deadline(s) of their own, "
            f"{sum(len(v) for v in links.values())} matched to Canvas")


def which(said):
    """"Lec 6", or "Lecture" when scribe had no number for it."""
    return f"Lec {said['lecture']}" if said.get("lecture") else "Lecture"


def said_in_class(row, links):
    """What was said in class about a row: its own body for a lecture item, or
    the lecture mentions matched to a Canvas item. [] if nothing."""
    if row["source"] == SOURCE and row["body"]:
        try:
            return [json.loads(row["body"])]
        except ValueError:
            return []
    return links.get(row["id"], [])


# ---- recap and weekly -----------------------------------------------------------------

def last_before(index, code, when):
    """The course's most recent recorded lecture that started before `when`."""
    if not index:
        return None
    earlier = [lec for lec in index.get("lectures", [])
               if lec["course"] == code and lec.get("topic") and _utc(lec["start"]) < when]
    return max(earlier, key=lambda lec: lec["start"], default=None)


def between(index, start, end):
    """Recorded lectures that started in [start, end), oldest first."""
    if not index:
        return []
    return sorted((lec for lec in index.get("lectures", []) if start <= _utc(lec["start"]) < end),
                  key=lambda lec: lec["start"])


def lecture_age_days(lec, now):
    return (now - _utc(lec["start"])) / timedelta(days=1)
