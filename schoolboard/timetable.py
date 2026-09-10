"""Turn the static term schedule into concrete meetings on concrete days.

All arithmetic happens in the configured campus timezone, never the host's.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _hhmm(value):
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


class Meeting:
    def __init__(self, course, day, tz):
        self.course = course
        self.day = day
        self.tz = tz
        self.start = datetime.combine(day, _hhmm(course["start"]), tzinfo=tz)
        self.end = datetime.combine(day, _hhmm(course["end"]), tzinfo=tz)

    @property
    def code(self):
        return self.course["code"]

    def status(self, now):
        if now < self.start:
            return "upcoming"
        return "current" if now <= self.end else "past"

    def minutes_until(self, now):
        return int((self.start - now).total_seconds() // 60)


def meetings_on(schedule, day, tz):
    """Every class meeting on `day`, in start order.

    Honours per-course `starts_on`, which is why CS 1802 correctly shows nothing
    before 2026-09-16 even though it is nominally a Wednesday course.
    """
    weekday = DAY_NAMES[day.weekday()]
    out = []
    for course in schedule["courses"]:
        if course.get("online") or weekday not in course.get("days", []):
            continue
        starts_on = course.get("starts_on")
        if starts_on and day < date.fromisoformat(starts_on):
            continue
        ends_on = course.get("ends_on")
        if ends_on and day > date.fromisoformat(ends_on):
            continue
        out.append(Meeting(course, day, tz))
    return sorted(out, key=lambda m: m.start)


def current_and_next(schedule, now, tz, lookahead_days=7):
    """The meeting happening now (if any) and the next one to walk to."""
    today = meetings_on(schedule, now.date(), tz)
    current = next((m for m in today if m.status(now) == "current"), None)
    upcoming = next((m for m in today if m.status(now) == "upcoming"), None)
    if upcoming is None:
        for offset in range(1, lookahead_days + 1):
            ahead = meetings_on(schedule, now.date() + timedelta(days=offset), tz)
            if ahead:
                upcoming = ahead[0]
                break
    return current, upcoming


def week_ahead(schedule, now, tz, days=7):
    """The next `days` days, today excluded, each with its meetings."""
    out = []
    for offset in range(1, days + 1):
        day = now.date() + timedelta(days=offset)
        out.append((day, meetings_on(schedule, day, tz)))
    return out


def online_courses(schedule):
    return [c for c in schedule["courses"] if c.get("online")]


def tzinfo(name):
    return ZoneInfo(name)
