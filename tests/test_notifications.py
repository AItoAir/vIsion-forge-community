from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Item,
    ItemKind,
    Notification,
    Project,
    Sam2JobStatus,
    Sam2TrackJob,
    Team,
    User,
    UserRole,
)
from app.services.notifications import (
    create_comment_mention_notifications,
    create_sam2_job_notifications,
    get_notification_list_response,
    mark_notifications_read,
)


class NotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        primary_team = Team(name="Primary team", is_active=True)
        other_team = Team(name="Other team", is_active=True)
        self.db.add_all([primary_team, other_team])
        self.db.flush()

        self.owner = User(
            email="owner@example.com",
            password_hash="hash",
            role=UserRole.project_admin,
            team_id=primary_team.id,
            is_active=True,
        )
        self.annotator = User(
            email="annotator@example.com",
            password_hash="hash",
            role=UserRole.annotator,
            team_id=primary_team.id,
            is_active=True,
        )
        self.reviewer = User(
            email="reviewer@example.com",
            password_hash="hash",
            role=UserRole.reviewer,
            team_id=primary_team.id,
            is_active=True,
        )
        self.inactive_user = User(
            email="inactive@example.com",
            password_hash="hash",
            role=UserRole.annotator,
            team_id=primary_team.id,
            is_active=False,
        )
        self.outsider = User(
            email="outsider@example.com",
            password_hash="hash",
            role=UserRole.annotator,
            team_id=other_team.id,
            is_active=True,
        )
        self.db.add_all(
            [
                self.owner,
                self.annotator,
                self.reviewer,
                self.inactive_user,
                self.outsider,
            ]
        )
        self.db.flush()

        self.project = Project(
            name="Traffic Signs",
            description="Test project",
            owner_user_id=self.owner.id,
            is_archived=False,
        )
        self.db.add(self.project)
        self.db.flush()

        self.item = Item(
            project_id=self.project.id,
            kind=ItemKind.video,
            path="videos/sample.mp4",
            sha256="abc123",
            w=1920,
            h=1080,
        )
        self.db.add(self.item)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_create_sam2_notifications_for_active_team_members(self) -> None:
        job = Sam2TrackJob(
            item_id=self.item.id,
            requested_by=self.annotator.id,
            status=Sam2JobStatus.completed,
            label_class_id=7,
            track_id=4,
            frame_index=12,
            track_start_frame=12,
            track_end_frame=24,
            payload_json="{}",
            result_annotation_count=18,
        )
        self.db.add(job)
        self.db.flush()

        notifications = create_sam2_job_notifications(
            db=self.db,
            project=self.project,
            job=job,
        )
        self.db.flush()

        self.assertEqual(3, len(notifications))
        recipient_ids = {notification.user_id for notification in notifications}
        self.assertEqual(
            {self.owner.id, self.annotator.id, self.reviewer.id},
            recipient_ids,
        )

        stored_notifications = self.db.query(Notification).all()
        self.assertEqual(3, len(stored_notifications))
        self.assertTrue(
            all(notification.event_type == "sam2_job_completed" for notification in stored_notifications)
        )
        self.assertTrue(
            all("Traffic Signs" in notification.body for notification in stored_notifications)
        )

    def test_mark_notifications_read_reduces_unread_count(self) -> None:
        job = Sam2TrackJob(
            item_id=self.item.id,
            requested_by=self.annotator.id,
            status=Sam2JobStatus.failed,
            label_class_id=7,
            track_id=2,
            frame_index=3,
            payload_json="{}",
            error_message="CUDA timed out",
        )
        self.db.add(job)
        self.db.flush()

        create_sam2_job_notifications(
            db=self.db,
            project=self.project,
            job=job,
        )
        self.db.flush()

        initial = get_notification_list_response(
            db=self.db,
            user_id=self.annotator.id,
            limit=8,
        )
        self.assertEqual(1, initial.unread_count)
        self.assertEqual("sam2_job_failed", initial.notifications[0].event_type)

        marked_count = mark_notifications_read(
            db=self.db,
            user_id=self.annotator.id,
            notification_ids=[initial.notifications[0].id],
        )
        self.db.commit()

        updated = get_notification_list_response(
            db=self.db,
            user_id=self.annotator.id,
            limit=8,
        )
        self.assertEqual(1, marked_count)
        self.assertEqual(0, updated.unread_count)
        self.assertFalse(updated.notifications[0].is_unread)

    def test_create_comment_mention_notifications_excludes_actor_and_links_region_comment(self) -> None:
        notifications = create_comment_mention_notifications(
            db=self.db,
            project=self.project,
            item_id=self.item.id,
            item_name=self.item.display_name,
            actor=self.annotator,
            comment_text="Please check @reviewer@example.com and @owner@example.com",
            mentions=[
                {
                    "user_id": self.reviewer.id,
                    "email": self.reviewer.email,
                    "display_name": self.reviewer.display_name,
                    "mention_text": f"@{self.reviewer.email}",
                    "start": 13,
                    "end": 34,
                },
                {
                    "user_id": self.owner.id,
                    "email": self.owner.email,
                    "display_name": self.owner.display_name,
                    "mention_text": f"@{self.owner.email}",
                    "start": 39,
                    "end": 57,
                },
                {
                    "user_id": self.annotator.id,
                    "email": self.annotator.email,
                    "display_name": self.annotator.display_name,
                    "mention_text": f"@{self.annotator.email}",
                    "start": 62,
                    "end": 82,
                },
            ],
            source="region_comment",
            region_comment_client_uid="comment123",
            frame_index=4,
        )
        self.db.flush()

        self.assertEqual(2, len(notifications))
        self.assertEqual(
            {self.owner.id, self.reviewer.id},
            {notification.user_id for notification in notifications},
        )
        self.assertTrue(
            all(notification.event_type == "comment_mention" for notification in notifications)
        )
        self.assertTrue(
            all("region_comment=comment123" in (notification.link_path or "") for notification in notifications)
        )
        self.assertTrue(
            all("frame=5" in (notification.link_path or "") for notification in notifications)
        )


if __name__ == "__main__":
    unittest.main()
