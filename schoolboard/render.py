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

from . import coursesite, timetable

CSS = """
:root {
  --ground:#16182A; --raised:#1E2138; --line:#2E3252;
  --ink:#E8E9F5; --muted:#8D92B4;
  --now:#6FE3C4; --due:#FFB454; --late:#FF6B7A;
  /* Course hues. Deliberately clear of the three signal colours above, so a
     course colour can never be misread as "now", "due soon" or "overdue". */
  --c0:#8AB4FF; --c1:#C6A2F0; --c2:#F09CC4; --c3:#A9D46B;
  --c4:#79C8D6; --c5:#D69A7A; --c6:#9AA7D9;
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

.hero{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap;
      padding:16px 0 4px;border-bottom:1px solid var(--line)}
.hero .lead{font-size:13px;color:var(--muted);letter-spacing:.06em;
            text-transform:lowercase}
.hero .what{font-size:29px;font-weight:700;letter-spacing:-0.02em}
.hero .meta{font-size:17px;color:var(--muted)}
.hero .count{margin-left:auto;font-family:var(--figures);font-size:17px;
             font-variant-numeric:tabular-nums;color:var(--now)}
.hero.idle .what{color:var(--muted);font-weight:400;font-size:20px}

.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
     margin-right:7px;vertical-align:baseline;flex:none}

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
.slot .prep{margin-top:5px;font-size:15px}
.slot .prep a{color:var(--now);border-bottom:1px solid rgba(111,227,196,.35)}

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
.task{display:grid;grid-template-columns:104px minmax(0,1fr) auto;gap:14px;
      padding:11px 0;border-bottom:1px solid var(--line);align-items:start}
.tick{border:0;background:none;padding:4px 6px;cursor:pointer;color:var(--muted);
      font:inherit;font-size:20px;line-height:1;border-radius:4px}
.tick:hover{color:var(--now);background:var(--raised)}
.tick:focus-visible{outline:2px solid var(--now);outline-offset:2px}
.task:last-child{border-bottom:0}
.task .due{font-family:var(--figures);font-size:15px;color:var(--muted);
           font-variant-numeric:tabular-nums;padding-top:1px}
.task.soon .due{color:var(--due)}
.task.late .due{color:var(--late)}
.task .what{font-size:17px;line-height:1.35}
.task .course{color:var(--muted);font-size:14px;margin-top:3px}
.daygroup{font-size:13px;color:var(--muted);letter-spacing:.05em;
          padding:16px 0 5px;border-bottom:1px solid var(--line)}
.daygroup:first-child{padding-top:2px}
.daygroup.late{color:var(--late)}
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
.grade{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:baseline;
       padding:8px 0;border-bottom:1px solid var(--line)}
.grade:last-child{border-bottom:0}
.grade .c{font-size:16px}
.grade .v{font-family:var(--figures);font-size:18px;font-variant-numeric:tabular-nums}
.grade .g{color:var(--muted);font-size:14px;margin-left:6px}
.changed{background:var(--raised);border-left:3px solid var(--now);border-radius:0 4px 4px 0;
         padding:12px 15px;margin-bottom:20px}
.changed .row{padding:4px 0;font-size:15px}
.changed .tag{color:var(--now);font-size:13px;margin-right:7px}
.changed .tag.moved{color:var(--due)}
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


COURSE_VARS = [f"var(--c{i})" for i in range(7)]


def colour_map(schedule):
    """Stable colour per course, assigned by position in the timetable.

    Position rather than a hash: it keeps neighbouring courses visually apart
    instead of relying on luck, and it never changes between renders.
    """
    return {c["code"]: COURSE_VARS[i % len(COURSE_VARS)]
            for i, c in enumerate(schedule["courses"])}


def dot(course, colours):
    colour = colours.get(course)
    return f'<span class="dot" style="background:{colour}"></span>' if colour else ""


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


def _slot(meeting, now, today=None, colours=None):
    """`now` positions the meeting in its own day; `today` is the real date.

    They differ in the look-ahead rail, which fakes `now` to the start of the day
    it is drawing. A start-date note is only worth showing while the course has
    not started yet — otherwise "does not meet before Sept 11" turns up in
    November.
    """
    state = meeting.status(now)
    course = meeting.course
    today = today or now.date()
    site = ""
    for entry in coursesite.entries_for(meeting.day, course["code"]):
        if entry["kind"] in ("class", "lab"):
            label = esc(entry["label"])
            site = (f'<div class="prep"><a href="{esc(entry["url"])}">{label}</a></div>'
                    if entry["url"] else f'<div class="prep">{label}</div>')
            break
    flag = ""
    if course.get("note"):
        starts_on = course.get("starts_on")
        if not starts_on or date.fromisoformat(starts_on) >= today:
            flag = f'<div class="flag">{esc(course["note"])}</div>'
    tint = colours.get(course["code"]) if colours else None
    accent = f' style="border-left:3px solid {tint};padding-left:19px;margin-left:-22px"' if tint and state != "current" else ""
    return f"""<div class="slot {state}"{accent}>
  <div class="when">{meeting.start.strftime('%-I:%M')}<span class="end">{meeting.end.strftime('%-I:%M %p').lower()}</span></div>
  <div class="code">{esc(course['code'])}</div>
  <div class="title">{esc(course['title'])}</div>
  <div class="where">{esc(course['room'])}</div>
  <div class="who">{esc(course['instructor'])}</div>
  {site}
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


def render_ahead(schedule, now, tz, colours=None):
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
    slots = "\n".join(_slot(m, m.start.replace(hour=0, minute=1), today=now.date(), colours=colours)
                       for m in meetings)
    html_out = f'''<section class="ahead"><h2>{esc(label)} &mdash; {esc(day.strftime("%B %-d"))}</h2>
  <div class="rail">{slots}</div></section>'''
    return html_out, day


def _gap(minutes):
    if minutes < 60:
        return f"in {minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"in {hours}h {rest:02d}m" if rest else f"in {hours}h"
    return f"in {hours // 24}d {hours % 24}h"


def render_hero(schedule, now, tz, colours):
    """The one line worth reading from across the room: where to be, and when."""
    current, nxt = timetable.current_and_next(schedule, now, tz)
    if current is not None:
        left = int((current.end - now).total_seconds() // 60)
        return (f'<div class="hero"><span class="lead">now</span>'
                f'<span class="what">{dot(current.code, colours)}{esc(current.code)}</span>'
                f'<span class="meta">{esc(current.course["room"])} &middot; '
                f'until {current.end.strftime("%-I:%M %p").lower()}</span>'
                f'<span class="count">{left} min left</span></div>')
    if nxt is not None:
        when = nxt.start.strftime("%-I:%M %p").lower()
        if nxt.day != now.date():
            when = f'{nxt.start.strftime("%A")} {when}'
        return (f'<div class="hero"><span class="lead">next</span>'
                f'<span class="what">{dot(nxt.code, colours)}{esc(nxt.code)}</span>'
                f'<span class="meta">{esc(nxt.course["room"])} &middot; {esc(when)}</span>'
                f'<span class="count">{esc(_gap(nxt.minutes_until(now)))}</span></div>')
    reason = timetable.no_class_reason(schedule, now.date()) or "No more classes scheduled"
    return (f'<div class="hero idle"><span class="lead">today</span>'
            f'<span class="what">{esc(reason)}</span></div>')


def render_day(schedule, now, tz, colours=None):
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
        parts.append(_slot(meeting, now, colours=colours))
    if not placed:
        parts.append(_nowline(now, current, None))
    return "\n".join(parts)


def _day_heading(due_local, now):
    if due_local.date() == now.date():
        return "Today"
    if due_local.date() == (now + timedelta(days=1)).date():
        return "Tomorrow"
    if (due_local.date() - now.date()).days < 7:
        return due_local.strftime("%A")
    return due_local.strftime("%A %-d %B")


def render_tasks(rows, now, tz, limit=12, colours=None):
    """Grouped by the day it is due — a flat list of twelve has no shape."""
    colours = colours or {}
    out, heading = [], None
    for row in rows[:limit]:
        due = parse_utc(row["due_utc"])
        if due is None:
            continue
        local = due.astimezone(tz)
        group = _day_heading(local, now)
        if group != heading:
            heading = group
            late = " late" if local < now else ""
            out.append(f'<div class="daygroup{late}">{esc(group)}</div>')
        kind = row["kind"]
        kind_html = f' <span class="kind">{esc(kind)}</span>' if kind not in ("assignment",) else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        out.append(f"""<div class="task {urgency(local, now)}">
  <div class="due">{esc(due_label(local, now))}</div>
  <div><div class="what">{title}{kind_html}</div>
       <div class="course">{dot(row['course'], colours)}{esc(row['course'])}</div></div>
  <form method="post" action="/done">
    <input type="hidden" name="id" value="{esc(row['id'])}">
    <button class="tick" type="submit" title="Mark done">&#10003;</button>
  </form>
</div>""")
    return "\n".join(out)


def render_week(schedule, now, tz, days=6, skip=(), colours=None):
    rows = []
    for day, meetings in timetable.week_ahead(schedule, now, tz, days):
        if day in skip:
            continue
        if meetings:
            inner = " ".join(
                f'<span>{dot(m.code, colours or {})}{esc(m.code)} '
                f'<span class="at">{m.start.strftime("%-I:%M")}</span></span>'
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


def render_grades(rows):
    out = []
    for row in rows or []:
        score = row.get("score")
        value = f"{score:g}%" if isinstance(score, (int, float)) else "&mdash;"
        letter = f'<span class="g">{esc(row.get("grade"))}</span>' if row.get("grade") else ""
        out.append(f'<div class="grade"><div class="c">{esc(row.get("course"))}</div>'
                   f'<div class="v">{value}{letter}</div></div>')
    return "\n".join(out)


def render_changed(rows, now, tz, limit=5):
    """New work, and deadlines that moved since you last looked."""
    out = []
    for row in (rows or [])[:limit]:
        due = parse_utc(row["due_utc"])
        when = due_label(due.astimezone(tz), now) if due else "no date"
        if row["is_new"]:
            tag = '<span class="tag">new</span>'
        else:
            prev = parse_utc(row["prev_due_utc"])
            was = f' (was {due_label(prev.astimezone(tz), now)})' if prev else ""
            tag = f'<span class="tag moved">moved{esc(was)}</span>'
        out.append(f'<div class="row">{tag}{esc(row["title"])} &mdash; '
                   f'{esc(when)} <span style="color:var(--muted)">{esc(row["course"])}</span></div>')
    return "\n".join(out)


def render_appointments(rows, now, tz, limit=6):
    out = []
    for row in (rows or [])[:limit]:
        when = parse_utc(row["due_utc"])
        if not when:
            continue
        local = when.astimezone(tz)
        where = f' <span class="course">{esc(row["body"])}</span>' if row["body"] else ""
        out.append(f'<div class="task"><div class="due">{esc(due_label(local, now))}</div>'
                   f'<div><div class="what">{esc(row["title"])}</div>'
                   f'<div class="course">{esc(row["course"])}{where}</div></div></div>')
    return "\n".join(out)


def page(schedule, now, tz, tasks, anns, sync_note, canvas_ready, refresh=60, mails="",
         grades="", changed="", appts=""):
    colours = colour_map(schedule)
    ahead_html, ahead_day = render_ahead(schedule, now, tz, colours)
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
    appt_block = ""
    if appts.strip():
        appt_block = f'<div class="week"><h2>Calendar</h2>{appts}</div>'
    grade_block = ""
    if grades.strip():
        grade_block = f'<div class="week"><h2>Grades</h2>{grades}</div>'
    changed_block = f'<div class="changed">{changed}</div>' if changed.strip() else "" 

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#16182A">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="schoolboard">
<link rel="manifest" href="/manifest.webmanifest">
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

{render_hero(schedule, now, tz, colours)}

<div class="columns">
  <section>
    <h2>Today</h2>
    <div class="rail">{render_day(schedule, now, tz, colours)}</div>
    {ahead_html}
    <div class="week">
      <h2>Rest of the week</h2>
      {render_week(schedule, now, tz, skip=skip, colours=colours)}
      {online_html}
    </div>
  </section>
  <section>
    <h2>Due soon</h2>
    {changed_block}
    {work}
    {appt_block}
    {ann_block}
    {mail_block}
    {grade_block}
  </section>
</div>

<footer>
  <span>{esc(sync_note)}</span>
  <span>times in {esc(now.strftime('%Z'))} · schedule from Student Hub</span>
</footer>
</body></html>"""
