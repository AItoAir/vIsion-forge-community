from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import Notification, Project, Sam2JobStatus, Sam2TrackJob, User
from ..schemas import NotificationListResponse, NotificationRead
from .comment_mentions import comment_preview, normalize_mentions_metadata


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _notification_link_path(job: Sam2TrackJob) -> str | None:
    if job.item_id is None:
        return None
    return f"/items/{job.item_id}/label"


def _sam2_scope_label(job: Sam2TrackJob) -> str:
    frame_parts: list[str] = []
    if job.frame_index is not None:
        frame_parts.append(f"seed frame {job.frame_index + 1}")
    if job.track_start_frame is not None and job.track_end_frame is not None:
        frame_parts.append(
            f"frames {job.track_start_frame + 1}-{job.track_end_frame + 1}"
        )
    elif job.track_start_frame is not None:
        frame_parts.append(f"from frame {job.track_start_frame + 1}")
    elif job.track_end_frame is not None:
        frame_parts.append(f"through frame {job.track_end_frame + 1}")

    if job.track_id is not None:
        frame_parts.append(f"track #{job.track_id}")

    return ", ".join(frame_parts)


def _build_sam2_notification_content(
    *,
    project: Project,
    job: Sam2TrackJob,
) -> tuple[str, str, str, dict[str, object]]:
    scope_label = _sam2_scope_label(job)
    project_name = (project.name or "").strip() or f"Project {project.id}"
    item_name = job.item.display_name if job.item is not None else f"Item {job.item_id}"

    payload: dict[str, object] = {
        "job_id": job.id,
        "project_id": project.id,
        "project_name": project_name,
        "item_id": job.item_id,
        "item_name": item_name,
        "track_id": job.track_id,
        "frame_index": job.frame_index,
        "track_start_frame": job.track_start_frame,
        "track_end_frame": job.track_end_frame,
        "result_annotation_count": job.result_annotation_count,
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at is not None else None
        ),
    }

    if job.status == Sam2JobStatus.completed:
        title = "SAM2 batch completed"
        body = (
            f"{item_name} in {project_name} finished successfully"
            + (f" for {scope_label}" if scope_label else "")
            + (
                f" and applied {int(job.result_annotation_count or 0)} tracked masks."
            )
        )
        payload["status"] = Sam2JobStatus.completed.value
        return "sam2_job_completed", title, body, payload

    title = "SAM2 batch failed"
    error_message = (job.error_message or "The SAM2 batch ended with an unknown error.").strip()
    body = (
        f"{item_name} in {project_name} failed"
        + (f" for {scope_label}" if scope_label else "")
        + f": {error_message}"
    )
    payload["status"] = Sam2JobStatus.failed.value
    payload["error_message"] = error_message
    return "sam2_job_failed", title, body, payload


def _notification_recipient_ids_for_project(db: Session, project: Project) -> list[int]:
    owner = project.owner
    team_id = owner.team_id if owner is not None else None
    if team_id is None:
        return []

    return list(
        db.execute(
            select(User.id)
            .where(
                User.team_id == team_id,
                User.is_active.is_(True),
            )
            .order_by(User.id.asc())
        ).scalars()
    )


def create_sam2_job_notifications(
    *,
    db: Session,
    project: Project,
    job: Sam2TrackJob,
) -> list[Notification]:
    if job.status not in {Sam2JobStatus.completed, Sam2JobStatus.failed}:
        return []

    recipient_ids = _notification_recipient_ids_for_project(db, project)
    if not recipient_ids:
        return []

    existing_recipient_ids = set(
        db.execute(
            select(Notification.user_id).where(
                Notification.sam2_track_job_id == job.id,
            )
        ).scalars()
    )

    event_type, title, body, payload = _build_sam2_notification_content(
        project=project,
        job=job,
    )
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    notifications: list[Notification] = []
    for user_id in recipient_ids:
        if user_id in existing_recipient_ids:
            continue
        notification = Notification(
            user_id=user_id,
            project_id=project.id,
            item_id=job.item_id,
            sam2_track_job_id=job.id,
            event_type=event_type,
            title=title,
            body=body,
            link_path=_notification_link_path(job),
            payload_json=payload_json,
            read_at=None,
        )
        db.add(notification)
        notifications.append(notification)

    return notifications


def _comment_mention_link_path(
    *,
    source: str,
    item_id: int,
    region_comment_client_uid: str | None = None,
    frame_index: int | None = None,
) -> str:
    if source == "region_comment":
        query_parts = []
        if region_comment_client_uid:
            query_parts.append(
                f"region_comment={quote(region_comment_client_uid, safe='')}"
            )
        if frame_index is not None:
            query_parts.append(f"frame={int(frame_index) + 1}")
        suffix = f"?{'&'.join(query_parts)}" if query_parts else ""
        return f"/items/{item_id}/label{suffix}"

    return f"/items/{item_id}/review"


def create_comment_mention_notifications(
    *,
    db: Session,
    project: Project,
    item_id: int,
    item_name: str,
    actor: User,
    comment_text: str,
    mentions: list[dict] | None,
    source: str,
    region_comment_client_uid: str | None = None,
    frame_index: int | None = None,
) -> list[Notification]:
    normalized_mentions = normalize_mentions_metadata(mentions or [])
    recipient_ids = sorted(
        {
            int(mention["user_id"])
            for mention in normalized_mentions
            if int(mention["user_id"]) != actor.id
        }
    )
    if not recipient_ids:
        return []

    actor_name = actor.display_name
    project_name = (project.name or "").strip() or f"Project {project.id}"
    preview = comment_preview(comment_text)
    source_label = "region comment" if source == "region_comment" else "review comment"
    title = f"You were mentioned in a {source_label}"
    body = f"{actor_name} mentioned you on {item_name} in {project_name}: {preview}"
    link_path = _comment_mention_link_path(
        source=source,
        item_id=item_id,
        region_comment_client_uid=region_comment_client_uid,
        frame_index=frame_index,
    )
    payload = {
        "source": source,
        "item_id": item_id,
        "item_name": item_name,
        "project_id": project.id,
        "project_name": project_name,
        "actor_user_id": actor.id,
        "actor_display_name": actor_name,
        "comment_preview": preview,
        "region_comment_client_uid": region_comment_client_uid,
        "frame_index": frame_index,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    notifications: list[Notification] = []
    for recipient_id in recipient_ids:
        notification = Notification(
            user_id=recipient_id,
            project_id=project.id,
            item_id=item_id,
            event_type="comment_mention",
            title=title,
            body=body,
            link_path=link_path,
            payload_json=payload_json,
            read_at=None,
        )
        db.add(notification)
        notifications.append(notification)

    return notifications


def _serialize_notification(notification: Notification) -> NotificationRead:
    return NotificationRead(
        id=notification.id,
        event_type=notification.event_type,
        title=notification.title,
        body=notification.body,
        link_path=notification.link_path,
        project_id=notification.project_id,
        item_id=notification.item_id,
        sam2_track_job_id=notification.sam2_track_job_id,
        created_at=notification.created_at,
        read_at=notification.read_at,
        is_unread=notification.read_at is None,
    )


def get_notification_list_response(
    *,
    db: Session,
    user_id: int,
    limit: int = 8,
) -> NotificationListResponse:
    normalized_limit = max(1, min(int(limit or 8), 20))
    notifications = (
        db.execute(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(normalized_limit)
        )
        .scalars()
        .all()
    )
    unread_count = int(
        db.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        ).scalar_one()
    )
    return NotificationListResponse(
        unread_count=unread_count,
        notifications=[_serialize_notification(notification) for notification in notifications],
    )


def mark_notifications_read(
    *,
    db: Session,
    user_id: int,
    notification_ids: list[int],
) -> int:
    normalized_ids = sorted({int(notification_id) for notification_id in notification_ids if int(notification_id) > 0})
    if not normalized_ids:
        return 0

    result = db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.id.in_(normalized_ids),
            Notification.read_at.is_(None),
        )
        .values(read_at=_utcnow())
    )
    return int(result.rowcount or 0)
