#!/usr/bin/env python3
"""Render the board across a matrix of times, dates and data states.

Written after a crash that reached the live site: the "now" bar only renders
when the displayed week contains today AND the clock is inside the grid's hours.
Every ad-hoc check had been run late at night, outside those hours, so that
branch was never executed and a broken call sat there undetected.

The lesson is that a page which changes with the clock has to be tested against
a clock that moves. This sweeps the day rather than trusting whatever time it
happens to be when someone runs it.
"""
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, "/home/miles/schoolboard")
from schoolboard import config, render, store, timetable  # noqa: E402

FAIL = []


def check(label, fn):
    try:
        html = fn()
    except Exception:
        FAIL.append((label, traceback.format_exc().strip().splitlines()[-1]))
        return
    if "<html" not in html or len(html) < 2000:
        FAIL.append((label, f"suspiciously small page: {len(html)} bytes"))
    if "Traceback" in html:
        FAIL.append((label, "traceback rendered into the page"))
    # Crashes are not the only failure: these rendered for weeks without one.
    for pattern, why in ((r"-\d+ days? left", "negative days left"), (r"Lec None", "lecture number None"),
                         (r'href="\s*javascript:', "javascript: link")):
        if re.search(pattern, html, re.I):
            FAIL.append((label, why))


def sample_rows(now):
    """Invented work around `now`, so every sweep renders real task rows: overdue,
    tonight, tomorrow, later this week, past the horizon, one of his own, one
    said in class, and a link that must not be followed. The repo is public,
    so none of it is real."""
    def at(delta, **extra):
        due = (now + delta).astimezone(timezone.utc).isoformat()
        row = {"id": f"test:{len(rows)}", "source": "canvas", "kind": "assignment", "course": "TEST 1000",
               "title": f"Sample {len(rows)}", "due_utc": due, "url": "https://example.edu/a", "done": 0,
               "body": None}
        row.update(extra)
        rows.append(row)
    rows = []
    at(timedelta(days=-3))
    at(timedelta(minutes=-20), url="javascript:alert(1)")
    at(timedelta(hours=2))
    at(timedelta(days=1), source="local", kind="task", url=None)
    at(timedelta(days=4), source="scribe", kind="said in class", url=None,
       body=json.dumps({"lecture": None, "date": "2026-09-28", "what": "Read", "quote": "read it"}))
    at(timedelta(days=20))
    return rows


def tasks_at(conn, now, tz):
    rows = list(store.upcoming(conn, now=now)) + sample_rows(now)
    rows.sort(key=lambda r: render.parse_utc(r["due_utc"]))
    return render.render_tasks(rows, now, tz)


def check_json(label, fn):
    """/next.json is read by a program (dormbot on .148), so check its shape."""
    try:
        body = fn()
        json.dumps(body)
        assert set(body) == {"now", "next", "walk_minutes", "leave_by"}, sorted(body)
        assert isinstance(body["walk_minutes"], int)
        nxt = body["next"]
        if nxt is None:
            assert body["leave_by"] is None, "leave_by without a meeting"
            return
        assert set(nxt) == {"code", "title", "room", "start", "end"}, sorted(nxt)
        start = datetime.fromisoformat(nxt["start"])
        assert start.utcoffset() is not None, "naive start time"
        if body["walk_minutes"]:
            leave = datetime.fromisoformat(body["leave_by"])
            assert leave == start - timedelta(minutes=body["walk_minutes"]), "leave_by drift"
        else:
            assert body["leave_by"] is None, "leave_by with walk_minutes 0"
    except Exception:
        FAIL.append((label, traceback.format_exc().strip().splitlines()[-1]))


def check_due(label, fn):
    """/due.json is read by dormbot too; its shape is a contract."""
    try:
        body = fn()
        json.dumps(body)
        assert set(body) == {"now", "horizon_days", "items"}, sorted(body)
        assert isinstance(body["horizon_days"], int)
        now = datetime.fromisoformat(body["now"])
        assert now.utcoffset() is not None, "naive now"
        dues = []
        for it in body["items"]:
            assert set(it) == {"course", "title", "kind", "due", "overdue", "url"}, sorted(it)
            due = datetime.fromisoformat(it["due"])
            assert due.utcoffset() is not None, "naive due"
            assert it["overdue"] == (due < now), "overdue flag wrong"
            assert due <= now + timedelta(days=body["horizon_days"]), "beyond the horizon"
            dues.append(due)
        assert dues == sorted(dues), "not sorted by due"
    except Exception:
        FAIL.append((label, traceback.format_exc().strip().splitlines()[-1]))


def main():
    schedule = config.load_schedule()
    tz = timetable.tzinfo(config.timezone_name())
    conn = store.connect()
    tasks = tasks_at(conn, datetime.now(tz), tz)
    done = render.render_completed(store.completed(conn), datetime.now(tz), tz)
    workload = store.workload(conn, tz)

    # Every hour of several representative days, so the now-bar, the day
    # progress bar, "in class", "next up" and the idle state all get exercised.
    days = [date(2026, 9, 9),    # first week, mid-week
            date(2026, 9, 11),   # a full teaching day
            date(2026, 9, 12),   # a Saturday
            date(2026, 10, 12),  # a holiday
            date(2026, 10, 31),  # the day before DST ends
            date(2026, 11, 1),   # DST ends: 1 am happens twice
            date(2026, 11, 2),   # the first teaching day after
            date(2026, 11, 26),  # fall break
            date(2026, 12, 16),  # exam period
            date(2027, 1, 20)]   # after the term
    for day in days:
        for hour in range(0, 24):
            now = datetime(day.year, day.month, day.day, hour, 30, tzinfo=tz)
            check(f"{day} {hour:02d}:30",
                  lambda n=now: render.page(schedule, n, tz, tasks_at(conn, n, tz), "", "t", True,
                                            workload=workload, completed=done))

    # Weeks either side of the term, where there is nothing to draw.
    for offset in (-6, -1, 0, 1, 6, 20):
        monday = render.monday_of(date(2026, 9, 9)) + timedelta(weeks=offset)
        now = datetime(2026, 9, 9, 13, 45, tzinfo=tz)
        check(f"week offset {offset:+}",
              lambda m=monday, n=now: render.page(schedule, n, tz, tasks, "", "t", True,
                                                  week=m, workload=workload))

    # The pieces added later: personal items, source ages, leave-by, dark theme.
    personal = render.render_personal(store.personal(conn), datetime.now(tz), tz)
    from schoolboard import status  # noqa: E402
    sources = render.render_sources(status.sources(config.load_config(), conn, store.get_meta))
    for theme in ("light", "auto"):
        for walk in (0, 10):
            for hour in (7, 8, 13, 23):
                now = datetime(2026, 9, 10, hour, 20, tzinfo=tz)
                check(f"theme={theme} walk={walk} {hour:02d}h",
                      lambda n=now, t=theme, w=walk: render.page(
                          schedule, n, tz, tasks, "", "t", True, workload=workload,
                          personal=personal, sources=sources, walk_minutes=w, theme=t))

    # The JSON dormbot polls for its leave-by nudge, across the same days.
    from schoolboard import server  # noqa: E402
    for day in days:
        for hour in (7, 13, 23):
            now = datetime(day.year, day.month, day.day, hour, 30, tzinfo=tz)
            check_json(f"next.json {day} {hour:02d}:30",
                       lambda n=now: server.next_meeting(now=n))
            check_due(f"due.json {day} {hour:02d}:30",
                      lambda n=now: server.due_items(now=n))

    # Empty data, which is what a fresh install looks like.
    check("no data", lambda: render.page(schedule, datetime.now(tz), tz, "", "", "t", False))
    check("login", lambda: render.login_page())
    check("login error", lambda: render.login_page(error="nope"))
    check("login throttled", lambda: render.login_page(retry_after=900))

    total = len(days) * 24 + 6 + (2 * 2 * 4) + len(days) * 3 * 2 + 4
    if FAIL:
        print(f"FAILED {len(FAIL)} of {total}")
        for label, why in FAIL[:12]:
            print(f"  {label}: {why}")
        return 1
    print(f"all {total} render states OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
