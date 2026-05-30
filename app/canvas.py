import json
import zoneinfo
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

_LOCAL_TZ = zoneinfo.ZoneInfo("America/Chicago")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _canvas_utc_to_local_date(dt_string: str) -> str | None:
    if not dt_string or dt_string in ("N/A", "null", "None", ""):
        return None
    try:
        normalized = dt_string.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_LOCAL_TZ)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        clean = dt_string.strip()
        if len(clean) >= 10 and clean[4:5] == "-":
            return clean[:10]
        return None


def _canvas_get(url: str, canvas_token: str) -> requests.Response:
    res = requests.get(url, headers={"Authorization": f"Bearer {canvas_token}"})
    res.raise_for_status()
    return res


def _cdn_get(url: str) -> requests.Response:
    res = requests.get(url)
    res.raise_for_status()
    return res


def _get_paginated(url: str, canvas_token: str) -> list:
    results = []
    while url:
        res = _canvas_get(url, canvas_token)
        data = res.json()
        if isinstance(data, list):
            results.extend(data)
        elif isinstance(data, dict):
            for key in ("modules", "items", "files", "courses", "assignments", "quizzes"):
                if key in data and isinstance(data[key], list):
                    results.extend(data[key])
                    break
            else:
                results.append(data)
        url = res.links.get("next", {}).get("url")
    return results


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n").encode("utf-8", "ignore").decode("utf-8")


def _fetch_page_text(item: dict, course_id: str, canvas_base: str, canvas_token: str) -> str:
    page_url = item.get("url", "")
    if page_url.startswith("/"):
        page_url = f"{canvas_base}{page_url}"
    if not page_url:
        return ""
    res = _canvas_get(page_url, canvas_token)
    return _html_to_text(res.json().get("body") or "")


def _fetch_assignment_text(item: dict, course_id: str, canvas_base: str, canvas_token: str) -> str:
    content_id = item.get("content_id")
    if not content_id:
        return ""
    url  = f"{canvas_base}/api/v1/courses/{course_id}/assignments/{content_id}"
    res  = _canvas_get(url, canvas_token)
    data = res.json()
    due_local = _canvas_utc_to_local_date(data.get("due_at")) or "N/A"
    header = (
        f"Assignment: {data.get('name', '')}\n"
        f"Due: {due_local}\n"
        f"Points: {data.get('points_possible', 'N/A')}\n\n"
    )
    return header + _html_to_text(data.get("description") or "")


def _fetch_quiz_text(item: dict, course_id: str, canvas_base: str, canvas_token: str) -> str:
    content_id = item.get("content_id")
    if not content_id:
        return ""
    url  = f"{canvas_base}/api/v1/courses/{course_id}/quizzes/{content_id}"
    res  = _canvas_get(url, canvas_token)
    data = res.json()
    due_local = _canvas_utc_to_local_date(data.get("due_at")) or "N/A"
    header = (
        f"Quiz: {data.get('title', '')}\n"
        f"Due: {due_local}\n"
        f"Points: {data.get('points_possible', 'N/A')}\n\n"
    )
    return header + _html_to_text(data.get("description") or "")


def _fetch_file_text(item: dict, canvas_base: str, canvas_token: str) -> str:
    TEXT_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".csv", ".json", ".xml"}
    import os
    file_api_url = item.get("url", "")
    if file_api_url.startswith("/"):
        file_api_url = f"{canvas_base}{file_api_url}"
    if not file_api_url:
        return ""
    res       = _canvas_get(file_api_url, canvas_token)
    file_data = res.json()
    download_url = file_data.get("url")
    filename     = file_data.get("filename", "")
    if not download_url:
        return ""
    ext = os.path.splitext(filename.lower())[1]
    if ext not in TEXT_EXTENSIONS:
        return f"[Binary file skipped: {filename}]"
    return _cdn_get(download_url).text


def _get_syllabus_text(course_id: str, canvas_base: str, canvas_token: str) -> str:
    try:
        res = _canvas_get(
            f"{canvas_base}/api/v1/courses/{course_id}?include[]=syllabus_body",
            canvas_token,
        )
        return _html_to_text(res.json().get("syllabus_body") or "")
    except requests.HTTPError as e:
        return f"[Syllabus fetch failed for course {course_id}: {e}]"


def _get_modules_text(course_id: str, canvas_base: str, canvas_token: str) -> str:
    sections: list[str] = []
    try:
        modules = _get_paginated(
            f"{canvas_base}/api/v1/courses/{course_id}/modules?per_page=100",
            canvas_token,
        )
    except requests.exceptions.RequestException as e:
        return f"[Module fetch failed for course {course_id}: {e}]"

    for module in modules:
        if not isinstance(module, dict) or "id" not in module:
            continue
        module_name = module.get("name", f"Module {module['id']}")
        try:
            items = _get_paginated(
                f"{canvas_base}/api/v1/courses/{course_id}/modules/{module['id']}/items?per_page=100",
                canvas_token,
            )
        except requests.exceptions.RequestException:
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            item_type  = item.get("type", "")
            item_title = item.get("title", "Untitled")
            text = ""
            try:
                if item_type == "Page":
                    text = _fetch_page_text(item, course_id, canvas_base, canvas_token)
                elif item_type == "Assignment":
                    text = _fetch_assignment_text(item, course_id, canvas_base, canvas_token)
                elif item_type == "Quiz":
                    text = _fetch_quiz_text(item, course_id, canvas_base, canvas_token)
                elif item_type == "File":
                    text = _fetch_file_text(item, canvas_base, canvas_token)
                elif item_type == "ExternalUrl":
                    text = f"External URL: {item.get('external_url', 'N/A')}"
            except requests.exceptions.RequestException as e:
                text = f"[Fetch error for {item_type} '{item_title}': {e}]"

            if text.strip():
                sections.append(
                    f"--- [{module_name}] {item_type}: {item_title} ---\n{text}"
                )

    return "\n\n".join(sections)


def _get_course_text(course_id: str, canvas_base: str, canvas_token: str) -> str:
    sections: list[str] = []
    syllabus_text = _get_syllabus_text(course_id, canvas_base, canvas_token)
    if syllabus_text.strip():
        sections.append(f"=== SYLLABUS (course {course_id}) ===\n{syllabus_text}")
    modules_text = _get_modules_text(course_id, canvas_base, canvas_token)
    if modules_text.strip():
        sections.append(modules_text)
    return "\n\n".join(sections)


def _fetch_canvas_calendar_events(course_id: str, canvas_base: str, canvas_token: str) -> list[dict]:
    results: dict[str, dict] = {}

    assignments_url = (
        f"{canvas_base}/api/v1/courses/{course_id}/assignments"
        f"?per_page=100&order_by=due_at"
    )
    try:
        for item in _get_paginated(assignments_url, canvas_token):
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            if not name:
                continue
            due = _canvas_utc_to_local_date(item.get("due_at"))
            results[name.lower()] = {"assignment_name": name, "due_date": due}
    except requests.HTTPError:
        pass

    today   = datetime.now(_LOCAL_TZ).date()
    cal_url = (
        f"{canvas_base}/api/v1/calendar_events"
        f"?context_codes[]=course_{course_id}"
        f"&type=assignment"
        f"&start_date={today.isoformat()}"
        f"&end_date={(today + timedelta(days=365)).isoformat()}"
        f"&per_page=100"
    )
    try:
        while cal_url:
            res = _canvas_get(cal_url, canvas_token)
            for item in res.json():
                name = (item.get("title") or "").strip()
                if not name or name.lower() in results:
                    continue
                due_raw = (
                    item.get("assignment", {}).get("due_at")
                    or item.get("start_at")
                )
                due = _canvas_utc_to_local_date(due_raw)
                results[name.lower()] = {"assignment_name": name, "due_date": due}
            cal_url = res.links.get("next", {}).get("url")
    except requests.HTTPError:
        pass

    return list(results.values())


# ── Public functions (called as tools via Agent_setup closures) ────────────────

def list_canvas_courses(canvas_base: str, canvas_token: str, canvas_courses: str) -> str:
    """Return JSON list of {course_id, course_name} for the configured courses."""
    if canvas_courses.strip():
        result = [
            {"course_id": cid.strip(), "course_name": f"Course {cid.strip()}"}
            for cid in canvas_courses.split(",") if cid.strip()
        ]
    else:
        try:
            courses = _get_paginated(
                f"{canvas_base}/api/v1/courses?enrollment_state=active&per_page=100",
                canvas_token,
            )
        except requests.HTTPError as e:
            return json.dumps({"error": str(e)})
        result = [
            {"course_id": str(c["id"]), "course_name": c.get("name", "Unknown")}
            for c in courses if isinstance(c, dict) and "id" in c
        ]
    return json.dumps(result)


def extract_assignments_from_canvas(course_id: str, canvas_base: str, canvas_token: str) -> str:
    """Extract assignments via RAG from all course content."""
    import app.RagPipeline as rp

    full_text = _get_course_text(course_id, canvas_base, canvas_token)
    if not full_text.strip():
        return "[]"

    rp.build_rag_chain(full_text)
    if rp.rag_chain is None:
        return f"[]  # RAG chain could not be built for course {course_id}"

    return rp.rag_chain.invoke(
        f"Extract all assignments, projects, and exams with their due dates "
        f"from Canvas course {course_id}."
    )


def get_canvas_calendar_events(course_id: str, canvas_base: str, canvas_token: str) -> str:
    """Fetch assignments directly from Canvas APIs."""
    events = _fetch_canvas_calendar_events(course_id, canvas_base, canvas_token)
    return json.dumps(events)
