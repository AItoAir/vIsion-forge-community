from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.websockets import WebSocket

from .config import settings


SAFE_CSRF_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_SESSION_KEY = "_csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def normalize_origin(value: str | None) -> str | None:
    raw_value = (value or "").strip()
    if not raw_value or raw_value.lower() == "null":
        return None

    parts = urlsplit(raw_value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None

    return f"{parts.scheme}://{parts.netloc}"


def current_request_origin(request: Request) -> str | None:
    return normalize_origin(str(request.base_url))


def current_websocket_origin(websocket: WebSocket) -> str | None:
    scheme = (websocket.url.scheme or "").strip().lower()
    if scheme == "ws":
        scheme = "http"
    elif scheme == "wss":
        scheme = "https"
    return normalize_origin(f"{scheme}://{websocket.url.netloc}")


def configured_allowed_origins(*, current_origin: str | None = None) -> set[str]:
    origins: set[str] = set()
    if current_origin:
        origins.add(current_origin)

    for raw_origin in settings.cors_allow_origins.split(","):
        normalized = normalize_origin(raw_origin)
        if normalized:
            origins.add(normalized)

    return origins


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token.strip():
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token

    request.state.csrf_token = token
    return token


def request_origin(request: Request) -> str | None:
    origin_header = normalize_origin(request.headers.get("origin"))
    if origin_header:
        return origin_header
    return normalize_origin(request.headers.get("referer"))


def request_has_allowed_origin(
    request: Request,
    *,
    allow_fetch_metadata_fallback: bool = False,
) -> bool:
    current_origin = current_request_origin(request)
    allowed_origins = configured_allowed_origins(current_origin=current_origin)

    provided_origin = request_origin(request)
    if provided_origin:
        return provided_origin in allowed_origins

    if not allow_fetch_metadata_fallback:
        return False

    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    return sec_fetch_site in {"same-origin", "same-site", "none"}


def request_has_valid_csrf_token(request: Request) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    provided = (request.headers.get(CSRF_HEADER_NAME) or "").strip()
    if not isinstance(expected, str) or not expected or not provided:
        return False
    return secrets.compare_digest(expected, provided)


def csrf_protection_required(method: str) -> bool:
    return method.upper() not in SAFE_CSRF_METHODS


def request_passes_csrf(request: Request) -> bool:
    if not csrf_protection_required(request.method):
        return True

    ensure_csrf_token(request)
    if request_has_allowed_origin(request):
        return True
    return request_has_valid_csrf_token(request)


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    current_origin = current_websocket_origin(websocket)
    allowed_origins = configured_allowed_origins(current_origin=current_origin)
    websocket_request_origin = normalize_origin(websocket.headers.get("origin"))
    if not websocket_request_origin:
        return False
    return websocket_request_origin in allowed_origins
