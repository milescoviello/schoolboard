"""Public course-site collector.

Some courses publish a class-by-class schedule on a public site, outside Canvas.
CS 2000 does: https://neu-pdi.github.io/cs2000-public-resources/ carries a Fall
2026 calendar of lectures, labs, homework and holidays, with a page per class.

This is the only source for "what should I read before Thursday" — Canvas has
the assignment, but the prep material lives on the course site. No auth needed,
which is why it is worth having.

Parsing is deliberately narrow: the calendar cells carry data-year/month/day
attributes, so the structure is stable enough to read without a HTML library
(there is none in the stdlib worth using here, and this box has no pip packages).
"""
import html
import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "coursesite.json"

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

KINDS = {"day-lecture": "class", "day-lab": "lab",
         "day-hw": "homework", "day-holiday-name": "holiday"}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "schoolboard/0.1"})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl.create_default_context()) as resp:
        return resp.read().decode("utf-8", "replace")


def _text(fragment):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_calendar(page, base):
    """[{date, kind, label, url}] from the course calendar table."""
    out = []
    cells = re.findall(
        r'<td[^>]*data-year="(\d+)"[^>]*data-month="(\w+)"[^>]*data-day="(\d+)"[^>]*>(.*?)</td>',
        page, re.S)
    for year, month, day, body in cells:
        if month not in MONTHS:
            continue
        try:
            when = date(int(year), MONTHS[month], int(day))
        except ValueError:
            continue
        for cls, inner in re.findall(r'<div class="(day-[a-z-]+)">(.*?)(?=<div class="day-|</div></div>)',
                                     body, re.S):
            kind = KINDS.get(cls)
            if not kind:
                continue                      # day-oh (office hours) and wrappers
            label = _text(inner)
            if not label:
                continue
            link = re.search(r'href="([^"]+)"', inner)
            url = link.group(1) if link else None
            if url and url.startswith("/"):
                url = base.rstrip("/").split("//", 1)[0] + "//" + \
                      base.split("//", 1)[1].split("/", 1)[0] + url
            out.append({"date": when.isoformat(), "kind": kind, "label": label, "url": url})
    return out


def refresh(sites, path=None):
    """Fetch every configured site into the cache. Returns a short report."""
    path = Path(path) if path else CACHE
    data = {"fetched_at": datetime.now(timezone.utc).isoformat(), "sites": {}}
    notes = []
    for site in sites or []:
        course, url = site.get("course"), site.get("url")
        if not course or not url:
            continue
        try:
            entries = parse_calendar(fetch(url), url)
            data["sites"][course] = {"url": url, "entries": entries}
            notes.append(f"{course}: {len(entries)}")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            notes.append(f"{course}: failed ({exc})")
    if data["sites"]:
        path.write_text(json.dumps(data, indent=1))
    return data, ", ".join(notes) or "no course sites configured"


def load(path=None):
    path = Path(path) if path else CACHE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def entries_for(day, course=None, path=None):
    """Everything a course site lists for `day` (a date)."""
    data = load(path)
    iso = day.isoformat()
    out = []
    for site_course, site in (data.get("sites") or {}).items():
        if course and site_course != course:
            continue
        for entry in site.get("entries", []):
            if entry["date"] == iso:
                out.append({**entry, "course": site_course})
    return out


def holidays(path=None):
    """Holidays the course site declares — an independent check on our calendar."""
    data = load(path)
    out = []
    for site in (data.get("sites") or {}).values():
        for entry in site.get("entries", []):
            if entry["kind"] == "holiday":
                out.append((entry["date"], entry["label"]))
    return sorted(set(out))
