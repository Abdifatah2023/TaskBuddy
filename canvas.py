import os
import json
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

CANVAS_TOKEN   = os.getenv("CANVAS_API_TOKEN")
CANVAS_BASE    = os.getenv("CANVAS_BASE_URL", "").rstrip("/")
CANVAS_COURSES = os.getenv("CANVAS_COURSE_IDS", "")  # comma-separated e.g. "12345,67890"

_canvas_headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _canvas_get(url: str) -> requests.Response:
    """Canvas API request with auth. Raises on HTTP error."""
    res = requests.get(url, headers=_canvas_headers)
    res.raise_for_status()
    return res


def _cdn_get(url: str) -> requests.Response:
    """Download from Canvas CDN / S3 pre-signed URLs without auth headers."""
    res = requests.get(url)
    res.raise_for_status()
    return res


def _get_paginated(url: str) -> list:
    """Follow Canvas Link-header pagination and return all results."""
    results = []
    while url:
        res = _canvas_get(url)
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
    """Strip HTML tags and return clean UTF-8 text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n").encode("utf-8", "ignore").decode("utf-8")


def _fetch_page_text(item: dict, course_id: str) -> str:
    page_url = item.get("url", "")
    if page_url.startswith("/"):
        page_url = f"{CANVAS_BASE}{page_url}"
    if not page_url:
        return ""
    res = _canvas_get(page_url)
    return _html_to_text(res.json().get("body") or "")


def _fetch_assignment_text(item: dict, course_id: str) -> str:
    content_id = item.get("content_id")
    if not content_id:
        return ""
    url = f"{CANVAS_BASE}/api/v1/courses/{course_id}/assignments/{content_id}"
    res = _canvas_get(url)
    data = res.json()
    header = (
        f"Assignment: {data.get('name', '')}\n"
        f"Due: {data.get('due_at', 'N/A')}\n"
        f"Points: {data.get('points_possible', 'N/A')}\n\n"
    )
    return header + _html_to_text(data.get("description") or "")


def _fetch_quiz_text(item: dict, course_id: str) -> str:
    content_id = item.get("content_id")
    if not content_id:
        return ""
    url = f"{CANVAS_BASE}/api/v1/courses/{course_id}/quizzes/{content_id}"
    res = _canvas_get(url)
    data = res.json()
    header = (
        f"Quiz: {data.get('title', '')}\n"
        f"Due: {data.get('due_at', 'N/A')}\n"
        f"Points: {data.get('points_possible', 'N/A')}\n\n"
    )
    return header + _html_to_text(data.get("description") or "")


def _fetch_file_text(item: dict) -> str:
    TEXT_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".csv", ".json", ".xml"}
    file_api_url = item.get("url", "")
    if file_api_url.startswith("/"):
        file_api_url = f"{CANVAS_BASE}{file_api_url}"
    if not file_api_url:
        return ""
    res = _canvas_get(file_api_url)
    file_data = res.json()
    download_url = file_data.get("url")
    filename = file_data.get("filename", "")
    if not download_url:
        return ""
    ext = os.path.splitext(filename.lower())[1]
    if ext not in TEXT_EXTENSIONS:
        return f"[Binary file skipped: {filename}]"
    return _cdn_get(download_url).text


def _get_syllabus_text(course_id: str) -> str:
    """Return only the syllabus body for a course as plain text."""
    try:
        res = _canvas_get(
            f"{CANVAS_BASE}/api/v1/courses/{course_id}?include[]=syllabus_body"
        )
        return _html_to_text(res.json().get("syllabus_body") or "")
    except requests.HTTPError as e:
        return f"[Syllabus fetch failed for course {course_id}: {e}]"


def _get_course_text(course_id: str) -> str:
    """
    Pull all readable text from a single Canvas course:
    syllabus + all module items (pages, assignments, quizzes, text files).
    Returns one concatenated string ready for the RAG pipeline.
    """
    sections: list[str] = []

    # Syllabus
    syllabus_text = _get_syllabus_text(course_id)
    if syllabus_text.strip():
        sections.append(f"=== SYLLABUS (course {course_id}) ===\n{syllabus_text}")

    # Modules → Items
    try:
        modules = _get_paginated(
            f"{CANVAS_BASE}/api/v1/courses/{course_id}/modules?per_page=100"
        )
    except requests.exceptions.RequestException as e:
        sections.append(f"[Module fetch failed for course {course_id}: {e}]")
        modules = []

    for module in modules:
        if not isinstance(module, dict) or "id" not in module:
            continue
        module_name = module.get("name", f"Module {module['id']}")
        try:
            items = _get_paginated(
                f"{CANVAS_BASE}/api/v1/courses/{course_id}/modules/{module['id']}/items?per_page=100"
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
                    text = _fetch_page_text(item, course_id)
                elif item_type == "Assignment":
                    text = _fetch_assignment_text(item, course_id)
                elif item_type == "Quiz":
                    text = _fetch_quiz_text(item, course_id)
                elif item_type == "File":
                    text = _fetch_file_text(item)
                elif item_type == "ExternalUrl":
                    text = f"External URL: {item.get('external_url', 'N/A')}"
            except requests.exceptions.RequestException as e:
                text = f"[Fetch error for {item_type} '{item_title}': {e}]"

            if text.strip():
                sections.append(
                    f"--- [{module_name}] {item_type}: {item_title} ---\n{text}"
                )

    return "\n\n".join(sections)


# ── Agent-facing tools ────────────────────────────────────────────────────────

@tool
def list_canvas_courses(_: str = "") -> str:
    """
    List all Canvas courses accessible with the configured API token.
    Returns a JSON list of {course_id, course_name}.
    Call this first to discover which courses are available.
    If CANVAS_COURSE_IDS is set in .env, only those courses are returned.
    """
    if CANVAS_COURSES.strip():
        result = [
            {"course_id": cid.strip(), "course_name": f"Course {cid.strip()}"}
            for cid in CANVAS_COURSES.split(",") if cid.strip()
        ]
    else:
        try:
            courses = _get_paginated(
                f"{CANVAS_BASE}/api/v1/courses?enrollment_state=active&per_page=100"
            )
        except requests.HTTPError as e:
            return json.dumps({"error": str(e)})
        result = [
            {"course_id": str(c["id"]), "course_name": c.get("name", "Unknown")}
            for c in courses if isinstance(c, dict) and "id" in c
        ]
    return json.dumps(result)


@tool
def save_canvas_course_to_drive(course_id: str, course_name: str) -> str:
    """
    Extract the syllabus and all course content from a Canvas course and save
    them to a dedicated subfolder inside the configured Google Drive folder.

    Creates:
      <course_name>/syllabus.txt        — syllabus body only
      <course_name>/course_content.txt  — all module content (pages, assignments, quizzes)

    Returns a JSON summary with the Drive folder ID and the IDs of saved files.
    Call this for EACH course returned by list_canvas_courses.
    """
    from google_drive import get_drive_service, FOLDER_ID
    from drive_upload_utils import get_or_create_folder, upload_text_file

    service = get_drive_service()

    # Sanitise folder name
    safe_name = "".join(
        c if c.isalnum() or c in (" ", "_", "-") else "_" for c in course_name
    ).strip() or f"course_{course_id}"

    course_folder_id = get_or_create_folder(service, safe_name, FOLDER_ID)
    saved = []

    # Save syllabus
    try:
        syllabus_text = _get_syllabus_text(course_id)
        if syllabus_text.strip():
            fid = upload_text_file(service, course_folder_id, "syllabus.txt", syllabus_text)
            saved.append({"file": "syllabus.txt", "file_id": fid})
        else:
            saved.append({"file": "syllabus.txt", "note": "empty — skipped"})
    except Exception as e:
        saved.append({"file": "syllabus.txt", "error": str(e)})

    # Save full course content
    try:
        content = _get_course_text(course_id)
        if content.strip():
            fid = upload_text_file(service, course_folder_id, "course_content.txt", content)
            saved.append({"file": "course_content.txt", "file_id": fid})
        else:
            saved.append({"file": "course_content.txt", "note": "empty — skipped"})
    except Exception as e:
        saved.append({"file": "course_content.txt", "error": str(e)})

    return json.dumps({
        "course_id": course_id,
        "course_name": course_name,
        "drive_folder_id": course_folder_id,
        "files_saved": saved,
    })


@tool
def extract_assignments_from_canvas(course_id: str) -> str:
    """
    Fetch all content (syllabus, pages, assignments, quizzes) from a Canvas
    course by its numeric course_id and extract every assignment and deadline
    using the RAG pipeline.

    Returns a JSON list of {assignment_name, due_date} objects.
    Call this for EACH course returned by list_canvas_courses.
    Pass one course_id at a time.
    """
    import RagPipeline as rp

    full_text = _get_course_text(course_id)
    if not full_text.strip():
        return "[]"

    rp.build_rag_chain(full_text)
    if rp.rag_chain is None:
        return f"[]  # RAG chain could not be built for course {course_id}"

    return rp.rag_chain.invoke(
        f"Extract all assignments, projects, and exams with their due dates "
        f"from Canvas course {course_id}."
    )
