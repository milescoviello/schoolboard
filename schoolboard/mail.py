"""Mail collector.

Reads `mail.json`, pushed here by `schoolboard-mail-push` on .148 (which owns the
Microsoft Graph OAuth credentials). Filtering happens on this side, against
schedule.json, so the relevance rules can be tuned without touching that host.

The point is not to mirror an inbox. Most of it is admissions blasts, orientation
mail and marketing; what belongs on the board is mail from a course or from an
instructor actually teaching him this term.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = ROOT / "mail.json"

NAME_SUFFIXES = {"PHD", "MD", "JR", "SR", "II", "III", "MS", "MA", "MBA"}


def _norm(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def surname(instructor):
    """Last name from 'Grace Hopper', 'Alan Turing, PhD' or 'Lovelace, Ada'.

    Two different commas need telling apart: a credential suffix and a
    surname-first ordering. Strip suffixes before deciding which one this is.
    """
    text = (instructor or "").strip()
    if not text:
        return ""
    chunks = [c.strip() for c in text.split(",") if c.strip()]
    while len(chunks) > 1 and _norm(chunks[-1]) in NAME_SUFFIXES:
        chunks.pop()
    if len(chunks) > 1:
        return _norm(chunks[0])          # "Lovelace, Ada" — surname first
    parts = [p for p in chunks[0].split() if p.strip(".")]
    return _norm(parts[-1]) if parts else ""


def build_matchers(schedule):
    """(course code -> normalised code, surname -> course code) for this term."""
    codes, names = {}, {}
    for course in schedule["courses"]:
        code = course["code"]
        codes[_norm(code)] = code
        name = surname(course.get("instructor"))
        # Short surnames are ambiguous; require at least 4 characters.
        if len(name) >= 4:
            names.setdefault(name, code)
    return codes, names


def classify(message, codes, names):
    """Return (course_label, reason) or (None, None) if it isn't school work."""
    sender_blob = _norm(f"{message.get('sender','')} {message.get('from_address','')}")
    subject_blob = _norm(message.get("subject", ""))
    for blob, reason in ((sender_blob, "course"), (subject_blob, "subject")):
        for norm_code, pretty in codes.items():
            if norm_code in blob:
                return pretty, reason
    for name, code in names.items():
        if name in sender_blob:
            return code, "instructor"
    return None, None


def load(path=None):
    path = Path(path) if path else MAIL_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def collect(schedule, path=None, days=21):
    """Normalised mail items plus a short report."""
    data = load(path)
    if data is None:
        return [], {"available": False}
    codes, names = build_matchers(schedule)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items, kept, seen = [], 0, 0
    dropped = []
    for message in data.get("messages", []):
        seen += 1
        received = message.get("received_at") or ""
        try:
            when = datetime.fromisoformat(received.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            continue
        course, reason = classify(message, codes, names)
        if not course:
            # A false negative is otherwise invisible: an instructor mailing from
            # an address with neither a course code nor their surname would just
            # vanish. Write the rejects out so the filter can be audited.
            dropped.append((when, message))
            continue
        kept += 1
        items.append({
            "id": f"mail:{message['id']}",
            "source": "mail",
            "kind": "mail",
            "course": course,
            "title": message.get("subject") or "(no subject)",
            # received_at, NOT a due date — store.upcoming() excludes kind='mail'
            # for exactly that reason.
            "due_utc": when.isoformat(),
            "url": message.get("web_link"),
            "done": bool(message.get("is_read")),
            "body": (message.get("preview") or "")[:300],
        })
    write_dropped_log(dropped)
    return items, {
        "available": True,
        "dropped": len(dropped),
        "generated_at": data.get("generated_at"),
        "scanned": seen,
        "relevant": kept,
        "index_note": data.get("index_note"),
    }


DROPPED_LOG = ROOT / "mail-dropped.log"


def write_dropped_log(dropped, path=None):
    """Snapshot of what the filter rejected, rewritten each sync.

    Not appended: this is "what is currently being hidden", not a history.
    """
    path = Path(path) if path else DROPPED_LOG
    lines = [f"# mail the filter dropped, as of {datetime.now(timezone.utc).isoformat()}",
             "# if something here should be on the board, the matcher needs widening",
             ""]
    for when, message in sorted(dropped, key=lambda d: d[0], reverse=True):
        unread = "UNREAD" if not message.get("is_read") else "read  "
        lines.append(f"{when.astimezone().strftime('%Y-%m-%d %H:%M')}  {unread}  "
                     f"{(message.get('from_address') or '')[:44]:<44}  "
                     f"{(message.get('subject') or '')[:70]}")
    try:
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        pass
