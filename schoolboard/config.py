"""Configuration loading.

Two files, deliberately separate:
  schedule.json  — the term timetable. Safe to commit; no secrets.
  config.json    — tokens and host settings. Never committed (see .gitignore).
"""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "port": 8888,
    "bind": "127.0.0.1",
    # Also listen on the tailnet address, so phone/laptop reach it by name or IP.
    # Never 0.0.0.0: the dorm /19 passes unicast between clients.
    "bind_tailnet": True,
    # Second listener, localhost-only, that cloudflared alone talks to. Auth is
    # decided by which socket accepted the connection — a listening socket is a
    # fact, a forwarded-for header is only a claim.
    "public_port": 8889,
    "auth": {"password_hash": "", "session_secret": ""},
    # Never inherit the host clock. The mini has been sitting on America/New_York
    # while physically in Oakland, which is exactly how you miss a class.
    "timezone": "America/Los_Angeles",
    "refresh_seconds": 60,
    "sync_minutes": 15,
    "due_soon_days": 10,
    # Minutes to allow for getting to the next class. Oakland is small but
    # Natural Science to CPM is still a walk.
    "walk_minutes": 10,
    # "light" or "auto". Light by default because he asked for a light theme;
    # "auto" adds a dark variant that follows the device at night.
    "theme": "light",
    "canvas": {"base_url": "https://northeastern.instructure.com", "token": ""},
    # Public course sites. The only source for per-class prep material — Canvas
    # has the assignment, the site has what to read before Thursday.
    "course_sites": [
        {"course": "CS 2000", "url": "https://neu-pdi.github.io/cs2000-public-resources/"},
    ],
    "course_site_refresh_hours": 6,
    # scribe's lecture export, synced into the vault by Syncthing: deadlines said
    # in class, and each lecture's recap for the class reminder and the weekly.
    "scribe_index": "~/Notes/.scribe/index.json",
    # scribe's phone library (listening copies + pages), pushed here by the laptop
    # and served at /lectures/. Audio: never in the vault, never public without login.
    "scribe_pod": "~/scribe-pod",
    # Graph calendar is blocked at the NU tenant (403, and consent is refused),
    # so calendars arrive as published .ics URLs instead. Outlook: Settings >
    # Calendar > Shared calendars > Publish a calendar > ICS link.
    "ics_feeds": [],
    "notify": {
        "enabled": True,
        # Reuses the bot the disc burner already set up. Home ntfy (.240) is
        # unreachable from the dorm network, so Telegram is the only path out.
        "telegram_env": "~/discburn/webhook.env",
        "class_lead_minutes": 15,
        "due_thresholds_hours": [24, 3],
        "digest_hour": 8,
        # Sunday evening look at the week ahead.
        "weekly_hour": 18,
        "quiet_start": 22,
        "quiet_end": 7,
    },
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config():
    path = ROOT / "config.json"
    user = {}
    if path.exists():
        user = json.loads(path.read_text())
    # A copy: _merge shares the sub-dicts it doesn't override, so setting
    # cfg["auth"]["password_hash"] used to write into DEFAULTS itself.
    cfg = _merge(copy.deepcopy(DEFAULTS), user)
    # Environment overrides win, so a token can be injected without touching disk.
    if os.environ.get("SCHOOLBOARD_CANVAS_TOKEN"):
        cfg["canvas"]["token"] = os.environ["SCHOOLBOARD_CANVAS_TOKEN"]
    return cfg


def _changed(cfg, base):
    """What in `cfg` differs from `base`, so a save doesn't freeze a copy of
    every default into the file where a later default can't reach it."""
    out = {}
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            inner = _changed(value, base[key])
            if inner:
                out[key] = inner
        elif key not in base or base[key] != value:
            out[key] = value
    return out


def save_config(cfg):
    """Write config.json whole or not at all: a temp file, created 0600 so the
    token is never readable, then renamed over it. Written in place, a page
    loading mid-write read half a file and 500'd."""
    path = ROOT / "config.json"
    cfg = json.loads(json.dumps(cfg))
    on_disk = json.loads(path.read_text()) if path.exists() else {}
    injected = os.environ.get("SCHOOLBOARD_CANVAS_TOKEN")
    if injected and cfg.get("canvas", {}).get("token") == injected:
        # Injected so that it needn't touch disk; keep what the file had.
        cfg["canvas"]["token"] = (on_disk.get("canvas") or {}).get("token", "")
    # The file's own settings stay, even one that equals today's default (a
    # pinned port must survive the default changing); only changes are added.
    changes = _changed(cfg, _merge(copy.deepcopy(DEFAULTS), on_disk))
    tmp = path.with_name(".config.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(json.dumps(_merge(on_disk, changes), indent=2) + "\n")
    os.replace(tmp, path)
    return path


def load_schedule():
    return json.loads((ROOT / "schedule.json").read_text())


def timezone_name():
    cfg = load_config()
    return cfg.get("timezone") or load_schedule().get("timezone") or "America/Los_Angeles"
