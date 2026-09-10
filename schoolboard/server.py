"""HTTP server for the board.

Single-purpose and deliberately small: render on request, sync Canvas on a timer
in the background. A failed sync never blanks the page — the last good data stays
on screen with an honest "last synced" line in the footer.
"""
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import canvas, config, render, store, timetable


def sync_once(cfg=None, schedule=None):
    """Pull Canvas into the store. Returns a human-readable result line."""
    cfg = cfg or config.load_config()
    schedule = schedule or config.load_schedule()
    token = cfg["canvas"]["token"]
    if not token:
        return "Canvas not connected"
    conn = store.connect()
    try:
        items, report = canvas.collect(cfg["canvas"]["base_url"], token, schedule)
        new, updated = store.upsert_items(conn, items)
        store.set_meta(conn, "last_sync", datetime.now().astimezone().isoformat())
        store.set_meta(conn, "last_sync_error", None)
        note = f"{report['courses']} courses, {new} new, {updated} updated"
        if report["notes"]:
            note += " (" + "; ".join(report["notes"]) + ")"
        store.set_meta(conn, "last_sync_note", note)
        return note
    except canvas.CanvasError as exc:
        store.set_meta(conn, "last_sync_error", str(exc))
        return f"sync failed: {exc}"
    finally:
        conn.close()


def _sync_note(conn, cfg):
    if not cfg["canvas"]["token"]:
        return "Canvas not connected"
    err = store.get_meta(conn, "last_sync_error")
    last = store.get_meta(conn, "last_sync")
    if err:
        return f"Canvas sync failing — {err}"
    if not last:
        return "Canvas connected, first sync pending"
    when = datetime.fromisoformat(last)
    mins = int((datetime.now().astimezone() - when).total_seconds() // 60)
    ago = "just now" if mins < 1 else (f"{mins} min ago" if mins < 90 else f"{mins // 60}h ago")
    return f"Canvas synced {ago} · {store.get_meta(conn, 'last_sync_note') or ''}".strip(" ·")


def build_page():
    cfg = config.load_config()
    schedule = config.load_schedule()
    tz = timetable.tzinfo(cfg["timezone"])
    now = datetime.now(tz)
    conn = store.connect()
    try:
        tasks = render.render_tasks(store.upcoming(conn), now, tz)
        anns = render.render_announcements(store.announcements(conn), now, tz)
        note = _sync_note(conn, cfg)
    finally:
        conn.close()
    return render.page(schedule, now, tz, tasks, anns, note,
                       canvas_ready=bool(cfg["canvas"]["token"]),
                       refresh=cfg["refresh_seconds"])


class Handler(BaseHTTPRequestHandler):
    server_version = "schoolboard"

    def log_message(self, fmt, *args):
        pass  # a kiosk refreshing every 60s would otherwise flood syslog

    def _send(self, body, status=200, ctype="text/html; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(build_page())
            elif path == "/sync":
                self._send(f"<pre>{sync_once()}</pre><p><a href='/'>back</a></p>")
            elif path == "/healthz":
                self._send("ok", ctype="text/plain; charset=utf-8")
            else:
                self._send("<h1>404</h1>", status=404)
        except Exception:
            self._send(f"<pre>{traceback.format_exc()}</pre>", status=500)


def _sync_loop(interval_minutes):
    while True:
        try:
            sync_once()
        except Exception:
            traceback.print_exc()
        time.sleep(max(60, interval_minutes * 60))


def serve(bind=None, port=None):
    cfg = config.load_config()
    bind = bind or cfg["bind"]
    port = port or cfg["port"]
    if cfg["canvas"]["token"]:
        threading.Thread(target=_sync_loop, args=(cfg["sync_minutes"],), daemon=True).start()
    httpd = ThreadingHTTPServer((bind, port), Handler)
    print(f"schoolboard on http://{bind}:{port}  (timezone {cfg['timezone']})")
    httpd.serve_forever()
