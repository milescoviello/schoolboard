"""HTTP server for the board.

Single-purpose and deliberately small: render on request, sync Canvas on a timer
in the background. A failed sync never blanks the page — the last good data stays
on screen with an honest "last synced" line in the footer.
"""
import fcntl
import json
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import canvas, config, coursesite, ics, mail, notify, render, store, timetable


def sync_once(cfg=None, schedule=None):
    """Pull every source into the store. Returns a human-readable result line.

    Sources are independent: mail still updates when Canvas has no token, and a
    Canvas outage never blocks mail.
    """
    cfg = cfg or config.load_config()
    schedule = schedule or config.load_schedule()
    conn = store.connect()
    parts = []
    try:
        token = cfg["canvas"]["token"]
        if token:
            try:
                items, report = canvas.collect(cfg["canvas"]["base_url"], token, schedule)
                new, updated = store.upsert_items(conn, items)
                note = f"Canvas: {report['courses']} courses, {new} new, {updated} updated"
                if report["notes"]:
                    note += " (" + "; ".join(report["notes"]) + ")"
                parts.append(note)
                store.set_meta(conn, "grades", report.get("grades") or [])
                store.set_meta(conn, "last_sync_error", None)
            except canvas.CanvasError as exc:
                store.set_meta(conn, "last_sync_error", str(exc))
                parts.append(f"Canvas failed: {exc}")
        else:
            parts.append("Canvas not connected")

        mail_items, mail_report = mail.collect(schedule)
        if mail_report.get("available"):
            store.upsert_items(conn, mail_items)
            parts.append(f"mail: {mail_report['relevant']} of "
                         f"{mail_report['scanned']} relevant")
            store.set_meta(conn, "mail_dropped", mail_report.get("dropped", 0))
            store.set_meta(conn, "mail_generated_at", mail_report.get("generated_at"))
        else:
            parts.append("mail: no drop file yet")

        if cfg.get("ics_feeds"):
            ics_items, ics_note = ics.collect(cfg["ics_feeds"])
            store.upsert_items(conn, ics_items)
            parts.append(f"calendar: {ics_note}")

        # Course sites are static pages; no need to hit them every sync.
        every = float(cfg.get("course_site_refresh_hours", 6)) * 3600
        last_site = store.get_meta(conn, "course_site_at")
        stale = True
        if last_site:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(last_site)).total_seconds()
            stale = age > every
        if stale and cfg.get("course_sites"):
            _, site_note = coursesite.refresh(cfg["course_sites"])
            store.set_meta(conn, "course_site_at", datetime.now(timezone.utc).isoformat())
            parts.append(f"sites: {site_note}")

        store.set_meta(conn, "last_sync", datetime.now().astimezone().isoformat())
        note = " · ".join(parts)
        store.set_meta(conn, "last_sync_note", note)
        return note
    finally:
        conn.close()


def _sync_note(conn, cfg):
    err = store.get_meta(conn, "last_sync_error")
    last = store.get_meta(conn, "last_sync")
    if err:
        return f"Canvas sync failing — {err}"
    if not last:
        return "first sync pending"
    when = datetime.fromisoformat(last)
    mins = int((datetime.now().astimezone() - when).total_seconds() // 60)
    ago = "just now" if mins < 1 else (f"{mins} min ago" if mins < 90 else f"{mins // 60}h ago")
    return f"synced {ago} · {store.get_meta(conn, 'last_sync_note') or ''}".strip(" ·")


def build_page():
    cfg = config.load_config()
    schedule = config.load_schedule()
    tz = timetable.tzinfo(cfg["timezone"])
    now = datetime.now(tz)
    conn = store.connect()
    try:
        tasks = render.render_tasks(store.upcoming(conn), now, tz)
        anns = render.render_announcements(store.announcements(conn), now, tz)
        mails = render.render_mail(store.mail(conn), now, tz)
        grades = render.render_grades(store.get_meta(conn, "grades") or [])
        changed = render.render_changed(store.recently_changed(conn), now, tz)
        appts = render.render_appointments(store.appointments(conn), now, tz)
        note = _sync_note(conn, cfg)
    finally:
        conn.close()
    return render.page(schedule, now, tz, tasks, anns, note, mails=mails,
                       grades=grades, changed=changed, appts=appts,
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

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/done":
            self._send("<h1>404</h1>", status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        fields = urllib.parse.parse_qs(body)
        item_id = (fields.get("id") or [""])[0]
        undo = (fields.get("undo") or [""])[0] == "1"
        try:
            mark_item(item_id, done=not undo)
        except Exception:
            traceback.print_exc()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(build_page())
            elif path == "/sync":
                self._send(f"<pre>{sync_once()}</pre><p><a href='/'>back</a></p>")
            elif path == "/manifest.webmanifest":
                self._send(json.dumps({
                    "name": "schoolboard", "short_name": "school",
                    "start_url": "/", "display": "standalone",
                    "background_color": "#16182A", "theme_color": "#16182A",
                }), ctype="application/manifest+json")
            elif path == "/healthz":
                self._send("ok", ctype="text/plain; charset=utf-8")
            else:
                self._send("<h1>404</h1>", status=404)
        except Exception:
            self._send(f"<pre>{traceback.format_exc()}</pre>", status=500)


def mark_item(item_id, done=True, cfg=None):
    """Mark one stored item complete, writing through to Canvas when possible.

    Local-only is a fallback, not the goal: if the state lives solely in this
    SQLite file it disappears the moment the database is rebuilt, and the Canvas
    app keeps nagging.
    """
    cfg = cfg or config.load_config()
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return False, f"no item {item_id}"
        note = "locally"
        parts = item_id.split(":")
        if parts[0] == "canvas" and len(parts) >= 3 and cfg["canvas"]["token"]:
            try:
                canvas.set_complete(cfg["canvas"]["base_url"], cfg["canvas"]["token"],
                                    parts[1], parts[2], complete=done)
                note = "in Canvas"
            except canvas.CanvasError as exc:
                note = f"locally only ({exc})"
        conn.execute("UPDATE items SET done=? WHERE id=?", (1 if done else 0, item_id))
        conn.commit()
        verb = "done" if done else "not done"
        return True, f"{row['title']} marked {verb} {note}"
    finally:
        conn.close()


def find_items(text, done=None):
    conn = store.connect()
    try:
        sql = "SELECT * FROM items WHERE title LIKE ? AND kind NOT IN ('announcement','mail','appointment')"
        args = [f"%{text}%"]
        if done is not None:
            sql += " AND done=?"
            args.append(1 if done else 0)
        return conn.execute(sql + " ORDER BY due_utc", args).fetchall()
    finally:
        conn.close()


def notify_once(cfg=None, schedule=None, force=False):
    """One notification pass. Cheap enough to run every minute."""
    cfg = cfg or config.load_config()
    schedule = schedule or config.load_schedule()
    tz = timetable.tzinfo(cfg["timezone"])
    conn = store.connect()
    try:
        return notify.tick(conn, schedule, cfg, datetime.now(tz), tz, force=force)
    finally:
        conn.close()


def _notify_loop():
    """Every 60s. A class starting in 15 minutes cannot wait for a 15-min sync."""
    while True:
        try:
            notify_once()
        except Exception:
            traceback.print_exc()
        time.sleep(60)


def _sync_loop(interval_minutes):
    while True:
        try:
            sync_once()
        except Exception:
            traceback.print_exc()
        time.sleep(max(60, interval_minutes * 60))


def tailnet_address(iface="tailscale0"):
    """The host's own tailnet IPv4, or None if Tailscale isn't up yet.

    Binding the server here directly (rather than proxying via `tailscale serve`)
    means the IP, the short name and the FQDN all work. `tailscale serve` matches
    on the Host header, so hitting the bare tailnet IP returns 404.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        packed = struct.pack("256s", iface.encode()[:15])
        addr = fcntl.ioctl(sock.fileno(), 0x8915, packed)[20:24]  # SIOCGIFADDR
        sock.close()
        return socket.inet_ntoa(addr)
    except OSError:
        pass
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                             text=True, timeout=10)
        return (out.stdout.strip().splitlines() or [None])[0]
    except (OSError, subprocess.SubprocessError):
        return None


def _listen(addr, port, label):
    httpd = ThreadingHTTPServer((addr, port), Handler)
    print(f"schoolboard listening on http://{addr}:{port}  ({label})", flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _bind_tailnet_when_ready(port, attempts=60, delay=10):
    """Tailscale often isn't up when this starts at boot. Keep trying rather
    than silently ending up localhost-only until someone notices."""
    for _ in range(attempts):
        addr = tailnet_address()
        if addr:
            try:
                _listen(addr, port, "tailnet")
                return
            except OSError as exc:
                print(f"tailnet bind on {addr} failed: {exc}", flush=True)
                return
        time.sleep(delay)
    print("tailnet address never appeared; serving on localhost only", flush=True)


def serve(bind=None, port=None):
    cfg = config.load_config()
    bind = bind or cfg["bind"]
    port = port or cfg["port"]
    threading.Thread(target=_sync_loop, args=(cfg["sync_minutes"],), daemon=True).start()
    if cfg.get("notify", {}).get("enabled"):
        threading.Thread(target=_notify_loop, daemon=True).start()
    print(f"schoolboard starting (timezone {cfg['timezone']})", flush=True)
    _listen(bind, port, "local")
    if cfg.get("bind_tailnet", True):
        threading.Thread(target=_bind_tailnet_when_ready, args=(port,), daemon=True).start()
    while True:
        time.sleep(3600)
