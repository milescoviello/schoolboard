"""Push notifications.

A board you have to remember to check is a board that tells you what you already
knew. This turns it into something that speaks first.

Delivery is Telegram, reusing the bot credentials the disc burner already put on
this machine (`~/discburn/webhook.env`). The home ntfy server is not an option
from campus — 192.168.1.240 is unreachable from the dorm network.

Every rule is deduplicated through the `notifications` table, and the first run
primes that table without sending, so enabling this does not fire a backlog at
someone's phone.
"""
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import lectures, store, timetable

TELEGRAM_ENV = Path.home() / "discburn" / "webhook.env"


def read_env(path=None):
    """Parse the KEY=value file the disc burner already maintains."""
    path = Path(path).expanduser() if path else TELEGRAM_ENV
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


class Telegram:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.ctx = ssl.create_default_context()

    @property
    def ready(self):
        return bool(self.token and self.chat_id)

    def send(self, text):
        if not self.ready:
            return False, "telegram not configured"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            with urllib.request.urlopen(url, data=data, timeout=20, context=self.ctx) as resp:
                return resp.status == 200, f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            return False, str(exc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _hours(delta):
    return delta.total_seconds() / 3600.0


def _fmt_due(due_local, now):
    delta = due_local - now
    hours = _hours(delta)
    if hours < 1:
        return f"in {max(0, int(delta.total_seconds() // 60))} min"
    if due_local.date() == now.date():
        return f"today {due_local.strftime('%-I:%M %p').lower()}"
    if due_local.date() == (now + timedelta(days=1)).date():
        return f"tomorrow {due_local.strftime('%-I:%M %p').lower()}"
    return due_local.strftime("%a %-I:%M %p").lower()


def _esc(text):
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def quiet_window_start(cfg, moment):
    """If `moment` falls inside quiet hours, when that quiet window began."""
    nc = cfg.get("notify", {})
    start, end = nc.get("quiet_start"), nc.get("quiet_end")
    if start is None or end is None or int(start) == int(end):
        return None
    start, end = int(start), int(end)
    if not in_quiet_hours(cfg, moment):
        return None
    boundary = moment.replace(hour=start, minute=0, second=0, microsecond=0)
    if start > end and moment.hour < end:
        boundary -= timedelta(days=1)      # window began the previous evening
    return boundary


def effective_send_time(ideal, cfg):
    """When to actually send a warning whose natural time is `ideal`.

    A deadline at 5:45am gets its 3h warning at 2:45am — inside quiet hours, so
    it is held, and by the time quiet ends the deadline has passed and the rule
    skips it entirely. Nothing is ever sent. So a warning that would land in the
    quiet window is pulled back to just before that window starts instead.
    """
    window = quiet_window_start(cfg, ideal)
    if window is None:
        return ideal
    return min(ideal, window - timedelta(minutes=5))


def _title(row, links):
    """A work item's title, marked when it was said in class (lectures.py)."""
    title = _esc(row["title"])
    if row["source"] == lectures.SOURCE:
        return f"{title} <i>(said in class)</i>"
    if links.get(row["id"]):
        return f"{title} <i>(also said in class)</i>"
    return title


RECAP_MAX_AGE_DAYS = 21   # an older "last time" is a stale recap, not a reminder


def recap_lines(conn, index, links, meeting, now, tz):
    """For the class reminder: what the course's last recorded lecture covered,
    and what's due for this course today."""
    lines = []
    lec = lectures.last_before(index, meeting.code, meeting.start.astimezone(timezone.utc))
    if lec and lectures.lecture_age_days(lec, now) <= RECAP_MAX_AGE_DAYS:
        day = date.fromisoformat(lec["date"])
        which = f"{day:%a}" + (f", Lec {lec['lecture']}" if lec.get("lecture") else "")
        lines.append(f"\nLast time ({which}): {_esc(lec['topic'])}")
        lines += [f"• {_esc(point)}" for point in lec.get("recap", [])]
    end_of_day = datetime.combine(meeting.start.date(), datetime.max.time(), tz)
    due = []
    for row in store.on_day(conn, now.astimezone(timezone.utc).isoformat(),
                            end_of_day.astimezone(timezone.utc).isoformat(), include_done=False):
        when = _parse(row["due_utc"])
        if row["course"] == meeting.code and when and now < when.astimezone(tz) <= end_of_day:
            line = f"• {when.astimezone(tz).strftime('%-I:%M %p').lower()} {_title(row, links)}"
            # What the professor said often says more than the Canvas title: which article.
            line += "".join(f"\n   ↳ {_esc(s['what'])}" for s in links.get(row["id"], [])[:2])
            due.append(line)
    if due:
        lines.append("\n<b>Due today</b>")
        lines += due[:4]
    return lines


def build_messages(conn, schedule, cfg, now, tz):
    """Every notification due right now, as (key, text, allow_in_quiet) triples.

    Keys are stable and unique per event, so a rule that keeps matching for an
    hour still only sends once.

    `allow_in_quiet` is set when the message was *scheduled* to go out before
    quiet hours began. Otherwise a tick arriving a minute late would hold it,
    quiet hours would end after the deadline had passed, and it would never be
    sent at all.
    """
    nc = cfg.get("notify", {})
    out = []
    index = lectures.load(cfg)
    links = store.get_meta(conn, lectures.LINKS_META) or {}

    # 1. A class is about to start. The most useful one on this board, because
    #    four courses render deadlines in the wrong timezone anyway.
    lead = int(nc.get("class_lead_minutes", 15))
    for meeting in timetable.meetings_on(schedule, now.date(), tz):
        minutes = meeting.minutes_until(now)
        if 0 < minutes <= lead:
            course = meeting.course
            # The recap rides on this reminder rather than being a message of
            # its own: PHIL mornings already get the digest, this and a leave-by
            # push within 35 minutes.
            out.append((
                f"class:{course['code']}:{now.date().isoformat()}",
                f"<b>{_esc(course['code'])}</b> in {minutes} min\n"
                f"{_esc(course['room'])} · {meeting.start.strftime('%-I:%M %p').lower()}"
                + "".join("\n" + line for line in recap_lines(conn, index, links, meeting, now, tz)),
                False,
            ))

    # 2. Unsubmitted work crossing a deadline threshold.
    # Ascending, so the TIGHTEST threshold that has come due wins. Descending
    # meant 24h always matched first and broke out of the loop, so the 3h
    # escalation could never fire for anything.
    thresholds = sorted(float(h) for h in nc.get("due_thresholds_hours", [24, 3]))
    for row in store.upcoming(conn, limit=60):
        due = _parse(row["due_utc"])
        if not due:
            continue
        local = due.astimezone(tz)
        remaining = _hours(local - now)
        if remaining <= 0:
            continue
        for threshold in thresholds:
            send_at = effective_send_time(local - timedelta(hours=threshold), cfg)
            if now >= send_at:
                label = f"{int(threshold)}h"
                out.append((
                    f"due:{row['id']}:{label}",
                    f"<b>Due {_fmt_due(local, now)}</b>\n"
                    f"{_title(row, links)}\n{_esc(row['course'])}",
                    not in_quiet_hours(cfg, send_at),
                ))
                break

    # 3. Anything that has gone quiet. A source that stops updating leaves the
    #    board looking healthy while showing stale data, so it has to speak up.
    from . import status
    for row in status.stale_sources(cfg, conn, store.get_meta):
        out.append((
            f"stale:{row['name']}:{now.date().isoformat()}",
            f"<b>{_esc(row['name'])} has gone quiet</b>\n"
            f"Last update {_esc(row['label'])}."
            + (f"\n{_esc(row['note'])}" if row["note"] else ""),
            True,   # deliver even in quiet hours: it means the board is lying
        ))

    # 4. A Sunday evening look at the week ahead.
    week_hour = nc.get("weekly_hour")
    if week_hour is not None and now.weekday() == 6 and now.hour >= int(week_hour):
        out.append((f"weekly:{now.date().isoformat()}",
                    weekly_text(conn, schedule, now, tz, index, links), False))

    # 5. A morning digest, once a day.
    digest_hour = nc.get("digest_hour")
    if digest_hour is not None and now.hour >= int(digest_hour):
        out.append((f"digest:{now.date().isoformat()}",
                    digest_text(conn, schedule, now, tz, links), False))

    return out


def weekly_text(conn, schedule, now, tz, index=None, links=None):
    """Sunday evening: the shape of the week that starts tomorrow, what's due,
    and what each course covered in the week just gone (from scribe)."""
    links = links or {}
    monday = now.date() + timedelta(days=1)
    lines = [f"<b>Week of {monday.strftime('%-d %B')}</b>"]
    for offset in range(5):
        day = monday + timedelta(days=offset)
        reason = timetable.no_class_reason(schedule, day)
        meetings = timetable.meetings_on(schedule, day, tz)
        if reason:
            lines.append(f"  {day.strftime('%a')}  {_esc(reason)}")
        elif meetings:
            codes = " ".join(m.code for m in meetings)
            lines.append(f"  {day.strftime('%a')}  {_esc(codes)}")
        else:
            lines.append(f"  {day.strftime('%a')}  clear")
    horizon = now + timedelta(days=8)
    due = []
    for row in store.upcoming(conn, limit=40):
        when = _parse(row["due_utc"])
        if not when:
            continue
        local = when.astimezone(tz)
        if now < local <= horizon:
            due.append(f"  {local.strftime('%a')} &mdash; {_title(row, links)}")
    if due:
        lines.append("")
        lines.append(f"<b>{len(due)} due this week</b>")
        lines.extend(due[:10])
    past = lectures.between(index, (now - timedelta(days=7)).astimezone(timezone.utc),
                            now.astimezone(timezone.utc))
    if past:
        lines.append("")
        lines.append("<b>Last week in class</b>")
        for lec in past:
            day = date.fromisoformat(lec["date"])
            which = f" Lec {lec['lecture']}" if lec.get("lecture") else ""
            lines.append(f"  {day:%a} {_esc(lec['course'])}{which} &mdash; "
                         f"{_esc(lec.get('topic') or 'recorded, no notes yet')}")
    return "\n".join(lines)


def digest_text(conn, schedule, now, tz, links=None):
    links = links or {}
    lines = [f"<b>{now.strftime('%A %B %-d')}</b>"]
    reason = timetable.no_class_reason(schedule, now.date())
    meetings = timetable.meetings_on(schedule, now.date(), tz)
    if reason:
        lines.append(f"{_esc(reason)} — no classes.")
    elif not meetings:
        lines.append("No classes today.")
    else:
        for m in meetings:
            lines.append(f"  {m.start.strftime('%-I:%M').rjust(5)}  "
                         f"{_esc(m.code)} · {_esc(m.course['room'])}")
    soon = []
    for row in store.upcoming(conn, limit=40):
        due = _parse(row["due_utc"])
        if not due:
            continue
        local = due.astimezone(tz)
        if 0 < _hours(local - now) <= 48:
            soon.append(f"  {_fmt_due(local, now)} — {_title(row, links)} ({_esc(row['course'])})")
    if soon:
        lines.append("")
        lines.append("<b>Due in the next 48h</b>")
        lines.extend(soon[:8])
    return "\n".join(lines)


def in_quiet_hours(cfg, now):
    nc = cfg.get("notify", {})
    start, end = nc.get("quiet_start"), nc.get("quiet_end")
    if start is None or end is None:
        return False
    start, end = int(start), int(end)
    if start == end:
        return False
    if start < end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end   # wraps midnight


def tick(conn, schedule, cfg, now, tz, force=False):
    """Evaluate rules and send. Returns a short report."""
    nc = cfg.get("notify", {})
    if not nc.get("enabled", False):
        return "notifications disabled"

    env = read_env(nc.get("telegram_env"))
    bot = Telegram(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    messages = build_messages(conn, schedule, cfg, now, tz)
    fresh = [m for m in messages if not already_sent(conn, m[0])]
    if not fresh:
        return "nothing to send"

    # First ever run: record what currently matches without sending, so turning
    # this on doesn't dump a backlog into the chat.
    if not force and not store.get_meta(conn, "notify_primed"):
        for key, _, _allow in fresh:
            mark_sent(conn, key, "(primed, not sent)")
        store.set_meta(conn, "notify_primed", True)
        return f"primed {len(fresh)} existing items without sending"

    if in_quiet_hours(cfg, now):
        held = [m for m in fresh if not m[2]]
        fresh = [m for m in fresh if m[2]]
        if not fresh:
            return f"quiet hours; holding {len(held)}"
    if not bot.ready:
        return "telegram credentials missing"

    sent = failed = 0
    for key, text, _allow in fresh:
        ok, note = bot.send(text)
        if ok:
            mark_sent(conn, key, text)
            sent += 1
        else:
            failed += 1
            store.set_meta(conn, "notify_error", note)
    if sent and not failed:
        store.set_meta(conn, "notify_error", None)
    return f"sent {sent}" + (f", {failed} failed" if failed else "")


def already_sent(conn, key):
    return conn.execute("SELECT 1 FROM notifications WHERE key=?", (key,)).fetchone() is not None


def mark_sent(conn, key, text):
    conn.execute("INSERT OR REPLACE INTO notifications (key, sent_at, text) VALUES (?,?,?)",
                 (key, datetime.now(timezone.utc).isoformat(), text))
    conn.commit()
