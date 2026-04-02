from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models import Item, ItemKind, ItemStatus, LabelClass, LabelGeometryKind, Project, Team, User, UserRole
from app.routers import api_v1
from app.security import create_api_key


class PublicApiV1Tests(unittest.TestCase):
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

        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_export_dir = settings.public_api_export_dir
        settings.public_api_export_dir = str(Path(self.temp_dir.name) / "exports")

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
        self.reviewer = User(
            email="reviewer@example.com",
            password_hash="hash",
            role=UserRole.reviewer,
            team_id=self.team.id,
            is_active=True,
        )
        self.db.add_all([self.owner, self.reviewer])
        self.db.flush()

        self.project = Project(
            name="Warehouse",
            description="Test project",
            owner_user_id=self.owner.id,
            is_archived=False,
        )
        self.db.add(self.project)
        self.db.flush()

        self.label_class = LabelClass(
            project_id=self.project.id,
            name="box",
            color_hex="#00ff00",
            geometry_kind=LabelGeometryKind.bbox,
            is_active=True,
            default_use_fixed_box=False,
            default_propagation_frames=0,
        )
        self.db.add(self.label_class)
        self.db.flush()

        self.static_dir = Path(__file__).resolve().parent.parent / "static"
        self.seed_upload_dir = self.static_dir / "uploads" / f"project_{self.project.id}"
        self.seed_upload_dir.mkdir(parents=True, exist_ok=True)
        self.seed_media_path = self.seed_upload_dir / "seed.jpg"
        self.seed_bytes = b"seed-image-bytes"
        self.seed_media_path.write_bytes(self.seed_bytes)

        self.item = Item(
            project_id=self.project.id,
            kind=ItemKind.image,
            path=str(self.seed_media_path.relative_to(self.static_dir)).replace("\\", "/"),
            sha256=hashlib.sha256(self.seed_bytes).hexdigest(),
            w=640,
            h=480,
            status=ItemStatus.unlabeled,
        )
        self.db.add(self.item)
        self.db.commit()
        self.db.refresh(self.item)

        _api_key, self.raw_api_key = create_api_key(
            self.db,
            user=self.owner,
            name="Initial key",
        )
        self.db.commit()

        self.app = FastAPI()
        self.app.include_router(api_v1.router)
        self.app.dependency_overrides[get_db] = self._override_get_db
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.db.close()
        self.engine.dispose()
        settings.public_api_export_dir = self.original_export_dir
        self.temp_dir.cleanup()
        shutil.rmtree(self.seed_upload_dir, ignore_errors=True)

    def _override_get_db(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _auth_headers(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.raw_api_key}"}

    def test_api_key_project_and_label_class_endpoints(self) -> None:
        response = self.client.get("/api/v1/api-keys", headers=self._auth_headers())
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.json()))

        created = self.client.post(
            "/api/v1/api-keys",
            headers=self._auth_headers(),
            json={"name": "CI key"},
        )
        self.assertEqual(201, created.status_code)
        created_payload = created.json()
        self.assertIn("token", created_payload)

        projects = self.client.get("/api/v1/projects", headers=self._auth_headers())
        self.assertEqual(200, projects.status_code)
        self.assertEqual(1, len(projects.json()))

        created_project = self.client.post(
            "/api/v1/projects",
            headers=self._auth_headers(),
            json={"name": "Robots", "description": "Dev API"},
        )
        self.assertEqual(201, created_project.status_code)
        created_project_id = created_project.json()["id"]

        updated_project = self.client.patch(
            f"/api/v1/projects/{created_project_id}",
            headers=self._auth_headers(),
            json={"name": "Robots v2", "description": "Updated"},
        )
        self.assertEqual(200, updated_project.status_code)
        self.assertEqual("Robots v2", updated_project.json()["name"])

        created_label = self.client.post(
            f"/api/v1/projects/{created_project_id}/label-classes",
            headers=self._auth_headers(),
            json={
                "name": "vehicle",
                "geometry_kind": "bbox",
                "color_hex": "#ff0000",
                "default_propagation_frames": 2,
            },
        )
        self.assertEqual(201, created_label.status_code)
        created_label_id = created_label.json()["id"]

        listed_labels = self.client.get(
            f"/api/v1/projects/{created_project_id}/label-classes",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, listed_labels.status_code)
        self.assertEqual(1, len(listed_labels.json()))

        updated_label = self.client.patch(
            f"/api/v1/projects/{created_project_id}/label-classes/{created_label_id}",
            headers=self._auth_headers(),
            json={
                "name": "vehicle-updated",
                "geometry_kind": "bbox",
                "color_hex": "#00ff00",
                "default_propagation_frames": 4,
            },
        )
        self.assertEqual(200, updated_label.status_code)
        self.assertEqual("vehicle-updated", updated_label.json()["name"])

        revoke = self.client.delete(
            f"/api/v1/api-keys/{created_payload['api_key']['id']}",
            headers=self._auth_headers(),
        )
        self.assertEqual(204, revoke.status_code)

    def test_non_framepin_api_key_prefix_is_rejected(self) -> None:
        invalid_token = self.raw_api_key.replace("fpk_", "bad_", 1)

        response = self.client.get("/api/v1/projects", headers=self._auth_headers(invalid_token))

        self.assertEqual(401, response.status_code)

    def test_item_create_get_media_and_delete(self) -> None:
        with patch(
            "app.routers.api_v1.probe_media_metadata",
            return_value=SimpleNamespace(
                width=320,
                height=240,
                duration_sec=None,
                fps=None,
                frame_rate_mode=None,
            ),
        ):
            created = self.client.post(
                f"/api/v1/projects/{self.project.id}/items",
                headers=self._auth_headers(),
                files={"file": ("upload.jpg", b"uploaded-image", "image/jpeg")},
            )
        self.assertEqual(201, created.status_code)
        item_id = created.json()["item"]["id"]

        listed = self.client.get(
            f"/api/v1/projects/{self.project.id}/items",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, listed.status_code)
        self.assertGreaterEqual(len(listed.json()), 2)

        fetched = self.client.get(f"/api/v1/items/{item_id}", headers=self._auth_headers())
        self.assertEqual(200, fetched.status_code)

        media = self.client.get(f"/api/v1/items/{item_id}/media", headers=self._auth_headers())
        self.assertEqual(200, media.status_code)
        self.assertEqual(b"uploaded-image", media.content)

        deleted = self.client.delete(f"/api/v1/items/{item_id}", headers=self._auth_headers())
        self.assertEqual(204, deleted.status_code)

    def test_annotations_review_and_export_flow(self) -> None:
        annotations = self.client.put(
            f"/api/v1/items/{self.item.id}/annotations",
            headers=self._auth_headers(),
            json=[
                {
                    "label_class_id": self.label_class.id,
                    "x1": 10,
                    "y1": 20,
                    "x2": 110,
                    "y2": 120,
                }
            ],
        )
        self.assertEqual(200, annotations.status_code)
        self.assertEqual("in_progress", annotations.json()["item_status"])

        listed_annotations = self.client.get(
            f"/api/v1/items/{self.item.id}/annotations",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, listed_annotations.status_code)
        self.assertEqual(1, len(listed_annotations.json()))

        patched = self.client.patch(
            f"/api/v1/items/{self.item.id}/annotations",
            headers=self._auth_headers(),
            json={
                "base_revision": annotations.json()["revision"],
                "upserts": [
                    {
                        "client_uid": annotations.json()["annotations"][0]["client_uid"],
                        "label_class_id": self.label_class.id,
                        "x1": 12,
                        "y1": 22,
                        "x2": 112,
                        "y2": 122,
                    }
                ],
                "deletes": [],
            },
        )
        self.assertEqual(200, patched.status_code)

        submitted = self.client.post(
            f"/api/v1/items/{self.item.id}/submit-review",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, submitted.status_code)
        self.assertEqual("needs_review", submitted.json()["item_status"])

        rejected = self.client.post(
            f"/api/v1/items/{self.item.id}/review/reject",
            headers=self._auth_headers(),
            json={"comment": "Needs adjustment"},
        )
        self.assertEqual(200, rejected.status_code)
        self.assertEqual("in_progress", rejected.json()["item_status"])

        review_comments = self.client.get(
            f"/api/v1/items/{self.item.id}/review-comments",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, review_comments.status_code)
        self.assertEqual(1, len(review_comments.json()))

        reset = self.client.post(
            f"/api/v1/items/{self.item.id}/review/reset",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, reset.status_code)
        self.assertEqual("needs_review", reset.json()["item_status"])

        approved = self.client.post(
            f"/api/v1/items/{self.item.id}/review/approve",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, approved.status_code)
        self.assertEqual("done", approved.json()["item_status"])

        export_job = self.client.post(
            f"/api/v1/projects/{self.project.id}/exports",
            headers=self._auth_headers(),
            json={"format": "json"},
        )
        self.assertEqual(202, export_job.status_code)
        job_id = export_job.json()["id"]
        self.assertEqual("completed", export_job.json()["status"])

        fetched_job = self.client.get(f"/api/v1/jobs/{job_id}", headers=self._auth_headers())
        self.assertEqual(200, fetched_job.status_code)

        artifact = self.client.get(f"/api/v1/jobs/{job_id}/artifact", headers=self._auth_headers())
        self.assertEqual(200, artifact.status_code)
        self.assertIn('"item_id"', artifact.text)

    def test_webhooks_and_prediction_import(self) -> None:
        delivered_events: list[str] = []

        def _fake_deliver(*, target_url: str, body: bytes, signature: str | None) -> int:
            delivered_events.append(json.loads(body.decode("utf-8"))["type"])
            self.assertEqual("https://example.com/webhook", target_url)
            self.assertTrue(signature and signature.startswith("sha256="))
            return 204

        created_webhook = self.client.post(
            "/api/v1/webhooks",
            headers=self._auth_headers(),
            json={
                "name": "Primary webhook",
                "target_url": "https://example.com/webhook",
                "events": ["annotations.updated", "export.completed"],
                "project_id": self.project.id,
            },
        )
        self.assertEqual(201, created_webhook.status_code)
        webhook_id = created_webhook.json()["webhook"]["id"]

        listed_webhooks = self.client.get("/api/v1/webhooks", headers=self._auth_headers())
        self.assertEqual(200, listed_webhooks.status_code)
        self.assertEqual(1, len(listed_webhooks.json()))

        updated_webhook = self.client.patch(
            f"/api/v1/webhooks/{webhook_id}",
            headers=self._auth_headers(),
            json={"events": ["annotations.updated", "export.completed", "item.created"]},
        )
        self.assertEqual(200, updated_webhook.status_code)

        with patch("app.services.webhooks._deliver_webhook_request", side_effect=_fake_deliver):
            put_annotations = self.client.put(
                f"/api/v1/items/{self.item.id}/annotations",
                headers=self._auth_headers(),
                json=[
                    {
                        "label_class_id": self.label_class.id,
                        "x1": 1,
                        "y1": 2,
                        "x2": 11,
                        "y2": 12,
                    }
                ],
            )
            self.assertEqual(200, put_annotations.status_code)

            export_job = self.client.post(
                f"/api/v1/projects/{self.project.id}/exports",
                headers=self._auth_headers(),
                json={"format": "json"},
            )
            self.assertEqual(202, export_job.status_code)

        self.assertIn("annotations.updated", delivered_events)
        self.assertIn("export.completed", delivered_events)

        prediction_import = self.client.post(
            f"/api/v1/projects/{self.project.id}/prediction-runs/import",
            headers=self._auth_headers(),
            json={
                "name": "Run 1",
                "model_name": "demo-detector",
                "model_version": "v1",
                "predictions": [
                    {
                        "item": {"item_path": self.item.path},
                        "label_name": self.label_class.name,
                        "x1": 5,
                        "y1": 6,
                        "x2": 25,
                        "y2": 26,
                        "confidence": 0.91,
                    }
                ],
            },
        )
        self.assertEqual(201, prediction_import.status_code)
        run_id = prediction_import.json()["run"]["id"]
        self.assertEqual(1, len(prediction_import.json()["predictions"]))

        fetched_run = self.client.get(
            f"/api/v1/prediction-runs/{run_id}",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, fetched_run.status_code)

        listed_predictions = self.client.get(
            f"/api/v1/prediction-runs/{run_id}/predictions",
            headers=self._auth_headers(),
        )
        self.assertEqual(200, listed_predictions.status_code)
        self.assertEqual(1, len(listed_predictions.json()))


if __name__ == "__main__":
    unittest.main()
