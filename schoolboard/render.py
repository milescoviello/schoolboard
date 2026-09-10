"""HTML rendering.

Built around the three things that actually exist in his day, in order of how
often he needs them:

  1. the next thing  — one sentence, the largest element on the page
  2. the week        — a timetable, drawn as a grid, because that is what a
                       timetable is
  3. the term        — progress through a bounded thing with an end

Everything else is subordinate to those. Earlier versions stacked information in
rows under tracked-out capital labels, which is a dashboard kit rather than a
design, and read as generic.

Light only, and deliberately not white: the page ground is a soft cool grey with
white cards on top. Course colour carries all the chroma; the interface chrome
stays neutral so colour always means something.

Motion is one orchestrated page-load — sections rise and fade, staggered — plus
a pulse on the live indicator and a press on the tick. Nothing else moves.
Everything animated is opacity or transform only, so the 2009 GeForce 9400 in
the Mac mini can composite it, and all of it is disabled under
prefers-reduced-motion.

Type leads with -apple-system so it renders natively on his phone, which is the
primary device now, and falls back to Cantarell on the mini.

Every time is computed in campus time, never the host clock, which is wrong.
"""
import html
from datetime import date, datetime, timedelta, timezone

from . import coursesite, timetable

CSS = r"""
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#F3F4F6; --card:#FFFFFF; --sunk:#F8F9FA;
  --ink:#191D24; --ink2:#464F5B; --mut:#8A929E;
  --line:#E7E9ED; --hair:#F0F1F4;
  --live:#2C6E63; --live-bg:#E4F0EE;
  --due:#A2621C; --late:#AC3646;
  --c0:#2F62C4; --c0b:#E9EEFB; --c1:#7042B8; --c1b:#F1EAFA;
  --c2:#AF3A72; --c2b:#FBEAF2; --c3:#3B7A31; --c3b:#EAF4E8;
  --c4:#12707A; --c4b:#E5F1F2; --c5:#8C5A22; --c5b:#F7EFE4;
  --c6:#454FA0; --c6b:#EBEDF8;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Cantarell,"Noto Sans","DejaVu Sans",sans-serif;
  --r:16px; --r2:11px;
  --shadow:0 1px 2px rgba(20,26,38,.05), 0 6px 18px -6px rgba(20,26,38,.10);
  --pad:18px;
}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--font);font-size:16px;
     line-height:1.45;padding:0 var(--pad) 64px;
     -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
.wrap{max-width:1180px;margin:0 auto}
a{color:inherit;text-decoration:none}
button{font:inherit}
a:focus-visible,button:focus-visible{outline:2px solid var(--live);outline-offset:3px;
                                     border-radius:6px}
.n{font-variant-numeric:tabular-nums}

/* motion: one orchestrated load, nothing else */
@keyframes rise{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
@keyframes glow{0%,100%{opacity:1}50%{opacity:.35}}
.rv{animation:rise .44s cubic-bezier(.22,.72,.28,1) both}
.d1{animation-delay:.06s}.d2{animation-delay:.12s}.d3{animation-delay:.18s}
.d4{animation-delay:.24s}.d5{animation-delay:.30s}

/* --- top bar --- */
.bar{display:flex;justify-content:space-between;align-items:center;gap:14px;
     padding:16px 0 14px}
.brand{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:600;
       letter-spacing:-.01em}
.pip{width:9px;height:9px;border-radius:50%;background:var(--live);flex:none;
     animation:glow 3.4s ease-in-out infinite}
.stamp{font-size:14px;color:var(--mut)}
.stamp b{color:var(--ink2);font-weight:600}

/* --- hero: the next thing --- */
.hero{background:var(--card);border-radius:var(--r);padding:22px 24px 20px;
      box-shadow:var(--shadow)}
.hero .k{font-size:13px;color:var(--live);font-weight:600;letter-spacing:.01em}
.hero .row{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-top:7px}
.hero .code{font-size:36px;font-weight:700;letter-spacing:-.035em;line-height:1.02}
.hero .cd{margin-left:auto;font-size:17px;font-weight:600;color:var(--live);
          background:var(--live-bg);padding:5px 12px;border-radius:99px;white-space:nowrap}
.hero .ttl{font-size:16px;color:var(--ink2);margin-top:5px}
.hero .meta{font-size:15px;color:var(--mut);margin-top:9px}
.hero.idle .code{font-size:22px;font-weight:600;color:var(--ink2);letter-spacing:-.02em}
.prog{height:4px;background:var(--hair);border-radius:99px;margin-top:18px;overflow:hidden}
.prog i{display:block;height:100%;background:var(--live);border-radius:99px}

/* --- day chips --- */
.days{display:flex;gap:8px;margin:20px 0 4px;overflow-x:auto;padding-bottom:4px;
      scrollbar-width:none}
.days::-webkit-scrollbar{display:none}
.chip{flex:1 0 auto;min-width:60px;background:var(--card);border:1px solid var(--line);
      border-radius:var(--r2);padding:9px 6px 8px;text-align:center}
.chip .w{font-size:11px;color:var(--mut);letter-spacing:.02em}
.chip .n{display:block;font-size:19px;font-weight:600;margin-top:1px;letter-spacing:-.02em}
.chip .ld{display:flex;gap:3px;justify-content:center;margin-top:6px;height:4px}
.chip .ld i{width:4px;height:4px;border-radius:50%;background:var(--mut);opacity:.42}
.chip.today{background:var(--ink);border-color:var(--ink)}
.chip.today .w,.chip.today .n{color:var(--card)}
.chip.today .ld i{background:var(--card)}
.chip.off{opacity:.5}
.chip:hover{border-color:var(--ink2)}
.chip.today:hover{border-color:var(--ink)}

/* --- cards --- */
.cols{display:grid;grid-template-columns:1fr;gap:16px;margin-top:16px}
.card{background:var(--card);border-radius:var(--r);box-shadow:var(--shadow);
      padding:20px var(--pad2,22px)}
.ch{display:flex;justify-content:space-between;align-items:center;gap:12px;
    margin-bottom:14px}
.ch h2{font-size:17px;font-weight:650;letter-spacing:-.015em}
.ch .sub{font-size:13px;color:var(--mut)}
.nav{display:flex;gap:6px;align-items:center}
.nav a{border:1px solid var(--line);border-radius:8px;min-width:32px;height:32px;
       display:inline-flex;align-items:center;justify-content:center;color:var(--ink2);
       font-size:14px;padding:0 10px}
.nav a:hover{border-color:var(--ink2)}
.nav a.on{background:var(--ink);border-color:var(--ink);color:var(--card);font-weight:600}

/* --- timetable grid --- */
.tt{border:1px solid var(--line);border-radius:var(--r2);overflow:hidden}
.tth{display:grid;grid-template-columns:42px repeat(5,1fr);background:var(--sunk);
     border-bottom:1px solid var(--line)}
.tth div{padding:9px 4px;font-size:12px;text-align:center;color:var(--mut)}
.tth div b{display:block;font-size:15px;font-weight:600;color:var(--ink2);
           letter-spacing:-.02em;margin-top:1px}
.tth div.t0{background:var(--ink)}
.tth div.t0,.tth div.t0 b{color:var(--card)}
.ttg{display:grid;grid-template-columns:42px repeat(5,1fr);position:relative}
.hr{grid-column:1;font-size:11px;color:var(--mut);text-align:right;padding:1px 7px 0 0;
    border-top:1px solid var(--hair)}
.cl{border-top:1px solid var(--hair);border-left:1px solid var(--hair)}
.blk{margin:2px 3px;border-radius:8px;padding:6px 8px;overflow:hidden;min-width:0;
     border-left:3px solid currentColor;background:var(--bl,var(--sunk))}
.blk .c{font-size:13px;font-weight:650;letter-spacing:-.015em;white-space:nowrap;
        overflow:hidden;text-overflow:ellipsis}
.blk .r{font-size:11px;color:var(--ink2);opacity:.8;white-space:nowrap;overflow:hidden;
        text-overflow:ellipsis;margin-top:1px}
.blk.gone{opacity:.42}
.nb{grid-column:2/7;height:0;border-top:2px solid var(--late);z-index:4;position:relative}
.nb span{position:absolute;left:-38px;top:-8px;font-size:10px;color:var(--late);
         background:var(--card);padding:0 3px;font-weight:600}

/* --- day list (phone form of the week) --- */
.dl{display:none}
.dsec{margin-bottom:6px}
.dsec .h{display:flex;justify-content:space-between;align-items:baseline;
         padding:14px 0 8px;border-bottom:1px solid var(--hair)}
.dsec .h .d{font-size:16px;font-weight:650;letter-spacing:-.015em}
.dsec .h .s{font-size:12px;color:var(--mut)}
.dsec.today .h .d{color:var(--live)}
.ev{display:grid;grid-template-columns:60px minmax(0,1fr);gap:12px;padding:12px 0;
    border-bottom:1px solid var(--hair)}
.ev:last-child{border-bottom:0}
.ev .t{font-size:15px;font-weight:600;line-height:1.2;font-variant-numeric:tabular-nums}
.ev .t small{display:block;font-size:12px;color:var(--mut);font-weight:400;
             white-space:nowrap;margin-top:2px}
.ev .b{border-left:3px solid currentColor;padding-left:12px}
.ev .c{font-size:17px;font-weight:650;letter-spacing:-.015em;color:var(--ink)}
.ev .r{font-size:14px;color:var(--mut);margin-top:1px}
.ev .p{margin-top:5px;font-size:14px}
.ev .p a{color:var(--live);font-weight:500;border-bottom:1px solid currentColor}
.ev.gone{opacity:.45}

/* --- work --- */
.grp{font-size:13px;font-weight:600;color:var(--mut);padding:16px 0 7px}
.grp:first-child{padding-top:0}
.grp.late{color:var(--late)}
.it{display:grid;grid-template-columns:minmax(0,1fr) 38px;gap:12px;align-items:center;
    padding:11px 0;border-bottom:1px solid var(--hair)}
.it:last-child{border-bottom:0}
.it .w{font-size:15.5px;line-height:1.35}
.it .kd{color:var(--mut);font-size:13px}
.it .m{color:var(--mut);font-size:13px;margin-top:4px;display:flex;gap:8px;
       align-items:center;flex-wrap:wrap}
.it .at{font-variant-numeric:tabular-nums}
.it.soon .at{color:var(--due);font-weight:600}
.it.late .at{color:var(--late);font-weight:600}
.tick{width:34px;height:34px;border-radius:9px;border:1px solid var(--line);
      background:var(--card);color:var(--mut);font-size:15px;line-height:1;cursor:pointer;
      transition:transform .12s ease,border-color .12s ease,color .12s ease}
.tick:hover{color:var(--live);border-color:var(--live)}
.tick:active{transform:scale(.9)}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
.done .w{text-decoration:line-through;text-decoration-thickness:1px;color:var(--mut)}

.li{padding:11px 0;border-bottom:1px solid var(--hair)}
.li:last-child{border-bottom:0}
.li .h{font-size:15px;line-height:1.4}
.li .m{color:var(--mut);font-size:13px;margin-top:3px;display:flex;gap:7px;align-items:center}
.li.unread .h{font-weight:650}
.li.read{opacity:.62}
.gr{display:flex;justify-content:space-between;align-items:baseline;padding:10px 0;
    border-bottom:1px solid var(--hair)}
.gr:last-child{border-bottom:0}
.gr .v{font-size:17px;font-weight:650;font-variant-numeric:tabular-nums}
.gr .g{color:var(--mut);font-size:13px;margin-left:5px;font-weight:400}
.empty{color:var(--mut);font-size:15px;padding:6px 0 2px}
.more{color:var(--mut);font-size:13.5px;padding:14px 0 2px}
.note{border:1px solid var(--line);border-left:3px solid var(--due);border-radius:var(--r2);
      padding:14px 16px;font-size:15px;line-height:1.55;background:var(--sunk)}
.note code{background:var(--card);padding:2px 6px;border-radius:5px;font-size:13px;
           border:1px solid var(--line)}
.chg{background:var(--live-bg);border-radius:var(--r2);padding:12px 15px;margin-bottom:14px}
.chg .r{padding:3px 0;font-size:14px;color:var(--ink2)}
.chg .tg{color:var(--live);font-weight:650;margin-right:8px;font-size:13px}
.chg .tg.mv{color:var(--due)}

/* --- term progress --- */
.term{background:var(--card);border-radius:var(--r);box-shadow:var(--shadow);
      padding:18px 22px;margin-top:16px}
.term .top{display:flex;justify-content:space-between;align-items:baseline;
           font-size:13px;color:var(--mut);margin-bottom:11px}
.term .top b{color:var(--ink);font-size:15px;font-weight:650;letter-spacing:-.01em}
.tbar{display:flex;gap:2px;height:26px;align-items:flex-end}
.tw{flex:1;background:var(--hair);border-radius:3px;height:9px;position:relative}
.tw.p{background:#CBD2DA}
.tw.c{background:var(--live);height:20px}
.tw i{position:absolute;left:0;right:0;bottom:-15px;text-align:center;font-size:9px;
      color:var(--mut);font-style:normal}
.tw.c i{color:var(--live);font-weight:700}
.term .lg{display:flex;gap:16px;font-size:12px;color:var(--mut);margin-top:22px}

/* --- login --- */
.gate{max-width:330px;margin:14vh auto 0}
.gate .card{padding:28px 26px}
.gate h1{font-size:25px;font-weight:700;letter-spacing:-.03em;display:flex;
         align-items:center;gap:10px}
.gate p{color:var(--mut);font-size:15px;margin:7px 0 20px}
.gate input{width:100%;font:inherit;font-size:16px;padding:13px 14px;
            border:1px solid var(--line);border-radius:var(--r2);background:var(--sunk);
            color:var(--ink)}
.gate input:focus{outline:2px solid var(--live);outline-offset:1px;background:var(--card)}
.gate button{width:100%;margin-top:10px;font-size:16px;font-weight:650;padding:13px;
             border:0;border-radius:var(--r2);background:var(--ink);color:var(--card);
             cursor:pointer;transition:transform .12s ease,opacity .12s ease}
.gate button:hover{opacity:.9}
.gate button:active{transform:scale(.985)}
.gate .err{color:var(--late);font-size:14.5px;margin-bottom:12px}

footer{color:var(--mut);font-size:12px;padding:22px 2px 0;display:flex;
       justify-content:space-between;gap:12px;flex-wrap:wrap}

@media (max-width:860px){
  .tt{display:none}
  .dl{display:block}
  .hero .code{font-size:30px}
  .hero .cd{margin-left:0;font-size:15px}
  .card{--pad2:17px}
  .term{padding:16px 17px}
}
@media (min-width:861px){
  :root{--pad:28px}
  .cols{grid-template-columns:minmax(0,1fr) 336px;gap:18px;align-items:start}
  .cols .side{display:flex;flex-direction:column;gap:16px}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none !important;transition:none !important}
}
"""

GRID_START_MIN = 8 * 60
GRID_END_MIN = 17 * 60
SLOT = 15
COURSE = [("var(--c0)", "var(--c0b)"), ("var(--c1)", "var(--c1b)"),
          ("var(--c2)", "var(--c2b)"), ("var(--c3)", "var(--c3b)"),
          ("var(--c4)", "var(--c4b)"), ("var(--c5)", "var(--c5b)"),
          ("var(--c6)", "var(--c6b)")]


def colour_map(schedule):
    """Stable (ink, tint) per course, by position in the timetable rather than a
    hash, so neighbouring courses stay distinct and never shift between loads."""
    return {c["code"]: COURSE[i % len(COURSE)]
            for i, c in enumerate(schedule["courses"])}


def ink_of(course, colours):
    pair = (colours or {}).get(course)
    return pair[0] if pair else "var(--mut)"


def dot(course, colours):
    return f'<span class="dot" style="background:{ink_of(course, colours)}"></span>'


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
    delta = due - now
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        days = (now.date() - due.date()).days
        if days == 0:
            return f"{-mins // 60}h late" if mins < -60 else f"{-mins}m late"
        return f"{days}d late"
    clock = due.strftime("%-I:%M %p").lower()
    if due.date() == now.date() or due.date() == (now + timedelta(days=1)).date():
        return clock
    if delta.days < 7:
        return f"{due.strftime('%a')} {clock}"
    return due.strftime("%-d %b")


def urgency(due, now):
    if due < now:
        return "late"
    return "soon" if (due - now) < timedelta(hours=48) else ""


def gap_text(minutes):
    if minutes < 60:
        return f"in {minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"in {hours}h {rest:02d}m" if rest else f"in {hours}h"
    return f"in {hours // 24}d {hours % 24}h"


def monday_of(day):
    return day - timedelta(days=day.weekday())


# ------------------------------------------------------------------ pieces

def hero(schedule, now, tz, colours):
    """The largest element: what happens next, and how long you have."""
    current, nxt = timetable.current_and_next(schedule, now, tz)
    meetings = timetable.meetings_on(schedule, now.date(), tz)
    bar = ""
    if meetings:
        first = meetings[0].start
        last = meetings[-1].end
        total = (last - first).total_seconds()
        # Only while the teaching day is actually running. A bar pinned at
        # 100% all evening says nothing, and one at 0% all morning says less.
        if total > 0 and first <= now <= last:
            pct = max(0, min(100, ((now - first).total_seconds() / total) * 100))
            bar = f'<div class="prog"><i style="width:{pct:.0f}%"></i></div>'

    if current is not None:
        left = int((current.end - now).total_seconds() // 60)
        return f"""<div class="hero">
  <div class="k">Happening now</div>
  <div class="row"><span class="code">{esc(current.code)}</span>
    <span class="cd n">{left} min left</span></div>
  <div class="ttl">{esc(current.course['title'])}</div>
  <div class="meta">{esc(current.course['room'])} &nbsp;&mdash;&nbsp; ends {current.end.strftime('%-I:%M %p').lower()}</div>
  {bar}
</div>"""
    if nxt is not None:
        when = nxt.start.strftime("%-I:%M %p").lower()
        day_word = "today" if nxt.day == now.date() else nxt.start.strftime("%A")
        return f"""<div class="hero">
  <div class="k">Next up</div>
  <div class="row"><span class="code">{esc(nxt.code)}</span>
    <span class="cd n">{esc(gap_text(nxt.minutes_until(now)))}</span></div>
  <div class="ttl">{esc(nxt.course['title'])}</div>
  <div class="meta">{esc(nxt.course['room'])} &nbsp;&mdash;&nbsp; {esc(day_word)} at {esc(when)}</div>
  {bar}
</div>"""
    reason = timetable.no_class_reason(schedule, now.date()) or "No more classes today"
    return f"""<div class="hero idle">
  <div class="k">Today</div>
  <div class="row"><span class="code">{esc(reason)}</span></div>
</div>"""


def day_chips(schedule, monday, tz, now, workload):
    """The week as tappable days, each showing how much is owed."""
    out = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        classes = ["chip"]
        if day == now.date():
            classes.append("today")
        elif timetable.no_class_reason(schedule, day) or day.weekday() >= 5:
            classes.append("off")
        load = (workload.get(day.isoformat()) or (0, 0))[0]
        pips = "".join("<i></i>" for _ in range(min(load, 3)))
        out.append(f'<a class="{" ".join(classes)}" href="/?day={day.isoformat()}">'
                   f'<span class="w">{day.strftime("%a")}</span>'
                   f'<span class="n">{day.day}</span>'
                   f'<span class="ld">{pips}</span></a>')
    return f'<div class="days">{"".join(out)}</div>'


def _row(minutes):
    return int((minutes - GRID_START_MIN) // SLOT) + 1


def week_grid(schedule, monday, tz, colours, now):
    days = [monday + timedelta(days=i) for i in range(5)]
    head = ["<div></div>"]
    for day in days:
        cls = "t0" if day == now.date() else ""
        head.append(f'<div class="{cls}">{day.strftime("%a")}<b>{day.day}</b></div>')

    cells = []
    for hour in range(GRID_START_MIN // 60, GRID_END_MIN // 60):
        row = _row(hour * 60)
        label = datetime(2000, 1, 1, hour).strftime("%-I")
        cells.append(f'<div class="hr" style="grid-row:{row}/span 4">{label}</div>')
        for col in range(2, 7):
            cells.append(f'<div class="cl" style="grid-row:{row}/span 4;grid-column:{col}"></div>')

    for index, day in enumerate(days):
        for meeting in timetable.meetings_on(schedule, day, tz):
            start = meeting.start.hour * 60 + meeting.start.minute
            finish = meeting.end.hour * 60 + meeting.end.minute
            row = max(1, _row(start))
            span = max(2, _row(finish) - _row(start))
            pair = colours.get(meeting.code, ("var(--mut)", "var(--sunk)"))
            gone = " gone" if meeting.end < now else ""
            cells.append(
                f'<div class="blk{gone}" style="grid-row:{row}/span {span};'
                f'grid-column:{index + 2};color:{pair[0]};--bl:{pair[1]}">'
                f'<div class="c">{esc(meeting.code)}</div>'
                f'<div class="r">{esc(meeting.course["room"])}</div></div>')

    if monday <= now.date() <= monday + timedelta(days=4):
        minutes = now.hour * 60 + now.minute
        if GRID_START_MIN <= minutes <= GRID_END_MIN:
            cells.append(f'<div class="nb" style="grid-row:{_row(minutes)}">'
                         f'<span class="n">{now.strftime("%-I:%M")}</span></div>')

    rows = (GRID_END_MIN - GRID_START_MIN) // SLOT
    return (f'<div class="tt"><div class="tth">{"".join(head)}</div>'
            f'<div class="ttg" style="grid-template-rows:repeat({rows},15px)">'
            f'{"".join(cells)}</div></div>')


def day_list(schedule, monday, tz, colours, now):
    """The phone form of the same week — a different shape, not a squeezed grid."""
    out = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        meetings = timetable.meetings_on(schedule, day, tz)
        reason = timetable.no_class_reason(schedule, day)
        if not meetings and not reason and day.weekday() >= 5:
            continue
        cls = " today" if day == now.date() else ""
        if reason:
            note = reason
        elif meetings:
            note = f"{len(meetings)} class" + ("es" if len(meetings) != 1 else "")
        else:
            note = "clear"
        rows = []
        for meeting in meetings:
            pair = colours.get(meeting.code, ("var(--mut)", "var(--sunk)"))
            gone = " gone" if meeting.end < now else ""
            prep = ""
            for entry in coursesite.entries_for(day, meeting.code):
                if entry["kind"] in ("class", "lab") and entry["url"]:
                    prep = (f'<div class="p"><a href="{esc(entry["url"])}">'
                            f'{esc(entry["label"])}</a></div>')
                    break
            rows.append(
                f'<div class="ev{gone}" style="color:{pair[0]}">'
                f'<div class="t" style="color:var(--ink)">{meeting.start.strftime("%-I:%M")}'
                f'<small>{meeting.end.strftime("%-I:%M %p").lower()}</small></div>'
                f'<div class="b"><div class="c">{esc(meeting.code)}</div>'
                f'<div class="r">{esc(meeting.course["room"])} &middot; '
                f'{esc(meeting.course["instructor"])}</div>{prep}</div></div>')
        out.append(f'<div class="dsec{cls}"><div class="h">'
                   f'<span class="d">{day.strftime("%A")} {day.day}</span>'
                   f'<span class="s">{esc(note)}</span></div>{"".join(rows)}</div>')
    return "".join(out)


def term_progress(schedule, workload, today):
    """Progress through a bounded thing. Replaces the heat strip, which read as
    decoration: a week you cannot name is not information."""
    start, end = schedule.get("term_start"), schedule.get("term_end")
    if not (start and end):
        return ""
    first = monday_of(date.fromisoformat(start))
    last = date.fromisoformat(end)
    here = monday_of(today)
    weeks, cursor, index, current_no = [], first, 1, 1
    while cursor <= last and index <= 24:
        if cursor < here:
            cls, label = "tw p", ""
        elif cursor == here:
            cls, label = "tw c", f"<i>{index}</i>"
            current_no = index
        else:
            cls, label = "tw", ""
        if index % 4 == 0 and cursor != here:
            label = f"<i>{index}</i>"
        weeks.append(f'<a class="{cls}" href="/?week={cursor.isoformat()}" '
                     f'title="Week {index}">{label}</a>')
        cursor += timedelta(days=7)
        index += 1
    total = sum(v[0] for v in workload.values())
    finished = sum(v[1] for v in workload.values())
    left = (last - today).days
    exam_text = ""
    if schedule.get("exam_start") and schedule.get("exam_end"):
        a = date.fromisoformat(schedule["exam_start"])
        b = date.fromisoformat(schedule["exam_end"])
        exam_text = (f"Exams {a.day}&ndash;{b.day} {b.strftime('%B')}" if a.month == b.month
                     else f"Exams {a.strftime('%-d %B')} &ndash; {b.strftime('%-d %B')}")
    return f"""<div class="term rv d5">
  <div class="top"><b>{esc(schedule.get('term', 'Term'))} &mdash; week {current_no} of {index - 1}</b>
    <span class="n">{finished} of {total} done &nbsp;&middot;&nbsp; {left} days left</span></div>
  <div class="tbar">{"".join(weeks)}</div>
  <div class="lg"><span>Classes end {last.strftime('%-d %B')}</span>
    <span>{exam_text}</span></div>
</div>"""


# ------------------------------------------------------------------- lists

def _group(due_local, now):
    if due_local.date() == now.date():
        return "Today"
    if due_local.date() == (now + timedelta(days=1)).date():
        return "Tomorrow"
    if (due_local.date() - now.date()).days < 7:
        return due_local.strftime("%A")
    return due_local.strftime("%A %-d %B")


def render_tasks(rows, now, tz, limit=16, colours=None, horizon_days=10):
    """Only the near horizon. A month of work in one column is a wall, not a
    list, and it buries everything below it. What is beyond the horizon is
    counted, not enumerated."""
    out, heading, beyond = [], None, 0
    cutoff = now + timedelta(days=horizon_days)
    for row in rows[:limit]:
        due = parse_utc(row["due_utc"])
        if due is None:
            continue
        local = due.astimezone(tz)
        if local > cutoff:
            beyond += 1
            continue
        group = _group(local, now)
        if group != heading:
            heading = group
            late = " late" if local < now else ""
            out.append(f'<div class="grp{late}">{esc(group)}</div>')
        kind = row["kind"]
        kind_html = f' <span class="kd">{esc(kind)}</span>' if kind != "assignment" else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        out.append(
            f'<div class="it {urgency(local, now)}"><div>'
            f'<div class="w">{title}{kind_html}</div>'
            f'<div class="m"><span class="at">{esc(due_label(local, now))}</span>'
            f'{dot(row["course"], colours)}<span>{esc(row["course"])}</span></div></div>'
            f'<form method="post" action="/done">'
            f'<input type="hidden" name="id" value="{esc(row["id"])}">'
            f'<button class="tick" type="submit" aria-label="Mark done">&#10003;</button>'
            f'</form></div>')
    if beyond:
        plural = "" if beyond == 1 else "s"
        out.append(f'<div class="more">{beyond} more piece{plural} of work further out</div>')
    return "".join(out)


def render_completed(rows, now, tz, limit=8, colours=None):
    out = []
    for row in (rows or [])[:limit]:
        due = parse_utc(row["due_utc"])
        when = due.astimezone(tz).strftime("%-d %b") if due else ""
        out.append(
            f'<div class="it done"><div>'
            f'<div class="w">{esc(row["title"])}</div>'
            f'<div class="m"><span class="at">{esc(when)}</span>'
            f'{dot(row["course"], colours)}<span>{esc(row["course"])}</span></div></div>'
            f'<form method="post" action="/done">'
            f'<input type="hidden" name="id" value="{esc(row["id"])}">'
            f'<input type="hidden" name="undo" value="1">'
            f'<button class="tick" type="submit" aria-label="Reopen">&#8630;</button>'
            f'</form></div>')
    return "".join(out)


def render_announcements(rows, now, tz, limit=4):
    out = []
    for row in rows[:limit]:
        posted = parse_utc(row["due_utc"])
        when = posted.astimezone(tz).strftime("%-d %b") if posted else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        out.append(f'<div class="li"><div class="h">{title}</div>'
                   f'<div class="m">{esc(row["course"])} &middot; {esc(when)}</div></div>')
    return "".join(out)


def render_mail(rows, now, tz, limit=5, colours=None):
    out = []
    for row in rows[:limit]:
        received = parse_utc(row["due_utc"])
        when = received.astimezone(tz).strftime("%-d %b") if received else ""
        title = esc(row["title"])
        if row["url"]:
            title = f'<a href="{esc(row["url"])}">{title}</a>'
        state = "read" if row["done"] else "unread"
        out.append(f'<div class="li {state}"><div class="h">{title}</div>'
                   f'<div class="m">{dot(row["course"], colours)}{esc(row["course"])}'
                   f' &middot; {esc(when)}</div></div>')
    return "".join(out)


def render_grades(rows, colours=None):
    out = []
    for row in rows or []:
        score = row.get("score")
        value = f"{score:g}%" if isinstance(score, (int, float)) else "&mdash;"
        letter = f'<span class="g">{esc(row.get("grade"))}</span>' if row.get("grade") else ""
        out.append(f'<div class="gr"><div class="m">{dot(row.get("course"), colours)}'
                   f'&nbsp;{esc(row.get("course"))}</div>'
                   f'<div class="v">{value}{letter}</div></div>')
    return "".join(out)


def render_changed(rows, now, tz, limit=5):
    out = []
    for row in (rows or [])[:limit]:
        due = parse_utc(row["due_utc"])
        when = due_label(due.astimezone(tz), now) if due else "no date"
        if row["is_new"]:
            tag = '<span class="tg">New</span>'
        else:
            prev = parse_utc(row["prev_due_utc"])
            was = f' (was {due_label(prev.astimezone(tz), now)})' if prev else ""
            tag = f'<span class="tg mv">Moved{esc(was)}</span>'
        out.append(f'<div class="r">{tag}{esc(row["title"])} &mdash; {esc(when)}</div>')
    return "".join(out)


def render_appointments(rows, now, tz, limit=6, colours=None):
    out = []
    for row in (rows or [])[:limit]:
        when = parse_utc(row["due_utc"])
        if not when:
            continue
        local = when.astimezone(tz)
        where = f' &middot; {esc(row["body"])}' if row["body"] else ""
        out.append(f'<div class="li"><div class="h">{esc(row["title"])}</div>'
                   f'<div class="m"><span class="at">{esc(due_label(local, now))}</span>'
                   f'{esc(row["course"])}{where}</div></div>')
    return "".join(out)


# ------------------------------------------------------------------- shell

def _shell(title, inner, refresh=None):
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#F3F4F6">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="schoolboard">
<link rel="manifest" href="/manifest.webmanifest">
{meta}
<title>{esc(title)}</title>
<style>{CSS}</style>
</head><body><div class="wrap">
{inner}
</div></body></html>"""


def login_page(error=None, retry_after=None):
    if retry_after:
        message = (f'<div class="err">Too many attempts. Try again in '
                   f'{int(retry_after // 60) + 1} min.</div>')
        form = ""
    else:
        message = f'<div class="err">{esc(error)}</div>' if error else ""
        form = ('<form method="post" action="/login">'
                '<input type="password" name="password" placeholder="Password" autofocus '
                'autocomplete="current-password" aria-label="Password">'
                '<button type="submit">Sign in</button></form>')
    return _shell("schoolboard — Sign in",
                  f'<div class="gate rv"><div class="card">'
                  f'<h1><span class="pip"></span> schoolboard</h1>'
                  f'<p>Classes, coursework and course mail.</p>{message}{form}</div></div>')


def card(heading, content, sub="", extra="", cls=""):
    if not content.strip():
        return ""
    right = extra or (f'<span class="sub">{sub}</span>' if sub else "")
    return (f'<section class="card {cls}"><div class="ch"><h2>{heading}</h2>{right}</div>'
            f'{content}</section>')


def page(schedule, now, tz, tasks, anns, sync_note, canvas_ready, refresh=60, mails="",
         grades="", changed="", appts="", week=None, workload=None, completed="",
         focus_day=None):
    colours = colour_map(schedule)
    workload = workload or {}
    monday = week or monday_of(focus_day or now.date())
    on_now = monday == monday_of(now.date())

    prev_week = (monday - timedelta(days=7)).isoformat()
    next_week = (monday + timedelta(days=7)).isoformat()
    nav = (f'<span class="nav"><a href="/?week={prev_week}" aria-label="Previous week">&#8592;</a>'
           f'<a href="/" class="{"on" if on_now else ""}">This week</a>'
           f'<a href="/?week={next_week}" aria-label="Next week">&#8594;</a></span>')
    span = (f'{monday.strftime("%-d %b")} &ndash; '
            f'{(monday + timedelta(days=4)).strftime("%-d %b")}')

    if not canvas_ready:
        work = ('<div class="note">Canvas isn\'t connected, so nothing here knows about your '
                'assignments. Create a token at <b>Canvas &rarr; Account &rarr; Settings &rarr; '
                'New Access Token</b>, then run <code>schoolboard connect</code></div>')
    elif tasks.strip():
        work = tasks
    else:
        work = '<div class="empty">Nothing due in the next stretch. Enjoy it.</div>'
    changed_block = f'<div class="chg">{changed}</div>' if changed.strip() else ""
    finished = card("Finished", completed, cls="rv d5")
    finished_block = f'<div style="margin-top:16px">{finished}</div>' if finished else ""

    side = "".join([
        card("Calendar", appts),
        card("Course mail", mails),
        card("Announcements", anns),
        card("Grades", grades),
    ])

    return _shell(f"{now.strftime('%A')} — schoolboard", f"""<div class="bar rv">
  <span class="brand"><span class="pip"></span> schoolboard</span>
  <span class="stamp"><b>{now.strftime('%A %-d %B')}</b> &nbsp; <span class="n">{now.strftime('%-I:%M %p').lower()}</span></span>
</div>

<div class="rv d1">{hero(schedule, now, tz, colours)}</div>
<div class="rv d2">{day_chips(schedule, monday, tz, now, workload)}</div>

<div class="cols">
  <div>
    <section class="card rv d3">
      <div class="ch"><h2>{span}</h2>{nav}</div>
      {week_grid(schedule, monday, tz, colours, now)}
      <div class="dl">{day_list(schedule, monday, tz, colours, now)}</div>
    </section>
    <section class="card rv d4" style="margin-top:16px">
      <div class="ch"><h2>Due</h2></div>
      {changed_block}{work}
    </section>
    {finished_block}
  </div>
  <div class="side rv d4">{side}</div>
</div>

{term_progress(schedule, workload, now.date())}

<footer><span>{esc(sync_note)}</span><span>{esc(now.strftime('%Z'))}</span></footer>""",
                  refresh=refresh)
