"""Calendar feeds: zones, all-day, EXDATE, per-instance overrides and simple
recurrence. Invented data only: this repo is public.

    python3 -m unittest tests.test_ics -v
"""
import http.client
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import ics, timetable  # noqa: E402

LA = timetable.tzinfo("America/Los_Angeles")
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)   # a Monday


def feed(*events):
    body = "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


def collect(text, now=NOW):
    with mock.patch.object(ics, "fetch", return_value=text):
        items, _, ok = ics.collect([{"name": "Outlook", "url": "https://calendar.example/a.ics"}],
                                   tz=LA, now=now)
    assert ok == ["Outlook"], ok
    return items


def starts(items):
    return sorted(it["due_utc"] for it in items)


class ZoneTest(unittest.TestCase):
    def test_windows_tzid(self):
        items = collect(feed("UID:a\nSUMMARY:Advising\nDTSTART;TZID=Pacific Standard Time:20261006T100000"))
        self.assertEqual(starts(items), ["2026-10-06T17:00:00+00:00"])

    def test_quoted_tzid_with_a_colon(self):
        items = collect(feed('UID:b\nSUMMARY:Advising\n'
                             'DTSTART;TZID="(UTC-08:00) Pacific Time (US & Canada)":20261006T100000'))
        self.assertEqual(starts(items), ["2026-10-06T17:00:00+00:00"])

    def test_floating_time_is_campus_time(self):
        items = collect(feed("UID:c\nSUMMARY:Club\nDTSTART:20261006T100000"))
        self.assertEqual(starts(items), ["2026-10-06T17:00:00+00:00"])

    def test_z_is_utc(self):
        items = collect(feed("UID:d\nSUMMARY:Call\nDTSTART:20261006T100000Z"))
        self.assertEqual(starts(items), ["2026-10-06T10:00:00+00:00"])

    def test_all_day_stays_on_its_own_day(self):
        (item,) = collect(feed("UID:e\nSUMMARY:Holiday\nDTSTART;VALUE=DATE:20261007"))
        local = datetime.fromisoformat(item["due_utc"]).astimezone(LA)
        self.assertEqual((local.date().isoformat(), local.time()), ("2026-10-07", ics.ALL_DAY_TIME))


class RecurrenceTest(unittest.TestCase):
    WEEKLY = ("UID:w\nSUMMARY:Tutoring\nDTSTART;TZID=America/Los_Angeles:20261006T100000\n"
              "RRULE:FREQ=WEEKLY;COUNT=3")

    def test_exdate(self):
        items = collect(feed(self.WEEKLY + "\nEXDATE;TZID=America/Los_Angeles:20261013T100000"))
        self.assertEqual(starts(items), ["2026-10-06T17:00:00+00:00", "2026-10-20T17:00:00+00:00"])

    def test_override_moves_one_occurrence(self):
        items = collect(feed(self.WEEKLY,
                             "UID:w\nSUMMARY:Tutoring (moved)\n"
                             "RECURRENCE-ID;TZID=America/Los_Angeles:20261013T100000\n"
                             "DTSTART;TZID=America/Los_Angeles:20261014T150000"))
        self.assertEqual(starts(items), ["2026-10-06T17:00:00+00:00", "2026-10-14T22:00:00+00:00",
                                         "2026-10-20T17:00:00+00:00"])

    def test_cancelled_override_removes_it(self):
        items = collect(feed(self.WEEKLY,
                             "UID:w\nSUMMARY:Tutoring\nSTATUS:CANCELLED\n"
                             "RECURRENCE-ID;TZID=America/Los_Angeles:20261013T100000\n"
                             "DTSTART;TZID=America/Los_Angeles:20261013T100000"))
        self.assertEqual(starts(items), ["2026-10-06T17:00:00+00:00", "2026-10-20T17:00:00+00:00"])

    def test_old_daily_series_still_reaches_the_window(self):
        items = collect(feed("UID:x\nSUMMARY:Standup\nDTSTART:20240101T160000Z\nRRULE:FREQ=DAILY"))
        self.assertEqual(len(items), 46)   # 1 day back to 45 ahead, one a day
        self.assertEqual(starts(items)[0], "2026-10-04T16:00:00+00:00")

    def test_monthly_31st_does_not_drift(self):
        event = ics.parse(feed("UID:m\nSUMMARY:Rent\nDTSTART:20270131T120000Z\nRRULE:FREQ=MONTHLY"), LA)[0]
        got = ics.expand(event, datetime(2027, 1, 1, tzinfo=timezone.utc),
                         datetime(2027, 6, 1, tzinfo=timezone.utc), tz=LA)
        self.assertEqual([d.day for d in got], [31, 28, 31, 30, 31])

    def test_monthly_by_weekday_keeps_only_the_first(self):
        event = ics.parse(feed("UID:t\nSUMMARY:Meeting\nDTSTART:20261006T170000Z\n"
                               "RRULE:FREQ=MONTHLY;BYDAY=1TU"), LA)[0]
        got = ics.expand(event, datetime(2026, 10, 1, tzinfo=timezone.utc),
                         datetime(2027, 1, 1, tzinfo=timezone.utc), tz=LA)
        self.assertEqual(got, [datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc)])

    def test_weekly_keeps_local_time_across_dst(self):
        items = collect(feed("UID:s\nSUMMARY:Section\nDTSTART;TZID=America/Los_Angeles:20261020T100000\n"
                             "RRULE:FREQ=WEEKLY;COUNT=4"),
                        now=datetime(2026, 10, 19, tzinfo=timezone.utc))
        local = [datetime.fromisoformat(s).astimezone(LA) for s in starts(items)]
        self.assertEqual([d.strftime("%m-%d %H:%M") for d in local],
                         ["10-20 10:00", "10-27 10:00", "11-03 10:00", "11-10 10:00"])
        self.assertEqual([datetime.fromisoformat(s).hour for s in starts(items)], [17, 17, 18, 18])


class FeedTest(unittest.TestCase):
    def test_a_bad_feed_does_not_escape(self):
        good = feed("UID:g\nSUMMARY:Office hours\nDTSTART:20261006T100000Z")

        def fetch(url):
            if "bad" in url:
                raise http.client.IncompleteRead(b"BEGIN:VCAL")
            return good

        with mock.patch.object(ics, "fetch", side_effect=fetch):
            items, note, ok = ics.collect([{"name": "Bad", "url": "https://bad.example/x.ics"},
                                           {"name": "Good", "url": "https://good.example/y.ics"}],
                                          tz=LA, now=NOW)
        self.assertEqual(ok, ["Good"])
        self.assertEqual(len(items), 1)
        self.assertIn("Bad: failed", note)

    def test_impossible_date_drops_the_event_not_the_feed(self):
        items = collect(feed("UID:i\nSUMMARY:Nope\nDTSTART;VALUE=DATE:20260231",
                             "UID:j\nSUMMARY:Fine\nDTSTART:20261006T100000Z"))
        self.assertEqual([it["title"] for it in items], ["Fine"])


if __name__ == "__main__":
    unittest.main()
