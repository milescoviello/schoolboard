"""Both listeners, over real HTTP: the error page, the public gate, the login
throttle, POST limits, cross-site POSTs and HEAD. Invented data only.

config.json is mocked throughout, save_config included: auth.ensure_secret
writes it, and a test must never leave one in the repo.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import auth, config, server, store  # noqa: E402

PASSWORD = "correct horse"
HASH = auth.hash_password(PASSWORD)


class ServerTest(unittest.TestCase):
    handler = server.Handler
    cfg = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patch in (mock.patch.object(config, "load_config", return_value=json.loads(json.dumps(self.cfg))),
                      mock.patch.object(config, "save_config"),
                      mock.patch.object(store, "DB_PATH", Path(self.tmp.name) / "test.db"),
                      mock.patch.object(server, "THROTTLE", auth.Throttle()),
                      mock.patch.object(server, "print", create=True)):   # "login failed from ..."
            patch.start()
            self.addCleanup(patch.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data


class TrustedTest(ServerTest):
    def test_the_error_page_escapes_the_traceback(self):
        with mock.patch.object(server, "build_page", side_effect=RuntimeError("<img src=x onerror=alert(1)>")), \
                mock.patch("traceback.print_exc"):
            r, body = self.request("GET", "/")
        self.assertEqual(r.status, 500)
        self.assertNotIn(b"<img", body)
        self.assertIn(b"&lt;img", body)

    def test_head_answers_like_get(self):
        with mock.patch.object(server, "build_page", return_value="<html>" + "x" * 3000):
            r, body = self.request("HEAD", "/")
        self.assertEqual((r.status, body), (200, b""))

    def test_cross_site_post_is_refused(self):
        r, _ = self.request("POST", "/add", body="title=x", **{"Sec-Fetch-Site": "cross-site",
                                                               "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 403)
        r, _ = self.request("POST", "/add", body="title=x", Origin="https://evil.example",
                            **{"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 403)

    def test_same_origin_post_goes_through(self):
        r, _ = self.request("POST", "/add", body="title=Read+ch+4", **{"Sec-Fetch-Site": "same-origin",
                                                                       "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 303)

    def test_bad_and_huge_bodies_are_refused(self):
        for length in ("abc", "-1", str(10 ** 8)):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
            conn.putrequest("POST", "/add")
            conn.putheader("Content-Length", length)
            conn.endheaders()
            r = conn.getresponse()
            conn.close()
            self.assertEqual(r.status, 413, length)

    def test_head_has_no_side_effects(self):
        with mock.patch.object(server, "work_items") as work:
            self.assertEqual(self.request("HEAD", "/work.json")[0].status, 405)
            self.assertEqual(self.request("HEAD", "/logout")[0].status, 405)
        work.assert_not_called()

    def test_odd_query_values_fall_back(self):
        with mock.patch.object(server, "build_page", return_value="<html>" + "x" * 3000) as build:
            self.assertEqual(self.request("GET", "/?week=0001-01-01")[0].status, 200)
            self.assertEqual(build.call_args.kwargs["week"], None)
        with mock.patch.object(server, "work_items", return_value={}) as work:
            self.assertEqual(self.request("GET", "/work.json?days=%C2%B2")[0].status, 200)
            self.assertEqual(work.call_args.args[1], 14)


class PublicTest(ServerTest):
    handler = server.PublicHandler
    cfg = {"auth": {"password_hash": HASH, "session_secret": "s"}}

    def login(self, password, ip="203.0.113.5"):
        return self.request("POST", "/login", body=f"password={password}", **{
            "CF-Connecting-IP": ip, "Content-Type": "application/x-www-form-urlencoded"})

    def test_the_error_page_says_nothing(self):
        token = auth.issue("s")
        with mock.patch.object(server, "build_page", side_effect=RuntimeError("/home/secret/path")), \
                mock.patch("traceback.print_exc"):
            r, body = self.request("GET", "/", Cookie=f"{auth.COOKIE}={token}")
        self.assertEqual(r.status, 500)
        self.assertNotIn(b"secret", body)

    def test_a_mangled_cookie_is_just_logged_out(self):
        r, _ = self.request("GET", "/", Cookie=f'{auth.COOKIE}="1.2.\\351"')
        self.assertEqual((r.status, r.getheader("Location")), (302, "/login"))

    def test_the_session_survives_another_sites_odd_cookie(self):
        with mock.patch.object(server, "build_page", return_value="<html>" + "x" * 3000):
            r, _ = self.request("GET", "/", Cookie=f'consent={{"a":false}}; {auth.COOKIE}={auth.issue("s")}')
        self.assertEqual(r.status, 200)

    def test_the_throttle_is_per_client(self):
        for _ in range(6):
            self.assertEqual(self.login("wrong")[0].status, 401)
        self.assertEqual(self.login(PASSWORD)[0].status, 429)
        # Someone else behind the same tunnel is not locked out by them.
        self.assertEqual(self.login(PASSWORD, ip="198.51.100.7")[0].status, 302)

    def test_ipv6_counts_by_64(self):
        self.assertEqual(auth.client_key("2001:db8::1"), auth.client_key("2001:db8::ffff"))


SCHEDULE = {"term": "Fall 2026", "term_start": "2026-09-09", "term_end": "2026-12-13", "timezone": "America/Los_Angeles",
            "no_class_days": [], "courses": [{"code": "PHIL 1000", "title": "Ethics", "days": ["Mon", "Thu"],
                                              "start": "08:45", "end": "10:25", "room": "Room 1"}]}


class PageTest(PublicTest):
    """What the browser pass on phone and desktop turned up. The whole page,
    so the full default config and an invented timetable (schedule.json is
    private and not in the repo)."""
    cfg = dict(json.loads(json.dumps(config.DEFAULTS)), auth={"password_hash": HASH, "session_secret": "s"})

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(config, "load_schedule", return_value=SCHEDULE)
        patch.start()
        self.addCleanup(patch.stop)

    def page(self):
        r, body = self.request("GET", "/", Cookie=f"{auth.COOKIE}={auth.issue('s')}")
        self.assertEqual(r.status, 200)
        return body.decode()

    def test_the_manifest_loads_before_login(self):
        r, body = self.request("GET", "/manifest.webmanifest")
        self.assertEqual((r.status, json.loads(body)["start_url"]), (200, "/"))

    def test_sign_out_is_offered_here_only(self):
        self.assertIn('href="/logout"', self.page())

    def test_no_token_still_lists_work_of_his_own(self):
        conn = store.connect()
        store.add_personal(conn, "Email advisor", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        conn.close()
        html = self.page()
        self.assertTrue("Canvas isn" in html and "Email advisor" in html)

    def test_a_ticked_undated_item_can_be_reopened(self):
        conn = store.connect()
        item_id = store.add_personal(conn, "Buy a notebook")
        conn.execute("UPDATE items SET done=1 WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        html = self.page()
        self.assertIn("Buy a notebook", html.split("Finished", 1)[1])

    def test_a_tick_that_missed_canvas_says_so(self):
        conn = store.connect()
        store.upsert_items(conn, [{"id": "canvas:assignment:1", "source": "canvas", "kind": "assignment",
                                   "course": "X", "title": "HW", "due_utc": "2026-09-20T00:00:00Z", "done": 1}])
        store.set_pending(conn, "canvas:assignment:1", True)
        conn.close()
        self.assertIn("not in Canvas yet", self.page())


class TrustedPageTest(ServerTest):
    cfg = PageTest.cfg

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(config, "load_schedule", return_value=SCHEDULE)
        patch.start()
        self.addCleanup(patch.stop)

    def test_no_sign_out_where_there_is_no_session(self):
        r, body = self.request("GET", "/")
        self.assertNotIn(b'href="/logout"', body)


class NoPasswordTest(ServerTest):
    handler = server.PublicHandler
    cfg = {"auth": {"password_hash": "", "session_secret": "s"}}

    def test_the_public_listener_fails_closed(self):
        self.assertEqual(self.request("GET", "/due.json")[0].status, 302)
        r, body = self.request("POST", "/login", body="password=x",
                               **{"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 503)
        self.assertIn(b"set-password", body)


class ConfigTest(unittest.TestCase):
    def test_save_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "ROOT", Path(tmp)), \
                mock.patch.dict(os.environ, {"SCHOOLBOARD_CANVAS_TOKEN": "from-env"}):
            (Path(tmp) / "config.json").write_text(json.dumps({"canvas": {"token": "on-disk"}}))
            cfg = config.load_config()
            cfg["auth"]["password_hash"] = "h"
            config.save_config(cfg)
            saved = json.loads((Path(tmp) / "config.json").read_text())
            mode = (Path(tmp) / "config.json").stat().st_mode & 0o777
        self.assertEqual(saved, {"auth": {"password_hash": "h"}, "canvas": {"token": "on-disk"}})
        self.assertEqual(mode, 0o600)
        self.assertEqual(config.DEFAULTS["auth"]["password_hash"], "")

    def test_a_pinned_value_survives_a_save(self):
        # Equal to today's default, but set on purpose: it must stay in the file.
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "ROOT", Path(tmp)):
            (Path(tmp) / "config.json").write_text(json.dumps({"public_port": config.DEFAULTS["public_port"]}))
            cfg = config.load_config()
            cfg["walk_minutes"] = 12
            config.save_config(cfg)
            saved = json.loads((Path(tmp) / "config.json").read_text())
        self.assertEqual(saved, {"public_port": config.DEFAULTS["public_port"], "walk_minutes": 12})


if __name__ == "__main__":
    unittest.main()
