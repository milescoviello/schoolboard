"""Dining hall menus.

Founders Commons, the all-you-care-to-eat hall on the Oakland campus, publishes
its menus through Dine On Campus: nudining.com/dining is a script-drawn page
over a JSON API that needs no login. Menus go up about a month ahead and are
edited on the day, so today and the next two days are fetched every few hours.

Three things about the API shape everything here:

  * A meal is asked for by id, and the ids are regenerated now and then. A
    stale one answers 200 with no stations, which looks exactly like "no
    menu", so each day starts from its list of meals.
  * Meals carry no times. Only the hall's opening hours exist, and it is open
    straight through, so which meal the board shows is decided by `meal_from`
    in config. Those are the board's guesses, never shown as the hall's times.
  * Cloudflare sits in front of it and refuses curl's and Python's own
    User-Agents. A named one is let through.
"""
import gzip
import http.client
import json
import ssl
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "dining.json"
API = "https://apiv4.dineoncampus.com"
USER_AGENT = "schoolboard/0.1 (personal dashboard)"

# Bread, fruit, cereal and spreads: the same eighty things at every meal.
SKIP = {"everyday"}

# Stations whose dishes overlap this much with the same meal on another day
# are the usual (the salad bar, the pizza, the yogurt), folded into one line.
USUAL_OVERLAP = 0.8


class DiningError(Exception):
    pass


def fetch(path, timeout=20, **params):
    """GET one API path as JSON. Gzipped: a meal is 200 KB plain, 14 KB
    gzipped, and urllib leaves the decoding to its caller."""
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        data = json.loads(body)
    except (OSError, EOFError, zlib.error, http.client.HTTPException, ValueError) as exc:
        raise DiningError(f"{path.rstrip('/').rsplit('/', 1)[-1]}: {exc}") from exc
    if not isinstance(data, dict):
        raise DiningError(f"{path}: not an object")
    return data


def _clean(text):
    # Some names arrive as " Diced Onions" or "Marinara Sauce ".
    return " ".join(str(text or "").split())


def parse_meal(period):
    """[{name, items: [{name, tags}]}], one per station, in the API's order.
    Tags are the badges it draws (Vegan, Vegetarian, Avoiding Gluten...), not
    the allergen list, which is longer than the dish."""
    stations = []
    for category in (period or {}).get("categories") or []:
        items, seen = [], set()
        for item in category.get("items") or []:
            name = _clean(item.get("name"))
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            tags = [_clean(f.get("name")) for f in item.get("filters") or []
                    if isinstance(f, dict) and f.get("icon") and f.get("name")]
            items.append({"name": name, "tags": tags})
        if items:
            stations.append({"name": _clean(category.get("name")) or "Other", "items": items})
    return stations


def parse_hours(body, location_id):
    """{iso date: [[start, end], ...]} in minutes after midnight, campus time.
    [] is closed that day. An end at or before its start is after midnight."""
    out = {}
    for place in body.get("theLocations") or []:
        if place.get("id") != location_id:
            continue
        for day in place.get("week") or []:
            iso = day.get("date")
            if not iso:
                continue
            if day.get("closed"):
                out[iso] = []
                continue
            spans = []
            for hours in day.get("hours") or []:
                if day.get("always_open") or hours.get("always_open"):
                    spans.append([0, 24 * 60])
                    continue
                try:
                    spans.append([int(hours["start_hour"]) * 60 + int(hours["start_minutes"]),
                                  int(hours["end_hour"]) * 60 + int(hours["end_minutes"])])
                except (KeyError, TypeError, ValueError):
                    continue
            out[iso] = sorted(spans)
    return out


def fetch_day(location_id, day):
    """Every meal served on `day`. The list of meals carries the first one's
    menu too, so a day of breakfast, lunch and dinner is three requests."""
    listing = fetch(f"/locations/{location_id}/periods/", date=day.isoformat())
    first = (listing.get("menu") or {}).get("period") or {}
    meals = []
    for period in listing.get("periods") or []:
        slug = _clean(period.get("slug")).lower()
        if not period.get("id") or slug in SKIP:
            continue
        if first.get("id") == period["id"] and first.get("categories"):
            menu = first
        else:
            menu = fetch(f"/locations/{location_id}/menu", date=day.isoformat(),
                         period=period["id"]).get("period") or {}
        meals.append({"name": _clean(period.get("name")) or "Meal", "slug": slug,
                      "stations": parse_meal(menu)})
    return meals


def _sunday(day):
    return day - timedelta(days=(day.weekday() + 1) % 7)


def refresh(settings, today, path=None):
    """Fetch the next few days into the cache. Returns (data, a short report,
    how many days were fetched).

    What comes back empty keeps what the cache had: a meal asked for by an id
    regenerated a moment before answers with no stations, and must not wipe a
    good menu (coursesite.py lost 61 entries that way). The first failure ends
    the run, since every later request would wait out the same timeout, and
    the sync that is waiting holds the lock Canvas needs."""
    path = Path(path) if path else CACHE
    location, site = settings.get("location_id"), settings.get("site_id")
    old = load(path).get("days") or {}
    window = [today + timedelta(days=i) for i in range(max(1, int(settings.get("days", 3))))]
    days, hours, weeks = {}, {}, set()
    fetched, meals_seen, trouble = 0, 0, ""
    for index, day in enumerate(window):
        iso = day.isoformat()
        was = old.get(iso) if isinstance(old.get(iso), dict) else {}
        try:
            if site and _sunday(day) not in weeks:   # the schedule is a Sunday-to-Saturday week
                weeks.add(_sunday(day))
                hours.update(parse_hours(fetch("/locations/weekly_schedule", site_id=site, date=iso,
                                               location_id=location, locale="en"), location))
            meals = fetch_day(location, day)
        except DiningError as exc:
            trouble = f"failed ({exc})"
            for later in window[index:]:
                if later.isoformat() in old:
                    days[later.isoformat()] = old[later.isoformat()]
            break
        before = {m.get("slug"): m for m in was.get("meals") or []}
        for meal in meals:
            if not meal["stations"] and (before.get(meal["slug"]) or {}).get("stations"):
                meal["stations"] = before[meal["slug"]]["stations"]
        if not meals and was.get("meals"):
            meals = was["meals"]
            trouble = trouble or "a day came back empty, keeping the last copy"
        days[iso] = {"hours": hours.get(iso, was.get("hours")), "meals": meals}
        fetched += 1
        meals_seen += sum(1 for m in meals if m["stations"])
    data = {"fetched_at": datetime.now(timezone.utc).isoformat(), "days": days}
    if fetched:
        path.write_text(json.dumps(data, indent=1))
    note = f"{fetched} day{'' if fetched == 1 else 's'}, {meals_seen} meals"
    return data, note + (f", {trouble}" if trouble else ""), fetched


def load(path=None):
    path = Path(path) if path else CACHE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def open_spans(entry, day, tz):
    """The hall's hours on `day` as (open, close) datetimes; None if unknown."""
    hours = entry.get("hours")
    if hours is None:
        return None
    midnight = datetime.combine(day, time(0), tzinfo=tz)
    out = []
    for start, end in hours:
        if end <= start:
            end += 24 * 60
        out.append((midnight + timedelta(minutes=start), midnight + timedelta(minutes=end)))
    return out


def _minutes(text):
    try:
        hour, minute = str(text).split(":")
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def current(data, now, settings=None, want=None):
    """The meal to show: {day, meal, meals, spans}, or None if nothing ahead is
    known. Today's, until the hall closes; then the next day it serves.

    Which of the day's meals is by the clock and `meal_from`, unless `want`
    names one (a tapped meal). A later day opens on its first meal."""
    settings = settings or {}
    starts = {slug: _minutes(clock) for slug, clock in (settings.get("meal_from") or {}).items()}
    days = (data or {}).get("days") or {}
    for iso in sorted(days):
        entry = days[iso]
        try:
            day = date.fromisoformat(iso)
        except ValueError:
            continue
        if day < now.date() or not isinstance(entry, dict):
            continue
        meals = [m for m in entry.get("meals") or [] if m.get("stations")]
        spans = open_spans(entry, day, now.tzinfo)
        # A menu isn't an open door: the hours decide (closed, or closed now).
        if not meals or (spans is not None and not any(end > now for _, end in spans)):
            continue
        meal = next((m for m in meals if want and m.get("slug") == want), None)
        if meal is None:
            meal = meals[0]
            if day == now.date():
                clock = now.hour * 60 + now.minute
                for candidate in meals:
                    start = starts.get(candidate.get("slug"))
                    if start is not None and clock >= start:
                        meal = candidate
        return {"day": day, "meal": meal, "meals": meals, "spans": spans}
    return None


def usual(data, day, meal):
    """Names of the stations serving `meal` what they serve at the same meal on
    another day: the salad bar, the pizza, the yogurt. Nothing is folded when
    there is no other day to compare with, or when every station would be."""
    def names(station):
        return {item["name"].lower() for item in station["items"]}

    others = [m for iso, entry in ((data or {}).get("days") or {}).items() if iso != day.isoformat()
              and isinstance(entry, dict) for m in entry.get("meals") or []
              if m.get("slug") == meal.get("slug") and m.get("stations")]
    out = set()
    for station in meal["stations"]:
        here = names(station)
        for other in others:
            there = next((names(s) for s in other["stations"] if s["name"] == station["name"]), set())
            if here and len(here & there) >= USUAL_OVERLAP * len(here | there):
                out.add(station["name"])
                break
    return set() if len(out) == len(meal["stations"]) else out
