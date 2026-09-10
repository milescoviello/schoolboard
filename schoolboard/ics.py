"""iCalendar (.ics) collector.

The fallback for calendar data after Microsoft Graph turned out to be blocked at
the tenant: `Calendars.Read` is not in the granted scope, `/me/calendar` returns
403, and NU refuses consent for the device-code client. Outlook's "publish a
calendar" feature is a user-level setting that produces a secret .ics URL and
needs no admin approval, so this reads that instead.

Generic on purpose — it will take any .ics URL (Outlook, Canvas, Google).

Parsing is hand-rolled because this box has no pip packages. Scope is deliberate:
plain events plus simple DAILY/WEEKLY/MONTHLY recurrence. Anything more exotic
(BYSETPOS, RDATE, per-instance overrides) is skipped rather than guessed at.
"""
import re
import ssl
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "schoolboard/0.1"})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl.create_default_context()) as resp:
        return resp.read().decode("utf-8", "replace")


def unfold(text):
    """RFC 5545 folds long lines; a leading space continues the previous one."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(value):
    return (value.replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def parse_dt(value, params):
    """Return (datetime|date, is_all_day). Naive times are treated as UTC."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date(), True
    match = re.fullmatch(r"(\d{8}T\d{6})(Z)?", value)
    if not match:
        return None, False
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
    # A TZID we cannot resolve is still better placed at UTC than dropped; the
    # board converts to campus time for display either way.
    return stamp.replace(tzinfo=timezone.utc), False


def parse(text):
    """[{uid, summary, location, start, end, all_day, rrule}]"""
    events, current = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current and current.get("start") is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, _, value = line.partition(":")
        parts = head.split(";")
        name = parts[0].upper()
        params = {}
        for chunk in parts[1:]:
            key, _, val = chunk.partition("=")
            params[key.upper()] = val.strip('"')
        if name == "UID":
            current["uid"] = value.strip()
        elif name == "SUMMARY":
            current["summary"] = _unescape(value).strip()
        elif name == "LOCATION":
            current["location"] = _unescape(value).strip()
        elif name == "DTSTART":
            current["start"], current["all_day"] = parse_dt(value, params)
        elif name == "DTEND":
            current["end"], _ = parse_dt(value, params)
        elif name == "RRULE":
            current["rrule"] = {k.upper(): v for k, v in
                                (p.split("=", 1) for p in value.split(";") if "=" in p)}
        elif name == "STATUS":
            current["status"] = value.strip().upper()
    return events


def _as_dt(value):
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def expand(event, window_start, window_end, cap=200):
    """Occurrence start times inside the window, honouring simple recurrence."""
    start = event.get("start")
    if start is None:
        return []
    first = _as_dt(start)
    rule = event.get("rrule")
    if not rule:
        return [first] if window_start <= first <= window_end else []

    freq = (rule.get("FREQ") or "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        return [first] if window_start <= first <= window_end else []
    interval = int(rule.get("INTERVAL", 1) or 1)
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        parsed, _ = parse_dt(rule["UNTIL"], {})
        until = _as_dt(parsed) if parsed else None
    days = [WEEKDAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",")
            if d[-2:] in WEEKDAYS] if rule.get("BYDAY") else []

    out, cursor, made = [], first, 0
    step = {"DAILY": timedelta(days=interval),
            "WEEKLY": timedelta(weeks=interval)}.get(freq)
    guard = 0
    while cursor <= window_end and guard < cap * 4:
        guard += 1
        if until and cursor > until:
            break
        if count is not None and made >= count:
            break
        if freq == "WEEKLY" and days:
            base = cursor - timedelta(days=cursor.weekday())
            for offset in sorted(days):
                moment = base + timedelta(days=offset)
                if moment < first or (until and moment > until):
                    continue
                if window_start <= moment <= window_end:
                    out.append(moment)
                made += 1
                if count is not None and made >= count:
                    break
        else:
            if window_start <= cursor <= window_end:
                out.append(cursor)
            made += 1
        if freq == "MONTHLY":
            year, month = cursor.year, cursor.month + interval
            year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
            day = min(cursor.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0)
                                   else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            cursor = cursor.replace(year=year, month=month, day=day)
        else:
            cursor = cursor + step
        if len(out) >= cap:
            break
    return sorted(set(out))[:cap]


def collect(feeds, days_back=1, days_ahead=45):
    """Normalised items from every configured feed, plus a report."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days_back)
    window_end = now + timedelta(days=days_ahead)
    items, notes = [], []
    for feed in feeds or []:
        name, url = feed.get("name") or "Calendar", feed.get("url")
        if not url:
            continue
        try:
            events = parse(fetch(url))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            notes.append(f"{name}: failed ({exc})")
            continue
        kept = 0
        for event in events:
            if event.get("status") == "CANCELLED":
                continue
            for occurrence in expand(event, window_start, window_end):
                kept += 1
                uid = event.get("uid") or event.get("summary", "?")
                items.append({
                    "id": f"ics:{name}:{uid}:{occurrence.isoformat()}",
                    "source": "ics",
                    # Not "event": Canvas already uses that, and an appointment is
                    # not owed work, so it must stay out of "Due soon".
                    "kind": "appointment",
                    "course": name,
                    "title": event.get("summary") or "(untitled)",
                    "due_utc": occurrence.isoformat(),
                    "url": None,
                    "done": False,
                    "body": event.get("location"),
                })
        notes.append(f"{name}: {kept}")
    return items, "; ".join(notes) or "no calendar feeds configured"
