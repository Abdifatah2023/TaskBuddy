import base64
import hashlib
import hmac
import json
import os

import requests

SESSION_SECRET = os.getenv("SESSION_SECRET", "taskbuddy-dev-secret-change-in-production")


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _encode(data: dict) -> str:
    """Encode session data as a signed, base64-encoded cookie value."""
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def _decode(token: str) -> dict | None:
    """Decode and verify a signed cookie. Returns None if invalid or tampered."""
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        return json.loads(base64.urlsafe_b64decode(payload + "=="))
    except Exception:
        return None


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
    """Return a signed cookie value containing the session data."""
    return _encode({
        "email": email,
        "canvas_token": canvas_token,
        "canvas_base_url": canvas_base_url.rstrip("/"),
        "canvas_course_ids": canvas_course_ids,
    })


def get_session(cookie_value: str | None) -> dict | None:
    """Decode and return the session dict, or None if the cookie is missing/invalid."""
    if not cookie_value:
        return None
    return _decode(cookie_value)


def delete_session(cookie_value: str) -> None:
    """No-op: stateless cookies are invalidated by deleting the cookie on the client."""
    pass
