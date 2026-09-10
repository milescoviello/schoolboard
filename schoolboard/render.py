"""HTML rendering.

Design constraints that drove every choice here:
  * It is read at a glance, sometimes from across a dorm room, not leaned into.
  * It renders in Firefox on a GeForce 9400 (2009) — so no JS, no webfonts, no
    shadows, no gradients, no transitions. Flat colour is free; compositing is not.
  * The host clock is wrong. Every time on this page is computed in campus time.

Type: Cantarell carries names, DejaVu Sans carries numbers (tabular figures keep
the time column aligned). Both are already installed, so nothing is downloaded.
Colour: one deep indigo ground, and three signal colours that mean exactly one
thing each — mint is now, amber is approaching, coral is overdue. Nothing else
is coloured, so colour always carries information.
"""
import html
from datetime import date, datetime, timedelta, timezone

from . import timetable

CSS = """
:root {
  --ground:#16182A; --raised:#1E2138; --line:#2E3252;
  --ink:#E8E9F5; --muted:#8D92B4;
  --now:#6FE3C4; --due:#FFB454; --late:#FF6B7A;
  --names:Cantarell,"Noto Sans","DejaVu Sans",sans-serif;
  --figures:"DejaVu Sans",Cantarell,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--ground);color:var(--ink);font-family:var(--names);
     font-size:17px;line-height:1.45;padding:28px 32px 40px;
     -moz-osx-font-smoothing:grayscale}
a{color:inherit;text-decoration:none}
a:focus-visible{outline:2px solid var(--now);outline-offset:3px}

header{display:flex;align-items:baseline;justify-content:space-between;
       gap:24px;flex-wrap:wrap;padding-bottom:18px;border-bottom:1px solid var(--line)}
.today-name{font-size:38px;font-weight:700;letter-spacing:-0.02em;line-height:1.05}
.today-date{color:var(--muted);font-size:19px;margin-top:2px}
.clock{font-family:var(--figures);font-size:34px;font-variant-numeric:tabular-nums;
       letter-spacing:-0.01em}
.place{color:var(--muted);font-size:15px;text-align:right;margin-top:2px}

.columns{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);
         gap:40px;margin-top:26px;align-items:start}
h2{font-size:15px;font-weight:700;color:var(--muted);margin-bottom:14px}

/* --- the day, as a rail --- */
.rail{position:relative;padding-left:96px}
.rail::before{content:"";position:absolute;left:88px;top:6px;bottom:6px;
              width:1px;background:var(--line)}
.slot{position:relative;padding:12px 0 12px 22px;min-height:56px}
.slot .when{position:absolute;left:-96px;width:74px;text-align:right;
            font-family:var(--figures);font-variant-numeric:tabular-nums;
            font-size:18px;line-height:1.25}
.slot .when .end{display:block;font-size:14px;color:var(--muted)}
.slot::before{content:"";position:absolute;left:-9px;top:20px;width:11px;height:11px;
              border-radius:50%;background:var(--ground);border:2px solid var(--line)}
.slot .code{font-size:26px;font-weight:700;letter-spacing:-0.015em}
.slot .title{color:var(--muted);font-size:16px}
.slot .where{margin-top:5px;font-size:16px}
.slot .who{color:var(--muted);font-size:15px}
.slot .flag{margin-top:6px;font-size:14px;color:var(--due)}

.slot.current{background:var(--raised);border-left:3px solid var(--now);
              margin-left:-22px;padding-left:39px;border-radius:0 4px 4px 0}
.slot.current::before{border-color:var(--now);background:var(--now)}
.slot.current .code{color:var(--now)}
.slot.past{opacity:.42}

.nowline{position:relative;height:0;margin:2px 0}
.nowline::before{content:"";position:absolute;left:-14px;top:-5px;width:11px;height:11px;
                 border-radius:50%;background:var(--now)}
.nowline::after{content:"";position:absolute;left:0;right:0;top:0;height:1px;
                background:var(--now);opacity:.5}
.nowlabel{position:absolute;left:-96px;width:74px;text-align:right;top:-11px;
          font-family:var(--figures);font-variant-numeric:tabular-nums;
          font-size:15px;color:var(--now)}
.nowtext{position:absolute;right:0;top:-11px;font-size:14px;color:var(--now);
         background:var(--ground);padding-left:10px;z-index:2}

.empty{color:var(--muted);padding:14px 0 14px 22px;position:relative;font-size:17px}

/* --- work --- */
.task{display:grid;grid-template-columns:104px minmax(0,1fr);gap:14px;
      padding:11px 0;border-bottom:1px solid var(--line)}
.task:last-child{border-bottom:0}
.task .due{font-family:var(--figures);font-size:15px;color:var(--muted);
           font-variant-numeric:tabular-nums;padding-top:1px}
.task.soon .due{color:var(--due)}
.task.late .due{color:var(--late)}
.task .what{font-size:17px;line-height:1.35}
.task .course{color:var(--muted);font-size:14px;margin-top:2px}
.task .kind{color:var(--muted)}

.week{margin-top:34px}
.ahead{margin-top:30px}
.ahead .rail .slot{min-height:auto;padding:9px 0 9px 22px}
.day{display:grid;grid-template-columns:64px minmax(0,1fr);gap:14px;
     padding:9px 0;border-bottom:1px solid var(--line)}
.day:last-child{border-bottom:0}
.day .dname{font-family:var(--figures);font-size:15px;color:var(--muted);
            font-variant-numeric:tabular-nums}
.day .none{color:var(--muted);opacity:.6}
.day .holiday{color:var(--due)}
.day .list{font-size:16px}
.day .list > span{display:inline-block;margin-right:18px;white-space:nowrap}
.day .at{color:var(--muted);font-family:var(--figures);font-size:14px}

.notice{background:var(--raised);border-left:3px solid var(--due);
        padding:14px 16px;border-radius:0 4px 4px 0;margin-bottom:20px;font-size:16px}
.notice code{font-family:var(--figures);background:var(--ground);
             padding:1px 6px;border-radius:3px;font-size:14px}
.mailrow{padding:10px 0 10px 17px;border-bottom:1px solid var(--line);position:relative}
.mailrow:last-child{border-bottom:0}
.mailrow.unread::before{content:"";position:absolute;left:0;top:17px;width:7px;height:7px;
                        border-radius:50%;background:var(--now)}
.mailrow.read{opacity:.6}
.mailrow .head{font-size:16px;line-height:1.35}
.mailrow .meta{color:var(--muted);font-size:14px;margin-top:2px}
.ann{padding:10px 0;border-bottom:1px solid var(--line)}
.ann:last-child{border-bottom:0}
.ann .head{font-size:16px}
.ann .meta{color:var(--muted);font-size:14px;margin-top:2px}

footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);
       color:var(--muted);font-size:14px;display:flex;justify-content:space-between;
       gap:16px;flex-wrap:wrap}

@media (max-width:900px){
  body{padding:20px 18px 32px;font-size:16px}
  .columns{grid-template-columns:1fr;gap:30px}
  .today-name{font-size:30px}.clock{font-size:28px}
  .rail{padding-left:74px}.rail::before{left:66px}
  .slot .when,.nowlabel{left:-74px;width:56px}
  .slot.current{margin-left:-22px;padding-left:39px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def esc(text):
    return html.escape(str(text or ""))


def parse_utc(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def due_label(due, now):
    """Short, human, and unambiguous about the day."""
    delta = due - now
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        days = (now.date() - due.date()).days
        if days == 0:
            return f"{-mins // 60}h late" if mins < -60 else f"{-mins}m late"
        return f"{days}d late"
    if due.date() == now.date():
        return f"today {due.strftime('%-I:%M %p').lower()}"
    if due.date() == (now + timedelta(days=1)).date():
        return f"tomorrow {due.strftime('%-I:%M %p').lower()}"
    if delta.days < 7:
        return f"{due.strftime('%a')} {due.strftime('%-I:%M %p').lower()}"
    return due.strftime("%b %-d")


def urgency(due, now):
    if due < now:
        return "late"
    return "soon" if (due - now) < timedelta(hours=48) else ""


def _slot(meeting, now, today=None):
    """`now` positions the meeting in its own day; `today` is the real date.

    They differ in the look-ahead rail, which fakes `now` to the start of the day
    it is drawing. A start-date note is only worth showing while the course has
    not started yet — otherwise "does not meet before Sept 11" turns up in
    November.
    """
    state = meeting.status(now)
    course = meeting.course
    today = today or now.date()
    flag = ""
    if course.get("note"):
        starts_on = course.get("starts_on")
        if not starts_on or date.fromisoformat(starts_on) >= today:
            flag = f'<div class="flag">{esc(course["note"])}</div>'
    return f"""<div class="slot {state}">
  <div class="when">{meeting.start.strftime('%-I:%M')}<span class="end">{meeting.end.strftime('%-I:%M %p').lower()}</span></div>
  <div class="code">{esc(course['code'])}</div>
  <div class="title">{esc(course['title'])}</div>
  <div class="where">{esc(course['room'])}</div>
  <div class="who">{esc(course['instructor'])}</div>
  {flag}
</div>"""


def _nowline(now, current, nxt):
    if current is not None:
        left = int((current.end - now).total_seconds() // 60)
        text = f"{left} min left" if left > 0 else "ending"
    elif nxt is not None and nxt.day == now.date():
        gap = nxt.minutes_until(now)
        text = f"{gap // 60}h {gap % 60}m until {nxt.code}" if gap >= 60 else f"{gap} min until {nxt.code}"
    else:
        text = "nothing else today"
    return (f'<div class="nowline"><span class="nowlabel">{now.strftime("%-I:%M")}</span>'
            f'<span class="nowtext">{esc(text)}</span></div>')


def next_teaching_day(schedule, now, tz, limit=7):
    """The next day that actually has a class, so an evening glance is useful."""
    from datetime import timedelta as _td
    for offset in range(1, limit + 1):
        day = now.date() + _td(days=offset)
        meetings = timetable.meetings_on(schedule, day, tz)
        if meetings:
            return day, meetings
    return None, []


def render_ahead(schedule, now, tz):
    """Rendered only once today is spent; otherwise it is noise.

    Returns (html, day_shown) so the week list below can avoid repeating it.
    """
    today = timetable.meetings_on(schedule, now.date(), tz)
    if any(m.status(now) != "past" for m in today):
        return "", None
    day, meetings = next_teaching_day(schedule, now, tz)
    if not meetings:
        return "", None
    label = "Tomorrow" if day == (now.date() + timedelta(days=1)) else day.strftime("%A")
    slots = "\n".join(_slot(m, m.start.replace(hour=0, minute=1), today=now.date())
                       for m in meetings)
    html_out = f'''<section class="ahead"><h2>{esc(label)} &mdash; {esc(day.strftime("%B %-d"))}</h2>
  <div class="rail">{slots}</div></section>'''
    return html_out, day


def render_day(schedule, now, tz):
    meetings = timetable.meetings_on(schedule, now.date(), tz)
    current, nxt = timetable.current_and_next(schedule, now, tz)
    if not meetings:
        reason = timetable.no_class_reason(schedule, now.date())
        text = f"{reason} &mdash; no classes." if reason else "No classes today."
        body = f'<div class="empty">{text}</div>'
        return body + _nowline(now, None, nxt if nxt and nxt.day == now.date() else None)
    parts = []
    placed = False
    for meeting in meetings:
        if not placed and now < meeting.start:
            parts.append(_nowline(now, current, meeting))
            placed = True
        parts.append(_slot(meeting, now))
    if not placed:
        parts.append(_nowline(now, current, None))
    return "\n".join(parts)


def render_tasks(rows, now, tz, limit=12):
    out = []
    for row in rows[:limit]:
        due = parse_utc(row["due_utc"])
        if due is None:
            continue
        local = due.astimezone(tz)
        kind = row["kind"]
        kind_html = f' <span class="kind">{esc(kind)}</span>' if kind not in ("assignment",) else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        out.append(f"""<div class="task {urgency(local, now)}">
  <div class="due">{esc(due_label(local, now))}</div>
  <div><div class="what">{title}{kind_html}</div>
       <div class="course">{esc(row['course'])}</div></div>
</div>""")
    return "\n".join(out)


def render_week(schedule, now, tz, days=6, skip=()):
    rows = []
    for day, meetings in timetable.week_ahead(schedule, now, tz, days):
        if day in skip:
            continue
        if meetings:
            inner = " ".join(
                f'<span>{esc(m.code)} <span class="at">{m.start.strftime("%-I:%M")}</span></span>'
                for m in meetings)
            listing = f'<div class="list">{inner}</div>'
        else:
            reason = timetable.no_class_reason(schedule, day)
            listing = (f'<div class="list holiday">{esc(reason)}</div>' if reason
                       else '<div class="list none">clear</div>')
        rows.append(f'<div class="day"><div class="dname">{day.strftime("%a %-d")}</div>{listing}</div>')
    return "\n".join(rows)


def render_announcements(rows, now, tz, limit=4):
    out = []
    for row in rows[:limit]:
        posted = parse_utc(row["due_utc"])
        when = posted.astimezone(tz).strftime("%b %-d") if posted else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        out.append(f'<div class="ann"><div class="head">{title}</div>'
                   f'<div class="meta">{esc(row["course"])} · {esc(when)}</div></div>')
    return "\n".join(out)


def render_mail(rows, now, tz, limit=5):
    """Course mail only. Unread first — that is the actionable set."""
    out = []
    for row in rows[:limit]:
        received = parse_utc(row["due_utc"])
        when = received.astimezone(tz).strftime("%b %-d") if received else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        state = "read" if row["done"] else "unread"
        out.append(f'<div class="mailrow {state}"><div class="head">{title}</div>'
                   f'<div class="meta">{esc(row["course"])} &middot; {esc(when)}</div></div>')
    return "\n".join(out)


def page(schedule, now, tz, tasks, anns, sync_note, canvas_ready, refresh=60, mails=""):
    ahead_html, ahead_day = render_ahead(schedule, now, tz)
    skip = (ahead_day,) if ahead_day else ()
    online = timetable.online_courses(schedule)
    online_html = ""
    if online:
        names = ", ".join(esc(c["code"]) for c in online)
        online_html = f'<div class="day"><div class="dname">any</div><div class="list none">{names} — online, no fixed meeting</div></div>'

    if not canvas_ready:
        work = ('<div class="notice">Canvas isn\'t connected yet, so nothing here knows about '
                'your assignments. Create a token at <b>Canvas → Account → Settings → '
                'New Access Token</b>, then run <code>schoolboard connect</code></div>')
    elif tasks.strip():
        work = tasks
    else:
        work = '<div class="empty" style="padding-left:0">Nothing due in the next stretch.</div>'

    ann_block = ""
    if anns.strip():
        ann_block = f'<div class="week"><h2>Announcements</h2>{anns}</div>'
    mail_block = ""
    if mails.strip():
        mail_block = f'<div class="week"><h2>Course mail</h2>{mails}</div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{int(refresh)}">
<title>{now.strftime('%A')} — schoolboard</title>
<style>{CSS}</style>
</head><body>
<header>
  <div>
    <div class="today-name">{now.strftime('%A')}</div>
    <div class="today-date">{now.strftime('%B %-d, %Y')} · {esc(schedule.get('term',''))}</div>
  </div>
  <div>
    <div class="clock">{now.strftime('%-I:%M %p').lower()}</div>
    <div class="place">{esc(schedule.get('campus',''))}<br>{esc(schedule.get('residence',''))}</div>
  </div>
</header>

<div class="columns">
  <section>
    <h2>Today</h2>
    <div class="rail">{render_day(schedule, now, tz)}</div>
    {ahead_html}
    <div class="week">
      <h2>Rest of the week</h2>
      {render_week(schedule, now, tz, skip=skip)}
      {online_html}
    </div>
  </section>
  <section>
    <h2>Due soon</h2>
    {work}
    {ann_block}
    {mail_block}
  </section>
</div>

<footer>
  <span>{esc(sync_note)}</span>
  <span>times in {esc(now.strftime('%Z'))} · schedule from Student Hub</span>
</footer>
</body></html>"""
