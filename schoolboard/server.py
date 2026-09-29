"""HTTP server for the board.

Single-purpose and deliberately small: render on request, sync Canvas on a timer
in the background. A failed sync never blanks the page — the last good data stays
on screen with an honest "last synced" line in the footer.
"""
import fcntl
import json
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import traceback
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import auth, canvas, config, coursesite, ics, lectures, mail, notify, render, status, store, timetable


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

        # After Canvas, so a deadline said in class can be matched to its Canvas item.
        parts.append(lectures.collect(conn, cfg, timetable.tzinfo(cfg["timezone"]))
                     or "lectures: no scribe export yet")

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


def build_page(week=None, focus_day=None):
    cfg = config.load_config()
    schedule = config.load_schedule()
    tz = timetable.tzinfo(cfg["timezone"])
    now = datetime.now(tz)
    conn = store.connect()
    try:
        colours = render.colour_map(schedule)
        tasks = render.render_tasks(store.upcoming(conn), now, tz, colours=colours,
                                    horizon_days=cfg.get("due_soon_days", 10),
                                    links=store.get_meta(conn, lectures.LINKS_META) or {})
        anns = render.render_announcements(store.announcements(conn), now, tz)
        mails = render.render_mail(store.mail(conn), now, tz, colours=colours)
        grades = render.render_grades(store.get_meta(conn, "grades") or [], colours=colours)
        changed = render.render_changed(store.recently_changed(conn), now, tz)
        appts = render.render_appointments(store.appointments(conn), now, tz, colours=colours)
        completed = render.render_completed(store.completed(conn), now, tz, colours=colours)
        workload = store.workload(conn)
        personal = render.render_personal(store.personal(conn), now, tz)
        sources = render.render_sources(status.sources(cfg, conn, store.get_meta))
        note = _sync_note(conn, cfg)
    finally:
        conn.close()
    return render.page(schedule, now, tz, tasks, anns, note, mails=mails,
                       grades=grades, changed=changed, appts=appts,
                       week=week, workload=workload, completed=completed,
                       focus_day=focus_day, personal=personal, sources=sources,
                       walk_minutes=cfg.get("walk_minutes", 0),
                       theme=cfg.get("theme", "light"),
                       canvas_ready=bool(cfg["canvas"]["token"]),
                       refresh=cfg["refresh_seconds"])


def next_meeting(cfg=None, schedule=None, now=None):
    """The next meeting to walk to, as data rather than HTML.

    Polled by dormbot on .148 for its leave-by nudge. It reuses the board's own
    timetable logic and `walk_minutes`, so the nudge and the page's "Leave by"
    line can never disagree. `leave_by` is given for later days too; deciding
    whether a meeting is today is the caller's job.
    """
    cfg = cfg or config.load_config()
    schedule = schedule or config.load_schedule()
    tz = timetable.tzinfo(cfg["timezone"])
    now = now or datetime.now(tz)
    _, nxt = timetable.current_and_next(schedule, now, tz)
    walk = int(cfg.get("walk_minutes") or 0)
    body = {"now": now.isoformat(timespec="seconds"), "next": None,
            "walk_minutes": walk, "leave_by": None}
    if nxt:
        course = nxt.course
        body["next"] = {"code": course["code"], "title": course.get("title", ""),
                        "room": course.get("room", ""),
                        "start": nxt.start.isoformat(), "end": nxt.end.isoformat()}
        if walk:
            body["leave_by"] = (nxt.start - timedelta(minutes=walk)).isoformat()
    return body


def due_items(cfg=None, now=None):
    """The "Due soon" column as data rather than HTML.

    Polled by dormbot on .148 to answer "hermes, what's due". Same rows and the
    same horizon as render.render_tasks (store.upcoming + cfg["due_soon_days"]),
    so the voice answer and the page can never disagree. Times are in the
    config timezone, never the host's.
    """
    cfg = cfg or config.load_config()
    tz = timetable.tzinfo(cfg["timezone"])
    now = now or datetime.now(tz)
    horizon = int(cfg.get("due_soon_days", 10))
    cutoff = now + timedelta(days=horizon)
    items = []
    conn = store.connect()
    try:
        for row in store.upcoming(conn):
            due = render.parse_utc(row["due_utc"])
            if due is None:
                continue
            local = due.astimezone(tz)
            if local > cutoff:
                continue
            items.append({"course": row["course"] or "", "title": row["title"] or "",
                          "kind": row["kind"] or "", "due": local.isoformat(),
                          "overdue": local < now, "url": row["url"] or ""})
    finally:
        conn.close()
    return {"now": now.isoformat(timespec="seconds"), "horizon_days": horizon, "items": items}


DESCRIPTION_CACHE_HOURS = 6


def work_items(course=None, days=14, cfg=None, now=None):
    """Upcoming Canvas work with its full text, for scribe's homework ↔ lecture
    links (scribe runs on the laptop, which doesn't hold the Canvas token).
    Descriptions are fetched from Canvas on demand and cached in meta for a few
    hours, so asking again doesn't hit Canvas."""
    cfg = cfg or config.load_config()
    tz = timetable.tzinfo(cfg["timezone"])
    now = now or datetime.now(tz)
    start, end = now - timedelta(days=3), now + timedelta(days=days)
    token = cfg["canvas"]["token"]
    items = []
    conn = store.connect()
    try:
        for row in store.upcoming(conn, limit=80):
            due = render.parse_utc(row["due_utc"])
            if (due is None or not str(row["id"]).startswith("canvas:") or (course and row["course"] != course)
                    or not start <= due.astimezone(tz) <= end):
                continue
            key = f"desc:{row['id']}"
            cached = store.get_meta(conn, key)
            fresh = cached and "got" in cached and (datetime.now(timezone.utc) - datetime.fromisoformat(cached["at"])
                                < timedelta(hours=DESCRIPTION_CACHE_HOURS))
            got = cached.get("got") if cached else None
            if not fresh and token:
                try:
                    got = canvas.describe(cfg["canvas"]["base_url"], token, row["url"])
                    store.set_meta(conn, key, {"at": datetime.now(timezone.utc).isoformat(), "got": got})
                except (canvas.CanvasError, OSError, ValueError):
                    pass  # keep the cached text, if any; a read timeout is an OSError, not a CanvasError
            got = got or {"text": "", "files": []}
            items.append({"id": row["id"], "course": row["course"] or "", "title": row["title"] or "",
                          "kind": row["kind"] or "", "due": due.astimezone(tz).isoformat(),
                          "url": row["url"] or "", "description": got["text"], "files": got["files"]})
    finally:
        conn.close()
    return {"now": now.isoformat(timespec="seconds"), "items": items}


THROTTLE = auth.Throttle()


class Handler(BaseHTTPRequestHandler):
    server_version = "schoolboard"
    # The trusted listener (localhost + tailnet). Tailscale has already
    # authenticated the caller, so a password there is friction without benefit.
    requires_auth = False

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

    # --- scribe's lecture library ------------------------------------------
    LIBRARY_TYPES = {".html": "text/html; charset=utf-8", ".m4a": "audio/mp4",
                     ".xml": "application/rss+xml; charset=utf-8", ".json": "application/json"}

    def _library(self, path, head=False):
        """scribe's phone library (copied to ~/scribe-pod by `scribe process` on the
        laptop): static files, with byte ranges, which Safari needs to play and
        seek audio. Same gate as every other page: open on the trusted
        listener, login on the public one."""
        root = Path(config.load_config().get("scribe_pod") or "~/scribe-pod").expanduser().resolve()
        name = urllib.parse.unquote(path[len("/lectures/"):]) or "index.html"
        try:
            target = (root / name).resolve()
        except (OSError, ValueError):  # "%00" in the path
            target = root
        ctype = self.LIBRARY_TYPES.get(target.suffix)
        if root not in target.parents or not ctype or not target.is_file():
            self._send("<h1>404</h1>", status=404)
            return
        size = target.stat().st_size
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1
            else:  # the last N bytes
                start, end = max(size - int(m[2]), 0), size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache" if ctype.startswith(("text/", "application/rss")) else "max-age=3600")
        self.end_headers()
        if head:
            return
        with open(target, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # the player moved on (seeking closes connections all the time)
                left -= len(chunk)

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if not self._authed():
            self._to_login()
        elif path.startswith("/lectures/"):
            self._library(path, head=True)
        else:
            self.send_response(404)
            self.end_headers()

    # --- auth helpers ----------------------------------------------------
    def _secret(self):
        cfg = config.load_config()
        return auth.ensure_secret(cfg, config.save_config)

    def _authed(self):
        if not self.requires_auth:
            return True
        cfg = config.load_config()
        if not (cfg.get("auth") or {}).get("password_hash"):
            return True          # no password set yet; do not lock him out
        token = auth.read_cookie(self.headers.get("Cookie"))
        return auth.valid(token, self._secret())

    def _to_login(self):
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _handle_login_post(self, fields):
        cfg = config.load_config()
        stored = (cfg.get("auth") or {}).get("password_hash")
        key = self.client_address[0]
        if THROTTLE.blocked(key):
            self._send(render.login_page(retry_after=THROTTLE.window), status=429)
            return
        password = (fields.get("password") or [""])[0]
        if stored and auth.verify_password(password, stored):
            THROTTLE.clear(key)
            token = auth.issue(self._secret())
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", auth.cookie_header(token))
            self.end_headers()
            return
        THROTTLE.record(key)
        self._send(render.login_page(error="That password is not right."), status=401)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}
        if path == "/login":
            self._handle_login_post(fields)
            return
        if not self._authed():
            self._to_login()
            return
        if path == "/add":
            title = (fields.get("title") or [""])[0].strip()
            raw_due = (fields.get("due") or [""])[0].strip()
            if title:
                due_utc = None
                if raw_due:
                    try:
                        cfg = config.load_config()
                        tz = timetable.tzinfo(cfg["timezone"])
                        local = datetime.fromisoformat(raw_due).replace(
                            hour=23, minute=59, tzinfo=tz)
                        due_utc = local.astimezone(timezone.utc).isoformat()
                    except ValueError:
                        due_utc = None
                conn = store.connect()
                try:
                    store.add_personal(conn, title, due_utc)
                finally:
                    conn.close()
            self._redirect("/")
            return
        if path == "/delete":
            conn = store.connect()
            try:
                store.delete_item(conn, (fields.get("id") or [""])[0])
            finally:
                conn.close()
            self._redirect("/")
            return
        if path != "/done":
            self._send("<h1>404</h1>", status=404)
            return
        item_id = (fields.get("id") or [""])[0]
        undo = (fields.get("undo") or [""])[0] == "1"
        try:
            mark_item(item_id, done=not undo)
        except Exception:
            traceback.print_exc()
        self._redirect("/")

    def _redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/login":
                if self._authed():
                    self.send_response(302); self.send_header("Location", "/"); self.end_headers()
                else:
                    self._send(render.login_page())
                return
            if path == "/logout":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Set-Cookie", auth.clear_header())
                self.end_headers()
                return
            if path == "/healthz":
                ok, detail = health()
                self._send(detail, status=200 if ok else 500,
                           ctype="text/plain; charset=utf-8")
                return
            if not self._authed():
                self._to_login()
                return
            if path in ("/", "/index.html"):
                query = urllib.parse.parse_qs(self.path.partition("?")[2])

                def as_date(key):
                    raw = (query.get(key) or [""])[0]
                    try:
                        return date.fromisoformat(raw) if raw else None
                    except ValueError:
                        return None

                focus = as_date("day")
                week = as_date("week")
                if week:
                    week -= timedelta(days=week.weekday())
                elif focus:
                    week = focus - timedelta(days=focus.weekday())
                self._send(build_page(week=week, focus_day=focus))
            elif path == "/next.json":
                self._send(json.dumps(next_meeting()), ctype="application/json")
            elif path == "/due.json":
                self._send(json.dumps(due_items()), ctype="application/json")
            elif path == "/work.json":
                query = urllib.parse.parse_qs(self.path.partition("?")[2])
                course = (query.get("course") or [None])[0]
                days = (query.get("days") or ["14"])[0]
                days = int(days) if days.isdigit() else 14
                self._send(json.dumps(work_items(course, min(max(days, 1), 60))), ctype="application/json")
            elif path == "/lectures":
                self._redirect("/lectures/")
            elif path.startswith("/lectures/"):
                self._library(path)
            elif path == "/sync":
                self._send(f"<pre>{sync_once()}</pre><p><a href='/'>back</a></p>")
            elif path == "/manifest.webmanifest":
                self._send(json.dumps({
                    "name": "schoolboard", "short_name": "school",
                    "start_url": "/", "display": "standalone",
                    "background_color": "#16182A", "theme_color": "#16182A",
                }), ctype="application/manifest+json")
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


def health_alert():
    """Tell him when the board itself is broken.

    This is the gap that let the page 500 for hours with nobody knowing: the old
    /healthz could not fail, and nothing was watching anyway. Rate-limited to
    once an hour so a sustained outage does not become a flood, and exempt from
    quiet hours because a dead board at 3am is still dead at 8am.
    """
    ok, detail = health(ttl=0)
    if ok:
        return
    cfg = config.load_config()
    conn = store.connect()
    try:
        key = f"health:{datetime.now().strftime('%Y-%m-%dT%H')}"
        if notify.already_sent(conn, key):
            return
        env = notify.read_env(cfg.get("notify", {}).get("telegram_env"))
        bot = notify.Telegram(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
        sent, _ = bot.send(f"<b>schoolboard is broken</b>\n{detail}")
        if sent:
            notify.mark_sent(conn, key, detail)
    finally:
        conn.close()


def _notify_loop():
    """Every 60s. A class starting in 15 minutes cannot wait for a 15-min sync."""
    while True:
        try:
            notify_once()
        except Exception:
            traceback.print_exc()
        try:
            health_alert()
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


def _listen(addr, port, label, handler=Handler):
    httpd = ThreadingHTTPServer((addr, port), handler)
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


_HEALTH_CACHE = {"at": 0.0, "ok": True, "detail": "ok"}


def health(ttl=20):
    """Actually render the board and report whether it worked.

    The old version returned a hardcoded "ok" whenever the socket was open, so
    it reported healthy throughout an outage where every page 500'd. A health
    check that cannot fail is not a health check.
    """
    now = time.time()
    if now - _HEALTH_CACHE["at"] < ttl:
        return _HEALTH_CACHE["ok"], _HEALTH_CACHE["detail"]
    try:
        page = build_page()
        if "<html" not in page or len(page) < 2000:
            raise RuntimeError(f"page too small ({len(page)} bytes)")
        ok, detail = True, f"ok {len(page)}"
    except Exception as exc:
        ok, detail = False, f"render failed: {type(exc).__name__}: {exc}"[:300]
    _HEALTH_CACHE.update(at=now, ok=ok, detail=detail)
    return ok, detail


class PublicHandler(Handler):
    """The tunnel's listener. Reached only by cloudflared, and always gated."""
    requires_auth = True


def serve(bind=None, port=None):
    cfg = config.load_config()
    bind = bind or cfg["bind"]
    port = port or cfg["port"]
    threading.Thread(target=_sync_loop, args=(cfg["sync_minutes"],), daemon=True).start()
    if cfg.get("notify", {}).get("enabled"):
        threading.Thread(target=_notify_loop, daemon=True).start()
    print(f"schoolboard starting (timezone {cfg['timezone']})", flush=True)
    _listen(bind, port, "local")
    public_port = cfg.get("public_port")
    if public_port:
        _listen("127.0.0.1", int(public_port), "public (login required)",
                handler=PublicHandler)
    if cfg.get("bind_tailnet", True):
        threading.Thread(target=_bind_tailnet_when_ready, args=(port,), daemon=True).start()
    while True:
        time.sleep(3600)
