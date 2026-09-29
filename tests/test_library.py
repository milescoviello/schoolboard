"""/lectures/: scribe's phone library, served with byte ranges (Safari needs
them to play and seek audio) and nothing outside its folder. Invented data."""
import http.client
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import config, server  # noqa: E402


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pod = root / "scribe-pod"
        self.pod.mkdir()
        (self.pod / "index.html").write_text("<h1>Lectures</h1>")
        (self.pod / "a.m4a").write_bytes(bytes(range(256)) * 4)       # 1024 bytes
        (root / "config.json").write_text("{\"canvas\": {\"token\": \"secret\"}}")
        patch = mock.patch.object(config, "load_config", return_value={"scribe_pod": str(self.pod)})
        patch.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, method="GET", **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request(method, path, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r, body

    def test_index_and_redirect(self):
        r, body = self.get("/lectures/")
        self.assertEqual((r.status, body), (200, b"<h1>Lectures</h1>"))
        self.assertEqual(self.get("/lectures")[0].status, 303)

    def test_full_file_advertises_ranges(self):
        r, body = self.get("/lectures/a.m4a")
        self.assertEqual((r.status, len(body), r.getheader("Accept-Ranges"), r.getheader("Content-Type")),
                         (200, 1024, "bytes", "audio/mp4"))

    def test_byte_range(self):
        r, body = self.get("/lectures/a.m4a", Range="bytes=10-19")
        self.assertEqual((r.status, body, r.getheader("Content-Range")), (206, bytes(range(10, 20)), "bytes 10-19/1024"))

    def test_open_ended_and_suffix_ranges(self):
        self.assertEqual(len(self.get("/lectures/a.m4a", Range="bytes=1000-")[1]), 24)
        r, body = self.get("/lectures/a.m4a", Range="bytes=-4")
        self.assertEqual((r.status, body), (206, bytes([252, 253, 254, 255])))

    def test_range_past_the_end(self):
        self.assertEqual(self.get("/lectures/a.m4a", Range="bytes=5000-")[0].status, 416)

    def test_nothing_outside_the_library(self):
        for path in ("/lectures/../config.json", "/lectures/%2e%2e/config.json", "/lectures/nope.m4a",
                     "/lectures/index.py"):
            self.assertEqual(self.get(path)[0].status, 404, path)

    def test_null_byte_is_a_404(self):
        self.assertEqual(self.get("/lectures/a%00.m4a")[0].status, 404)

    def test_head(self):
        r, body = self.get("/lectures/a.m4a", method="HEAD")
        self.assertEqual((r.status, r.getheader("Content-Length"), body), (200, "1024", b""))


class PublicLibraryTest(unittest.TestCase):
    """The tunnel's listener: no cookie, no lectures."""

    def setUp(self):
        cfg = {"auth": {"password_hash": "x", "session_secret": "s"}}
        for name, value in (("load_config", cfg), ("save_config", None)):  # never the real config.json
            patch = mock.patch.object(config, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.PublicHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def test_get_and_head_go_to_login(self):
        for method in ("GET", "HEAD"):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
            conn.request(method, "/lectures/a.m4a")
            r = conn.getresponse()
            r.read()
            conn.close()
            self.assertEqual((r.status, r.getheader("Location")), (302, "/login"), method)


if __name__ == "__main__":
    unittest.main()
