"""iCalendar (.ics) collector.

The fallback for calendar data after Microsoft Graph turned out to be blocked at
the tenant: `Calendars.Read` is not in the granted scope, `/me/calendar` returns
403, and NU refuses consent for the device-code client. Outlook's "publish a
calendar" feature is a user-level setting that produces a secret .ics URL and
needs no admin approval, so this reads that instead.

Generic on purpose — it will take any .ics URL (Outlook, Canvas, Google).

Parsing is hand-rolled because this box has no pip packages. Scope is deliberate:
plain events, simple DAILY/WEEKLY/MONTHLY recurrence, EXDATE, and per-instance
overrides (RECURRENCE-ID), which move or cancel one occurrence. Anything more
exotic (BYSETPOS, RDATE, MONTHLY by weekday or by day list) keeps only its first
occurrence rather than being guessed at.

Every time is placed in its own zone before it is stored as UTC: a trailing Z
is UTC, a TZID is resolved (IANA names, and the Windows names Outlook
publishes), and floating times or zones that cannot be resolved are campus
time. Recurrence steps in the event's zone, so a weekly 10:00 stays at 10:00
across a DST change. Treating all of it as UTC put every Outlook event 7 hours
early.

All-day events are stored at 23:59:59 campus time on their day. At midnight
UTC they fell on 5 pm the evening before, and dropped out of the appointments
list before their own day began.
"""
import calendar
import http.client
import re
import ssl
import urllib.request
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

# Where an all-day event sits on its day. render labels this time "all day".
ALL_DAY_TIME = time(23, 59, 59)

# Outlook publishes Windows zone names, not IANA ones. Exchange sometimes
# publishes the display name instead: "(UTC-08:00) Pacific Time (US & Canada)".
WINDOWS_ZONES = {
    "pacific standard time": "America/Los_Angeles",
    "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "us mountain standard time": "America/Phoenix",
    "utc": "UTC",
    "gmt standard time": "Europe/London",
}
DISPLAY_ZONES = {
    "pacific time": "America/Los_Angeles",
    "eastern time": "America/New_York",
    "central time": "America/Chicago",
    "mountain time": "America/Denver",
}


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


def _split(text, sep):
    """Split on `sep` outside double quotes: a quoted TZID can hold a colon."""
    out, quoted, last = [], False, 0
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif ch == sep and not quoted:
            out.append(text[last:i])
            last = i + 1
    out.append(text[last:])
    return out


def zone(name, default):
    """The tzinfo a TZID names, or `default` when there is none or it is unknown."""
    key = (name or "").strip().strip('"')
    if not key:
        return default
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass
    low = key.lower()
    if low in WINDOWS_ZONES:
        return ZoneInfo(WINDOWS_ZONES[low])
    for label, iana in DISPLAY_ZONES.items():
        if label in low:
            return ZoneInfo(iana)
    return default


def parse_dt(value, params, tz=timezone.utc):
    """Return (datetime|date, is_all_day). A trailing Z is UTC; otherwise the
    TZID's zone, or `tz` for floating times and zones that cannot be resolved."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date(), True
        match = re.fullmatch(r"(\d{8}T\d{6})(Z)?", value)
        if not match:
            return None, False
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
    except ValueError:              # 20260231: a date that does not exist
        return None, False
    if match.group(2):
        return stamp.replace(tzinfo=timezone.utc), False
    return stamp.replace(tzinfo=zone(params.get("TZID"), tz)), False


def parse(text, tz=timezone.utc):
    """[{uid, summary, location, start, end, all_day, rrule, status, exdates,
    recurrence_id}]"""
    events, current = [], None
    for line in unfold(text):
        marker = line.strip().upper()
        if marker == "BEGIN:VEVENT":
            current = {"exdates": []}
            continue
        if marker == "END:VEVENT":
            if current and current.get("start") is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        pieces = _split(line, ":")
        if len(pieces) < 2:
            continue
        head, value = pieces[0], ":".join(pieces[1:])
        parts = _split(head, ";")
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
            current["start"], current["all_day"] = parse_dt(value, params, tz)
        elif name == "DTEND":
            current["end"], _ = parse_dt(value, params, tz)
        elif name == "RRULE":
            current["rrule"] = {k.upper(): v for k, v in
                                (p.split("=", 1) for p in value.split(";") if "=" in p)}
        elif name == "EXDATE":
            for one in value.split(","):
                moment, _ = parse_dt(one, params, tz)
                if moment is not None:
                    current["exdates"].append(moment)
        elif name == "RECURRENCE-ID":
            current["recurrence_id"], _ = parse_dt(value, params, tz)
        elif name == "STATUS":
            current["status"] = value.strip().upper()
    return events


def _as_dt(value, tz=timezone.utc):
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, ALL_DAY_TIME, tzinfo=tz)


def _utc(moment):
    return moment.astimezone(timezone.utc)


def expand(event, window_start, window_end, cap=200, tz=timezone.utc):
    """Occurrence start times inside the window, honouring simple recurrence
    and EXDATE. Each is in the event's own zone; the caller stores UTC."""
    start = event.get("start")
    if start is None:
        return []
    first = _as_dt(start, tz)
    skip = {_utc(_as_dt(d, tz)) for d in event.get("exdates") or []}

    def keep(moment):
        return window_start <= moment <= window_end and _utc(moment) not in skip

    single = [first] if keep(first) else []
    rule = event.get("rrule")
    if not rule:
        return single

    freq = (rule.get("FREQ") or "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        return single
    if freq == "MONTHLY" and any(rule.get(k) for k in ("BYDAY", "BYMONTHDAY", "BYSETPOS")):
        return single               # "first Tuesday" is not "the 6th of every month"
    raw = rule.get("INTERVAL", "1")
    interval = int(raw) if raw.isdigit() and int(raw) > 0 else 1
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        parsed, _ = parse_dt(rule["UNTIL"], {}, tz)
        until = _as_dt(parsed, tz) if parsed else None
    days = [WEEKDAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",")
            if d[-2:] in WEEKDAYS] if rule.get("BYDAY") else []
    step = {"DAILY": timedelta(days=interval), "WEEKLY": timedelta(weeks=interval)}.get(freq)

    def nth(n):
        # From DTSTART each time rather than from the previous occurrence, so a
        # monthly 31st comes back to the 31st after a short month.
        if freq == "MONTHLY":
            months = first.month - 1 + n * interval
            year, month = first.year + months // 12, months % 12 + 1
            return first.replace(year=year, month=month,
                                 day=min(first.day, calendar.monthrange(year, month)[1]))
        return first + n * step     # wall-clock in the event's zone, across DST

    # Without COUNT, nothing before the window matters, so jump to just before
    # it: a DAILY series from 2024 used to exhaust the loop guard first.
    n = 0
    if count is None and first < window_start:
        if freq == "MONTHLY":
            behind = (window_start.year - first.year) * 12 + window_start.month - first.month
            n = max(0, behind // interval - 1)
        else:
            n = max(0, (window_start - first) // step - 1)

    out, made = [], 0
    for _ in range(cap * 50):
        cursor = nth(n)
        n += 1
        base = cursor - timedelta(days=cursor.weekday()) if freq == "WEEKLY" and days else cursor
        if base > window_end or (until and base > until):
            break
        if count is not None and made >= count:
            break
        if freq == "WEEKLY" and days:
            for offset in sorted(days):
                moment = base + timedelta(days=offset)
                if moment < first or (until and moment > until):
                    continue
                if count is not None and made >= count:
                    break
                made += 1           # an EXDATE still uses up one of COUNT
                if keep(moment):
                    out.append(moment)
        else:
            made += 1
            if keep(cursor):
                out.append(cursor)
        if len(out) >= cap:
            break
    return sorted(set(out))[:cap]


def collect(feeds, days_back=1, days_ahead=45, tz=timezone.utc, now=None):
    """Normalised items from every configured feed, a report, and the names of
    the feeds that were fetched cleanly (so the caller replaces only those)."""
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=days_back)
    window_end = now + timedelta(days=days_ahead)
    items, notes, ok = [], [], []
    for feed in feeds or []:
        name, url = feed.get("name") or "Calendar", feed.get("url")
        if not url:
            continue
        try:
            events = parse(fetch(url), tz)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # OSError covers urllib's errors and a timeout mid-read, which
            # urllib does not wrap; HTTPException a connection cut mid-body.
            notes.append(f"{name}: failed ({exc})")
            continue
        # An override (RECURRENCE-ID) replaces one occurrence of its master:
        # the master skips that instant, and the override stands on its own,
        # or stands for nothing if it was cancelled.
        overridden = {}
        for event in events:
            if event.get("recurrence_id") is not None:
                overridden.setdefault(event.get("uid"), []).append(event["recurrence_id"])
        kept = 0
        for event in events:
            if event.get("status") == "CANCELLED":
                continue
            if event.get("recurrence_id") is not None:
                event = dict(event, rrule=None)
            else:
                event = dict(event, exdates=event["exdates"] + overridden.get(event.get("uid"), []))
            try:
                occurrences = expand(event, window_start, window_end, tz=tz)
            except (ValueError, OverflowError):
                continue            # one malformed event, not the whole feed
            for occurrence in occurrences:
                kept += 1
                when = _utc(occurrence)
                uid = event.get("uid") or event.get("summary", "?")
                items.append({
                    "id": f"ics:{name}:{uid}:{when.isoformat()}",
                    "source": "ics",
                    # Not "event": Canvas already uses that, and an appointment is
                    # not owed work, so it must stay out of "Due soon".
                    "kind": "appointment",
                    "course": name,
                    "title": event.get("summary") or "(untitled)",
                    "due_utc": when.isoformat(),
                    "url": None,
                    "done": False,
                    "body": event.get("location"),
                })
        notes.append(f"{name}: {kept}")
        ok.append(name)
    return items, "; ".join(notes) or "no calendar feeds configured", ok
