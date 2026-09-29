"""Canvas LMS collector.

Uses a personal access token (Canvas > Account > Settings > New Access Token).
That is deliberately the only auth path here: Student Hub and everything else
behind NU's SSO needs Duo, and scraping a push-MFA login is fragile in a way a
token is not.

stdlib only — no requests. This runs on a 2009 Core2Duo with no pip packages,
and every dependency avoided is a wheel that can't fail to build.
"""
import html
import http.client
import json
import re
import shutil
import ssl
import subprocess
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
        self.truncated = False

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
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # urllib wraps only the connect: a timeout waiting for the reply or
            # mid-body, a cut connection, or a 200 maintenance page in HTML all
            # arrive raw, and used to escape the sync.
            raise CanvasError(f"Canvas request failed: {type(exc).__name__}: {exc}") from exc

    def get(self, path, **params):
        """GET with Link-header pagination followed to the end. `truncated` is
        set if the page cap stopped it first, so nothing treats a partial
        listing as the whole of it."""
        params.setdefault("per_page", 100)
        url = f"{self.base}/api/v1/{path.lstrip('/')}?{urllib.parse.urlencode(params, doseq=True)}"
        out = []
        seen = 0
        while url and seen < 20:  # hard page cap; a runaway loop must not hang the sync
            data, link = self._request(url)
            out.extend(data if isinstance(data, list) else [data])
            url = _next_link(link)
            seen += 1
        self.truncated = bool(url)
        return out

    def profile(self):
        return self.get("users/self/profile")[0]

    def courses(self):
        # total_scores brings the enrollment's current score back with the course,
        # so grades cost no extra request.
        return self.get("courses", enrollment_state="active",
                        include=["term", "total_scores"], state=["available"])

    def planner(self, days_back=14, days_ahead=45):
        # days_back matches store.upcoming's stale_days: a late submission of
        # something due 9 days ago must still come back marked done.
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
        label = course.get("course_code") or course.get("name") or f"Course {course['id']}"
        # course_code FIRST, and only fall back to the name. "CS2001 Lab for
        # CS2000 MERGED" contains both codes, so matching the name would label
        # the CS 2001 lab as CS 2000.
        for field in (course.get("course_code"), course.get("name")):
            blob = _norm_code(field)
            if not blob:
                continue
            # The code that comes first in the text, not first in the schedule.
            found = [(blob.find(norm), pretty) for norm, pretty in known.items() if norm and norm in blob]
            if found:
                label = min(found)[1]
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
    override = entry.get("planner_override")
    if override:
        # An override is the answer when there is one, as in Canvas's own
        # planner: undoing a submitted item sets marked_complete false, and
        # falling through to "submitted" would re-mark it done on every sync.
        return bool(override.get("marked_complete") or override.get("dismissed"))
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


def set_complete(base_url, token, plannable_type, plannable_id, complete=True):
    """Mark a planner item done in Canvas itself, not just locally.

    Writing the override back means the state follows him into the Canvas app
    and survives a rebuild of this database. `_is_done` already reads it.
    """
    api = Canvas(base_url, token)

    # POST *creates* an override; a second one for the same item is rejected with
    # 400 "has already been taken". So an existing override must be PUT, or undo
    # silently never reaches Canvas and the next sync re-marks it done.
    existing = None
    try:
        for override in api.get("planner/overrides"):
            if (str(override.get("plannable_id")) == str(plannable_id)
                    and override.get("plannable_type") == plannable_type):
                existing = override.get("id")
                break
    except CanvasError:
        existing = None

    if existing:
        url = f"{api.base}/api/v1/planner/overrides/{existing}"
        method = "PUT"
        payload = json.dumps({"marked_complete": bool(complete)}).encode()
    else:
        url = f"{api.base}/api/v1/planner/overrides"
        method = "POST"
        payload = json.dumps({
            "plannable_type": plannable_type,
            "plannable_id": int(plannable_id),
            "marked_complete": bool(complete),
        }).encode()

    req = urllib.request.Request(url, data=payload, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=api.ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:200]
        except Exception:
            pass
        raise CanvasError(f"Canvas refused the update (HTTP {exc.code}) {detail}") from exc
    except urllib.error.URLError as exc:
        raise CanvasError(f"Could not reach Canvas: {exc.reason}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise CanvasError(f"Canvas request failed: {type(exc).__name__}: {exc}") from exc


def collect(base_url, token, schedule):
    """Fetch everything and return normalised items plus a short sync report."""
    api = Canvas(base_url, token)
    courses = api.courses()
    labels = course_labeller(courses, schedule)
    planner = api.planner()
    # Whether this is the whole planner window: retiring what it lacks is only
    # safe then, and an empty answer from an account with courses isn't.
    complete = not api.truncated and bool(planner or not courses)
    items = normalise_planner(planner, labels, base_url)
    notes = []
    try:
        anns = api.announcements(list(labels.keys()))
        items += normalise_announcements(anns, labels, base_url)
    except CanvasError as exc:
        # Announcements are a bonus; never let them fail the whole sync.
        notes.append(f"announcements skipped: {exc}")
    return items, {"courses": len(courses), "planner": len(planner), "complete": complete,
                   "items": len(items), "notes": notes,
                   "grades": grades(courses, labels)}


# ---- one item's full text, for scribe's homework ↔ lecture links -------------------------

_ITEM_URL = re.compile(r"/courses/(\d+)/(assignments|discussion_topics|quizzes)/(\d+)")
_TEXT_FIELD = {"assignments": "description", "discussion_topics": "message", "quizzes": "description"}


def html_to_text(markup):
    """Canvas descriptions are HTML; keep the paragraph and list breaks."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>", "\n", markup or "")
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


FILE_LINK = re.compile(r'<a\b([^>]*data-api-endpoint="[^"]*/api/v1/courses/(\d+)/files/(\d+)"[^>]*)>(.*?)</a>',
                       re.S | re.I)
SOLUTIONS = re.compile(r"\bsolutions?\b|\bsol(?:n|ns|s)?\b|\bhws\d|answer key|\banswers\b|\bkey\b", re.I)


def _solutions(text):
    """"hw3_key.pdf" and "HW3 Solns" too: \b doesn't see a word end at _."""
    return SOLUTIONS.search(re.sub(r"[_.\-]+", " ", text or ""))
MAX_FILES, MAX_FILE_BYTES, MAX_FILE_CHARS = 3, 10_000_000, 40_000


def _file_text(api, endpoint):
    """(name, text) of a linked Canvas file: PDFs through pdftotext, plain text
    as is. None for anything else, or anything too big."""
    meta, _ = api._request(endpoint)
    ctype, size = (meta.get("content-type") or ""), int(meta.get("size") or 0)
    if not meta.get("url") or size > MAX_FILE_BYTES:
        return None
    req = urllib.request.Request(meta["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=api.ctx) as resp:
        data = resp.read(MAX_FILE_BYTES + 1)
    name = meta.get("display_name") or meta.get("filename") or "file"
    if ctype.startswith("text/"):
        return name, data.decode("utf-8", "replace")[:MAX_FILE_CHARS]
    if ctype == "application/pdf" and shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", "-q", "-", "-"], input=data, capture_output=True, timeout=60)
        return name, out.stdout.decode("utf-8", "replace")[:MAX_FILE_CHARS]
    return None


def describe(base_url, token, item_url):
    """{"text", "files": [{"name", "text"}]} for the assignment, discussion or
    quiz at `item_url`. Homework often lives in a linked PDF (CS 1800's
    description is only the link), so up to MAX_FILES linked files are read too."""
    m = _ITEM_URL.search(item_url or "")
    if not m:
        return {"text": "", "files": []}
    course_id, kind, item_id = m.groups()
    api = Canvas(base_url, token)
    data, _ = api._request(f"{base_url.rstrip('/')}/api/v1/courses/{course_id}/{kind}/{item_id}")
    markup = (data or {}).get(_TEXT_FIELD[kind]) or ""
    files, seen = [], set()
    for attrs, link_course, file_id, label in FILE_LINK.findall(markup):
        # Never the solutions: this feeds homework help, and he wants hints, not answers.
        if file_id in seen or _solutions(attrs + " " + label) or len(files) >= MAX_FILES:
            continue
        seen.add(file_id)
        # Rebuilt on the Canvas host rather than taken from the link, which
        # could name any host, and would be sent the token.
        endpoint = f"{api.base}/api/v1/courses/{link_course}/files/{file_id}"
        try:
            got = _file_text(api, endpoint)
        except (CanvasError, OSError, http.client.HTTPException, ValueError, subprocess.SubprocessError):
            got = None
        if got and got[1].strip() and not _solutions(got[0]):
            files.append({"name": got[0], "text": got[1]})
    return {"text": html_to_text(markup), "files": files}
