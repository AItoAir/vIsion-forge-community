from __future__ import annotations

from math import isfinite
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import (
    Annotation,
    AnnotationStatus,
    Item,
    ItemKind,
    ItemStatus,
    LabelClass,
    LabelGeometryKind,
    RegionComment,
    UserRole,
)
from ..schemas import (
    AnnotationCreate,
    AnnotationRead,
    AnnotationSaveResponse,
    AnnotationsPatchRequest,
    RegionCommentCreate,
    RegionCommentRead,
    RegionCommentSaveResponse,
    RegionCommentsPatchRequest,
)
from ..security import ensure_project_team_access, require_roles
from ..services.audit import log_audit
from ..services.comment_mentions import (
    build_project_mention_candidates,
    mentioned_user_ids,
    normalize_comment_and_mentions,
)
from ..services.collaboration import collaboration_hub
from ..services.notifications import create_comment_mention_notifications

router = APIRouter(tags=["annotations"])


def _annotation_select_stmt(item_id: int):
    return (
        select(Annotation)
        .options(
            selectinload(Annotation.created_by_user),
            selectinload(Annotation.updated_by_user),
        )
        .where(Annotation.item_id == item_id)
        .order_by(Annotation.frame_index, Annotation.track_id, Annotation.id)
    )


def _region_comment_select_stmt(item_id: int):
    return (
        select(RegionComment)
        .options(
            selectinload(RegionComment.created_by_user),
            selectinload(RegionComment.updated_by_user),
        )
        .where(RegionComment.item_id == item_id)
        .order_by(RegionComment.frame_index, RegionComment.created_at, RegionComment.id)
    )


def _normalize_polygon_points(
    points: list[list[float]] | None,
) -> list[list[float]] | None:
    if points is None:
        return None

    normalized_points: list[list[float]] = []
    for point in points:
        if len(point) != 2:
            raise HTTPException(
                status_code=400,
                detail="Each polygon point must contain exactly 2 coordinates",
            )

        x = float(point[0])
        y = float(point[1])
        if not isfinite(x) or not isfinite(y):
            raise HTTPException(
                status_code=400,
                detail="Polygon coordinates must be finite numbers",
            )
        normalized_points.append([x, y])

    if len(normalized_points) < 3:
        raise HTTPException(
            status_code=400,
            detail="Polygon annotations require at least 3 points",
        )

    return normalized_points


def _compute_polygon_bounds(
    points: list[list[float]],
) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _normalize_payload_geometry(
    payload: list[AnnotationCreate],
    label_classes_by_id: dict[int, LabelClass],
) -> list[AnnotationCreate]:
    normalized_payload: list[AnnotationCreate] = []

    for annotation in payload:
        label_class = label_classes_by_id[annotation.label_class_id]
        polygon_points = _normalize_polygon_points(annotation.polygon_points)

        if label_class.geometry_kind == LabelGeometryKind.polygon:
            if not polygon_points:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Polygon label_class_id {annotation.label_class_id} "
                        "requires polygon_points"
                    ),
                )

            x1, y1, x2, y2 = _compute_polygon_bounds(polygon_points)
            normalized_payload.append(
                annotation.model_copy(
                    update={
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "polygon_points": polygon_points,
                    },
                    deep=True,
                )
            )
            continue

        normalized_payload.append(
            annotation.model_copy(update={"polygon_points": None}, deep=True)
        )

    return normalized_payload


def _normalize_sparse_payload(
    payload: list[AnnotationCreate],
    label_classes_by_id: dict[int, LabelClass],
) -> list[AnnotationCreate]:
    normalized_payload: list[AnnotationCreate] = []
    seen_client_uids: set[str] = set()

    for annotation in _normalize_payload_geometry(payload, label_classes_by_id):
        client_uid = (annotation.client_uid or "").strip() or uuid4().hex
        if client_uid in seen_client_uids:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate client_uid detected in request payload: {client_uid}",
            )
        seen_client_uids.add(client_uid)
        normalized_payload.append(
            annotation.model_copy(update={"client_uid": client_uid}, deep=True)
        )

    return normalized_payload


def _validate_payload_for_item_kind(item: Item, payload: list[AnnotationCreate]) -> None:
    if item.kind == ItemKind.image:
        invalid_frames = [
            annotation.frame_index for annotation in payload if annotation.frame_index is not None
        ]
        if invalid_frames:
            raise HTTPException(
                status_code=400,
                detail="Image items must not include frame_index values",
            )
        return

    missing_frames = [
        index for index, annotation in enumerate(payload) if annotation.frame_index is None
    ]
    if missing_frames:
        raise HTTPException(
            status_code=400,
            detail="Video items require frame_index on every annotation",
        )


def _count_item_annotations(db: Session, item_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Annotation)
            .where(Annotation.item_id == item_id)
        ).scalar_one()
    )


def _serialize_item_annotations(db: Session, item_id: int) -> list[dict]:
    annotations = db.execute(_annotation_select_stmt(item_id)).scalars().all()
    return [
        AnnotationRead.model_validate(annotation).model_dump(mode="json")
        for annotation in annotations
    ]


def _serialize_item_region_comments(db: Session, item_id: int) -> list[dict]:
    comments = db.execute(_region_comment_select_stmt(item_id)).scalars().all()
    return [
        RegionCommentRead.model_validate(comment).model_dump(mode="json")
        for comment in comments
    ]


def _annotation_payload_values(item: Item, annotation_in: AnnotationCreate) -> dict:
    return {
        "label_class_id": annotation_in.label_class_id,
        "frame_index": None if item.kind == ItemKind.image else annotation_in.frame_index,
        "track_id": annotation_in.track_id,
        "propagation_frames": (
            0
            if item.kind == ItemKind.image
            else max(0, annotation_in.propagation_frames or 0)
        ),
        "is_occluded": bool(annotation_in.is_occluded),
        "is_truncated": bool(annotation_in.is_truncated),
        "is_outside": bool(annotation_in.is_outside),
        "is_lost": bool(annotation_in.is_lost),
        "x1": annotation_in.x1,
        "y1": annotation_in.y1,
        "x2": annotation_in.x2,
        "y2": annotation_in.y2,
        "polygon_points": annotation_in.polygon_points,
        "status": annotation_in.status or AnnotationStatus.pending,
    }


def _validate_region_comment_payload_for_item_kind(
    item: Item,
    payload: list[RegionCommentCreate],
) -> None:
    if item.kind == ItemKind.image:
        invalid_frames = [
            region_comment.frame_index
            for region_comment in payload
            if region_comment.frame_index is not None
        ]
        if invalid_frames:
            raise HTTPException(
                status_code=400,
                detail="Image items must not include frame_index values on comments",
            )
        return

    missing_frames = [
        index
        for index, region_comment in enumerate(payload)
        if region_comment.frame_index is None
    ]
    if missing_frames:
        raise HTTPException(
            status_code=400,
            detail="Video items require frame_index on every region comment",
        )


def _region_comment_payload_values(
    item: Item,
    region_comment_in: RegionCommentCreate,
    *,
    mention_candidates: list[dict] | None = None,
) -> dict:
    normalized_comment, mentions = normalize_comment_and_mentions(
        region_comment_in.comment,
        mention_candidates or [],
    )
    return {
        "frame_index": None
        if item.kind == ItemKind.image
        else region_comment_in.frame_index,
        "x1": region_comment_in.x1,
        "y1": region_comment_in.y1,
        "x2": region_comment_in.x2,
        "y2": region_comment_in.y2,
        "comment": normalized_comment,
        "mentions": mentions,
    }


def _annotation_matches_payload(annotation: Annotation, payload_values: dict) -> bool:
    return (
        annotation.label_class_id == payload_values["label_class_id"]
        and annotation.frame_index == payload_values["frame_index"]
        and annotation.track_id == payload_values["track_id"]
        and (annotation.propagation_frames or 0) == payload_values["propagation_frames"]
        and bool(annotation.is_occluded) == payload_values["is_occluded"]
        and bool(annotation.is_truncated) == payload_values["is_truncated"]
        and bool(annotation.is_outside) == payload_values["is_outside"]
        and bool(annotation.is_lost) == payload_values["is_lost"]
        and annotation.x1 == payload_values["x1"]
        and annotation.y1 == payload_values["y1"]
        and annotation.x2 == payload_values["x2"]
        and annotation.y2 == payload_values["y2"]
        and annotation.polygon_points == payload_values["polygon_points"]
        and annotation.status == payload_values["status"]
    )


def _apply_annotation_payload(annotation: Annotation, payload_values: dict) -> None:
    annotation.label_class_id = payload_values["label_class_id"]
    annotation.frame_index = payload_values["frame_index"]
    annotation.track_id = payload_values["track_id"]
    annotation.propagation_frames = payload_values["propagation_frames"]
    annotation.is_occluded = payload_values["is_occluded"]
    annotation.is_truncated = payload_values["is_truncated"]
    annotation.is_outside = payload_values["is_outside"]
    annotation.is_lost = payload_values["is_lost"]
    annotation.x1 = payload_values["x1"]
    annotation.y1 = payload_values["y1"]
    annotation.x2 = payload_values["x2"]
    annotation.y2 = payload_values["y2"]
    annotation.polygon_points = payload_values["polygon_points"]
    annotation.status = payload_values["status"]


def _region_comment_matches_payload(
    region_comment: RegionComment,
    payload_values: dict,
) -> bool:
    return (
        region_comment.frame_index == payload_values["frame_index"]
        and region_comment.x1 == payload_values["x1"]
        and region_comment.y1 == payload_values["y1"]
        and region_comment.x2 == payload_values["x2"]
        and region_comment.y2 == payload_values["y2"]
        and region_comment.comment == payload_values["comment"]
        and region_comment.mentions == payload_values["mentions"]
    )


def _apply_region_comment_payload(
    region_comment: RegionComment,
    payload_values: dict,
) -> None:
    region_comment.frame_index = payload_values["frame_index"]
    region_comment.x1 = payload_values["x1"]
    region_comment.y1 = payload_values["y1"]
    region_comment.x2 = payload_values["x2"]
    region_comment.y2 = payload_values["y2"]
    region_comment.comment = payload_values["comment"]
    region_comment.mentions = payload_values["mentions"]


def _apply_sparse_upserts(
    *,
    db: Session,
    item: Item,
    upserts: list[AnnotationCreate],
    existing_annotations_by_uid: dict[str, Annotation],
    current_user_id: int,
) -> tuple[int, int]:
    if not upserts:
        return 0, 0

    created_count = 0
    updated_count = 0

    for annotation_in in upserts:
        annotation = existing_annotations_by_uid.get(annotation_in.client_uid)
        payload_values = _annotation_payload_values(item, annotation_in)
        if annotation is None:
            annotation = Annotation(
                item_id=item.id,
                client_uid=annotation_in.client_uid or uuid4().hex,
                created_by=current_user_id,
                updated_by=current_user_id,
            )
            _apply_annotation_payload(annotation, payload_values)
            existing_annotations_by_uid[annotation.client_uid] = annotation
            created_count += 1
        elif _annotation_matches_payload(annotation, payload_values):
            continue
        else:
            _apply_annotation_payload(annotation, payload_values)
            annotation.updated_by = current_user_id
            updated_count += 1
        db.add(annotation)
    return created_count, updated_count


def _apply_region_comment_upserts(
    *,
    db: Session,
    item: Item,
    upserts: list[RegionCommentCreate],
    existing_comments_by_uid: dict[str, RegionComment],
    current_user_id: int,
    mention_candidates: list[dict] | None = None,
) -> tuple[int, int, list[tuple[RegionComment, set[int]]]]:
    if not upserts:
        return 0, 0, []

    created_count = 0
    updated_count = 0
    changed_comments: list[tuple[RegionComment, set[int]]] = []

    for region_comment_in in upserts:
        client_uid = (region_comment_in.client_uid or "").strip() or uuid4().hex
        region_comment = existing_comments_by_uid.get(client_uid)
        payload_values = _region_comment_payload_values(
            item,
            region_comment_in,
            mention_candidates=mention_candidates,
        )
        previous_mentioned_user_ids = (
            mentioned_user_ids(region_comment.mentions)
            if region_comment is not None
            else set()
        )
        if region_comment is None:
            region_comment = RegionComment(
                item_id=item.id,
                client_uid=client_uid,
                created_by=current_user_id,
                updated_by=current_user_id,
            )
            _apply_region_comment_payload(region_comment, payload_values)
            existing_comments_by_uid[client_uid] = region_comment
            created_count += 1
        elif _region_comment_matches_payload(region_comment, payload_values):
            continue
        else:
            _apply_region_comment_payload(region_comment, payload_values)
            region_comment.updated_by = current_user_id
            updated_count += 1
        db.add(region_comment)
        changed_comments.append((region_comment, previous_mentioned_user_ids))

    return created_count, updated_count, changed_comments


def _revision_conflict_response(db: Session, item: Item) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "Annotation revision conflict. Reload the latest annotations and retry.",
            "revision": item.annotation_revision,
            "annotations": _serialize_item_annotations(db, item.id),
        },
    )


@router.get(
    "/items/{item_id}/annotations",
    response_model=list[AnnotationRead],
)
def list_annotations(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    annotations = db.execute(
        _annotation_select_stmt(item_id)
    ).scalars().all()
    return annotations


@router.put(
    "/items/{item_id}/annotations",
    response_model=AnnotationSaveResponse,
)
def replace_annotations(
    item_id: int,
    payload: list[AnnotationCreate],
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    valid_label_classes = {
        label_class.id: label_class
        for label_class in db.execute(
            select(LabelClass).where(LabelClass.project_id == item.project_id)
        ).scalars().all()
    }
    valid_label_ids = set(valid_label_classes)

    invalid_label_ids = sorted(
        {annotation.label_class_id for annotation in payload if annotation.label_class_id not in valid_label_ids}
    )
    if invalid_label_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid label_class_id values for this project: {invalid_label_ids}",
        )

    normalized_payload = _normalize_sparse_payload(payload, valid_label_classes)
    _validate_payload_for_item_kind(item, normalized_payload)

    existing_annotations = db.execute(
        select(Annotation).where(Annotation.item_id == item.id)
    ).scalars().all()
    existing_annotations_by_uid = {
        annotation.client_uid: annotation
        for annotation in existing_annotations
        if annotation.client_uid
    }
    payload_client_uids = {
        annotation.client_uid for annotation in normalized_payload if annotation.client_uid
    }
    delete_client_uids = sorted(
        client_uid
        for client_uid in existing_annotations_by_uid
        if client_uid not in payload_client_uids
    )
    if delete_client_uids:
        db.execute(
            delete(Annotation).where(
                Annotation.item_id == item.id,
                Annotation.client_uid.in_(delete_client_uids),
            )
        )
        for client_uid in delete_client_uids:
            existing_annotations_by_uid.pop(client_uid, None)

    created_count, updated_count = _apply_sparse_upserts(
        db=db,
        item=item,
        upserts=normalized_payload,
        existing_annotations_by_uid=existing_annotations_by_uid,
        current_user_id=current_user.id,
    )

    db.flush()
    annotation_count = _count_item_annotations(db, item.id)
    item.status = ItemStatus.in_progress if annotation_count else ItemStatus.unlabeled
    changed = bool(delete_client_uids or created_count or updated_count)
    if changed:
        item.annotation_revision += 1
    db.add(item)
    db.flush()

    if changed:
        log_audit(
            db,
            actor_id=current_user.id,
            object_type="item",
            object_id=item.id,
            action="annotations_replaced",
            payload={
                "sparse_annotation_count": len(normalized_payload),
                "expanded_annotation_count": annotation_count,
                "created_count": created_count,
                "updated_count": updated_count,
                "delete_count": len(delete_client_uids),
                "item_kind": item.kind.value,
                "item_status": item.status.value,
                "revision": item.annotation_revision,
            },
        )

    final_revision = item.annotation_revision
    final_item_status = item.status
    db.commit()
    annotations = _serialize_item_annotations(db, item.id)
    if changed:
        collaboration_hub.publish_annotation_commit(
            item_id=item.id,
            revision=final_revision,
            annotations=annotations,
            item_status=final_item_status.value,
            actor_user_id=current_user.id,
        )
    return AnnotationSaveResponse(
        annotation_count=annotation_count,
        item_status=final_item_status,
        revision=final_revision,
        annotations=annotations,
    )


@router.patch(
    "/items/{item_id}/annotations",
    response_model=AnnotationSaveResponse,
)
def patch_annotations(
    item_id: int,
    payload: AnnotationsPatchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    if payload.base_revision != item.annotation_revision:
        return _revision_conflict_response(db, item)

    valid_label_classes = {
        label_class.id: label_class
        for label_class in db.execute(
            select(LabelClass).where(LabelClass.project_id == item.project_id)
        ).scalars().all()
    }
    valid_label_ids = set(valid_label_classes)

    invalid_label_ids = sorted(
        {
            annotation.label_class_id
            for annotation in payload.upserts
            if annotation.label_class_id not in valid_label_ids
        }
    )
    if invalid_label_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid label_class_id values for this project: {invalid_label_ids}",
        )

    normalized_upserts = _normalize_sparse_payload(payload.upserts, valid_label_classes)
    _validate_payload_for_item_kind(item, normalized_upserts)

    delete_client_uids = set(payload.deletes)
    upsert_client_uids = {
        annotation.client_uid for annotation in normalized_upserts if annotation.client_uid
    }
    overlap = sorted(delete_client_uids & upsert_client_uids)
    if overlap:
        raise HTTPException(
            status_code=400,
            detail=f"The same client_uid cannot be deleted and upserted in one request: {overlap}",
        )

    existing_annotations = db.execute(
        select(Annotation).where(Annotation.item_id == item.id)
    ).scalars().all()
    existing_annotations_by_uid = {
        annotation.client_uid: annotation
        for annotation in existing_annotations
        if annotation.client_uid
    }

    delete_existing_client_uids = sorted(
        client_uid for client_uid in delete_client_uids if client_uid in existing_annotations_by_uid
    )
    if delete_existing_client_uids:
        db.execute(
            delete(Annotation).where(
                Annotation.item_id == item.id,
                Annotation.client_uid.in_(delete_existing_client_uids),
            )
        )
        for client_uid in delete_existing_client_uids:
            existing_annotations_by_uid.pop(client_uid, None)

    created_count, updated_count = _apply_sparse_upserts(
        db=db,
        item=item,
        upserts=normalized_upserts,
        existing_annotations_by_uid=existing_annotations_by_uid,
        current_user_id=current_user.id,
    )

    db.flush()
    annotation_count = _count_item_annotations(db, item.id)
    item.status = ItemStatus.in_progress if annotation_count else ItemStatus.unlabeled

    changed = bool(delete_existing_client_uids or created_count or updated_count)
    if changed:
        item.annotation_revision += 1

    db.add(item)
    db.flush()

    if changed:
        log_audit(
            db,
            actor_id=current_user.id,
            object_type="item",
            object_id=item.id,
            action="annotations_patched",
            payload={
                "base_revision": payload.base_revision,
                "new_revision": item.annotation_revision,
                "upsert_count": len(normalized_upserts),
                "created_count": created_count,
                "updated_count": updated_count,
                "delete_count": len(delete_existing_client_uids),
                "item_kind": item.kind.value,
                "item_status": item.status.value,
            },
        )

    final_revision = item.annotation_revision
    final_item_status = item.status
    db.commit()
    annotations = _serialize_item_annotations(db, item.id)
    if changed:
        collaboration_hub.publish_annotation_commit(
            item_id=item.id,
            revision=final_revision,
            annotations=annotations,
            item_status=final_item_status.value,
            actor_user_id=current_user.id,
        )
    return AnnotationSaveResponse(
        annotation_count=annotation_count,
        item_status=final_item_status,
        revision=final_revision,
        annotations=annotations,
    )


@router.get(
    "/items/{item_id}/region-comments",
    response_model=list[RegionCommentRead],
)
def list_region_comments(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    return db.execute(_region_comment_select_stmt(item_id)).scalars().all()


@router.patch(
    "/items/{item_id}/region-comments",
    response_model=RegionCommentSaveResponse,
)
def patch_region_comments(
    item_id: int,
    payload: RegionCommentsPatchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    _validate_region_comment_payload_for_item_kind(item, payload.upserts)

    delete_client_uids = set(payload.deletes)
    upsert_client_uids = {
        (region_comment.client_uid or "").strip()
        for region_comment in payload.upserts
        if (region_comment.client_uid or "").strip()
    }
    overlap = sorted(delete_client_uids & upsert_client_uids)
    if overlap:
        raise HTTPException(
            status_code=400,
            detail=f"The same client_uid cannot be deleted and upserted in one request: {overlap}",
        )

    existing_comments = db.execute(
        select(RegionComment).where(RegionComment.item_id == item.id)
    ).scalars().all()
    existing_comments_by_uid = {
        region_comment.client_uid: region_comment
        for region_comment in existing_comments
        if region_comment.client_uid
    }
    mention_candidates = build_project_mention_candidates(db, item.project)

    delete_existing_client_uids = sorted(
        client_uid
        for client_uid in delete_client_uids
        if client_uid in existing_comments_by_uid
    )
    if delete_existing_client_uids:
        db.execute(
            delete(RegionComment).where(
                RegionComment.item_id == item.id,
                RegionComment.client_uid.in_(delete_existing_client_uids),
            )
        )
        for client_uid in delete_existing_client_uids:
            existing_comments_by_uid.pop(client_uid, None)

    created_count, updated_count, changed_comments = _apply_region_comment_upserts(
        db=db,
        item=item,
        upserts=payload.upserts,
        existing_comments_by_uid=existing_comments_by_uid,
        current_user_id=current_user.id,
        mention_candidates=mention_candidates,
    )

    for region_comment, previous_mentioned_user_ids in changed_comments:
        new_mentions = [
            mention
            for mention in region_comment.mentions
            if int(mention.get("user_id") or 0) not in previous_mentioned_user_ids
        ]
        if not new_mentions:
            continue
        create_comment_mention_notifications(
            db=db,
            project=item.project,
            item_id=item.id,
            item_name=item.display_name,
            actor=current_user,
            comment_text=region_comment.comment,
            mentions=new_mentions,
            source="region_comment",
            region_comment_client_uid=region_comment.client_uid,
            frame_index=region_comment.frame_index,
        )

    changed = bool(delete_existing_client_uids or created_count or updated_count)
    comment_count = len(existing_comments_by_uid)

    if changed:
        log_audit(
            db,
            actor_id=current_user.id,
            object_type="item",
            object_id=item.id,
            action="region_comments_patched",
            payload={
                "upsert_count": len(payload.upserts),
                "created_count": created_count,
                "updated_count": updated_count,
                "delete_count": len(delete_existing_client_uids),
                "comment_count": comment_count,
                "item_kind": item.kind.value,
            },
        )

    db.commit()
    comments = _serialize_item_region_comments(db, item.id)
    return RegionCommentSaveResponse(
        comment_count=len(comments),
        comments=comments,
    )
