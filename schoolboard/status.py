"""How fresh each source is, and whether anything has gone quiet.

Every source fails silently by design: Canvas keeps serving the last sync, the
mail file just ages on disk, the course site cache sits there. The board looks
perfectly healthy while showing yesterday's data. This makes the age visible and
gives the notifier something to complain about.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _age_seconds(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def _file_stamp(name, key):
    path = ROOT / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get(key)
    except (ValueError, OSError):
        return None


def humanise(seconds):
    if seconds is None:
        return "never"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours}h ago" if hours < 48 else f"{hours // 24}d ago"


def sources(cfg, conn, get_meta):
    """[{name, age, stale, note}] — one row per source, worst first."""
    out = []

    # The last sync that reached Canvas, not the last sync: that is stamped
    # whether Canvas answered or not, so two days of 401s read "just now".
    canvas_age = _age_seconds(get_meta(conn, "canvas_ok_at"))
    error = get_meta(conn, "last_sync_error") or ""
    out.append({
        "name": "Canvas",
        "age": canvas_age,
        # Sync runs every 15 min; three misses is a real fault, not a blip.
        # Never reached and failing is stale too; never reached and not failing
        # is a first sync still to come, or no token.
        "stale": (canvas_age > cfg.get("sync_minutes", 15) * 60 * 3) if canvas_age is not None else bool(error),
        "note": error,
    })

    mail_age = _age_seconds(_file_stamp("mail.json", "generated_at"))
    out.append({
        "name": "Mail",
        "age": mail_age,
        # Pushed from another host every 30 min, and that host has been flaky.
        "stale": mail_age is None or mail_age > 3 * 3600,
        "note": "pushed from .148" if mail_age is None else "",
    })

    site_age = _age_seconds(_file_stamp("coursesite.json", "fetched_at"))
    out.append({
        "name": "Course site",
        "age": site_age,
        "stale": site_age is not None and site_age > cfg.get("course_site_refresh_hours", 6) * 3600 * 3,
        "note": "",
    })

    if (cfg.get("dining") or {}).get("location_id"):
        dining_age = _age_seconds(_file_stamp("dining.json", "fetched_at"))
        out.append({
            "name": "Dining",
            "age": dining_age,
            # Three days are cached, so a missed refresh still leaves today's
            # menu; a whole day of them is edits and new days going unseen.
            "stale": dining_age is not None and dining_age > 24 * 3600,
            "note": "",
        })

    if cfg.get("ics_feeds"):
        ics_age = _age_seconds(get_meta(conn, "ics_at"))
        out.append({"name": "Calendar", "age": ics_age,
                    "stale": ics_age is None or ics_age > 6 * 3600, "note": ""})

    for row in out:
        row["label"] = humanise(row["age"])
    # Worst first, and "never" is the worst age there is.
    out.sort(key=lambda r: (not r["stale"], -(r["age"] if r["age"] is not None else float("inf"))))
    return out


def stale_sources(cfg, conn, get_meta):
    return [r for r in sources(cfg, conn, get_meta) if r["stale"]]
