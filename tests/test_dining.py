"""The dining hall: reading Dine On Campus, keeping a good copy through a bad
fetch, and picking the meal to show. Invented dishes, in the API's shape.

    python3 -m unittest tests.test_dining
"""
import gzip
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schoolboard import dining, mail, render, server, status, store, timetable  # noqa: E402

LA = timetable.tzinfo("America/Los_Angeles")
SETTINGS = {"site_id": "S", "location_id": "L", "days": 3, "meal_from": {"lunch": "10:30", "dinner": "16:30"}}
TODAY = date(2026, 9, 29)   # a Tuesday


def dish(name, *badges):
    return {"name": name, "filters": [{"name": b, "icon": True} for b in badges]
            + [{"name": "Milk", "icon": False}]}


def period(pid, name, stations):
    return {"id": pid, "name": name, "slug": name.lower(), "categories": [
        {"id": f"c{i}", "name": station, "sortOrder": 1, "items": items}
        for i, (station, items) in enumerate(stations.items())]}


MEALS = {
    "b": period("b", "Breakfast", {"Griddle": [dish("Oat Pancakes", "Vegetarian")]}),
    "l": period("l", "Lunch", {"Grill": [dish(" Lentil Burger ", "Vegan", "Vegetarian"), dish("Lentil Burger")],
                               "Salad Bar": [dish(f"Topping {i}") for i in range(10)]}),
    "d": period("d", "Dinner", {"Grill": [dish("Roast Squash", "Vegan")]}),
    "e": period("e", "Everyday", {"Everyday": [dish("Toast")]}),
}


class FakeAPI:
    """Answers like apiv4.dineoncampus.com; `fail_on` names a date to refuse."""

    def __init__(self, fail_on=None, empty=()):
        self.calls, self.fail_on, self.empty = [], fail_on, set(empty)

    def __call__(self, path, timeout=20, **params):
        self.calls.append((path, params))
        if params.get("date") == self.fail_on:
            raise dining.DiningError("HTTP Error 403: Forbidden")
        if path.endswith("weekly_schedule"):
            sunday = date.fromisoformat(params["date"])
            sunday -= timedelta(days=(sunday.weekday() + 1) % 7)
            week = [{"date": (sunday + timedelta(days=i)).isoformat(), "closed": False,
                     "hours": [{"start_hour": 7, "start_minutes": 0, "end_hour": 20, "end_minutes": 30}]}
                    for i in range(7)]
            return {"theLocations": [{"id": "Other", "week": []}, {"id": "L", "week": week}]}
        if path.endswith("/periods/"):
            listed = [{"id": p["id"], "name": p["name"], "slug": p["slug"]} for p in MEALS.values()]
            return {"periods": listed, "menu": {"period": MEALS["b"]}}
        if params["period"] in self.empty:
            return {"period": {"id": params["period"], "categories": []}}
        return {"period": MEALS[params["period"]]}


class CollectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "dining.json"

    def refresh(self, api, today=TODAY):
        with mock.patch.object(dining, "fetch", api):
            return dining.refresh(SETTINGS, today, path=self.cache)

    def test_a_day_is_read_into_meals(self):
        data, note, fetched = self.refresh(FakeAPI())
        self.assertEqual((fetched, note), (3, "3 days, 9 meals"))
        day = data["days"]["2026-09-29"]
        self.assertEqual(day["hours"], [[420, 1230]])
        self.assertEqual([m["name"] for m in day["meals"]], ["Breakfast", "Lunch", "Dinner"])   # no Everyday
        grill = day["meals"][1]["stations"][0]
        # Trimmed, the repeat dropped, and only the badges kept as tags.
        self.assertEqual(grill["items"], [{"name": "Lentil Burger", "tags": ["Vegan", "Vegetarian"]}])

    def test_breakfast_comes_with_the_list_of_meals(self):
        api = FakeAPI()
        self.refresh(api)
        asked = [p["period"] for path, p in api.calls if path.endswith("/menu")]
        self.assertNotIn("b", asked)
        self.assertNotIn("e", asked)
        # One weekly schedule covers Tuesday to Thursday.
        self.assertEqual(sum(path.endswith("weekly_schedule") for path, _ in api.calls), 1)

    def test_a_window_across_sunday_asks_for_both_weeks(self):
        api = FakeAPI()
        data, _, _ = self.refresh(api, today=date(2026, 10, 3))   # Saturday
        self.assertEqual(sum(path.endswith("weekly_schedule") for path, _ in api.calls), 2)
        self.assertEqual(data["days"]["2026-10-05"]["hours"], [[420, 1230]])

    def test_a_failure_keeps_the_last_copy_and_stops(self):
        self.refresh(FakeAPI())
        api = FakeAPI(fail_on="2026-09-30")
        data, note, fetched = self.refresh(api)
        self.assertEqual(fetched, 1)
        self.assertIn("failed (HTTP Error 403: Forbidden)", note)
        self.assertEqual(len(data["days"]["2026-10-01"]["meals"]), 3)
        self.assertFalse([p for _, p in api.calls if p.get("date") == "2026-10-01"])

    def test_nothing_fetched_writes_nothing(self):
        self.refresh(FakeAPI())
        before = self.cache.read_text()
        _, _, fetched = self.refresh(FakeAPI(fail_on="2026-09-29"))
        self.assertEqual((fetched, self.cache.read_text()), (0, before))

    def test_an_empty_meal_keeps_the_last_good_one(self):
        self.refresh(FakeAPI())
        data, _, _ = self.refresh(FakeAPI(empty={"l"}))
        self.assertEqual(data["days"]["2026-09-29"]["meals"][1]["stations"][0]["name"], "Grill")

    def test_past_days_are_dropped(self):
        self.refresh(FakeAPI())
        data, _, _ = self.refresh(FakeAPI(), today=TODAY + timedelta(days=1))
        self.assertNotIn("2026-09-29", data["days"])

    def test_fetch_reads_gzip_and_refuses_what_isnt_json(self):
        class Resp:
            def __init__(self, body, encoding):
                self.body, self.headers = body, {"Content-Encoding": encoding}

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        packed = Resp(gzip.compress(b'{"periods": []}'), "gzip")
        with mock.patch("urllib.request.urlopen", return_value=packed) as opened:
            self.assertEqual(dining.fetch("/locations/L/periods/", date="2026-09-29"), {"periods": []})
        self.assertIn("schoolboard", opened.call_args.args[0].get_header("User-agent"))
        challenge = Resp(b"<html>Attention Required! | Cloudflare</html>", None)
        with mock.patch("urllib.request.urlopen", return_value=challenge), \
                self.assertRaises(dining.DiningError):
            dining.fetch("/locations/L/menu")
        with mock.patch("urllib.request.urlopen", return_value=Resp(gzip.compress(b"{}")[:9], "gzip")), \
                self.assertRaises(dining.DiningError):
            dining.fetch("/locations/L/menu")

    def test_hours_that_run_past_midnight(self):
        body = {"theLocations": [{"id": "L", "week": [
            {"date": "2026-09-29", "hours": [{"start_hour": 21, "start_minutes": 0,
                                               "end_hour": 1, "end_minutes": 0}]},
            {"date": "2026-09-30", "closed": True, "hours": []}]}]}
        hours = dining.parse_hours(body, "L")
        self.assertEqual(hours["2026-09-30"], [])
        (start, end), = dining.open_spans({"hours": hours["2026-09-29"]}, TODAY, LA)
        self.assertEqual((start.hour, end.date(), end.hour), (21, date(2026, 9, 30), 1))


def cached(days=3, hours=((420, 1230),), today=TODAY):
    """A cache as refresh writes it, one menu repeated for `days` days."""
    def meal(name, stations):
        return {"name": name, "slug": name.lower(), "stations": [
            {"name": station, "items": [{"name": n, "tags": []} for n in items]}
            for station, items in stations.items()]}
    out = {}
    for i in range(days):
        out[(today + timedelta(days=i)).isoformat()] = {"hours": [list(h) for h in hours] if hours is not None
                                                        else None, "meals": [
            meal("Breakfast", {"Griddle": [f"Pancakes {i}"]}),
            meal("Lunch", {"Grill": [f"Burger {i}"], "Salad Bar": [f"Topping {t}" for t in range(10)]}),
            meal("Dinner", {"Grill": [f"Squash {i}"]})]}
    return {"fetched_at": "2026-09-29T12:00:00+00:00", "days": out}


class PickTest(unittest.TestCase):
    def at(self, hour, minute=0, day=TODAY):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA)

    def pick(self, now, data=None, want=None):
        got = dining.current(data or cached(), now, SETTINGS, want)
        return (got["day"], got["meal"]["name"]) if got else None

    def test_the_clock_picks_the_meal(self):
        self.assertEqual(self.pick(self.at(6)), (TODAY, "Breakfast"))     # before the doors open
        self.assertEqual(self.pick(self.at(10, 29)), (TODAY, "Breakfast"))
        self.assertEqual(self.pick(self.at(10, 30)), (TODAY, "Lunch"))
        self.assertEqual(self.pick(self.at(17)), (TODAY, "Dinner"))

    def test_after_closing_it_is_tomorrows_breakfast(self):
        self.assertEqual(self.pick(self.at(20, 30)), (TODAY + timedelta(days=1), "Breakfast"))

    def test_a_closed_day_is_skipped_whatever_its_menu(self):
        data = cached()
        data["days"]["2026-09-29"]["hours"] = []
        self.assertEqual(self.pick(self.at(12), data), (TODAY + timedelta(days=1), "Breakfast"))

    def test_unknown_hours_still_show_the_menu(self):
        self.assertEqual(self.pick(self.at(23), cached(hours=None)), (TODAY, "Dinner"))

    def test_a_tapped_meal_wins_and_a_made_up_one_is_ignored(self):
        self.assertEqual(self.pick(self.at(9), want="dinner"), (TODAY, "Dinner"))
        self.assertEqual(self.pick(self.at(9), want="elevenses"), (TODAY, "Breakfast"))

    def test_nothing_ahead_is_none(self):
        self.assertIsNone(self.pick(self.at(12, day=TODAY + timedelta(days=5))))

    def test_what_is_the_same_as_other_days_is_the_usual(self):
        data = cached()
        lunch = data["days"]["2026-09-29"]["meals"][1]
        self.assertEqual(dining.usual(data, TODAY, lunch), {"Salad Bar"})
        # One topping in ten changed is still the usual; one dish in two isn't.
        lunch["stations"][1]["items"][0]["name"] = "Chard"
        self.assertEqual(dining.usual(data, TODAY, lunch), {"Salad Bar"})
        lunch["stations"][1]["items"][1:] = [{"name": "Topping 1", "tags": []}]
        self.assertEqual(dining.usual(data, TODAY, lunch), set())
        # With no other day to compare with, nothing is folded away.
        self.assertEqual(dining.usual(cached(days=1), TODAY, lunch), set())

    def test_a_meal_that_is_all_usual_folds_nothing(self):
        data = cached()
        for entry in data["days"].values():
            entry["meals"][1]["stations"][0]["items"] = [{"name": "Burger", "tags": []}]
        self.assertEqual(dining.usual(data, TODAY, data["days"]["2026-09-29"]["meals"][1]), set())


class CardTest(unittest.TestCase):
    SETTINGS = dict(SETTINGS, name="Main Hall", url="https://dining.example.edu/menu",
                    marks={"Vegan": "vg", "Vegetarian": "v"})

    def card(self, hour, data=None, want=None, **settings):
        now = datetime(2026, 9, 29, hour, 0, tzinfo=LA)
        return render.render_meals(data or cached(), now, dict(self.SETTINGS, **settings), want=want)

    def test_it_names_the_meal_and_the_halls_hours(self):
        self.assertIn("<h2>Lunch</h2><span class=\"sub\">open until 8:30 pm</span>", self.card(12))
        self.assertIn("<h2>Breakfast</h2><span class=\"sub\">opens 7:00 am</span>", self.card(6))
        self.assertIn("<h2>Breakfast tomorrow</h2><span class=\"sub\">opens 7:00 am</span>", self.card(21))
        self.assertIn("Squash 0", self.card(9, want="dinner"))

    def test_the_other_meals_are_a_tap_away(self):
        html = self.card(12)
        self.assertIn('<a href="/?meal=dinner#meals" class="">Dinner</a>', html)
        self.assertIn('<a href="/?meal=lunch#meals" class="on">Lunch</a>', html)
        self.assertIn('id="meals"', html)

    def test_the_usual_is_one_line_and_a_long_station_is_counted(self):
        html = self.card(12)
        self.assertIn("As usual: Salad Bar", html)
        self.assertNotIn("Topping 3", html)
        data = cached(days=1)
        html = self.card(12, data)
        self.assertIn("Topping 5", html)
        self.assertNotIn("Topping 6", html)
        self.assertIn("4 more", html)

    def test_marks_follow_the_config_and_vegan_wins(self):
        data = cached()
        data["days"]["2026-09-29"]["meals"][1]["stations"][0]["items"] = [
            {"name": "Bean Bowl", "tags": ["Vegetarian", "Vegan"]}, {"name": "Cheese Toast", "tags": ["Vegetarian"]}]
        html = self.card(12, data)
        self.assertIn('Bean Bowl<i class="mk" title="Vegan">vg</i>', html)
        self.assertIn('Cheese Toast<i class="mk" title="Vegetarian">v</i>', html)
        self.assertIn('<i class="mk">vg</i> vegan &middot; <i class="mk">v</i> vegetarian', html)
        self.assertNotIn('class="mk"', self.card(12, data, marks={}))

    def test_what_the_menu_says_is_escaped(self):
        data = cached()
        station = data["days"]["2026-09-29"]["meals"][1]["stations"][0]
        station["name"] = "<b>Grill</b>"
        station["items"] = [{"name": "<img src=x onerror=alert(1)>", "tags": []}]
        html = self.card(12, data, url="javascript:alert(1)")
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>Grill", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("Main Hall menu", html)

    def test_nothing_known_is_no_card_and_nothing_ahead_says_so(self):
        self.assertEqual(render.render_meals({}, datetime(2026, 9, 29, 12, tzinfo=LA), self.SETTINGS), "")
        later = datetime(2026, 10, 9, 12, tzinfo=LA)
        self.assertIn("No menu from Main Hall", render.render_meals(cached(), later, self.SETTINGS))


class SourceTest(unittest.TestCase):
    def ages(self, cfg, stamp):
        with mock.patch.object(status, "_file_stamp",
                               side_effect=lambda name, key: stamp if name == "dining.json" else None):
            return {r["name"]: r for r in status.sources(cfg, None, lambda conn, key: None)}

    def test_a_day_without_a_refresh_is_stale(self):
        cfg = {"dining": {"location_id": "L"}}
        fresh = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.assertFalse(self.ages(cfg, fresh)["Dining"]["stale"])
        self.assertTrue(self.ages(cfg, old)["Dining"]["stale"])
        self.assertNotIn("Dining", self.ages({"dining": {"location_id": ""}}, old))


class SyncTest(unittest.TestCase):
    def test_menus_are_fetched_every_few_hours_not_every_sync(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = {"timezone": "America/Los_Angeles", "canvas": {"token": "", "base_url": "https://c.example"},
               "scribe_index": "/nonexistent", "dining": {"location_id": "L", "refresh_hours": 3}}
        with mock.patch.object(store, "DB_PATH", Path(tmp.name) / "test.db"), \
                mock.patch.object(mail, "collect", return_value=([], {"available": False})), \
                mock.patch.object(dining, "refresh", return_value=({}, "3 days, 9 meals", 3)) as refresh:
            first = server.sync_once(cfg, {"courses": []})
            second = server.sync_once(cfg, {"courses": []})
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[1], datetime.now(LA).date())   # the campus date
        self.assertIn("dining: 3 days, 9 meals", first)
        self.assertNotIn("dining", second)


if __name__ == "__main__":
    unittest.main()
