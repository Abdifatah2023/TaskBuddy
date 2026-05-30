import uuid
import requests

_auth_sessions: dict[str, dict] = {}


def validate_canvas_token(canvas_base_url: str, canvas_token: str) -> bool:
    """Return True if the Canvas token is valid for the given base URL."""
    try:
        url = f"{canvas_base_url.rstrip('/')}/api/v1/courses?enrollment_state=active&per_page=1"
        res = requests.get(
            url,
            headers={"Authorization": f"Bearer {canvas_token}"},
            timeout=10,
        )
        return res.status_code == 200
    except Exception:
        return False


def create_session(
    email: str,
    canvas_token: str,
    canvas_base_url: str,
    canvas_course_ids: str = "",
) -> str:
    """Store user details in the session store. Returns a new session ID."""
    session_id = str(uuid.uuid4())
    _auth_sessions[session_id] = {
        "email": email,
        "canvas_token": canvas_token,
        "canvas_base_url": canvas_base_url.rstrip("/"),
        "canvas_course_ids": canvas_course_ids,
    }
    return session_id


def get_session(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    return _auth_sessions.get(session_id)


def delete_session(session_id: str) -> None:
    _auth_sessions.pop(session_id, None)
