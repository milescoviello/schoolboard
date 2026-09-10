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
/* ---------------------------------------------------------------------------
   Two surfaces, one layout: a phone held in one hand, and a screen read from
   across a dorm room. Mobile-first single column; the desktop view widens the
   same spine rather than inventing a second, unrelated arrangement.

   Light and dark both ship, chosen by the device, because this gets looked at
   in a bright room at 8am and a dark one at midnight.

   Constraint throughout: Firefox on a 2009 GeForce 9400. No JS, no webfonts,
   no shadows, no gradients, no transforms. Flat colour is free.
--------------------------------------------------------------------------- */
:root{
  --ground:#0F1418; --raised:#161D22; --line:#25313A; --edge:#1B242B;
  --ink:#EBF1F3; --muted:#8DA2AC;
  --now:#2ED6A1; --due:#F0A03C; --late:#FF6E72;
  --c0:#7FB2FF; --c1:#C79BF5; --c2:#F58FBF; --c3:#8FD46B;
  --c4:#5FD0DA; --c5:#E0A46F; --c6:#9EA9E8;
  --names:Cantarell,"Noto Sans","DejaVu Sans",sans-serif;
  --figures:"DejaVu Sans",Cantarell,sans-serif;
  --pad:20px;
}
@media (prefers-color-scheme: light){
  :root{
    --ground:#FBFAF7; --raised:#FFFFFF; --line:#E2DED6; --edge:#EEEAE3;
    --ink:#131A1E; --muted:#5E6E76;
    --now:#0E9E76; --due:#B4711A; --late:#C7343B;
    --c0:#2F6DD0; --c1:#7B45C0; --c2:#C0407F; --c3:#3F8B2A;
    --c4:#12808C; --c5:#9A5A20; --c6:#4A56B0;
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--ground);color:var(--ink);font-family:var(--names);
     font-size:17px;line-height:1.45;padding:0 var(--pad) 56px;
     -moz-osx-font-smoothing:grayscale}
a{color:inherit;text-decoration:none}
a:focus-visible,button:focus-visible{outline:2px solid var(--now);outline-offset:3px}
.num{font-family:var(--figures);font-variant-numeric:tabular-nums}

/* --- masthead --- */
header{padding:22px 0 14px}
.daylabel{font-size:31px;font-weight:700;letter-spacing:-.02em;line-height:1.05}
.subhead{color:var(--muted);font-size:15px;margin-top:3px}
.clock{font-family:var(--figures);font-size:31px;font-variant-numeric:tabular-nums;
       letter-spacing:-.01em;line-height:1}
.place{color:var(--muted);font-size:13px;margin-top:2px}
.masthead{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.masthead .right{text-align:right;flex:none}

/* --- the now/next band: the one thing readable across a room --- */
.band{background:var(--raised);border:1px solid var(--line);border-radius:14px;
      padding:15px 17px;margin-bottom:22px}
.band .lead{font-size:12px;color:var(--muted);letter-spacing:.09em}
.band .headline{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-top:4px}
.band .code{font-size:27px;font-weight:700;letter-spacing:-.02em}
.band .where{font-size:16px;color:var(--muted)}
.band .count{margin-top:6px;font-family:var(--figures);font-size:16px;color:var(--now)}
.band.idle .code{font-size:19px;font-weight:400;color:var(--muted)}

h2{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.09em;
   padding-bottom:8px;margin-bottom:4px;border-bottom:1px solid var(--line)}
section{margin-bottom:30px}

/* --- today, as a spine --- */
.rail{position:relative}
.slot{position:relative;display:grid;grid-template-columns:68px minmax(0,1fr);
      gap:14px;padding:14px 0;border-bottom:1px solid var(--edge)}
.slot:last-child{border-bottom:0}
.slot .when{font-family:var(--figures);font-variant-numeric:tabular-nums;
            font-size:17px;line-height:1.2;padding-top:2px}
.slot .when .end{display:block;font-size:13px;color:var(--muted);margin-top:2px;white-space:nowrap}
.slot .body{border-left:3px solid var(--edge);padding-left:14px}
.slot .code{font-size:21px;font-weight:700;letter-spacing:-.01em}
.slot .title{color:var(--muted);font-size:15px}
.slot .where{margin-top:5px;font-size:16px}
.slot .who{color:var(--muted);font-size:14px}
.slot .prep{margin-top:6px;font-size:15px}
.slot .prep a{color:var(--now);border-bottom:1px solid currentColor;padding-bottom:1px}
.slot .flag{margin-top:6px;font-size:14px;color:var(--due)}
.slot.past{opacity:.45}
.slot.current .body{border-left-color:var(--now)}
.slot.current .code{color:var(--now)}

.nowline{display:grid;grid-template-columns:68px minmax(0,1fr);gap:14px;
         align-items:center;padding:7px 0}
.nowline .t{font-family:var(--figures);font-size:14px;color:var(--now);
            font-variant-numeric:tabular-nums}
.nowline .bar{display:flex;align-items:center;gap:9px;color:var(--now);font-size:14px}
.nowline .bar::before{content:"";flex:none;width:8px;height:8px;border-radius:50%;
                      background:var(--now)}
.empty{color:var(--muted);padding:14px 0;font-size:16px}

/* --- work --- */
.daygroup{font-size:12px;color:var(--muted);letter-spacing:.06em;
          padding:18px 0 6px;border-bottom:1px solid var(--edge)}
.daygroup:first-child{padding-top:4px}
.daygroup.late{color:var(--late)}
.task{display:grid;grid-template-columns:minmax(0,1fr) 44px;gap:10px;
      align-items:center;padding:12px 0;border-bottom:1px solid var(--edge)}
.task:last-child{border-bottom:0}
.task .what{font-size:16px;line-height:1.35}
.task .kind{color:var(--muted)}
.task .meta{color:var(--muted);font-size:13px;margin-top:3px;
            display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.task .at{font-family:var(--figures);font-variant-numeric:tabular-nums}
.task.soon .at{color:var(--due)}
.task.late .at{color:var(--late)}
.tick{border:1px solid var(--line);background:none;color:var(--muted);
      width:42px;height:42px;border-radius:11px;font-size:19px;line-height:1;
      cursor:pointer;font-family:inherit}
.tick:hover{color:var(--now);border-color:var(--now)}

.dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:none}
.notice{background:var(--raised);border:1px solid var(--line);border-left:3px solid var(--due);
        padding:14px 16px;border-radius:12px;font-size:15px;line-height:1.5}
.notice code{font-family:var(--figures);background:var(--ground);padding:1px 6px;
             border-radius:5px;font-size:13px}
.changed{background:var(--raised);border:1px solid var(--line);border-radius:12px;
         padding:12px 15px;margin-bottom:14px}
.changed .row{padding:4px 0;font-size:15px}
.changed .tag{color:var(--now);font-size:12px;letter-spacing:.06em;margin-right:8px}
.changed .tag.moved{color:var(--due)}

/* --- week --- */
.day{display:grid;grid-template-columns:58px minmax(0,1fr);gap:12px;
     padding:11px 0;border-bottom:1px solid var(--edge);align-items:baseline}
.day:last-child{border-bottom:0}
.day .dname{font-family:var(--figures);font-size:14px;color:var(--muted)}
.day .list{font-size:15px;display:flex;flex-wrap:wrap;gap:6px 16px}
.day .list > span{display:inline-flex;align-items:center;gap:7px;white-space:nowrap}
.day .at{color:var(--muted);font-family:var(--figures);font-size:13px}
.day .none{color:var(--muted);opacity:.65}
.day .holiday{color:var(--due)}

/* --- lists --- */
.row{padding:11px 0;border-bottom:1px solid var(--edge)}
.row:last-child{border-bottom:0}
.row .head{font-size:15px;line-height:1.4}
.row .meta{color:var(--muted);font-size:13px;margin-top:3px;
           display:flex;align-items:center;gap:7px}
.row.unread .head{font-weight:700}
.row.read{opacity:.62}
.grade{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
       padding:10px 0;border-bottom:1px solid var(--edge)}
.grade:last-child{border-bottom:0}
.grade .v{font-family:var(--figures);font-size:17px;font-variant-numeric:tabular-nums}
.grade .g{color:var(--muted);font-size:13px;margin-left:6px}

footer{color:var(--muted);font-size:12px;padding-top:16px;border-top:1px solid var(--line);
       display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap}

/* --- login --- */
.gate{max-width:340px;margin:16vh auto 0}
.gate h1{font-size:26px;font-weight:700;letter-spacing:-.02em}
.gate p{color:var(--muted);font-size:15px;margin:6px 0 22px}
.gate input{width:100%;font:inherit;font-size:17px;padding:14px 15px;
            border:1px solid var(--line);border-radius:12px;
            background:var(--raised);color:var(--ink)}
.gate input:focus{outline:2px solid var(--now);outline-offset:1px}
.gate button{width:100%;margin-top:12px;font:inherit;font-size:17px;font-weight:700;
             padding:14px;border:0;border-radius:12px;background:var(--now);
             color:var(--ground);cursor:pointer}
.gate .err{color:var(--late);font-size:15px;margin-bottom:14px}

/* --- desktop: widen the same spine, do not invent a new layout --- */
@media (min-width:960px){
  :root{--pad:38px}
  body{font-size:17px}
  .daylabel{font-size:42px}
  .clock{font-size:38px}
  .band{padding:18px 22px}
  .band .code{font-size:32px}
  .columns{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
           gap:44px;align-items:start}
  .slot{grid-template-columns:74px minmax(0,1fr)}
  .nowline{grid-template-columns:74px minmax(0,1fr)}
  .slot .code{font-size:24px}
}
@media (min-width:1400px){ body{max-width:1440px;margin:0 auto} }
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


COURSE_VARS = [f"var(--c{i})" for i in range(7)]


def colour_map(schedule):
    """Stable colour per course, assigned by position in the timetable.

    Position rather than a hash: neighbouring courses stay visually apart by
    construction, and the mapping never shifts between renders.
    """
    return {c["code"]: COURSE_VARS[i % len(COURSE_VARS)]
            for i, c in enumerate(schedule["courses"])}


def dot(course, colours):
    colour = (colours or {}).get(course)
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
    """Short, human, and never ambiguous about which day."""
    delta = due - now
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        days = (now.date() - due.date()).days
        if days == 0:
            return f"{-mins // 60}h late" if mins < -60 else f"{-mins}m late"
        return f"{days}d late"
    clock = due.strftime("%-I:%M %p").lower()
    if due.date() == now.date():
        return clock
    if due.date() == (now + timedelta(days=1)).date():
        return clock
    if delta.days < 7:
        return f"{due.strftime('%a')} {clock}"
    return due.strftime("%b %-d")


def urgency(due, now):
    if due < now:
        return "late"
    return "soon" if (due - now) < timedelta(hours=48) else ""


def _gap(minutes):
    if minutes < 60:
        return f"in {minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"in {hours}h {rest:02d}m" if rest else f"in {hours}h"
    return f"in {hours // 24}d {hours % 24}h"


def _slot(meeting, now, today=None, colours=None):
    """`now` places the meeting within its own day; `today` is the real date.

    They differ in the look-ahead rail, which fakes `now` to the day it draws. A
    start-date note is only worth showing before the course has started, or
    "does not meet before Sept 11" turns up in November.
    """
    state = meeting.status(now)
    course = meeting.course
    today = today or now.date()
    colours = colours or {}
    tint = colours.get(course["code"])

    flag = ""
    if course.get("note"):
        starts_on = course.get("starts_on")
        if not starts_on or date.fromisoformat(starts_on) >= today:
            flag = f'<div class="flag">{esc(course["note"])}</div>'

    prep = ""
    for entry in coursesite.entries_for(meeting.day, course["code"]):
        if entry["kind"] in ("class", "lab"):
            label = esc(entry["label"])
            prep = (f'<div class="prep"><a href="{esc(entry["url"])}">{label}</a></div>'
                    if entry["url"] else f'<div class="prep">{label}</div>')
            break

    edge = f' style="border-left-color:{tint}"' if tint and state != "current" else ""
    return f"""<div class="slot {state}">
  <div class="when num">{meeting.start.strftime('%-I:%M')}<span class="end">{meeting.end.strftime('%-I:%M %p').lower()}</span></div>
  <div class="body"{edge}>
    <div class="code">{esc(course['code'])}</div>
    <div class="title">{esc(course['title'])}</div>
    <div class="where">{esc(course['room'])}</div>
    <div class="who">{esc(course['instructor'])}</div>
    {prep}
    {flag}
  </div>
</div>"""


def _nowline(now, current, nxt):
    if current is not None:
        left = int((current.end - now).total_seconds() // 60)
        text = f"{left} min left" if left > 0 else "ending now"
    elif nxt is not None and nxt.day == now.date():
        gap = nxt.minutes_until(now)
        text = f"{_gap(gap)} until {nxt.code}"
    else:
        text = "nothing else today"
    return (f'<div class="nowline"><div class="t">{now.strftime("%-I:%M")}</div>'
            f'<div class="bar">{esc(text)}</div></div>')


def render_band(schedule, now, tz, colours):
    """The line worth reading from across the room: where to be, and when."""
    current, nxt = timetable.current_and_next(schedule, now, tz)
    if current is not None:
        left = int((current.end - now).total_seconds() // 60)
        return f"""<div class="band">
  <div class="lead">happening now</div>
  <div class="headline">{dot(current.code, colours)}<span class="code">{esc(current.code)}</span>
    <span class="where">{esc(current.course['room'])}</span></div>
  <div class="count num">{left} min left &middot; until {current.end.strftime('%-I:%M %p').lower()}</div>
</div>"""
    if nxt is not None:
        when = nxt.start.strftime("%-I:%M %p").lower()
        if nxt.day != now.date():
            when = f"{nxt.start.strftime('%A')} {when}"
        return f"""<div class="band">
  <div class="lead">up next</div>
  <div class="headline">{dot(nxt.code, colours)}<span class="code">{esc(nxt.code)}</span>
    <span class="where">{esc(nxt.course['room'])}</span></div>
  <div class="count num">{esc(_gap(nxt.minutes_until(now)))} &middot; {esc(when)}</div>
</div>"""
    reason = timetable.no_class_reason(schedule, now.date()) or "No more classes scheduled"
    return (f'<div class="band idle"><div class="lead">today</div>'
            f'<div class="headline"><span class="code">{esc(reason)}</span></div></div>')


def next_teaching_day(schedule, now, tz, limit=7):
    for offset in range(1, limit + 1):
        day = now.date() + timedelta(days=offset)
        meetings = timetable.meetings_on(schedule, day, tz)
        if meetings:
            return day, meetings
    return None, []


def render_ahead(schedule, now, tz, colours=None):
    """Only once today is spent; otherwise it is noise.

    Returns (html, day_shown) so the week list below can avoid repeating it.
    """
    today = timetable.meetings_on(schedule, now.date(), tz)
    if any(m.status(now) != "past" for m in today):
        return "", None
    day, meetings = next_teaching_day(schedule, now, tz)
    if not meetings:
        return "", None
    label = "Tomorrow" if day == (now.date() + timedelta(days=1)) else day.strftime("%A")
    slots = "\n".join(_slot(m, m.start.replace(hour=0, minute=1), today=now.date(),
                            colours=colours) for m in meetings)
    return (f'<section><h2>{esc(label.upper())} &middot; {esc(day.strftime("%B %-d"))}</h2>'
            f'<div class="rail">{slots}</div></section>'), day


def render_day(schedule, now, tz, colours=None):
    meetings = timetable.meetings_on(schedule, now.date(), tz)
    current, nxt = timetable.current_and_next(schedule, now, tz)
    if not meetings:
        reason = timetable.no_class_reason(schedule, now.date())
        text = f"{reason} &mdash; no classes." if reason else "No classes today."
        return (f'<div class="empty">{text}</div>'
                + _nowline(now, None, nxt if nxt and nxt.day == now.date() else None))
    parts, placed = [], False
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
        return "TODAY"
    if due_local.date() == (now + timedelta(days=1)).date():
        return "TOMORROW"
    if (due_local.date() - now.date()).days < 7:
        return due_local.strftime("%A").upper()
    return due_local.strftime("%A %-d %B").upper()


def render_tasks(rows, now, tz, limit=14, colours=None):
    """Grouped by the day it is due — a flat list of fourteen has no shape."""
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
        kind_html = f' <span class="kind">{esc(kind)}</span>' if kind != "assignment" else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        out.append(f"""<div class="task {urgency(local, now)}">
  <div>
    <div class="what">{title}{kind_html}</div>
    <div class="meta"><span class="at">{esc(due_label(local, now))}</span>
      {dot(row['course'], colours)}<span>{esc(row['course'])}</span></div>
  </div>
  <form method="post" action="/done">
    <input type="hidden" name="id" value="{esc(row['id'])}">
    <button class="tick" type="submit" title="Mark done" aria-label="Mark done">&#10003;</button>
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
                f'<span>{dot(m.code, colours)}{esc(m.code)} '
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
        out.append(f'<div class="row"><div class="head">{title}</div>'
                   f'<div class="meta">{esc(row["course"])} &middot; {esc(when)}</div></div>')
    return "\n".join(out)


def render_mail(rows, now, tz, limit=5, colours=None):
    """Course mail only. Unread first — that is the actionable set."""
    out = []
    for row in rows[:limit]:
        received = parse_utc(row["due_utc"])
        when = received.astimezone(tz).strftime("%b %-d") if received else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        state = "read" if row["done"] else "unread"
        out.append(f'<div class="row {state}"><div class="head">{title}</div>'
                   f'<div class="meta">{dot(row["course"], colours)}{esc(row["course"])}'
                   f' &middot; {esc(when)}</div></div>')
    return "\n".join(out)


def render_grades(rows, colours=None):
    out = []
    for row in rows or []:
        score = row.get("score")
        value = f"{score:g}%" if isinstance(score, (int, float)) else "&mdash;"
        letter = f'<span class="g">{esc(row.get("grade"))}</span>' if row.get("grade") else ""
        out.append(f'<div class="grade"><div class="meta">{dot(row.get("course"), colours)}'
                   f'{esc(row.get("course"))}</div>'
                   f'<div class="v">{value}{letter}</div></div>')
    return "\n".join(out)


def render_changed(rows, now, tz, limit=5):
    out = []
    for row in (rows or [])[:limit]:
        due = parse_utc(row["due_utc"])
        when = due_label(due.astimezone(tz), now) if due else "no date"
        if row["is_new"]:
            tag = '<span class="tag">NEW</span>'
        else:
            prev = parse_utc(row["prev_due_utc"])
            was = f' (was {due_label(prev.astimezone(tz), now)})' if prev else ""
            tag = f'<span class="tag moved">MOVED{esc(was)}</span>'
        out.append(f'<div class="row">{tag}{esc(row["title"])} &mdash; {esc(when)}</div>')
    return "\n".join(out)


def render_appointments(rows, now, tz, limit=6, colours=None):
    out = []
    for row in (rows or [])[:limit]:
        when = parse_utc(row["due_utc"])
        if not when:
            continue
        local = when.astimezone(tz)
        where = f' &middot; {esc(row["body"])}' if row["body"] else ""
        out.append(f'<div class="row"><div class="head">{esc(row["title"])}</div>'
                   f'<div class="meta"><span class="at">{esc(due_label(local, now))}</span>'
                   f'{esc(row["course"])}{where}</div></div>')
    return "\n".join(out)


def _shell(title, inner, refresh=None):
    meta_refresh = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="schoolboard">
<link rel="manifest" href="/manifest.webmanifest">
{meta_refresh}
<title>{esc(title)}</title>
<style>{CSS}</style>
</head><body>
{inner}
</body></html>"""


def login_page(error=None, retry_after=None):
    """Own front door rather than a third-party gate."""
    if retry_after:
        message = (f'<div class="err">Too many attempts. Try again in '
                   f'{int(retry_after // 60) + 1} min.</div>')
        form = ""
    else:
        message = f'<div class="err">{esc(error)}</div>' if error else ""
        form = """<form method="post" action="/login">
  <input type="password" name="password" placeholder="Password" autofocus
         autocomplete="current-password" aria-label="Password">
  <button type="submit">Sign in</button>
</form>"""
    return _shell("schoolboard — Sign in", f"""<div class="gate">
  <h1>schoolboard</h1>
  <p>Classes, coursework and course mail.</p>
  {message}
  {form}
</div>""")


def page(schedule, now, tz, tasks, anns, sync_note, canvas_ready, refresh=60, mails="",
         grades="", changed="", appts=""):
    colours = colour_map(schedule)
    ahead_html, ahead_day = render_ahead(schedule, now, tz, colours)
    skip = (ahead_day,) if ahead_day else ()

    online = timetable.online_courses(schedule)
    online_html = ""
    if online:
        names = ", ".join(esc(c["code"]) for c in online)
        online_html = (f'<div class="day"><div class="dname">any</div>'
                       f'<div class="list none">{names} &mdash; online, no fixed meeting</div></div>')

    if not canvas_ready:
        work = ('<div class="notice">Canvas isn\'t connected yet, so nothing here knows about '
                'your assignments. Create a token at <b>Canvas &rarr; Account &rarr; Settings '
                '&rarr; New Access Token</b>, then run <code>schoolboard connect</code></div>')
    elif tasks.strip():
        work = tasks
    else:
        work = '<div class="empty">Nothing due in the next stretch.</div>'

    def block(heading, content):
        return f'<section><h2>{heading}</h2>{content}</section>' if content.strip() else ""

    changed_block = f'<div class="changed">{changed}</div>' if changed.strip() else ""

    return _shell(f"{now.strftime('%A')} — schoolboard", f"""<header>
  <div class="masthead">
    <div>
      <div class="daylabel">{now.strftime('%A')}</div>
      <div class="subhead">{now.strftime('%B %-d, %Y')} &middot; {esc(schedule.get('term',''))}</div>
    </div>
    <div class="right">
      <div class="clock">{now.strftime('%-I:%M')}<span style="font-size:.5em"> {now.strftime('%p').lower()}</span></div>
      <div class="place">{esc(schedule.get('campus',''))}</div>
    </div>
  </div>
</header>

{render_band(schedule, now, tz, colours)}

<div class="columns">
  <div>
    <section>
      <h2>TODAY</h2>
      <div class="rail">{render_day(schedule, now, tz, colours)}</div>
    </section>
    {ahead_html}
    <section>
      <h2>REST OF THE WEEK</h2>
      {render_week(schedule, now, tz, skip=skip, colours=colours)}
      {online_html}
    </section>
  </div>
  <div>
    <section>
      <h2>DUE SOON</h2>
      {changed_block}
      {work}
    </section>
    {block("CALENDAR", appts)}
    {block("ANNOUNCEMENTS", anns)}
    {block("COURSE MAIL", mails)}
    {block("GRADES", grades)}
  </div>
</div>

<footer>
  <span>{esc(sync_note)}</span>
  <span>{esc(now.strftime('%Z'))}</span>
</footer>""", refresh=refresh)
