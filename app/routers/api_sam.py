from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Item, LabelClass, LabelGeometryKind, UserRole
from ..schemas import (
    Sam2AnnotationRead,
    Sam2PromptRequest,
    Sam2PromptResponse,
    Sam2TrackJobEnqueueResponse,
    Sam2TrackJobStatusResponse,
)
from ..security import ensure_project_team_access, require_roles
from ..services.sam2 import (
    Sam2Error,
    Sam2UnavailableError,
    get_current_frame_suggestions,
    make_prompt_payload,
    sam2_feature_configured,
    sam2_feature_enabled,
)
from ..services.sam2_jobs import (
    Sam2QueueFullError,
    build_track_job_status_response,
    enqueue_track_job,
    sam2_track_job_runner,
)

router = APIRouter(tags=["sam2"])


def _get_item_and_label_class(
    *,
    db: Session,
    item_id: int,
    label_class_id: int,
    current_user,
) -> tuple[Item, LabelClass]:
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    label_class = db.execute(
        select(LabelClass).where(
            LabelClass.id == label_class_id,
            LabelClass.project_id == item.project_id,
        )
    ).scalar_one_or_none()
    if label_class is None:
        raise HTTPException(
            status_code=400,
            detail=f"label_class_id {label_class_id} does not belong to this project",
        )
    if label_class.geometry_kind not in {
        LabelGeometryKind.bbox,
        LabelGeometryKind.polygon,
    }:
        raise HTTPException(
            status_code=400,
            detail="SAM2 output requires a bbox or polygon label class",
        )
    return item, label_class


def _validate_frame_index(item: Item, frame_index: int | None) -> int | None:
    if item.kind.value == "image":
        return None

    if frame_index is None:
        raise HTTPException(
            status_code=400,
            detail="frame_index is required for video SAM2 prompts",
        )
    if frame_index < 0:
        raise HTTPException(status_code=400, detail="frame_index must be >= 0")

    if item.duration_sec is not None and item.fps is not None:
        total_frames = max(1, round(item.duration_sec * item.fps))
        if frame_index >= total_frames:
            raise HTTPException(
                status_code=400,
                detail=f"frame_index must be < {total_frames} for this video",
            )
    return frame_index


def _to_response(suggestions, *, item_id: int, mode: str, frame_index: int | None):
    return Sam2PromptResponse(
        item_id=item_id,
        mode=mode,
        frame_index=frame_index,
        annotation_count=len(suggestions),
        annotations=[
            Sam2AnnotationRead(
                label_class_id=suggestion.label_class_id,
                frame_index=suggestion.frame_index,
                track_id=suggestion.track_id,
                x1=suggestion.x1,
                y1=suggestion.y1,
                x2=suggestion.x2,
                y2=suggestion.y2,
                polygon_points=suggestion.polygon_points,
            )
            for suggestion in suggestions
        ],
    )


def _ensure_sam2_track_available() -> None:
    if not sam2_feature_enabled():
        raise HTTPException(
            status_code=503,
            detail="SAM2 is disabled in the current server configuration.",
        )
    if not sam2_feature_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "SAM2 is not fully configured. Set SAM2_CHECKPOINT and "
                "SAM2_MODEL_CFG before using this endpoint."
            ),
        )


@router.post(
    "/items/{item_id}/sam2/current-frame",
    response_model=Sam2PromptResponse,
)
def sam2_current_frame(
    item_id: int,
    payload: Sam2PromptRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
):
    item, _label_class = _get_item_and_label_class(
        db=db,
        item_id=item_id,
        label_class_id=payload.label_class_id,
        current_user=current_user,
    )
    frame_index = _validate_frame_index(item, payload.frame_index)

    prompt = make_prompt_payload(
        label_class_id=payload.label_class_id,
        frame_index=frame_index,
        box_xyxy=payload.box_xyxy,
        prompt_points=[point.model_dump() for point in payload.prompt_points],
        track_id=payload.track_id,
        track_start_frame=payload.track_start_frame,
        track_end_frame=payload.track_end_frame,
        include_reverse=payload.include_reverse,
        simplify_tolerance=payload.simplify_tolerance,
    )

    try:
        suggestions = get_current_frame_suggestions(item, prompt)
    except Sam2UnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Sam2Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _to_response(
        suggestions,
        item_id=item.id,
        mode="current_frame",
        frame_index=frame_index,
    )


@router.post(
    "/items/{item_id}/sam2/track",
    response_model=Sam2TrackJobEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def sam2_track_video(
    item_id: int,
    payload: Sam2PromptRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
):
    item, _label_class = _get_item_and_label_class(
        db=db,
        item_id=item_id,
        label_class_id=payload.label_class_id,
        current_user=current_user,
    )
    if item.kind.value != "video":
        raise HTTPException(
            status_code=400,
            detail="SAM2 video tracking is only available for video items",
        )

    _ensure_sam2_track_available()
    frame_index = _validate_frame_index(item, payload.frame_index)
    prompt = make_prompt_payload(
        label_class_id=payload.label_class_id,
        frame_index=frame_index,
        box_xyxy=payload.box_xyxy,
        prompt_points=[point.model_dump() for point in payload.prompt_points],
        track_id=payload.track_id,
        track_start_frame=payload.track_start_frame,
        track_end_frame=payload.track_end_frame,
        include_reverse=payload.include_reverse,
        simplify_tolerance=payload.simplify_tolerance,
    )

    try:
        job, running_count, queued_count = enqueue_track_job(
            db=db,
            item=item,
            label_class=_label_class,
            prompt=prompt,
            requested_by_user_id=current_user.id,
        )
        db.commit()
    except Sam2QueueFullError as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Sam2Error as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sam2_track_job_runner.wake()
    return Sam2TrackJobEnqueueResponse(
        item_id=item.id,
        job=job,
        running_count=running_count,
        queued_count=queued_count,
        max_concurrent_jobs=max(1, int(settings.sam2_max_concurrent_jobs or 1)),
        max_queue_size=max(1, int(settings.sam2_max_queue_size or 8)),
    )


@router.get(
    "/items/{item_id}/sam2/track-status",
    response_model=Sam2TrackJobStatusResponse,
)
def sam2_track_status(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)
    return build_track_job_status_response(db=db, item=item)
