"""The store, the notifier, the collectors and the lecture matcher: the bugs
found in the September sweep, one test each. Invented data only: this repo is
public.

    python3 -m unittest discover -s tests
"""
import json
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import canvas, coursesite, lectures, mail, notify, server, status, store, timetable  # noqa: E402

LA = timetable.tzinfo("America/Los_Angeles")
UTC = timezone.utc
SCHEDULE = {"term_start": "2026-09-09", "term_end": "2026-12-13", "timezone": "America/Los_Angeles",
            "no_class_days": [], "courses": [
                {"code": "PHIL 1000", "title": "Ethics", "days": ["Mon", "Thu"], "start": "08:45",
                 "end": "10:25", "room": "Room 1", "instructor": "Ada Wang"}]}
CFG = {"notify": {"due_thresholds_hours": [24, 3], "quiet_start": 22, "quiet_end": 7,
                  "class_lead_minutes": 15}}


def item(id, due, **over):
    it = {"id": id, "source": "canvas", "kind": "assignment", "course": "PHIL 1000",
          "title": id.split(":")[-1], "due_utc": due}
    it.update(over)
    return it


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(store, "DB_PATH", Path(self.tmp.name) / "test.db")
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = store.connect()
        self.addCleanup(self.conn.close)

    def keys(self, now, cfg=CFG):
        return [k for k, _, _ in notify.build_messages(self.conn, SCHEDULE, dict(cfg, scribe_index="/nonexistent"),
                                                       now, LA) if k.startswith("due:")]


class UpcomingTest(StoreTest):
    def test_overdue_rows_do_not_crowd_out_tonight(self):
        now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        store.upsert_items(self.conn, [item(f"canvas:assignment:old{i}", f"2026-10-0{1 + i % 3}T12:00:00Z",
                                            kind="event") for i in range(60)])
        store.upsert_items(self.conn, [item("canvas:assignment:tonight", "2026-10-05T14:00:00Z")])
        ids = [r["id"] for r in store.upcoming(self.conn, limit=60, now=now)]
        self.assertIn("canvas:assignment:tonight", ids)

    def test_workload_is_by_campus_date(self):
        # 11:59 pm Tuesday in Oakland is Wednesday in UTC.
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-09-30T06:59:59Z")])
        self.assertEqual(store.workload(self.conn, LA), {"2026-09-29": (1, 0)})

    def test_baseline_waits_for_the_first_import(self):
        self.assertEqual(store.recently_changed(self.conn), [])
        self.assertIsNone(store.get_meta(self.conn, "changed_baseline"))

    def test_retire_refuses_a_partial_answer(self):
        store.upsert_items(self.conn, [item(f"canvas:assignment:{i}", "2026-10-02T12:00:00Z") for i in range(20)])
        self.assertIsNone(store.retire(self.conn, "canvas", {"canvas:assignment:0"},
                                       "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 20)

    def test_retire_removes_only_what_the_window_lost(self):
        store.upsert_items(self.conn, [item("canvas:assignment:kept", "2026-10-02T12:00:00Z"),
                                       item("canvas:assignment:deleted", "2026-10-03T12:00:00Z"),
                                       item("canvas:assignment:outside", "2027-01-03T12:00:00Z"),
                                       item("canvas:announcement:9", "2026-10-03T12:00:00Z", kind="announcement")])
        gone = store.retire(self.conn, "canvas", {"canvas:assignment:kept"},
                            "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00")
        self.assertEqual(gone, 1)
        left = {r["id"] for r in self.conn.execute("SELECT id FROM items")}
        self.assertEqual(left, {"canvas:assignment:kept", "canvas:assignment:outside", "canvas:announcement:9"})


class NotifyTest(StoreTest):
    def test_extended_deadline_warns_again(self):
        store.upsert_items(self.conn, [item("canvas:assignment:essay", "2026-10-07T03:59:59Z")])
        first = self.keys(datetime(2026, 10, 6, 18, 0, tzinfo=LA))
        self.assertEqual(first, ["due:canvas:assignment:essay:3h"])
        store.upsert_items(self.conn, [item("canvas:assignment:essay", "2026-10-10T03:59:59Z")])
        again = self.keys(datetime(2026, 10, 9, 18, 0, tzinfo=LA))
        self.assertEqual(again, ["due:canvas:assignment:essay:3h:2026-10-10T03:59:59Z"])

    def test_a_warning_sent_after_the_move_is_not_repeated(self):
        store.upsert_items(self.conn, [item("canvas:assignment:essay", "2026-10-07T03:59:59Z")])
        store.upsert_items(self.conn, [item("canvas:assignment:essay", "2026-10-10T03:59:59Z")])
        notify.mark_sent(self.conn, "due:canvas:assignment:essay:3h", "sent under the old key, after the move")
        self.assertEqual(self.keys(datetime(2026, 10, 9, 18, 0, tzinfo=LA)), ["due:canvas:assignment:essay:3h"])

    def test_quiet_hours_hold_what_can_wait(self):
        # Synced at 23:10, due tomorrow evening: the 24h warning can wait for 07:00.
        store.upsert_items(self.conn, [item("canvas:assignment:later", "2026-10-07T03:00:00Z")])
        (msg,) = [m for m in notify.build_messages(self.conn, SCHEDULE, dict(CFG, scribe_index="/x"),
                                                   datetime(2026, 10, 5, 23, 10, tzinfo=LA), LA)
                  if m[0].startswith("due:")]
        self.assertFalse(msg[2])

    def test_quiet_hours_let_through_what_cannot(self):
        # Due at 05:45, before quiet ends: held, it would arrive too late.
        store.upsert_items(self.conn, [item("canvas:assignment:dawn", "2026-10-06T12:45:00Z")])
        (msg,) = [m for m in notify.build_messages(self.conn, SCHEDULE, dict(CFG, scribe_index="/x"),
                                                   datetime(2026, 10, 5, 23, 10, tzinfo=LA), LA)
                  if m[0].startswith("due:")]
        self.assertTrue(msg[2])

    def test_stale_alert_turns_over_when_quiet_ends(self):
        with mock.patch.object(status, "stale_sources",
                               return_value=[{"name": "Mail", "label": "never", "note": ""}]):
            def key(now):
                return [k for k, _, _ in notify.build_messages(self.conn, SCHEDULE, dict(CFG, scribe_index="/x"),
                                                               now, LA) if k.startswith("stale:")]
            self.assertEqual(key(datetime(2026, 10, 6, 0, 0, tzinfo=LA)), key(datetime(2026, 10, 5, 20, 0, tzinfo=LA)))
            self.assertNotEqual(key(datetime(2026, 10, 6, 7, 0, tzinfo=LA)), key(datetime(2026, 10, 6, 6, 59, tzinfo=LA)))

    def test_dst_countdown_is_real_time(self):
        store.upsert_items(self.conn, [item("canvas:assignment:x", "2026-11-01T10:00:00Z")])  # 02:00 PST
        # 01:30 PDT is 90 minutes before 02:00 PST, not 30.
        text = [t for k, t, _ in notify.build_messages(self.conn, SCHEDULE, dict(CFG, scribe_index="/x"),
                                                       datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=0), LA)
                if k.startswith("due:")]
        self.assertTrue(text and "in 30 min" not in text[0], text)

    def test_force_counts_as_priming(self):
        store.upsert_items(self.conn, [item("canvas:assignment:x", "2026-10-06T03:00:00Z")])

        class Bot:
            ready = True

            def __init__(self, *a):
                pass

            def send(self, text):
                return True, "ok"
        cfg = {"notify": dict(CFG["notify"], enabled=True), "scribe_index": "/x"}
        with mock.patch.object(notify, "Telegram", Bot), mock.patch.object(notify, "read_env", return_value={}):
            notify.tick(self.conn, SCHEDULE, cfg, datetime(2026, 10, 5, 12, 0, tzinfo=LA), LA, force=True)
        self.assertTrue(store.get_meta(self.conn, "notify_primed"))


class StatusTest(StoreTest):
    def test_failing_canvas_is_stale_even_though_the_loop_ran(self):
        store.set_meta(self.conn, "last_sync", datetime.now(UTC).isoformat())
        store.set_meta(self.conn, "canvas_ok_at", (datetime.now(UTC) - timedelta(days=2)).isoformat())
        store.set_meta(self.conn, "last_sync_error", "Canvas rejected the token (401)")
        (row,) = [r for r in status.sources({}, self.conn, store.get_meta) if r["name"] == "Canvas"]
        self.assertTrue(row["stale"])


class LectureMatchTest(StoreTest):
    def setUp(self):
        super().setUp()
        self.index = Path(self.tmp.name) / "index.json"
        self.cfg = {"scribe_index": str(self.index)}

    def collect(self, what, quote, due="2026-10-01T08:45:00-07:00"):
        lec = {"id": "L", "course": "PHIL 1000", "date": "2026-09-28", "start": "2026-09-28T08:51:26-07:00",
               "deadlines": [{"id": "scribe:L:1", "t": 1, "what": what, "quote": quote, "due": due}]}
        self.index.write_text(json.dumps({"version": 1, "lectures": [lec]}))
        lectures.collect(self.conn, self.cfg, LA)
        return store.get_meta(self.conn, lectures.LINKS_META)

    def test_an_announcement_is_not_the_deadline(self):
        store.upsert_items(self.conn, [item("canvas:announcement:1", "2026-09-30T12:00:00Z", kind="announcement",
                                            title="Reminder: reflection essay draft due Thursday")])
        self.assertEqual(self.collect("Reflection essay draft", "reflection essay draft due Thursday"), {})

    def test_a_weekday_is_not_a_match(self):
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-10-02T03:59:59Z",
                                            title="Thursday reading response")])
        self.assertEqual(self.collect("Bring a laptop", "bring your laptop on Thursday"), {})

    def test_one_shared_word_is_not_a_match(self):
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-10-02T03:59:59Z",
                                            title="Essay 1 final submission")])
        self.assertEqual(self.collect("Bring a printed copy of your essay", "bring a printed copy"), {})

    def test_a_spelled_count_is_not_a_number(self):
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-10-02T03:59:59Z",
                                            title="Week 4: articles on driverless cars")])
        self.assertEqual(list(self.collect("Read two articles on driverless cars", "read two of the articles")),
                         ["canvas:assignment:1"])

    def test_spelled_number_is_checked(self):
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-10-02T03:59:59Z", title="Homework 2")])
        self.assertEqual(self.collect("Homework three", "homework three is due"), {})

    def test_an_extension_keeps_the_match(self):
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-10-02T03:59:59Z",
                                            title="Read one of the articles")])
        store.upsert_items(self.conn, [item("canvas:assignment:1", "2026-10-07T03:59:59Z",
                                            title="Read one of the articles")])
        self.assertEqual(list(self.collect("Read an article", "read one of the articles")), ["canvas:assignment:1"])

    def test_naive_times_and_null_recaps_are_dropped(self):
        self.index.write_text(json.dumps({"version": 1, "lectures": [
            {"id": "A", "course": "PHIL 1000", "date": "2026-09-28", "start": "2026-09-28T08:51:26",
             "deadlines": []},
            {"id": "B", "course": "PHIL 1000", "date": "2026-09-28", "start": "2026-09-28T08:51:26-07:00",
             "recap": None, "deadlines": 3}]}))
        index = lectures.load(self.cfg)
        self.assertEqual([(lec["id"], lec["recap"], lec["deadlines"]) for lec in index["lectures"]], [("B", [], [])])


class CollectorTest(StoreTest):
    def test_override_false_is_not_done(self):
        entry = {"planner_override": {"marked_complete": False}, "submissions": {"submitted": True}}
        self.assertFalse(canvas._is_done(entry))

    def test_a_read_timeout_is_a_canvas_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.side_effect = socket.timeout("timed out")
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(canvas.CanvasError):
                canvas.Canvas("https://canvas.example", "t")._request("https://canvas.example/api/v1/x")

    def test_file_links_go_to_the_canvas_host_only(self):
        asked = []

        def fake(api, endpoint):
            asked.append(endpoint)
            return None
        markup = '<a data-api-endpoint="https://evil.example/api/v1/courses/1/files/2">hw</a>'
        with mock.patch.object(canvas.Canvas, "_request", return_value=({"description": markup}, "")), \
                mock.patch.object(canvas, "_file_text", fake):
            canvas.describe("https://canvas.example", "t", "https://canvas.example/courses/1/assignments/5")
        self.assertEqual(asked, ["https://canvas.example/api/v1/courses/1/files/2"])

    def test_course_label_is_the_first_code_in_the_text(self):
        schedule = {"courses": [{"code": "CS 2000"}, {"code": "CS 2001"}]}
        labels = canvas.course_labeller([{"id": 1, "course_code": "CS2001 Lab for CS2000 MERGED"}], schedule)
        self.assertEqual(labels, {1: "CS 2001"})

    def test_a_hyphenated_surname_still_matches(self):
        schedule = {"courses": [{"code": "PHIL 1000", "instructor": "Ana Ruiz-Delgado"}]}
        codes, names = mail.build_matchers(schedule)
        self.assertEqual(mail.classify({"sender": "Ana Ruiz-Delgado", "from_address": "j.c@example.edu"},
                                       codes, names), ("PHIL 1000", "instructor"))

    def test_surname_must_be_a_whole_word(self):
        codes, names = mail.build_matchers(SCHEDULE)
        self.assertEqual(mail.classify({"sender": "Min-jun Hwang", "from_address": "mh@example.edu"}, codes, names),
                         (None, None))
        self.assertEqual(mail.classify({"sender": "Ada Wang", "from_address": "aw@example.edu"}, codes, names),
                         ("PHIL 1000", "instructor"))

    def test_an_empty_parse_keeps_the_last_good_copy(self):
        cache = Path(self.tmp.name) / "coursesite.json"
        good = {"fetched_at": "x", "sites": {"CS 2000": {"url": "u", "entries": [{"date": "2026-09-28"}]}}}
        cache.write_text(json.dumps(good))
        with mock.patch.object(coursesite, "fetch", return_value="<html>redesigned</html>"):
            _, note, fetched = coursesite.refresh([{"course": "CS 2000", "url": "u"}], path=cache)
        self.assertEqual((fetched, json.loads(cache.read_text())["sites"]), (0, good["sites"]))


class SyncTest(StoreTest):
    def test_one_sync_at_a_time(self):
        with server._SYNC_LOCK:
            self.assertEqual(server.sync_once({}, {}), "sync already running")

    def test_a_source_that_raises_does_not_end_the_sync(self):
        cfg = {"timezone": "America/Los_Angeles", "canvas": {"token": "t", "base_url": "https://c.example"},
               "scribe_index": "/nonexistent"}
        with mock.patch.object(canvas, "collect", side_effect=RuntimeError("boom")), \
                mock.patch.object(mail, "collect", return_value=([], {"available": False})), \
                mock.patch("traceback.print_exc"):
            note = server.sync_once(cfg, SCHEDULE)
        self.assertIn("Canvas failed: RuntimeError: boom", note)
        self.assertIn("mail: no drop file yet", note)
        self.assertIsNotNone(store.get_meta(self.conn, "last_sync"))

    CFG = {"canvas": {"token": "t", "base_url": "https://c.example"}}

    def test_a_tick_that_missed_canvas_is_retried(self):
        store.upsert_items(self.conn, [item("canvas:assignment:7", "2026-10-06T03:00:00Z")])
        with mock.patch.object(canvas, "set_complete", side_effect=canvas.CanvasError("503")):
            server.mark_item("canvas:assignment:7", done=True, cfg=self.CFG)
        self.assertEqual(store.pending(self.conn), {"canvas:assignment:7": True})
        # The next sync reads "not done" from Canvas; the pending tick holds.
        store.upsert_items(self.conn, [item("canvas:assignment:7", "2026-10-06T03:00:00Z", done=0)])
        self.assertEqual(self.conn.execute("SELECT done FROM items").fetchone()["done"], 1)
        with mock.patch.object(canvas, "set_complete") as sent:
            self.assertEqual(server._retry_pending(self.conn, self.CFG), 0)
        sent.assert_called_once()
        self.assertEqual(store.pending(self.conn), {})

    def test_the_retry_holds_no_lock_while_canvas_answers(self):
        store.upsert_items(self.conn, [item(f"canvas:assignment:{i}", "2026-10-06T03:00:00Z") for i in (1, 2)])
        for i in (1, 2):
            store.set_pending(self.conn, f"canvas:assignment:{i}", True)

        def meanwhile(*a, **k):
            other = store.connect()
            other.execute("PRAGMA busy_timeout=100")
            other.execute("INSERT INTO notifications VALUES (?, 'now', '')", (f"k{a[3]}",))
            other.commit()
            other.close()
            raise canvas.CanvasError("slow, then 503")   # the path that counts a try
        with mock.patch.object(canvas, "set_complete", side_effect=meanwhile):
            server._retry_pending(self.conn, self.CFG)   # "database is locked" if it did
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 2)

    def test_an_untick_during_the_retry_is_kept(self):
        store.upsert_items(self.conn, [item("canvas:assignment:7", "2026-10-06T03:00:00Z")])
        store.set_pending(self.conn, "canvas:assignment:7", True)
        other = store.connect()
        with mock.patch.object(canvas, "set_complete",
                               side_effect=lambda *a, **k: store.set_pending(other, "canvas:assignment:7", False)):
            server._retry_pending(self.conn, self.CFG)
        other.close()
        self.assertEqual(store.pending(self.conn), {"canvas:assignment:7": False})

    def test_a_tick_for_a_vanished_item_is_dropped(self):
        store.set_pending(self.conn, "canvas:assignment:gone", True)
        with mock.patch.object(canvas, "set_complete") as sent:
            server._retry_pending(self.conn, self.CFG)
        sent.assert_not_called()
        self.assertEqual(store.pending(self.conn), {})

    def test_an_unopenable_lock_file_does_not_wedge_syncing(self):
        with mock.patch.object(server.os, "open", side_effect=PermissionError("root-owned")):
            with self.assertRaises(PermissionError):
                server.sync_once({}, {})
        self.assertNotEqual(server.sync_once({"timezone": "UTC", "canvas": {"token": ""}, "scribe_index": "/x"},
                                             SCHEDULE), "sync already running")


if __name__ == "__main__":
    unittest.main()
