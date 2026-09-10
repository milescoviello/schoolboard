"""Configuration loading.

Two files, deliberately separate:
  schedule.json  — the term timetable. Safe to commit; no secrets.
  config.json    — tokens and host settings. Never committed (see .gitignore).
"""
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
    # Never inherit the host clock. The mini has been sitting on America/New_York
    # while physically in Oakland, which is exactly how you miss a class.
    "timezone": "America/Los_Angeles",
    "refresh_seconds": 60,
    "sync_minutes": 15,
    "due_soon_days": 10,
    "canvas": {"base_url": "https://northeastern.instructure.com", "token": ""},
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
    cfg = _merge(DEFAULTS, user)
    # Environment overrides win, so a token can be injected without touching disk.
    if os.environ.get("SCHOOLBOARD_CANVAS_TOKEN"):
        cfg["canvas"]["token"] = os.environ["SCHOOLBOARD_CANVAS_TOKEN"]
    return cfg


def save_config(cfg):
    path = ROOT / "config.json"
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    path.chmod(0o600)
    return path


def load_schedule():
    return json.loads((ROOT / "schedule.json").read_text())


def timezone_name():
    cfg = load_config()
    return cfg.get("timezone") or load_schedule().get("timezone") or "America/Los_Angeles"
