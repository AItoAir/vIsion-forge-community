from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, get_db
from app.models import ApiKey, Team, User, UserRole
from app.routers import auth
from app.security import create_api_key, get_current_user, get_optional_user


class MyPageApiKeysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        self.team = Team(name="Vision team", is_active=True)
        self.db.add(self.team)
        self.db.flush()

        self.user = User(
            email="annotator@example.com",
            password_hash="hash",
            role=UserRole.annotator,
            team_id=self.team.id,
            is_active=True,
            name="Annotator",
            department="Ops",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test-secret")

        static_dir = Path(__file__).resolve().parent.parent / "static"
        self.app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @self.app.middleware("http")
        async def inject_request_state(request: Request, call_next):
            request.state.csrf_token = "test-csrf-token"
            request.state.user = self.db.get(User, self.user.id)
            return await call_next(request)

        @self.app.get("/", name="projects_index")
        async def projects_index():
            return PlainTextResponse("ok")

        @self.app.get("/api/notifications", name="list_notifications")
        async def list_notifications():
            return JSONResponse({"notifications": [], "unread_count": 0})

        @self.app.post("/api/notifications/read", name="read_notifications")
        async def read_notifications():
            return JSONResponse({"marked_count": 0, "unread_count": 0})

        self.app.include_router(auth.router)
        self.app.dependency_overrides[get_db] = self._override_get_db
        self.app.dependency_overrides[get_current_user] = self._override_get_current_user
        self.app.dependency_overrides[get_optional_user] = (
            self._override_get_optional_user
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def _override_get_db(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _override_get_current_user(self):
        return self.db.get(User, self.user.id)

    def _override_get_optional_user(self):
        return self.db.get(User, self.user.id)

    def test_my_page_shows_developer_api_key_section(self) -> None:
        response = self.client.get("/my-page")

        self.assertEqual(200, response.status_code)
        self.assertIn("Developer API Keys", response.text)
        self.assertIn("No developer API keys yet.", response.text)
        self.assertIn("/api/v1", response.text)

    def test_create_api_key_from_my_page_shows_token_once(self) -> None:
        response = self.client.post(
            "/my-page/api-keys",
            data={"name": "Local automation"},
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("Developer API key created.", response.text)
        self.assertIn("This token is shown only once.", response.text)
        self.assertIn("Developer API keys use the", response.text)
        self.assertIn("Local automation", response.text)
        self.assertIn("fpk_", response.text)

        api_keys = (
            self.db.query(ApiKey)
            .filter(ApiKey.user_id == self.user.id)
            .order_by(ApiKey.id.asc())
            .all()
        )
        self.assertEqual(1, len(api_keys))
        self.assertEqual("Local automation", api_keys[0].name)
        self.assertTrue(api_keys[0].is_active)

        refreshed_page = self.client.get("/my-page")
        self.assertEqual(200, refreshed_page.status_code)
        self.assertNotIn("This token is shown only once.", refreshed_page.text)

    def test_revoke_api_key_from_my_page_marks_key_inactive(self) -> None:
        api_key, _raw_token = create_api_key(
            self.db,
            user=self.user,
            name="CI key",
        )
        self.db.commit()
        self.db.refresh(api_key)

        response = self.client.post(
            f"/my-page/api-keys/{api_key.id}/revoke",
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn("api_key=revoked", response.headers.get("location", ""))

        self.db.expire_all()
        reloaded_key = self.db.get(ApiKey, api_key.id)
        self.assertIsNotNone(reloaded_key)
        self.assertFalse(reloaded_key.is_active)
        self.assertIsNotNone(reloaded_key.revoked_at)


if __name__ == "__main__":
    unittest.main()
