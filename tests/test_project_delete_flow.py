# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import hashlib
import shutil
import unittest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Annotation,
    Item,
    ItemKind,
    ItemStatus,
    LabelClass,
    LabelGeometryKind,
    Notification,
    Project,
    Team,
    User,
    UserRole,
)
from app.routers import web_projects
from app.security import get_current_user


class ProjectDeleteFlowTests(unittest.TestCase):
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

        self.owner = User(
            email="owner@example.com",
            password_hash="hash",
            role=UserRole.project_admin,
            team_id=self.team.id,
            is_active=True,
        )
        self.db.add(self.owner)
        self.db.flush()

        self.project = Project(
            name="Warehouse Cleanup",
            description="Delete me",
            owner_user_id=self.owner.id,
            is_archived=False,
        )
        self.keep_project = Project(
            name="Keep Project",
            description="Should remain",
            owner_user_id=self.owner.id,
            is_archived=False,
        )
        self.db.add_all([self.project, self.keep_project])
        self.db.flush()
        self.project_id = self.project.id
        self.keep_project_id = self.keep_project.id

        self.label_class = LabelClass(
            project_id=self.project_id,
            name="vehicle",
            color_hex="#00ff00",
            geometry_kind=LabelGeometryKind.bbox,
            is_active=True,
            default_use_fixed_box=False,
            default_propagation_frames=0,
        )
        self.keep_label_class = LabelClass(
            project_id=self.keep_project_id,
            name="person",
            color_hex="#ff0000",
            geometry_kind=LabelGeometryKind.bbox,
            is_active=True,
            default_use_fixed_box=False,
            default_propagation_frames=0,
        )
        self.db.add_all([self.label_class, self.keep_label_class])
        self.db.flush()

        self.static_dir = Path(__file__).resolve().parent.parent / "static"
        self.project_upload_dir = self.static_dir / "uploads" / f"project_{self.project_id}"
        self.keep_project_upload_dir = (
            self.static_dir / "uploads" / f"project_{self.keep_project_id}"
        )
        self.project_upload_dir.mkdir(parents=True, exist_ok=True)
        self.keep_project_upload_dir.mkdir(parents=True, exist_ok=True)

        self.project_items: list[Item] = []
        self.project_sample_names: list[str] = []
        for index in range(6):
            file_name = f"sample_{index + 1}.jpg"
            file_bytes = f"sample-{index + 1}".encode("utf-8")
            file_path = self.project_upload_dir / file_name
            file_path.write_bytes(file_bytes)
            self.project_sample_names.append(file_name)

            item = Item(
                project_id=self.project_id,
                kind=ItemKind.image,
                path=str(file_path.relative_to(self.static_dir)).replace("\\", "/"),
                sha256=hashlib.sha256(file_bytes).hexdigest(),
                w=640,
                h=480,
                status=ItemStatus.unlabeled,
            )
            self.db.add(item)
            self.db.flush()
            self.project_items.append(item)

        keep_bytes = b"keep-project"
        self.keep_item_file = self.keep_project_upload_dir / "keep.jpg"
        self.keep_item_file.write_bytes(keep_bytes)
        self.keep_item = Item(
            project_id=self.keep_project_id,
            kind=ItemKind.image,
            path=str(self.keep_item_file.relative_to(self.static_dir)).replace("\\", "/"),
            sha256=hashlib.sha256(keep_bytes).hexdigest(),
            w=640,
            h=480,
            status=ItemStatus.unlabeled,
        )
        self.db.add(self.keep_item)
        self.db.flush()

        annotation_item_indexes = [0, 1, 2, 3, 4, 5, 5]
        for index in annotation_item_indexes:
            item = self.project_items[index]
            self.db.add(
                Annotation(
                    item_id=item.id,
                    label_class_id=self.label_class.id,
                    x1=10,
                    y1=10,
                    x2=100,
                    y2=100,
                )
            )

        self.db.add_all(
            [
                Notification(
                    user_id=self.owner.id,
                    project_id=self.project_id,
                    item_id=self.project_items[0].id,
                    event_type="item.updated",
                    title="Delete target",
                    body="Target project notification",
                ),
                Notification(
                    user_id=self.owner.id,
                    project_id=self.keep_project_id,
                    item_id=self.keep_item.id,
                    event_type="item.updated",
                    title="Keep target",
                    body="Keep project notification",
                ),
            ]
        )
        self.db.commit()

        self.app = FastAPI()
        self.app.mount("/static", StaticFiles(directory=self.static_dir), name="static")

        @self.app.middleware("http")
        async def inject_request_state(request: Request, call_next):
            request.state.csrf_token = "test-csrf-token"
            request.state.user = self.db.get(User, self.owner.id)
            return await call_next(request)

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

        self.app.include_router(web_projects.router)
        self.app.dependency_overrides[get_db] = self._override_get_db
        self.app.dependency_overrides[get_current_user] = self._override_get_current_user
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.project_upload_dir, ignore_errors=True)
        shutil.rmtree(self.keep_project_upload_dir, ignore_errors=True)

    def _override_get_db(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _override_get_current_user(self):
        return self.db.get(User, self.owner.id)

    def _count_project_annotations(self, project_id: int) -> int:
        return int(
            self.db.execute(
                select(func.count(Annotation.id))
                .join(Item, Item.id == Annotation.item_id)
                .where(Item.project_id == project_id)
            ).scalar_one()
            or 0
        )

    def test_project_settings_delete_modal_shows_annotation_total_and_limited_samples(self) -> None:
        response = self.client.get(f"/projects/{self.project_id}/settings")

        self.assertEqual(200, response.status_code)
        self.assertIn("Delete project", response.text)
        self.assertRegex(
            response.text,
            r'id="project-delete-total-items">\s*6\s*<',
        )
        self.assertRegex(
            response.text,
            r'id="project-delete-total-annotations">\s*7\s*<',
        )
        self.assertRegex(
            response.text,
            r'id="project-delete-total-label-classes">\s*1\s*<',
        )

        for sample_name in self.project_sample_names[:5]:
            self.assertIn(sample_name, response.text)
        self.assertNotIn(self.project_sample_names[5], response.text)
        self.assertIn("1 more file is not shown here.", response.text)

    def test_delete_project_requires_name_match(self) -> None:
        response = self.client.post(
            f"/projects/{self.project_id}/delete",
            data={"confirmation_name": "wrong name"},
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn(
            "delete_error=name_mismatch",
            response.headers.get("location", ""),
        )

        self.db.expire_all()
        self.assertIsNotNone(self.db.get(Project, self.project_id))
        self.assertEqual(7, self._count_project_annotations(self.project_id))
        self.assertTrue(self.project_upload_dir.exists())

    def test_delete_project_removes_project_records_and_upload_directory(self) -> None:
        response = self.client.post(
            f"/projects/{self.project_id}/delete",
            data={"confirmation_name": self.project.name},
            follow_redirects=False,
        )

        self.assertEqual(303, response.status_code)
        self.assertIn(
            "notice=project_deleted",
            response.headers.get("location", ""),
        )

        self.db.expire_all()
        self.assertIsNone(self.db.get(Project, self.project_id))
        self.assertEqual(
            0,
            int(
                self.db.execute(
                    select(func.count(Item.id)).where(Item.project_id == self.project_id)
                ).scalar_one()
                or 0
            ),
        )
        self.assertEqual(0, self._count_project_annotations(self.project_id))
        self.assertEqual(
            0,
            int(
                self.db.execute(
                    select(func.count(Notification.id)).where(
                        Notification.project_id == self.project_id
                    )
                ).scalar_one()
                or 0
            ),
        )
        self.assertFalse(self.project_upload_dir.exists())

        self.assertIsNotNone(self.db.get(Project, self.keep_project_id))
        self.assertTrue(self.keep_project_upload_dir.exists())
        self.assertTrue(self.keep_item_file.exists())
        self.assertEqual(
            1,
            int(
                self.db.execute(
                    select(func.count(Notification.id)).where(
                        Notification.project_id == self.keep_project_id
                    )
                ).scalar_one()
                or 0
            ),
        )

        page = self.client.get(response.headers["location"])
        self.assertEqual(200, page.status_code)
        self.assertIn("Project was deleted.", page.text)


if __name__ == "__main__":
    unittest.main()
