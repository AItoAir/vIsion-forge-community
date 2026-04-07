# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import unittest

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.template_utils import STATIC_DIR, TEMPLATES_DIR, create_templates


class TemplateAssetRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        self.templates = create_templates()

        @self.app.get("/", name="projects_index")
        async def projects_index(request: Request) -> HTMLResponse:
            request.state.user = None
            request.state.csrf_token = "test-csrf-token"
            return self.templates.TemplateResponse(request, "base.html")

        @self.app.get("/login", name="login")
        async def login() -> HTMLResponse:
            return HTMLResponse("login")

        @self.app.get("/api/notifications", name="list_notifications")
        async def list_notifications() -> JSONResponse:
            return JSONResponse([])

        @self.app.post("/api/notifications/read", name="read_notifications")
        async def read_notifications() -> JSONResponse:
            return JSONResponse({"ok": True})

        self.client = TestClient(self.app)

    def test_base_template_disables_body_level_hx_boost(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('hx-boost="true"', response.text)

    def test_base_template_uses_versioned_static_assets(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        css_version = (STATIC_DIR / "css" / "app.css").stat().st_mtime_ns
        bootstrap_fallback_version = (
            STATIC_DIR / "js" / "bootstrap_fallback.js"
        ).stat().st_mtime_ns
        notification_center_version = (
            STATIC_DIR / "js" / "notification_center.js"
        ).stat().st_mtime_ns

        self.assertIn(f"/static/css/app.css?v={css_version}", response.text)
        self.assertIn(
            f"/static/js/bootstrap_fallback.js?v={bootstrap_fallback_version}",
            response.text,
        )
        self.assertIn(
            f"/static/js/notification_center.js?v={notification_center_version}",
            response.text,
        )

    def test_item_label_template_keeps_quiet_annotation_panels(self) -> None:
        template_text = (TEMPLATES_DIR / "item_label.html").read_text(encoding="utf-8")

        self.assertIn('id="btn-prev-item"', template_text)
        self.assertIn('id="btn-next-item"', template_text)
        self.assertIn('id="collaboration-live-count"', template_text)
        self.assertIn('id="collaboration-participant-list"', template_text)
        self.assertNotIn('id="item-status-badge"', template_text)
        self.assertNotIn('id="annotation-pending-state-indicator"', template_text)
        self.assertNotIn('id="collaboration-status"', template_text)
        self.assertNotIn('id="collaboration-follow-status"', template_text)

    def test_annotation_core_includes_navigation_guard_wiring(self) -> None:
        script_text = (STATIC_DIR / "js" / "annotation_core.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const ANNOTATION_SAVE_DEBOUNCE_MS = 150;", script_text)
        self.assertIn("beforeunload", script_text)
        self.assertIn("Polygon draft in progress", script_text)
        self.assertIn("getPendingNavigationWarning", script_text)

    def test_annotation_collaboration_avoids_transient_status_messages(self) -> None:
        script_text = (STATIC_DIR / "js" / "annotation_collaboration.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("toggleFollowParticipant", script_text)
        self.assertNotIn("Connecting collaboration channel...", script_text)
        self.assertNotIn("Synced latest teammate annotations.", script_text)
        self.assertNotIn("Collaboration channel disconnected. Reconnecting...", script_text)


if __name__ == "__main__":
    unittest.main()
