from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.csrf import (
    CSRF_HEADER_NAME,
    configured_allowed_origins,
    csrf_protection_required,
    ensure_csrf_token,
    normalize_origin,
    request_has_allowed_origin,
    request_has_valid_csrf_token,
    request_passes_csrf,
)
from app.config import settings


class DummyRequest:
    def __init__(
        self,
        *,
        method: str = "GET",
        base_url: str = "http://testserver/",
        headers: dict[str, str] | None = None,
        session: dict[str, str] | None = None,
    ) -> None:
        self.method = method
        self.base_url = base_url
        self.headers = headers or {}
        self.session = session or {}
        self.state = SimpleNamespace()


class CsrfHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_cors_allow_origins = settings.cors_allow_origins
        settings.cors_allow_origins = "https://console.example.com"

    def tearDown(self) -> None:
        settings.cors_allow_origins = self.original_cors_allow_origins

    def test_normalize_origin_rejects_invalid_values(self) -> None:
        self.assertIsNone(normalize_origin("null"))
        self.assertIsNone(normalize_origin("/relative/path"))
        self.assertEqual(
            normalize_origin("https://example.com/path?q=1"),
            "https://example.com",
        )

    def test_configured_allowed_origins_includes_current_origin(self) -> None:
        self.assertEqual(
            configured_allowed_origins(current_origin="http://testserver"),
            {"http://testserver", "https://console.example.com"},
        )

    def test_ensure_csrf_token_persists_session_value(self) -> None:
        request = DummyRequest()

        token = ensure_csrf_token(request)

        self.assertEqual(request.session["_csrf_token"], token)
        self.assertEqual(request.state.csrf_token, token)

    def test_request_has_allowed_origin_accepts_same_origin(self) -> None:
        request = DummyRequest(
            method="POST",
            headers={"origin": "http://testserver"},
        )

        self.assertTrue(request_has_allowed_origin(request))

    def test_request_has_allowed_origin_accepts_allowlisted_origin(self) -> None:
        request = DummyRequest(
            method="PATCH",
            headers={"origin": "https://console.example.com"},
        )

        self.assertTrue(request_has_allowed_origin(request))

    def test_request_has_valid_csrf_token_accepts_matching_header(self) -> None:
        request = DummyRequest(
            method="PATCH",
            session={"_csrf_token": "abc123"},
            headers={CSRF_HEADER_NAME: "abc123"},
        )

        self.assertTrue(request_has_valid_csrf_token(request))

    def test_request_passes_csrf_rejects_cross_site_without_token(self) -> None:
        request = DummyRequest(
            method="POST",
            headers={"origin": "https://attacker.example.com"},
            session={"_csrf_token": "abc123"},
        )

        self.assertFalse(request_passes_csrf(request))

    def test_request_passes_csrf_accepts_cross_site_with_token(self) -> None:
        request = DummyRequest(
            method="POST",
            headers={
                "origin": "https://attacker.example.com",
                CSRF_HEADER_NAME: "abc123",
            },
            session={"_csrf_token": "abc123"},
        )

        self.assertTrue(request_passes_csrf(request))

    def test_csrf_protection_required_for_mutating_methods(self) -> None:
        self.assertFalse(csrf_protection_required("GET"))
        self.assertTrue(csrf_protection_required("PATCH"))


if __name__ == "__main__":
    unittest.main()
