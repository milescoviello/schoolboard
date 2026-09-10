"""Canvas LMS collector.

Uses a personal access token (Canvas > Account > Settings > New Access Token).
That is deliberately the only auth path here: Student Hub and everything else
behind NU's SSO needs Duo, and scraping a push-MFA login is fragile in a way a
token is not.

stdlib only — no requests. This runs on a 2009 Core2Duo with no pip packages,
and every dependency avoided is a wheel that can't fail to build.
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

USER_AGENT = "schoolboard/0.1 (personal dashboard)"
TIMEOUT = 25


class CanvasError(RuntimeError):
    pass


class Canvas:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context()

    def _request(self, url):
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=self.ctx) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.headers.get("Link", "")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise CanvasError(
                    "Canvas rejected the token (401). It may be expired or revoked — "
                    "generate a new one at Account > Settings > New Access Token."
                ) from exc
            raise CanvasError(f"Canvas returned HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise CanvasError(f"Could not reach Canvas: {exc.reason}") from exc

    def get(self, path, **params):
        """GET with Link-header pagination followed to the end."""
        params.setdefault("per_page", 100)
        url = f"{self.base}/api/v1/{path.lstrip('/')}?{urllib.parse.urlencode(params, doseq=True)}"
        out = []
        seen = 0
        while url and seen < 20:  # hard page cap; a runaway loop must not hang the sync
            data, link = self._request(url)
            out.extend(data if isinstance(data, list) else [data])
            url = _next_link(link)
            seen += 1
        return out

    def profile(self):
        return self.get("users/self/profile")[0]

    def courses(self):
        # total_scores brings the enrollment's current score back with the course,
        # so grades cost no extra request.
        return self.get("courses", enrollment_state="active",
                        include=["term", "total_scores"], state=["available"])

    def planner(self, days_back=7, days_ahead=45):
        now = datetime.now(timezone.utc)
        return self.get(
            "planner/items",
            start_date=(now - timedelta(days=days_back)).strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
        )

    def announcements(self, course_ids, days_back=14):
        if not course_ids:
            return []
        now = datetime.now(timezone.utc)
        return self.get(
            "announcements",
            context_codes=[f"course_{cid}" for cid in course_ids],
            start_date=(now - timedelta(days=days_back)).strftime("%Y-%m-%d"),
            end_date=(now + timedelta(days=1)).strftime("%Y-%m-%d"),
        )


def _next_link(header):
    for part in (header or "").split(","):
        chunk = part.split(";")
        if len(chunk) >= 2 and 'rel="next"' in chunk[1]:
            return chunk[0].strip().strip("<>")
    return None


def _norm_code(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def course_labeller(canvas_courses, schedule):
    """Map a Canvas course id to the course code Miles actually uses.

    Canvas names courses things like "202610_CS2000_Merged"; the board should say
    "CS 2000". Falls back to Canvas's own name when nothing matches.
    """
    known = {_norm_code(c["code"]): c["code"] for c in schedule["courses"]}
    table = {}
    for course in canvas_courses:
        blob = _norm_code(f"{course.get('course_code','')}{course.get('name','')}")
        label = course.get("course_code") or course.get("name") or f"Course {course['id']}"
        for norm, pretty in known.items():
            if norm and norm in blob:
                label = pretty
                break
        table[course["id"]] = label
    return table


_KIND = {
    "assignment": "assignment",
    "quiz": "quiz",
    "discussion_topic": "discussion",
    "announcement": "announcement",
    "calendar_event": "event",
    "planner_note": "note",
    "assessment_request": "peer review",
}

# Checkpointed discussions arrive as two items with an identical title and
# different deadlines. The tag is the only thing that tells them apart.
_CHECKPOINT = {"reply_to_topic": "initial post", "reply_to_entry": "reply to classmates"}


def _is_done(entry):
    override = entry.get("planner_override") or {}
    if override.get("marked_complete") or override.get("dismissed"):
        return True
    subs = entry.get("submissions")
    if isinstance(subs, dict) and (subs.get("submitted") or subs.get("excused")):
        return True
    return False


def normalise_planner(entries, labels, base_url):
    items = []
    for entry in entries:
        plannable = entry.get("plannable") or {}
        ptype = entry.get("plannable_type") or "note"
        due = plannable.get("due_at") or entry.get("plannable_date") or plannable.get("todo_date")
        url = entry.get("html_url") or ""
        if url.startswith("/"):
            url = base_url.rstrip("/") + url
        kind = _KIND.get(ptype, ptype)
        if ptype == "sub_assignment":
            kind = _CHECKPOINT.get(plannable.get("sub_assignment_tag"), "checkpoint")
        items.append({
            "id": f"canvas:{ptype}:{plannable.get('id') or entry.get('plannable_id')}",
            "source": "canvas",
            "kind": kind,
            "course": labels.get(entry.get("course_id"), "Canvas"),
            "title": plannable.get("title") or plannable.get("name") or "(untitled)",
            "due_utc": due,
            "url": url,
            "done": _is_done(entry),
            "body": None,
        })
    return items


def normalise_announcements(entries, labels, base_url):
    items = []
    for entry in entries:
        cid = None
        match = re.search(r"course_(\d+)", entry.get("context_code", "") or "")
        if match:
            cid = int(match.group(1))
        url = entry.get("html_url") or ""
        if url.startswith("/"):
            url = base_url.rstrip("/") + url
        body = re.sub(r"<[^>]+>", " ", entry.get("message") or "")
        body = re.sub(r"\s+", " ", body).strip()
        items.append({
            "id": f"canvas:announcement:{entry.get('id')}",
            "source": "canvas",
            "kind": "announcement",
            "course": labels.get(cid, "Canvas"),
            "title": entry.get("title") or "(untitled)",
            "due_utc": entry.get("posted_at"),
            "url": url,
            "done": False,
            "body": body[:400],
        })
    return items


def grades(canvas_courses, labels):
    """Current score per course, for the courses that report one."""
    out = []
    for course in canvas_courses:
        for enrolment in course.get("enrollments") or []:
            if enrolment.get("type") not in ("student", "StudentEnrollment"):
                continue
            score = enrolment.get("computed_current_score")
            grade = enrolment.get("computed_current_grade")
            if score is None and not grade:
                continue
            out.append({
                "course": labels.get(course["id"], course.get("name")),
                "score": score,
                "grade": grade,
            })
            break
    out.sort(key=lambda g: (g["score"] is None, g["score"] if g["score"] is not None else 0))
    return out


def collect(base_url, token, schedule):
    """Fetch everything and return normalised items plus a short sync report."""
    api = Canvas(base_url, token)
    courses = api.courses()
    labels = course_labeller(courses, schedule)
    planner = api.planner()
    items = normalise_planner(planner, labels, base_url)
    notes = []
    try:
        anns = api.announcements(list(labels.keys()))
        items += normalise_announcements(anns, labels, base_url)
    except CanvasError as exc:
        # Announcements are a bonus; never let them fail the whole sync.
        notes.append(f"announcements skipped: {exc}")
    return items, {"courses": len(courses), "planner": len(planner),
                   "items": len(items), "notes": notes,
                   "grades": grades(courses, labels)}
