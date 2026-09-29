"""Deadlines said in class (scribe's export) → the store, the board, the
messages. Invented data only: this repo is public.

    python3 -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import lectures, notify, render, store, timetable  # noqa: E402

LA = timetable.tzinfo("America/Los_Angeles")
SCHEDULE = {"term_start": "2026-09-09", "term_end": "2026-12-13", "timezone": "America/Los_Angeles",
            "no_class_days": [], "courses": [
                {"code": "PHIL 1000", "title": "Ethics", "days": ["Mon", "Thu"], "start": "08:45",
                 "end": "10:25", "room": "Room 1"}]}


def lecture(**over):
    lec = {"id": "2026-09-28_0851", "course": "PHIL 1000", "course_title": "Ethics", "date": "2026-09-28",
           "lecture": 6, "start": "2026-09-28T08:51:26-07:00", "duration": 5000, "topic": "Trolley problems",
           "recap": ["Rules must be refined as events unfold.", "Confirmation bias hurts every method."],
           "deadlines": [], "notes_note": "Courses/PHIL/Recorded/Lec6 - 2026-09-28 (notes).md"}
    lec.update(over)
    return lec


def deadline(t, what, quote, due="2026-10-01T08:45:00-07:00"):
    return {"id": f"scribe:2026-09-28_0851:{t}", "t": t, "what": what, "quote": quote, "due": due,
            "by_class": True}


class LecturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.index = root / "index.json"
        patch = mock.patch.object(store, "DB_PATH", root / "test.db")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {"scribe_index": str(self.index), "notify": {"class_lead_minutes": 15}}
        self.conn = store.connect()
        self.addCleanup(self.conn.close)
        store.upsert_items(self.conn, [
            {"id": "canvas:assignment:1", "source": "canvas", "kind": "assignment", "course": "PHIL 1000",
             "title": "Read one of the articles plus the Forbes one", "due_utc": "2026-10-02T03:59:59Z"},
            {"id": "canvas:assignment:2", "source": "canvas", "kind": "assignment", "course": "PHIL 1000",
             "title": "Homework 2", "due_utc": "2026-10-02T03:59:59Z"}])

    def write_index(self, *lecs, version=1):
        self.index.write_text(json.dumps({"version": version, "lectures": list(lecs)}))

    def items(self):
        return {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM items WHERE source='scribe'")}

    def test_reading_matches_the_canvas_item(self):
        self.write_index(lecture(deadlines=[deadline(4957, "Read an article on driverless cars",
                                                     "read one of the articles for Thursday")]))
        lectures.collect(self.conn, self.cfg, LA)
        self.assertEqual(self.items(), {})  # not a second row
        links = store.get_meta(self.conn, lectures.LINKS_META)
        self.assertEqual(list(links), ["canvas:assignment:1"])
        self.assertEqual(links["canvas:assignment:1"][0]["quote"], "read one of the articles for Thursday")

    def test_different_number_is_not_a_match(self):
        self.write_index(lecture(deadlines=[deadline(10, "Homework 3", "homework three is due Thursday")]))
        lectures.collect(self.conn, self.cfg, LA)
        (item,) = self.items().values()
        self.assertEqual((item["kind"], item["title"], item["due_utc"]),
                         ("said in class", "Homework 3", "2026-10-01T15:45:00+00:00"))

    def test_done_survives_resync_and_retraction_removes(self):
        self.write_index(lecture(deadlines=[deadline(10, "Bring a laptop", "bring your laptop on Thursday")]))
        lectures.collect(self.conn, self.cfg, LA)
        self.conn.execute("UPDATE items SET done=1 WHERE id='scribe:2026-09-28_0851:10'")
        lectures.collect(self.conn, self.cfg, LA)
        self.assertEqual(self.items()["scribe:2026-09-28_0851:10"]["done"], 1)
        self.write_index(lecture())  # the notes no longer have it
        lectures.collect(self.conn, self.cfg, LA)
        self.assertEqual(self.items(), {})

    def test_malformed_entries_are_dropped_not_fatal(self):
        # A lecture with no start, and a deadline scribe couldn't date, must not
        # take the sync or the class reminder down with them.
        self.write_index({"id": "broken", "course": "PHIL 1000", "date": "2026-09-24", "topic": "?"},
                         lecture(deadlines=[deadline(10, "Bring a laptop", "bring your laptop"),
                                            deadline(20, "Something", "at some point", due=None)]))
        lectures.collect(self.conn, self.cfg, LA)
        self.assertEqual(list(self.items()), ["scribe:2026-09-28_0851:10"])
        now = datetime(2026, 10, 1, 8, 32, tzinfo=LA)
        (text,) = [t for k, t, _ in notify.build_messages(self.conn, SCHEDULE, self.cfg, now, LA)
                   if k.startswith("class:")]
        self.assertIn("Last time (Mon, Lec 6)", text)

    def test_unknown_version_is_ignored(self):
        self.write_index(lecture(), version=99)
        self.assertIsNone(lectures.collect(self.conn, self.cfg, LA))

    def test_board_tags_said_in_class(self):
        self.write_index(lecture(deadlines=[
            deadline(4957, "Read an article", "read one of the articles for Thursday"),
            deadline(10, "Bring a laptop", "bring your laptop on Thursday")]))
        lectures.collect(self.conn, self.cfg, LA)
        now = datetime(2026, 9, 29, 12, 0, tzinfo=LA)
        html = render.render_tasks(store.upcoming(self.conn, now=now), now, LA,
                                   links=store.get_meta(self.conn, lectures.LINKS_META))
        self.assertIn('title="Lec 6 (2026-09-28): Read an article (“read one of the articles for Thursday”)">'
                      'also said in class', html)
        self.assertIn('>said in class</span>', html)

    def test_class_reminder_carries_the_recap(self):
        self.write_index(lecture(deadlines=[deadline(4957, "Read an article", "read one of the articles")]))
        lectures.collect(self.conn, self.cfg, LA)
        now = datetime(2026, 10, 1, 8, 32, tzinfo=LA)  # 13 minutes before Thursday's class
        msgs = notify.build_messages(self.conn, SCHEDULE, self.cfg, now, LA)
        (text,) = [t for k, t, _ in msgs if k.startswith("class:")]
        self.assertIn("<b>PHIL 1000</b> in 13 min", text)
        self.assertIn("Last time (Mon, Lec 6): Trolley problems", text)
        self.assertIn("• Rules must be refined as events unfold.", text)
        self.assertIn("• 8:59 pm Read one of the articles plus the Forbes one <i>(also said in class)</i>\n"
                      "   ↳ Read an article", text)

    def test_old_recap_is_left_out(self):
        self.write_index(lecture())
        now = datetime(2026, 10, 29, 8, 32, tzinfo=LA)  # a month on, nothing recorded since
        (text,) = [t for k, t, _ in notify.build_messages(self.conn, SCHEDULE, self.cfg, now, LA)
                   if k.startswith("class:")]
        self.assertNotIn("Last time", text)

    def test_weekly_lists_last_week_in_class(self):
        self.write_index(lecture())
        now = datetime(2026, 10, 4, 18, 5, tzinfo=LA)  # Sunday evening
        text = notify.weekly_text(self.conn, SCHEDULE, now, LA, lectures.load(self.cfg), {})
        self.assertIn("<b>Last week in class</b>\n  Mon PHIL 1000 Lec 6 &mdash; Trolley problems", text)


if __name__ == "__main__":
    unittest.main()
