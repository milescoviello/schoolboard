"""The board's words, not just whether it renders: labels, headings, the term
line, links and odd schedules. Invented data only: this repo is public.

    python3 -m unittest tests.test_render
"""
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import coursesite, render, timetable  # noqa: E402

LA = timetable.tzinfo("America/Los_Angeles")
SCHEDULE = {"term": "Fall 2026", "term_start": "2026-09-09", "term_end": "2026-12-13",
            "exam_start": "2026-12-14", "exam_end": "2026-12-20", "timezone": "America/Los_Angeles",
            "no_class_days": [], "courses": [
                {"code": "CS 1000", "title": "Intro", "days": ["Mon", "Wed"], "start": "10:00",
                 "end": "11:40", "room": "Room 1", "instructor": "A. Lecturer"},
                {"code": "CS 1001", "title": "Lab for CS 1000", "days": ["Tue"], "start": "14:00",
                 "end": "15:40", "room": "Room 2", "instructor": "A. Lecturer", "site_course": "CS 1000"}]}


def row(due, n=0, **extra):
    item = {"id": f"canvas:assignment:{n}", "source": "canvas", "kind": "assignment", "course": "CS 1000",
            "title": f"Work {n}", "due_utc": due.astimezone(timezone.utc).isoformat(),
            "url": "https://example.edu/w", "done": 0, "body": None, "is_new": 0, "prev_due_utc": None}
    item.update(extra)
    return item


class TasksTest(unittest.TestCase):
    now = datetime(2026, 9, 28, 12, 0, tzinfo=LA)   # a Monday

    def test_nothing_in_the_horizon_is_dropped_silently(self):
        rows = [row(self.now + timedelta(hours=6 + i), i) for i in range(22)]
        html = render.render_tasks(rows, self.now, LA)
        self.assertEqual(html.count('class="it '), 16)
        self.assertIn("6 more in the next 10 days", html)

    def test_overdue_pile_cannot_push_tonight_off(self):
        rows = [row(self.now - timedelta(days=10) + timedelta(hours=i), i) for i in range(20)]
        rows.append(row(self.now.replace(hour=23, minute=59), 99, title="Due tonight"))
        html = render.render_tasks(rows, self.now, LA)
        self.assertIn("Due tonight", html)
        self.assertIn("16 more overdue", html)

    def test_beyond_horizon_is_counted(self):
        html = render.render_tasks([row(self.now + timedelta(days=30))], self.now, LA)
        self.assertIn("1 more piece of work further out", html)

    def test_overdue_gets_its_own_heading(self):
        rows = [row(self.now - timedelta(days=7), 1),            # last Monday
                row(self.now - timedelta(days=1), 2),            # yesterday, Sunday
                row(self.now + timedelta(days=6), 3)]            # next Sunday
        html = render.render_tasks(rows, self.now, LA)
        self.assertIn('<div class="grp late">Overdue</div>', html)
        self.assertNotIn(">Monday<", html)
        self.assertEqual(html.count('class="grp'), 2)             # Overdue, then Sunday
        self.assertIn('<div class="grp">Sunday</div>', html)

    def test_javascript_link_is_not_rendered(self):
        html = render.render_tasks([row(self.now + timedelta(hours=3), url="javascript:alert(1)")],
                                   self.now, LA)
        self.assertNotIn("javascript:", html)
        self.assertIn("Work 0", html)


class LabelsTest(unittest.TestCase):
    def test_minutes_late_just_after_midnight(self):
        due = datetime(2026, 9, 28, 23, 59, tzinfo=LA)
        self.assertEqual(render.due_label(due, datetime(2026, 9, 29, 0, 5, tzinfo=LA)), "6m late")
        self.assertEqual(render.due_label(due, datetime(2026, 9, 29, 5, 0, tzinfo=LA)), "5h late")
        self.assertEqual(render.due_label(due, datetime(2026, 9, 30, 2, 0, tzinfo=LA)), "1d late")

    def test_tomorrow_says_so_where_there_is_no_heading(self):
        now = datetime(2026, 9, 28, 12, 0, tzinfo=LA)
        due = datetime(2026, 9, 29, 15, 0, tzinfo=LA)
        self.assertEqual(render.due_label(due, now), "3:00 pm")
        self.assertEqual(render.due_label(due, now, with_day=True), "tomorrow 3:00 pm")

    def test_repeated_hour_on_dst_day(self):
        # 1:30 PST comes 45 minutes after 1:45 PDT: not late.
        due = datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=1)
        self.assertEqual(render.due_label(due, datetime(2026, 11, 1, 1, 45, tzinfo=LA)), "1:30 am")

    def test_moved_shows_the_old_date(self):
        now = datetime(2026, 9, 28, 12, 0, tzinfo=LA)
        moved = row(now + timedelta(days=3), prev_due_utc=(now - timedelta(days=1)).isoformat())
        html = render.render_changed([moved], now, LA)
        self.assertIn("Moved (was Sun 27 Sep)", html)
        self.assertNotIn("late", html)

    def test_appointments_carry_their_day(self):
        now = datetime(2026, 9, 28, 12, 0, tzinfo=LA)
        timed = row(datetime(2026, 9, 29, 15, 0, tzinfo=LA), kind="appointment", body=None)
        all_day = row(datetime.combine(date(2026, 9, 30), render.ALL_DAY_TIME, tzinfo=LA), 1,
                      kind="appointment", body=None)
        html = render.render_appointments([timed, all_day], now, LA)
        self.assertIn("tomorrow 3:00 pm", html)
        self.assertIn("Wed, all day", html)


class SafeUrlTest(unittest.TestCase):
    def test_schemes(self):
        for bad in ("javascript:alert(1)", " JavaScript:x", "java\tscript:x", "data:text/html,x", "vbscript:x"):
            self.assertIsNone(render.safe_url(bad), bad)
        for good in ("https://example.edu/a", "http://example.edu", "/courses/1", "page.html?x=a:b"):
            self.assertEqual(render.safe_url(good), good)


class TermTest(unittest.TestCase):
    def term(self, day):
        return render.term_progress(SCHEDULE, {}, day)

    def test_before_term(self):
        html = self.term(date(2026, 9, 1))
        self.assertIn("starts 9 September", html)
        self.assertIn("8 days to go", html)
        self.assertNotIn("week 1", html)

    def test_during_term(self):
        html = self.term(date(2026, 9, 28))
        self.assertIn("week 4 of 14", html)
        self.assertIn("76 days left", html)

    def test_exams_and_after(self):
        exams, after = self.term(date(2026, 12, 16)), self.term(date(2027, 1, 20))
        self.assertIn("final exams", exams)
        self.assertIn("over", after)
        for html in (exams, after):
            self.assertNotIn("days left", html)
            self.assertNotIn("week 1 of", html)


class ExamsTest(unittest.TestCase):
    def test_times_are_joined_and_escaped(self):
        sched = dict(SCHEDULE, exams=[{"course": "CS 1000", "date": "2026-12-15", "start": "10:30 am",
                                       "end": "noon"},
                                      {"course": "CS 1001", "date": "2026-12-16", "start": "<b>1pm</b>"},
                                      {"course": "CS 1002", "date": None}])
        html = render.render_exams(sched, datetime(2026, 12, 1, 9, 0, tzinfo=LA), LA)
        self.assertIn("10:30 am&ndash;noon", html)
        self.assertIn("&lt;b&gt;1pm&lt;/b&gt;", html)
        self.assertNotIn("CS 1002", html)


class OddScheduleTest(unittest.TestCase):
    now = datetime(2026, 9, 28, 9, 50, tzinfo=LA)

    def page(self, schedule):
        return render.page(schedule, self.now, LA, "", "", "t", True)

    def test_missing_keys_do_not_take_the_page_down(self):
        bare = {"code": "CS 1000", "days": ["Mon"], "start": "10:00", "end": "11:40"}
        for courses in ([bare], [dict(bare, days=None)], [{"code": "CS 1000", "days": ["Mon"]}],
                        [dict(bare, start="ten")]):
            self.assertIn("<html", self.page(dict(SCHEDULE, courses=courses)), courses)
        no_courses = {k: v for k, v in SCHEDULE.items() if k != "courses"}
        self.assertIn("<html", self.page(no_courses))


class WeekTest(unittest.TestCase):
    now = datetime(2026, 9, 28, 9, 0, tzinfo=LA)

    def test_focus_day_is_marked_and_anchored(self):
        html = render.page(SCHEDULE, self.now, LA, "", "", "t", True, focus_day=date(2026, 10, 1))
        self.assertIn('class="chip focus" href="/?day=2026-10-01#d-2026-10-01"', html)
        self.assertIn('<div class="dsec focus" id="d-2026-10-01">', html)
        self.assertIn('id="d-2026-09-28"', html)

    def test_lab_prep_comes_from_the_lecture_site(self):
        def entries_for(day, course=None, path=None):
            if course == "CS 1000" and day == date(2026, 9, 29):
                return [{"kind": "lab", "label": "Lab 3", "url": "https://example.edu/lab3", "date": "2026-09-29"}]
            return []
        with mock.patch.object(coursesite, "entries_for", side_effect=entries_for):
            html = render.day_list(SCHEDULE, date(2026, 9, 28), LA, render.colour_map(SCHEDULE), self.now)
        self.assertIn('<a href="https://example.edu/lab3">Lab 3</a>', html)


if __name__ == "__main__":
    unittest.main()
