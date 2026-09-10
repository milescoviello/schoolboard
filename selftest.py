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
import sys
import traceback
from datetime import date, datetime, timedelta

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


def main():
    schedule = config.load_schedule()
    tz = timetable.tzinfo(config.timezone_name())
    conn = store.connect()
    tasks = render.render_tasks(store.upcoming(conn), datetime.now(tz), tz)
    done = render.render_completed(store.completed(conn), datetime.now(tz), tz)
    workload = store.workload(conn)

    # Every hour of several representative days, so the now-bar, the day
    # progress bar, "in class", "next up" and the idle state all get exercised.
    days = [date(2026, 9, 9),    # first week, mid-week
            date(2026, 9, 11),   # a full teaching day
            date(2026, 9, 12),   # a Saturday
            date(2026, 10, 12),  # a holiday
            date(2026, 11, 26),  # fall break
            date(2026, 12, 16),  # exam period
            date(2027, 1, 20)]   # after the term
    for day in days:
        for hour in range(0, 24):
            now = datetime(day.year, day.month, day.day, hour, 30, tzinfo=tz)
            check(f"{day} {hour:02d}:30",
                  lambda n=now: render.page(schedule, n, tz, tasks, "", "t", True,
                                            workload=workload, completed=done))

    # Weeks either side of the term, where there is nothing to draw.
    for offset in (-6, -1, 0, 1, 6, 20):
        monday = render.monday_of(date(2026, 9, 9)) + timedelta(weeks=offset)
        now = datetime(2026, 9, 9, 13, 45, tzinfo=tz)
        check(f"week offset {offset:+}",
              lambda m=monday, n=now: render.page(schedule, n, tz, tasks, "", "t", True,
                                                  week=m, workload=workload))

    # Empty data, which is what a fresh install looks like.
    check("no data", lambda: render.page(schedule, datetime.now(tz), tz, "", "", "t", False))
    check("login", lambda: render.login_page())
    check("login error", lambda: render.login_page(error="nope"))
    check("login throttled", lambda: render.login_page(retry_after=900))

    total = 7 * 24 + 6 + 4
    if FAIL:
        print(f"FAILED {len(FAIL)} of {total}")
        for label, why in FAIL[:12]:
            print(f"  {label}: {why}")
        return 1
    print(f"all {total} render states OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
