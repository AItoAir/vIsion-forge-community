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

from app.database import Base, get_db
from app.models import Project, Team, User, UserRole
from app.routers import web_teams
from app.security import get_current_user, verify_password


class TeamManagementRoutesTests(unittest.TestCase):
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

        self.admin = User(
            email="admin@example.com",
            password_hash="hash",
            role=UserRole.system_admin,
            team_id=self.team.id,
            is_active=True,
        )
        self.member = User(
            email="member@example.com",
            password_hash="hash",
            role=UserRole.annotator,
            team_id=self.team.id,
            is_active=True,
        )
        self.inactive_member = User(
            email="inactive@example.com",
            password_hash="old-hash",
            role=UserRole.reviewer,
            team_id=self.team.id,
            is_active=False,
        )
        self.owner = User(
            email="owner@example.com",
            password_hash="hash",
            role=UserRole.project_admin,
            team_id=self.team.id,
            is_active=True,
        )
        self.db.add_all(
            [
                self.admin,
                self.member,
                self.inactive_member,
                self.owner,
            ]
        )
        self.db.flush()

        self.project = Project(
            name="Warehouse",
            description="Test project",
            owner_user_id=self.owner.id,
            is_archived=False,
        )
        self.db.add(self.project)
        self.db.commit()

        self.app = FastAPI()
        static_dir = Path(__file__).resolve().parent.parent / "static"
        self.app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @self.app.middleware("http")
        async def inject_request_state(request: Request, call_next):
            request.state.csrf_token = "test-csrf-token"
            request.state.user = self.db.get(User, self.admin.id)
            return await call_next(request)

        @self.app.get("/", name="projects_index")
        async def projects_index():
            return PlainTextResponse("ok")

        @self.app.get("/my-page", name="my_page")
        async def my_page():
            return PlainTextResponse("ok")

        @self.app.get("/login", name="login")
        async def login():
            return PlainTextResponse("ok")

        @self.app.post("/logout", name="logout")
        async def logout():
            return PlainTextResponse("ok")

        @self.app.get("/api/notifications", name="list_notifications")
        async def list_notifications():
            return JSONResponse({"notifications": [], "unread_count": 0})

        @self.app.post("/api/notifications/read", name="read_notifications")
        async def read_notifications():
            return JSONResponse({"marked_count": 0, "unread_count": 0})

        self.app.include_router(web_teams.router)
        self.app.dependency_overrides[get_db] = self._override_get_db
        self.app.dependency_overrides[get_current_user] = self._override_get_current_user
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
        return self.db.get(User, self.admin.id)

    def test_invite_existing_inactive_member_reactivates_and_updates_role(self) -> None:
        response = self.client.post(
            f"/teams/{self.team.id}/invite",
            data={
                "email": self.inactive_member.email,
                "password": "FreshPass123",
                "role": "project_admin",
            },
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn("notice=invited", response.headers.get("location", ""))

        self.db.expire_all()
        member = self.db.get(User, self.inactive_member.id)
        self.assertIsNotNone(member)
        self.assertTrue(member.is_active)
        self.assertEqual(UserRole.project_admin, member.role)
        self.assertEqual(self.team.id, member.team_id)
        self.assertTrue(verify_password("FreshPass123", member.password_hash))

    def test_update_team_member_role_changes_existing_member(self) -> None:
        response = self.client.post(
            f"/teams/{self.team.id}/members/{self.member.id}/role",
            data={"role": "reviewer"},
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn("notice=role_updated", response.headers.get("location", ""))

        self.db.expire_all()
        member = self.db.get(User, self.member.id)
        self.assertIsNotNone(member)
        self.assertEqual(UserRole.reviewer, member.role)

    def test_team_settings_lists_inactive_members(self) -> None:
        page = self.client.get(f"/teams/{self.team.id}/settings")

        self.assertEqual(200, page.status_code)
        self.assertIn(self.member.email, page.text)
        self.assertIn(self.inactive_member.email, page.text)
        self.assertIn("inactive", page.text)

    def test_remove_team_member_deactivates_member_without_clearing_team(self) -> None:
        response = self.client.post(
            f"/teams/{self.team.id}/members/{self.owner.id}/remove",
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn("notice=removed", response.headers.get("location", ""))

        self.db.expire_all()
        owner = self.db.get(User, self.owner.id)
        self.assertIsNotNone(owner)
        self.assertFalse(owner.is_active)
        self.assertEqual(self.team.id, owner.team_id)

        page = self.client.get(f"/teams/{self.team.id}/settings")
        self.assertEqual(200, page.status_code)
        self.assertIn(self.owner.email, page.text)
        self.assertIn("inactive", page.text)

    def test_system_admin_account_cannot_be_changed_from_team_settings(self) -> None:
        response = self.client.post(
            f"/teams/{self.team.id}/members/{self.admin.id}/remove",
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn(
            "error=system_admin_managed_separately",
            response.headers.get("location", ""),
        )

        self.db.expire_all()
        admin = self.db.get(User, self.admin.id)
        self.assertIsNotNone(admin)
        self.assertTrue(admin.is_active)
        self.assertEqual(UserRole.system_admin, admin.role)


if __name__ == "__main__":
    unittest.main()
