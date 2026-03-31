from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import (
    Annotation,
    AnnotationStatus,
    Item,
    ItemStatus,
    LabelClass,
    RegionComment,
    ReviewComment,
    UserRole,
)
from ..security import ensure_project_team_access, require_roles
from ..services.audit import log_audit
from ..services.comment_mentions import (
    build_project_mention_candidates,
    normalize_comment_and_mentions,
    render_comment_html,
)
from ..services.notifications import create_comment_mention_notifications
from .web_items import _get_prev_next_item_ids, _item_media_conversion_payload

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _annotation_flags_snapshot(annotation: Annotation) -> dict[str, bool]:
    return {
        "occluded": bool(annotation.is_occluded),
        "truncated": bool(annotation.is_truncated),
        "outside": bool(annotation.is_outside),
        "lost": bool(annotation.is_lost),
    }


def _annotation_snapshot_entry(annotation: Annotation) -> dict:
    return {
        "label_class_id": annotation.label_class_id,
        "frame_index": annotation.frame_index,
        "track_id": annotation.track_id,
        "propagation_frames": int(max(0, annotation.propagation_frames or 0)),
        "bbox": [annotation.x1, annotation.y1, annotation.x2, annotation.y2],
        "polygon_points": annotation.polygon_points,
        "status": annotation.status.value,
        "flags": _annotation_flags_snapshot(annotation),
    }


def _annotation_snapshot_signature(entry: dict) -> str:
    return json.dumps(
        entry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_reject_snapshot(item: Item, annotations: list[Annotation]) -> str:
    payload = {
        "item_id": item.id,
        "annotation_revision": item.annotation_revision,
        "annotations": {
            annotation.client_uid: _annotation_snapshot_entry(annotation)
            for annotation in annotations
            if annotation.client_uid
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_review_change_awareness(
    *,
    item: Item,
    annotations: list[Annotation],
    review_comments: list[ReviewComment],
) -> dict | None:
    baseline_comment = next(
        (
            review_comment
            for review_comment in review_comments
            if review_comment.snapshot_payload
        ),
        None,
    )
    if baseline_comment is None:
        return None

    baseline_payload = baseline_comment.snapshot_payload or {}
    baseline_annotations_raw = baseline_payload.get("annotations")
    if not isinstance(baseline_annotations_raw, dict):
        return None

    baseline_annotations = {
        str(client_uid): entry
        for client_uid, entry in baseline_annotations_raw.items()
        if isinstance(entry, dict)
    }
    current_annotations = {
        annotation.client_uid: _annotation_snapshot_entry(annotation)
        for annotation in annotations
        if annotation.client_uid
    }

    states_by_client_uid: dict[str, str] = {}
    counts = {
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "deleted": 0,
    }

    for client_uid, current_entry in current_annotations.items():
        baseline_entry = baseline_annotations.get(client_uid)
        if baseline_entry is None:
            states_by_client_uid[client_uid] = "new"
            counts["new"] += 1
            continue

        if _annotation_snapshot_signature(current_entry) == _annotation_snapshot_signature(
            baseline_entry
        ):
            states_by_client_uid[client_uid] = "unchanged"
            counts["unchanged"] += 1
            continue

        states_by_client_uid[client_uid] = "changed"
        counts["changed"] += 1

    deleted_annotations: list[dict] = []
    for client_uid, baseline_entry in baseline_annotations.items():
        if client_uid in current_annotations:
            continue

        deleted_annotations.append(
            {
                "client_uid": client_uid,
                "label_class_id": baseline_entry.get("label_class_id"),
                "frame_index": baseline_entry.get("frame_index"),
                "track_id": baseline_entry.get("track_id"),
                "status": baseline_entry.get("status"),
            }
        )
        counts["deleted"] += 1

    deleted_annotations.sort(
        key=lambda entry: (
            entry.get("track_id") if entry.get("track_id") is not None else 10**12,
            entry.get("frame_index") if entry.get("frame_index") is not None else -1,
            entry.get("label_class_id") if entry.get("label_class_id") is not None else 10**12,
        )
    )

    return {
        "base_revision": baseline_payload.get("annotation_revision"),
        "current_revision": item.annotation_revision,
        "baseline_comment": baseline_comment.comment,
        "baseline_comment_mentions": baseline_comment.mentions,
        "baseline_created_at": baseline_comment.created_at,
        "states_by_client_uid": states_by_client_uid,
        "counts": counts,
        "deleted_annotations": deleted_annotations,
    }


def _build_changed_item_navigation(
    *,
    project,
    current_item_id: int,
    db: Session,
) -> tuple[int | None, int | None]:
    items = db.execute(
        select(Item)
        .where(Item.project_id == project.id)
        .order_by(Item.id.asc())
    ).scalars().all()
    if not items:
        return None, None

    item_ids = [item.id for item in items]
    snapshot_comments = db.execute(
        select(ReviewComment)
        .where(
            ReviewComment.item_id.in_(item_ids),
            ReviewComment.snapshot_json.is_not(None),
        )
        .order_by(ReviewComment.created_at.desc(), ReviewComment.id.desc())
    ).scalars().all()

    latest_snapshot_comment_by_item_id: dict[int, ReviewComment] = {}
    for review_comment in snapshot_comments:
        latest_snapshot_comment_by_item_id.setdefault(review_comment.item_id, review_comment)

    changed_item_ids: set[int] = set()
    for item in items:
        latest_snapshot_comment = latest_snapshot_comment_by_item_id.get(item.id)
        if latest_snapshot_comment is None:
            continue
        if latest_snapshot_comment.annotation_revision is None:
            continue
        if latest_snapshot_comment.annotation_revision != item.annotation_revision:
            changed_item_ids.add(item.id)

    ordered_ids = [item.id for item in items]
    try:
        current_index = ordered_ids.index(current_item_id)
    except ValueError:
        return None, None

    prev_changed_item_id = next(
        (item_id for item_id in reversed(ordered_ids[:current_index]) if item_id in changed_item_ids),
        None,
    )
    next_changed_item_id = next(
        (item_id for item_id in ordered_ids[current_index + 1 :] if item_id in changed_item_ids),
        None,
    )
    return prev_changed_item_id, next_changed_item_id


def _safe_internal_redirect_target(redirect_to: str | None, fallback_url: str) -> str:
    target = (redirect_to or "").strip()
    if not target or not target.startswith("/") or target.startswith("//"):
        return fallback_url
    return target


@router.get("/items/{item_id}/review", response_class=HTMLResponse, name="review_item")
def review_item(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        return HTMLResponse(status_code=404, content="Item not found")

    project = item.project
    ensure_project_team_access(project, current_user)

    label_classes = (
        db.execute(
            select(LabelClass)
            .where(LabelClass.project_id == project.id)
            .order_by(LabelClass.id.asc())
        )
        .scalars()
        .all()
    )
    annotations = (
        db.execute(
            select(Annotation)
            .options(
                selectinload(Annotation.created_by_user),
                selectinload(Annotation.updated_by_user),
            )
            .where(Annotation.item_id == item.id)
            .order_by(Annotation.id.asc())
        )
        .scalars()
        .all()
    )
    region_comments = (
        db.execute(
            select(RegionComment)
            .options(
                selectinload(RegionComment.created_by_user),
                selectinload(RegionComment.updated_by_user),
            )
            .where(RegionComment.item_id == item.id)
            .order_by(RegionComment.frame_index, RegionComment.created_at, RegionComment.id)
        )
        .scalars()
        .all()
    )
    review_comments = (
        db.execute(
            select(ReviewComment)
            .options(selectinload(ReviewComment.reviewer))
            .where(ReviewComment.item_id == item.id)
            .order_by(ReviewComment.created_at.desc(), ReviewComment.id.desc())
        )
        .scalars()
        .all()
    )
    prev_item_id, next_item_id = _get_prev_next_item_ids(db, project.id, item)
    review_change_awareness = _build_review_change_awareness(
        item=item,
        annotations=annotations,
        review_comments=review_comments,
    )
    prev_changed_item_id, next_changed_item_id = _build_changed_item_navigation(
        project=project,
        current_item_id=item.id,
        db=db,
    )
    label_names_by_id = {label_class.id: label_class.name for label_class in label_classes}
    media_conversion = _item_media_conversion_payload(
        db,
        item,
        auto_enqueue=item.kind.value == "video",
        record_access=item.kind.value == "video",
    )
    display_media_variant = (
        "display"
        if item.kind.value == "video" and media_conversion["ready"]
        else "original"
    )
    display_media_url = str(
        request.url_for("item_media", item_id=item.id, variant=display_media_variant)
    )
    mention_candidates = build_project_mention_candidates(db, project)

    return templates.TemplateResponse(
        request=request,
        name="item_review.html",
        context={
            "request": request,
            "item": item,
            "project": project,
            "annotation_revision": item.annotation_revision,
            "label_classes": label_classes,
            "label_names_by_id": label_names_by_id,
            "annotations": annotations,
            "region_comments": region_comments,
            "review_comments": review_comments,
            "review_change_awareness": review_change_awareness,
            "current_user": current_user,
            "mention_candidates": mention_candidates,
            "prev_item_id": prev_item_id,
            "next_item_id": next_item_id,
            "prev_changed_item_id": prev_changed_item_id,
            "next_changed_item_id": next_changed_item_id,
            "display_media_url": display_media_url,
            "media_conversion": media_conversion,
            "can_review": current_user.role in {UserRole.reviewer, UserRole.project_admin, UserRole.system_admin},
            "render_comment_html": render_comment_html,
        },
    )


@router.post(
    "/items/{item_id}/review/approve",
    response_class=HTMLResponse,
    name="approve_item_review",
)
def approve_item_review(
    request: Request,
    item_id: int,
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        return HTMLResponse(status_code=404, content="Item not found")

    ensure_project_team_access(item.project, current_user)

    annotations = (
        db.execute(select(Annotation).where(Annotation.item_id == item.id))
        .scalars()
        .all()
    )
    if not annotations:
        return PlainTextResponse("Cannot approve an item with no annotations", status_code=400)

    for ann in annotations:
        ann.status = AnnotationStatus.approved
        ann.updated_by = current_user.id
        db.add(ann)

    item.status = ItemStatus.done
    db.add(item)

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_review_approved",
        payload={
            "annotation_count": len(annotations),
            "item_status": item.status.value,
        },
    )

    db.commit()
    fallback_url = str(request.url_for("review_item", item_id=item.id))
    return RedirectResponse(
        url=_safe_internal_redirect_target(redirect_to, fallback_url),
        status_code=303,
    )


@router.post(
    "/items/{item_id}/review/reject",
    response_class=HTMLResponse,
    name="reject_item_review",
)
def reject_item_review(
    request: Request,
    item_id: int,
    comment: str = Form(...),
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        return HTMLResponse(status_code=404, content="Item not found")

    ensure_project_team_access(item.project, current_user)

    mention_candidates = build_project_mention_candidates(db, item.project)
    clean_comment, mentions = normalize_comment_and_mentions(comment, mention_candidates)
    if not clean_comment:
        return PlainTextResponse("Reject comment is required", status_code=400)

    annotations = (
        db.execute(select(Annotation).where(Annotation.item_id == item.id))
        .scalars()
        .all()
    )
    if not annotations:
        return PlainTextResponse("Cannot reject an item with no annotations", status_code=400)

    for ann in annotations:
        ann.status = AnnotationStatus.rejected
        ann.updated_by = current_user.id
        db.add(ann)

    review_comment = ReviewComment(
        item_id=item.id,
        reviewer_id=current_user.id,
        comment=clean_comment,
        annotation_revision=item.annotation_revision,
        snapshot_json=_build_reject_snapshot(item, annotations),
    )
    review_comment.mentions = mentions
    db.add(review_comment)

    create_comment_mention_notifications(
        db=db,
        project=item.project,
        item_id=item.id,
        item_name=item.display_name,
        actor=current_user,
        comment_text=clean_comment,
        mentions=mentions,
        source="review_comment",
    )

    item.status = ItemStatus.in_progress
    db.add(item)

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_review_rejected",
        payload={
            "annotation_count": len(annotations),
            "item_status": item.status.value,
            "comment": clean_comment,
        },
    )

    db.commit()
    fallback_url = str(request.url_for("review_item", item_id=item.id))
    return RedirectResponse(
        url=_safe_internal_redirect_target(redirect_to, fallback_url),
        status_code=303,
    )


@router.post(
    "/items/{item_id}/review/reset",
    response_class=HTMLResponse,
    name="reset_item_review",
)
def reset_item_review(
    request: Request,
    item_id: int,
    redirect_to: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        return HTMLResponse(status_code=404, content="Item not found")

    ensure_project_team_access(item.project, current_user)

    annotations = (
        db.execute(select(Annotation).where(Annotation.item_id == item.id))
        .scalars()
        .all()
    )
    if not annotations:
        return PlainTextResponse("Cannot reset review for an item with no annotations", status_code=400)

    for ann in annotations:
        ann.status = AnnotationStatus.pending
        ann.updated_by = current_user.id
        db.add(ann)

    item.status = ItemStatus.needs_review
    db.add(item)

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_review_reset",
        payload={
            "annotation_count": len(annotations),
            "item_status": item.status.value,
        },
    )

    db.commit()
    fallback_url = str(request.url_for("review_item", item_id=item.id))
    return RedirectResponse(
        url=_safe_internal_redirect_target(redirect_to, fallback_url),
        status_code=303,
    )
