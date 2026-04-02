from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import (
    Annotation,
    AnnotationStatus,
    ApiKey,
    ExportJob,
    ExportJobStatus,
    Item,
    ItemKind,
    ItemStatus,
    LabelClass,
    LabelGeometryKind,
    Prediction,
    PredictionRun,
    PredictionRunStatus,
    Project,
    ReviewComment,
    UserRole,
    Webhook,
)
from ..routers import api_annotations as internal_annotations
from ..schemas import AnnotationCreate, AnnotationsPatchRequest
from ..schemas_api_v1 import (
    AnnotationRead,
    AnnotationSaveResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyRead,
    ExportJobCreateRequest,
    ExportJobRead,
    ItemReadV1,
    ItemUploadResponse,
    LabelClassReadV1,
    LabelClassUpsertRequest,
    PredictionReadV1,
    PredictionRunImportRequest,
    PredictionRunImportResponse,
    PredictionRunReadV1,
    ProjectCreateRequest,
    ProjectReadV1,
    ProjectUpdateRequest,
    ReviewCommentReadV1,
    WebhookPatchRequest,
    WebhookRead,
    WebhookUpsertRequest,
)
from ..security import create_api_key, ensure_project_team_access, require_api_roles
from ..services.audit import log_audit
from ..services.comment_mentions import (
    build_project_mention_candidates,
    normalize_comment_and_mentions,
)
from ..services.export_jobs import process_export_job
from ..services.media import (
    MediaProbeError,
    enqueue_media_conversion,
    labeling_proxy_profile_token,
    media_conversion_payload,
    media_storage_path,
    probe_media_metadata,
    remove_labeling_proxy_video,
    resolve_annotation_media_path,
    resolve_media_source_path,
)
from ..services.notifications import create_comment_mention_notifications
from ..services.webhooks import SUPPORTED_WEBHOOK_EVENTS, dispatch_webhook_event


router = APIRouter(prefix="/api/v1", tags=["public-api-v1"])


class ItemStatusResponse(BaseModel):
    item_id: int
    item_status: ItemStatus


class ReviewRejectRequest(BaseModel):
    comment: str

    @model_validator(mode="after")
    def validate_comment(self) -> "ReviewRejectRequest":
        self.comment = (self.comment or "").strip()
        if not self.comment:
            raise ValueError("comment must not be blank")
        return self


class WebhookCreateResponse(BaseModel):
    webhook: WebhookRead
    signing_secret: str | None = None


def _serialize_api_key(api_key: ApiKey) -> ApiKeyRead:
    return ApiKeyRead.model_validate(api_key)


def _serialize_project(project: Project) -> ProjectReadV1:
    return ProjectReadV1.model_validate(project)


def _serialize_label_class(label_class: LabelClass) -> LabelClassReadV1:
    return LabelClassReadV1.model_validate(label_class)


def _serialize_item(item: Item) -> ItemReadV1:
    return ItemReadV1.model_validate(item)


def _serialize_export_job(job: ExportJob) -> ExportJobRead:
    return ExportJobRead.model_validate(job)


def _serialize_webhook(webhook: Webhook) -> WebhookRead:
    payload = {
        "id": webhook.id,
        "owner_user_id": webhook.owner_user_id,
        "project_id": webhook.project_id,
        "name": webhook.name,
        "target_url": webhook.target_url,
        "events": webhook.events,
        "is_active": webhook.is_active,
        "last_delivered_at": webhook.last_delivered_at,
        "last_response_status": webhook.last_response_status,
        "last_error": webhook.last_error,
        "created_at": webhook.created_at,
        "updated_at": webhook.updated_at,
    }
    return WebhookRead.model_validate(payload)


def _serialize_review_comment(review_comment: ReviewComment) -> ReviewCommentReadV1:
    return ReviewCommentReadV1(
        id=review_comment.id,
        item_id=review_comment.item_id,
        annotation_id=review_comment.annotation_id,
        reviewer_id=review_comment.reviewer_id,
        comment=review_comment.comment,
        mentions=review_comment.mentions,
        annotation_revision=review_comment.annotation_revision,
        created_at=review_comment.created_at,
    )


def _serialize_prediction(prediction: Prediction) -> PredictionReadV1:
    return PredictionReadV1(
        id=prediction.id,
        item_id=prediction.item_id,
        label_class_id=prediction.label_class_id,
        frame_index=prediction.frame_index,
        track_id=prediction.track_id,
        propagation_frames=prediction.propagation_frames,
        external_prediction_id=prediction.external_prediction_id,
        confidence=prediction.confidence,
        x1=prediction.x1,
        y1=prediction.y1,
        x2=prediction.x2,
        y2=prediction.y2,
        polygon_points=prediction.polygon_points,
        metadata=prediction.metadata_payload,
        created_at=prediction.created_at,
    )


def _serialize_prediction_run(run: PredictionRun) -> PredictionRunReadV1:
    return PredictionRunReadV1(
        id=run.id,
        project_id=run.project_id,
        created_by=run.created_by,
        name=run.name,
        model_name=run.model_name,
        model_version=run.model_version,
        external_run_id=run.external_run_id,
        status=run.status,
        imported_prediction_count=run.imported_prediction_count,
        error_message=run.error_message,
        metadata=run.metadata_payload,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def _visible_projects(db: Session, current_user) -> list[Project]:
    projects = db.execute(select(Project).order_by(Project.updated_at.desc())).scalars().all()
    if current_user.role == UserRole.system_admin:
        return projects
    return [
        project
        for project in projects
        if project.owner is not None and project.owner.team_id == current_user.team_id
    ]


def _get_project_or_404(db: Session, project_id: int, current_user) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_team_access(project, current_user)
    return project


def _get_item_or_404(db: Session, item_id: int, current_user) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    ensure_project_team_access(item.project, current_user)
    return item


def _resolve_project_label_class(
    db: Session,
    *,
    project_id: int,
    label_class_id: int | None,
    label_name: str | None,
) -> LabelClass:
    if label_class_id is not None:
        label_class = db.execute(
            select(LabelClass).where(
                LabelClass.id == label_class_id,
                LabelClass.project_id == project_id,
            )
        ).scalar_one_or_none()
        if label_class is None:
            raise HTTPException(status_code=400, detail="Invalid label_class_id for project")
        return label_class

    normalized_name = (label_name or "").strip()
    label_class = db.execute(
        select(LabelClass).where(
            LabelClass.project_id == project_id,
            LabelClass.name == normalized_name,
        )
    ).scalar_one_or_none()
    if label_class is None:
        raise HTTPException(status_code=400, detail="Invalid label_name for project")
    return label_class


def _resolve_prediction_item(db: Session, *, project_id: int, reference) -> Item:
    if reference.item_id is not None:
        item = db.get(Item, reference.item_id)
        if item is None or item.project_id != project_id:
            raise HTTPException(status_code=400, detail="Invalid item_id for prediction import")
        return item

    if reference.item_path:
        item = db.execute(
            select(Item).where(
                Item.project_id == project_id,
                Item.path == reference.item_path,
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=400, detail="Invalid item_path for prediction import")
        return item

    item = db.execute(
        select(Item).where(
            Item.project_id == project_id,
            Item.sha256 == reference.item_sha256,
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=400, detail="Invalid item_sha256 for prediction import")
    return item


def _normalize_item_kind(kind: str | None, file: UploadFile) -> ItemKind:
    normalized = (kind or "").strip().lower()
    if normalized in {ItemKind.image.value, ItemKind.video.value}:
        return ItemKind(normalized)
    content_type = file.content_type or ""
    return ItemKind.video if content_type.startswith("video/") else ItemKind.image


def _create_uploaded_item(
    *,
    db: Session,
    project: Project,
    file: UploadFile,
    current_user,
    kind: str | None = None,
) -> Item:
    item_kind = _normalize_item_kind(kind, file)
    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    uploads_root = static_dir / "uploads"
    project_dir = uploads_root / f"project_{project.id}"
    project_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(file.filename or "uploaded").name
    target_path = project_dir / filename
    hasher = hashlib.sha256()
    try:
        with target_path.open("xb") as output:
            while True:
                chunk = file.file.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
                output.write(chunk)
    except FileExistsError as exc:
        raise HTTPException(status_code=400, detail=f"File already exists in this project: {filename}") from exc
    finally:
        file.file.close()

    try:
        metadata = probe_media_metadata(target_path, item_kind)
    except MediaProbeError as exc:
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if item_kind == ItemKind.video and metadata.frame_rate_mode == "vfr":
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(
            status_code=400,
            detail=(
                "Variable frame rate videos are not supported for frame-accurate labeling yet. "
                "Please convert this video to constant frame rate (CFR) before uploading."
            ),
        )

    item = Item(
        project_id=project.id,
        kind=item_kind,
        path=str(target_path.relative_to(static_dir)).replace("\\", "/"),
        sha256=hasher.hexdigest(),
        w=metadata.width,
        h=metadata.height,
        duration_sec=metadata.duration_sec,
        fps=metadata.fps,
        media_conversion_status="pending" if item_kind == ItemKind.video else "not_required",
        media_conversion_error=None,
        media_conversion_profile=labeling_proxy_profile_token() if item_kind == ItemKind.video else None,
        frame_rate_mode=metadata.frame_rate_mode if item_kind == ItemKind.video else None,
        status=ItemStatus.unlabeled,
    )
    db.add(item)
    db.flush()
    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_uploaded_via_public_api",
        payload={
            "path": item.path,
            "kind": item.kind.value,
            "width": item.w,
            "height": item.h,
            "fps": item.fps,
            "duration_sec": item.duration_sec,
            "sha256": item.sha256,
        },
    )
    return item


def _apply_label_class_updates(label_class: LabelClass, payload: LabelClassUpsertRequest) -> None:
    label_class.name = payload.name
    label_class.geometry_kind = payload.geometry_kind
    label_class.color_hex = payload.color_hex
    label_class.shortcut_key = payload.shortcut_key
    label_class.is_active = payload.is_active
    label_class.default_use_fixed_box = payload.default_use_fixed_box
    label_class.default_box_w = payload.default_box_w
    label_class.default_box_h = payload.default_box_h
    label_class.default_propagation_frames = payload.default_propagation_frames


def _compute_polygon_bounds(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _dispatch_item_status_changed(
    db: Session,
    *,
    item: Item,
    previous_status: ItemStatus,
) -> None:
    if previous_status == item.status:
        return
    dispatch_webhook_event(
        db,
        event_type="item.status_changed",
        project_id=item.project_id,
        payload={
            "project_id": item.project_id,
            "item_id": item.id,
            "previous_status": previous_status.value,
            "current_status": item.status.value,
            "annotation_revision": item.annotation_revision,
        },
    )
    db.commit()


@router.get("/api-keys", response_model=list[ApiKeyRead])
def list_api_keys(
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    api_keys = db.execute(
        select(ApiKey).where(ApiKey.user_id == current_user.id).order_by(ApiKey.id.asc())
    ).scalars().all()
    return [_serialize_api_key(api_key) for api_key in api_keys]


@router.post("/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
def create_api_key_endpoint(
    payload: ApiKeyCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    api_key, raw_token = create_api_key(
        db,
        user=current_user,
        name=payload.name,
        expires_at=payload.expires_at,
    )
    db.commit()
    db.refresh(api_key)
    return ApiKeyCreateResponse(api_key=_serialize_api_key(api_key), token=raw_token)


@router.delete("/api-keys/{api_key_id}", status_code=204)
def revoke_api_key_endpoint(
    api_key_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    api_key = db.get(ApiKey, api_key_id)
    if api_key is None or api_key.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.is_active = False
    db.add(api_key)
    db.commit()
    return Response(status_code=204)


@router.get("/projects", response_model=list[ProjectReadV1])
def list_projects(
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    return [_serialize_project(project) for project in _visible_projects(db, current_user)]


@router.post("/projects", response_model=ProjectReadV1, status_code=201)
def create_project(
    payload: ProjectCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    project = Project(
        name=payload.name,
        description=payload.description,
        owner_user_id=current_user.id,
        is_archived=False,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project)


@router.get("/projects/{project_id}", response_model=ProjectReadV1)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    return _serialize_project(_get_project_or_404(db, project_id, current_user))


@router.patch("/projects/{project_id}", response_model=ProjectReadV1)
def update_project(
    project_id: int,
    payload: ProjectUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    project = _get_project_or_404(db, project_id, current_user)
    project.name = payload.name
    project.description = payload.description
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project)


@router.get("/projects/{project_id}/label-classes", response_model=list[LabelClassReadV1])
def list_label_classes(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    _project = _get_project_or_404(db, project_id, current_user)
    label_classes = db.execute(
        select(LabelClass)
        .where(LabelClass.project_id == project_id)
        .order_by(LabelClass.id.asc())
    ).scalars().all()
    return [_serialize_label_class(label_class) for label_class in label_classes]


@router.post("/projects/{project_id}/label-classes", response_model=LabelClassReadV1, status_code=201)
def create_label_class(
    project_id: int,
    payload: LabelClassUpsertRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    _project = _get_project_or_404(db, project_id, current_user)
    label_class = LabelClass(project_id=project_id)
    _apply_label_class_updates(label_class, payload)
    db.add(label_class)
    db.commit()
    db.refresh(label_class)
    return _serialize_label_class(label_class)


@router.patch("/projects/{project_id}/label-classes/{label_class_id}", response_model=LabelClassReadV1)
def update_label_class(
    project_id: int,
    label_class_id: int,
    payload: LabelClassUpsertRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    _project = _get_project_or_404(db, project_id, current_user)
    label_class = db.get(LabelClass, label_class_id)
    if label_class is None or label_class.project_id != project_id:
        raise HTTPException(status_code=404, detail="Label class not found")
    _apply_label_class_updates(label_class, payload)
    db.add(label_class)
    db.commit()
    db.refresh(label_class)
    return _serialize_label_class(label_class)


@router.get("/projects/{project_id}/items", response_model=list[ItemReadV1])
def list_items(
    project_id: int,
    q: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    _project = _get_project_or_404(db, project_id, current_user)
    stmt = select(Item).where(Item.project_id == project_id).order_by(Item.id.asc())
    if (q or "").strip():
        stmt = stmt.where(Item.path.ilike(f"%{q.strip()}%"))
    if (kind or "").strip():
        normalized_kind = (kind or "").strip().lower()
        if normalized_kind in {ItemKind.image.value, ItemKind.video.value}:
            stmt = stmt.where(Item.kind == ItemKind(normalized_kind))
    if (status or "").strip():
        normalized_status = (status or "").strip().lower()
        if normalized_status in {
            ItemStatus.unlabeled.value,
            ItemStatus.in_progress.value,
            ItemStatus.done.value,
            ItemStatus.needs_review.value,
        }:
            stmt = stmt.where(Item.status == ItemStatus(normalized_status))
    items = db.execute(stmt).scalars().all()
    return [_serialize_item(item) for item in items]


@router.post("/projects/{project_id}/items", response_model=ItemUploadResponse, status_code=201)
def create_item(
    project_id: int,
    file: UploadFile = File(...),
    kind: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    project = _get_project_or_404(db, project_id, current_user)
    item = _create_uploaded_item(
        db=db,
        project=project,
        file=file,
        current_user=current_user,
        kind=kind,
    )
    db.commit()
    db.refresh(item)
    if item.kind == ItemKind.video:
        enqueue_media_conversion(item.id)
    dispatch_webhook_event(
        db,
        event_type="item.created",
        project_id=project.id,
        payload={
            "project_id": project.id,
            "item_id": item.id,
            "kind": item.kind.value,
            "path": item.path,
            "sha256": item.sha256,
        },
    )
    db.commit()
    return ItemUploadResponse(
        item=_serialize_item(item),
        media_conversion=media_conversion_payload(item),
    )


@router.get("/items/{item_id}", response_model=ItemReadV1)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    return _serialize_item(_get_item_or_404(db, item_id, current_user))


@router.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    file_path = static_dir / item.path
    other_item_exists = db.execute(
        select(Item.id).where(Item.path == item.path, Item.id != item.id).limit(1)
    ).scalar_one_or_none()
    if other_item_exists is None and file_path.is_file():
        file_path.unlink(missing_ok=True)
    if other_item_exists is None:
        remove_labeling_proxy_video(item)
    project_id = item.project_id
    db.execute(delete(Prediction).where(Prediction.item_id == item.id))
    db.execute(delete(Annotation).where(Annotation.item_id == item.id))
    db.delete(item)
    db.commit()
    dispatch_webhook_event(
        db,
        event_type="item.status_changed",
        project_id=project_id,
        payload={
            "project_id": project_id,
            "item_id": item_id,
            "previous_status": item.status.value,
            "current_status": "deleted",
        },
    )
    db.commit()
    return Response(status_code=204)


@router.get("/items/{item_id}/media")
def get_item_media(
    item_id: int,
    variant: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    item = _get_item_or_404(db, item_id, current_user)
    requested_variant = (variant or "").strip().lower()
    if item.kind == ItemKind.video and requested_variant == "display":
        relative_path = resolve_annotation_media_path(item)
    else:
        relative_path = item.path
    file_path = media_storage_path(relative_path)
    if not file_path.is_file() and relative_path == item.path:
        recovered_source_path = resolve_media_source_path(item)
        if recovered_source_path is not None:
            file_path = recovered_source_path
    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Item media not found on disk: {item.path}",
        )
    return FileResponse(path=file_path)


@router.get("/items/{item_id}/annotations", response_model=list[AnnotationRead])
def list_annotations(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    return internal_annotations.list_annotations(item_id=item_id, db=db, current_user=current_user)


@router.put("/items/{item_id}/annotations", response_model=AnnotationSaveResponse)
def replace_annotations(
    item_id: int,
    payload: list[AnnotationCreate],
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    previous_revision = item.annotation_revision
    response = internal_annotations.replace_annotations(
        item_id=item_id,
        payload=payload,
        db=db,
        current_user=current_user,
    )
    if response.revision != previous_revision:
        dispatch_webhook_event(
            db,
            event_type="annotations.updated",
            project_id=item.project_id,
            payload={
                "project_id": item.project_id,
                "item_id": item.id,
                "revision": response.revision,
                "annotation_count": response.annotation_count,
            },
        )
        db.commit()
    item = _get_item_or_404(db, item_id, current_user)
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return response


@router.patch("/items/{item_id}/annotations", response_model=AnnotationSaveResponse)
def patch_annotations(
    item_id: int,
    payload: AnnotationsPatchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    previous_revision = item.annotation_revision
    response = internal_annotations.patch_annotations(
        item_id=item_id,
        payload=payload,
        db=db,
        current_user=current_user,
    )
    if response.revision != previous_revision:
        dispatch_webhook_event(
            db,
            event_type="annotations.updated",
            project_id=item.project_id,
            payload={
                "project_id": item.project_id,
                "item_id": item.id,
                "revision": response.revision,
                "annotation_count": response.annotation_count,
            },
        )
        db.commit()
    item = _get_item_or_404(db, item_id, current_user)
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return response


@router.post("/items/{item_id}/submit-review", response_model=ItemStatusResponse)
def submit_review(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    annotations = db.execute(select(Annotation).where(Annotation.item_id == item.id)).scalars().all()
    if not annotations:
        raise HTTPException(status_code=400, detail="Cannot submit an item with no annotations")
    for annotation in annotations:
        annotation.status = AnnotationStatus.pending
        db.add(annotation)
    item.status = ItemStatus.needs_review
    db.add(item)
    db.commit()
    db.refresh(item)
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return ItemStatusResponse(item_id=item.id, item_status=item.status)


@router.post("/items/{item_id}/reopen", response_model=ItemStatusResponse)
def reopen_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.annotator, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    annotations = db.execute(select(Annotation).where(Annotation.item_id == item.id)).scalars().all()
    for annotation in annotations:
        annotation.status = AnnotationStatus.pending
        db.add(annotation)
    item.status = ItemStatus.in_progress if annotations else ItemStatus.unlabeled
    db.add(item)
    db.commit()
    db.refresh(item)
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return ItemStatusResponse(item_id=item.id, item_status=item.status)


@router.get("/items/{item_id}/review-comments", response_model=list[ReviewCommentReadV1])
def list_review_comments(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    item = _get_item_or_404(db, item_id, current_user)
    review_comments = db.execute(
        select(ReviewComment)
        .options(selectinload(ReviewComment.reviewer))
        .where(ReviewComment.item_id == item.id)
        .order_by(ReviewComment.created_at.desc(), ReviewComment.id.desc())
    ).scalars().all()
    return [_serialize_review_comment(review_comment) for review_comment in review_comments]


@router.post("/items/{item_id}/review/approve", response_model=ItemStatusResponse)
def approve_review(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.reviewer, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    annotations = db.execute(select(Annotation).where(Annotation.item_id == item.id)).scalars().all()
    if not annotations:
        raise HTTPException(status_code=400, detail="Cannot approve an item with no annotations")
    for annotation in annotations:
        annotation.status = AnnotationStatus.approved
        annotation.updated_by = current_user.id
        db.add(annotation)
    item.status = ItemStatus.done
    db.add(item)
    db.commit()
    db.refresh(item)
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return ItemStatusResponse(item_id=item.id, item_status=item.status)


@router.post("/items/{item_id}/review/reject", response_model=ItemStatusResponse)
def reject_review(
    item_id: int,
    payload: ReviewRejectRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.reviewer, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    mention_candidates = build_project_mention_candidates(db, item.project)
    clean_comment, mentions = normalize_comment_and_mentions(payload.comment, mention_candidates)
    if not clean_comment:
        raise HTTPException(status_code=400, detail="Reject comment is required")

    annotations = db.execute(select(Annotation).where(Annotation.item_id == item.id)).scalars().all()
    if not annotations:
        raise HTTPException(status_code=400, detail="Cannot reject an item with no annotations")

    for annotation in annotations:
        annotation.status = AnnotationStatus.rejected
        annotation.updated_by = current_user.id
        db.add(annotation)

    review_comment = ReviewComment(
        item_id=item.id,
        reviewer_id=current_user.id,
        comment=clean_comment,
        annotation_revision=item.annotation_revision,
        snapshot_json=json.dumps(
            {
                "item_id": item.id,
                "annotation_revision": item.annotation_revision,
                "annotations": {
                    annotation.client_uid: {
                        "label_class_id": annotation.label_class_id,
                        "frame_index": annotation.frame_index,
                        "track_id": annotation.track_id,
                        "propagation_frames": int(max(0, annotation.propagation_frames or 0)),
                        "bbox": [annotation.x1, annotation.y1, annotation.x2, annotation.y2],
                        "polygon_points": annotation.polygon_points,
                        "status": annotation.status.value,
                        "flags": {
                            "occluded": bool(annotation.is_occluded),
                            "truncated": bool(annotation.is_truncated),
                            "outside": bool(annotation.is_outside),
                            "lost": bool(annotation.is_lost),
                        },
                    }
                    for annotation in annotations
                    if annotation.client_uid
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
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
    db.commit()
    db.refresh(item)
    dispatch_webhook_event(
        db,
        event_type="review.rejected",
        project_id=item.project_id,
        payload={
            "project_id": item.project_id,
            "item_id": item.id,
            "comment": clean_comment,
            "annotation_revision": item.annotation_revision,
        },
    )
    db.commit()
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return ItemStatusResponse(item_id=item.id, item_status=item.status)


@router.post("/items/{item_id}/review/reset", response_model=ItemStatusResponse)
def reset_review(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.reviewer, UserRole.project_admin)),
):
    item = _get_item_or_404(db, item_id, current_user)
    previous_status = item.status
    annotations = db.execute(select(Annotation).where(Annotation.item_id == item.id)).scalars().all()
    if not annotations:
        raise HTTPException(status_code=400, detail="Cannot reset review for an item with no annotations")
    for annotation in annotations:
        annotation.status = AnnotationStatus.pending
        annotation.updated_by = current_user.id
        db.add(annotation)
    item.status = ItemStatus.needs_review
    db.add(item)
    db.commit()
    db.refresh(item)
    _dispatch_item_status_changed(db, item=item, previous_status=previous_status)
    return ItemStatusResponse(item_id=item.id, item_status=item.status)


@router.post("/projects/{project_id}/exports", response_model=ExportJobRead, status_code=202)
def create_export_job(
    project_id: int,
    payload: ExportJobCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    project = _get_project_or_404(db, project_id, current_user)
    job = ExportJob(
        project_id=project.id,
        requested_by=current_user.id,
        format=payload.format,
        status=ExportJobStatus.queued,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    job = process_export_job(db, job=job, project=project, current_user=current_user)
    if job.status == ExportJobStatus.completed:
        dispatch_webhook_event(
            db,
            event_type="export.completed",
            project_id=project.id,
            payload={
                "project_id": project.id,
                "job_id": job.id,
                "format": job.format,
                "download_name": job.download_name,
            },
        )
        db.commit()
    return _serialize_export_job(job)


@router.get("/jobs/{job_id}", response_model=ExportJobRead)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    job = db.get(ExportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _get_project_or_404(db, job.project_id, current_user)
    return _serialize_export_job(job)


@router.get("/jobs/{job_id}/artifact")
def get_job_artifact(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_api_roles(
            UserRole.annotator,
            UserRole.reviewer,
            UserRole.project_admin,
        )
    ),
):
    job = db.get(ExportJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _get_project_or_404(db, job.project_id, current_user)
    if job.status != ExportJobStatus.completed or not job.artifact_path:
        raise HTTPException(status_code=409, detail="Job artifact is not ready")
    artifact_path = Path(job.artifact_path)
    if not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(
        path=artifact_path,
        media_type=job.content_type or "application/octet-stream",
        filename=job.download_name or artifact_path.name,
    )


@router.get("/webhooks", response_model=list[WebhookRead])
def list_webhooks(
    project_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    if project_id is not None:
        _get_project_or_404(db, project_id, current_user)
    webhooks = db.execute(
        select(Webhook)
        .where(Webhook.owner_user_id == current_user.id)
        .order_by(Webhook.id.asc())
    ).scalars().all()
    if project_id is not None:
        webhooks = [webhook for webhook in webhooks if webhook.project_id == project_id]
    return [_serialize_webhook(webhook) for webhook in webhooks]


@router.post("/webhooks", response_model=WebhookCreateResponse, status_code=201)
def create_webhook(
    payload: WebhookUpsertRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    if payload.project_id is not None:
        _get_project_or_404(db, payload.project_id, current_user)
    unsupported = sorted(set(payload.events) - SUPPORTED_WEBHOOK_EVENTS)
    if unsupported:
        raise HTTPException(status_code=400, detail=f"Unsupported webhook events: {unsupported}")
    generated_secret = payload.signing_secret or f"whsec_{secrets.token_urlsafe(24)}"
    webhook = Webhook(
        owner_user_id=current_user.id,
        project_id=payload.project_id,
        name=payload.name,
        target_url=payload.target_url,
        signing_secret=generated_secret,
        is_active=payload.is_active,
    )
    webhook.events = payload.events
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return WebhookCreateResponse(
        webhook=_serialize_webhook(webhook),
        signing_secret=generated_secret,
    )


@router.patch("/webhooks/{webhook_id}", response_model=WebhookRead)
def update_webhook(
    webhook_id: int,
    payload: WebhookPatchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    webhook = db.get(Webhook, webhook_id)
    if webhook is None or webhook.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if payload.project_id is not None:
        _get_project_or_404(db, payload.project_id, current_user)
        webhook.project_id = payload.project_id
    if payload.name is not None:
        webhook.name = payload.name
    if payload.target_url is not None:
        webhook.target_url = payload.target_url
    if payload.events is not None:
        unsupported = sorted(set(payload.events) - SUPPORTED_WEBHOOK_EVENTS)
        if unsupported:
            raise HTTPException(status_code=400, detail=f"Unsupported webhook events: {unsupported}")
        webhook.events = payload.events
    if payload.is_active is not None:
        webhook.is_active = payload.is_active
    if payload.signing_secret is not None:
        webhook.signing_secret = payload.signing_secret
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return _serialize_webhook(webhook)


@router.post("/projects/{project_id}/prediction-runs/import", response_model=PredictionRunImportResponse, status_code=201)
def import_prediction_run(
    project_id: int,
    payload: PredictionRunImportRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    project = _get_project_or_404(db, project_id, current_user)
    run = PredictionRun(
        project_id=project.id,
        created_by=current_user.id,
        name=payload.name,
        model_name=payload.model_name,
        model_version=payload.model_version,
        external_run_id=payload.external_run_id,
        status=PredictionRunStatus.pending,
    )
    run.metadata_payload = payload.metadata
    db.add(run)
    db.flush()

    try:
        for entry in payload.predictions:
            item = _resolve_prediction_item(db, project_id=project.id, reference=entry.item)
            label_class = _resolve_project_label_class(
                db,
                project_id=project.id,
                label_class_id=entry.label_class_id,
                label_name=entry.label_name,
            )
            if item.kind == ItemKind.image and entry.frame_index is not None:
                raise HTTPException(status_code=400, detail="Image items must not include frame_index values")
            if item.kind == ItemKind.video and entry.frame_index is None:
                raise HTTPException(status_code=400, detail="Video prediction entries require frame_index")

            polygon_points = entry.polygon_points
            x1, y1, x2, y2 = entry.x1, entry.y1, entry.x2, entry.y2
            if label_class.geometry_kind == LabelGeometryKind.polygon:
                if not polygon_points:
                    raise HTTPException(status_code=400, detail="Polygon label classes require polygon_points")
                x1, y1, x2, y2 = _compute_polygon_bounds(polygon_points)

            prediction = Prediction(
                prediction_run_id=run.id,
                item_id=item.id,
                label_class_id=label_class.id,
                frame_index=entry.frame_index,
                track_id=entry.track_id,
                propagation_frames=max(0, int(entry.propagation_frames or 0)) if entry.propagation_frames is not None else None,
                external_prediction_id=entry.external_prediction_id,
                confidence=entry.confidence,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
            prediction.polygon_points = polygon_points
            prediction.metadata_payload = entry.metadata
            db.add(prediction)

        run.imported_prediction_count = len(payload.predictions)
        run.status = PredictionRunStatus.completed
        db.add(run)
        db.commit()
        db.refresh(run)
    except Exception:
        db.rollback()
        raise

    predictions = db.execute(
        select(Prediction)
        .where(Prediction.prediction_run_id == run.id)
        .order_by(Prediction.id.asc())
    ).scalars().all()
    return PredictionRunImportResponse(
        run=_serialize_prediction_run(run),
        predictions=[_serialize_prediction(prediction) for prediction in predictions],
    )


@router.get("/prediction-runs/{run_id}", response_model=PredictionRunReadV1)
def get_prediction_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    run = db.get(PredictionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Prediction run not found")
    _get_project_or_404(db, run.project_id, current_user)
    return _serialize_prediction_run(run)


@router.get("/prediction-runs/{run_id}/predictions", response_model=list[PredictionReadV1])
def list_run_predictions(
    run_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_api_roles(UserRole.project_admin)),
):
    run = db.get(PredictionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Prediction run not found")
    _get_project_or_404(db, run.project_id, current_user)
    predictions = db.execute(
        select(Prediction)
        .where(Prediction.prediction_run_id == run.id)
        .order_by(Prediction.id.asc())
    ).scalars().all()
    return [_serialize_prediction(prediction) for prediction in predictions]
