from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..csrf import request_has_allowed_origin
from ..config import settings
from ..database import get_db
from ..models import (
    Annotation,
    AnnotationStatus,
    Item,
    ItemKind,
    ItemStatus,
    LabelClass,
    Project,
    RegionComment,
    UserRole,
)
from ..security import ensure_project_team_access, require_roles
from ..services.audit import log_audit
from ..services.comment_mentions import build_project_mention_candidates
from ..services.media import (
    MediaProbeError,
    build_annotation_media_state,
    enqueue_media_conversion,
    labeling_proxy_profile_token,
    media_conversion_payload,
    media_storage_path,
    probe_media_metadata,
    refresh_annotation_media_state,
    remove_labeling_proxy_video,
    resolve_annotation_media_path,
    touch_media_conversion_access,
)
from ..services.sam2 import sam2_feature_configured, sam2_feature_enabled

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


def _persist_item_media_state(
    db: Session,
    item: Item,
    *,
    auto_enqueue: bool = False,
):
    state = refresh_annotation_media_state(item, auto_enqueue=auto_enqueue)
    if db.is_modified(item, include_collections=False):
        db.add(item)
        db.commit()
        db.refresh(item)
        state = build_annotation_media_state(item)
    return state


def _item_media_conversion_payload(
    db: Session,
    item: Item,
    *,
    auto_enqueue: bool = False,
    record_access: bool = False,
) -> dict:
    state = _persist_item_media_state(db, item, auto_enqueue=auto_enqueue)
    if record_access and state.ready and touch_media_conversion_access(item):
        db.add(item)
        db.commit()
        db.refresh(item)
        state = build_annotation_media_state(item)
    payload = media_conversion_payload(item)
    return payload


def _is_async_upload_request(request: Request) -> bool:
    return request.headers.get("x-vision-forge-upload", "").strip() == "1"


def _get_prev_next_item_ids(db: Session, project_id: int, item: Item) -> tuple[int | None, int | None]:
    prev_item_id = (
        db.execute(
            select(Item.id)
            .where(
                Item.project_id == project_id,
                Item.kind == item.kind,
                Item.id < item.id,
            )
            .order_by(Item.id.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    next_item_id = (
        db.execute(
            select(Item.id)
            .where(
                Item.project_id == project_id,
                Item.kind == item.kind,
                Item.id > item.id,
            )
            .order_by(Item.id.asc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    return prev_item_id, next_item_id


def _item_media_relative_path(item: Item, variant: str) -> str:
    normalized_variant = (variant or "").strip().lower()
    if normalized_variant == "original" or item.kind != ItemKind.video:
        return item.path
    if normalized_variant == "display":
        return resolve_annotation_media_path(item)
    raise HTTPException(status_code=404, detail="Unsupported media variant")


@router.get(
    "/items/{item_id}/media/{variant}",
    response_class=FileResponse,
    name="item_media",
)
def item_media(
    request: Request,
    item_id: int,
    variant: str,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)
    ),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    ensure_project_team_access(item.project, current_user)

    if not request_has_allowed_origin(request, allow_fetch_metadata_fallback=True):
        raise HTTPException(status_code=403, detail="Forbidden")

    media_path = media_storage_path(_item_media_relative_path(item, variant))
    if not media_path.is_file():
        raise HTTPException(status_code=404, detail="Item media not found")

    return FileResponse(
        path=media_path,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/items/{item_id}/label", response_class=HTMLResponse, name="label_item")
def label_item(
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
            .where(LabelClass.project_id == project.id, LabelClass.is_active.is_(True))
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

    prev_item_id, next_item_id = _get_prev_next_item_ids(db, project.id, item)
    media_conversion = _item_media_conversion_payload(
        db,
        item,
        auto_enqueue=item.kind == ItemKind.video,
        record_access=item.kind == ItemKind.video,
    )
    display_media_variant = (
        "display"
        if item.kind == ItemKind.video and media_conversion["ready"]
        else "original"
    )
    display_media_url = str(
        request.url_for("item_media", item_id=item.id, variant=display_media_variant)
    )
    mention_candidates = build_project_mention_candidates(db, project)

    return templates.TemplateResponse(
        request=request,
        name="item_label.html",
        context={
            "request": request,
            "item": item,
            "project": project,
            "annotation_revision": item.annotation_revision,
            "label_classes": label_classes,
            "annotations": annotations,
            "region_comments": region_comments,
            "current_user": current_user,
            "mention_candidates": mention_candidates,
            "prev_item_id": prev_item_id,
            "next_item_id": next_item_id,
            "display_media_url": display_media_url,
            "media_conversion": media_conversion,
            "read_only": current_user.role == UserRole.reviewer,
            "sam2_enabled": sam2_feature_enabled(),
            "sam2_configured": sam2_feature_configured(),
            "sam2_job_poll_interval_ms": settings.sam2_job_poll_interval_ms,
        },
    )


@router.post(
    "/projects/{project_id}/items/upload",
    response_class=HTMLResponse,
    name="upload_item",
)
def upload_item(
    request: Request,
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    content_type = file.content_type or ""
    if content_type.startswith("video/"):
        kind = ItemKind.video
    else:
        kind = ItemKind.image

    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    uploads_root = static_dir / "uploads"
    project_dir = uploads_root / f"project_{project_id}"
    project_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(file.filename or "uploaded").name
    target_path = project_dir / filename

    hasher = hashlib.sha256()
    try:
        with target_path.open("xb") as out:
            while True:
                chunk = file.file.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
    except FileExistsError:
        return PlainTextResponse(
            f"File already exists in this project: {filename}",
            status_code=400,
        )
    finally:
        file.file.close()

    sha256_hex = hasher.hexdigest()

    try:
        metadata = probe_media_metadata(target_path, kind)
    except MediaProbeError as exc:
        try:
            target_path.unlink(missing_ok=True)
        except TypeError:
            if target_path.exists():
                target_path.unlink()
        return PlainTextResponse(str(exc), status_code=400)

    if kind == ItemKind.video and metadata.frame_rate_mode == "vfr":
        try:
            target_path.unlink(missing_ok=True)
        except TypeError:
            if target_path.exists():
                target_path.unlink()
        return PlainTextResponse(
            (
                "Variable frame rate videos are not supported for frame-accurate labeling yet. "
                "Please convert this video to constant frame rate (CFR) before uploading."
            ),
            status_code=400,
        )

    rel_path = target_path.relative_to(static_dir)

    item = Item(
        project_id=project_id,
        kind=kind,
        path=str(rel_path).replace("\\", "/"),
        sha256=sha256_hex,
        w=metadata.width,
        h=metadata.height,
        duration_sec=metadata.duration_sec,
        fps=metadata.fps,
        media_conversion_status="pending" if kind == ItemKind.video else "not_required",
        media_conversion_error=None,
        media_conversion_profile=(
            labeling_proxy_profile_token() if kind == ItemKind.video else None
        ),
        frame_rate_mode=metadata.frame_rate_mode if kind == ItemKind.video else None,
        status=ItemStatus.unlabeled,
    )
    db.add(item)
    db.flush()

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_uploaded",
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

    db.commit()
    if item.kind == ItemKind.video:
        enqueue_media_conversion(item.id)

    if _is_async_upload_request(request):
        return JSONResponse(
            {
                "ok": True,
                "item_id": item.id,
                "kind": item.kind.value,
                "path": item.path,
                "media_conversion": media_conversion_payload(item),
            }
        )

    return RedirectResponse(
        url=request.url_for("project_items", project_id=project_id),
        status_code=303,
    )


@router.get(
    "/items/{item_id}/media-conversion-status",
    response_class=JSONResponse,
    name="item_media_conversion_status",
)
def item_media_conversion_status(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    item = db.get(Item, item_id)
    if not item:
        return JSONResponse(status_code=404, content={"detail": "Item not found"})

    ensure_project_team_access(item.project, current_user)
    return JSONResponse(_item_media_conversion_payload(db, item, auto_enqueue=True))


@router.post(
    "/items/{item_id}/submit-review",
    response_class=HTMLResponse,
    name="submit_item_for_review",
)
def submit_item_for_review(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
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
        return PlainTextResponse("Cannot submit an item with no annotations", status_code=400)

    for ann in annotations:
        ann.status = AnnotationStatus.pending
        db.add(ann)

    item.status = ItemStatus.needs_review
    db.add(item)

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_submitted_for_review",
        payload={
            "annotation_count": len(annotations),
            "item_status": item.status.value,
        },
    )

    db.commit()
    return RedirectResponse(url=request.url_for("label_item", item_id=item.id), status_code=303)


@router.post(
    "/items/{item_id}/reopen",
    response_class=HTMLResponse,
    name="reopen_item_for_editing",
)
def reopen_item_for_editing(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.project_admin)),
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
    for ann in annotations:
        ann.status = AnnotationStatus.pending
        db.add(ann)

    item.status = ItemStatus.in_progress if annotations else ItemStatus.unlabeled
    db.add(item)

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_reopened_for_editing",
        payload={
            "annotation_count": len(annotations),
            "item_status": item.status.value,
        },
    )

    db.commit()
    return RedirectResponse(url=request.url_for("label_item", item_id=item.id), status_code=303)


@router.post(
    "/projects/{project_id}/items/{item_id}/delete",
    response_class=HTMLResponse,
    name="delete_item",
)
def delete_item(
    request: Request,
    project_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    item = db.get(Item, item_id)
    if not item or item.project_id != project_id:
        return HTMLResponse(status_code=404, content="Item not found")

    ensure_project_team_access(project, current_user)

    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    file_path = static_dir / item.path

    other_item_exists = db.execute(
        select(Item.id).where(Item.path == item.path, Item.id != item.id).limit(1)
    ).scalar_one_or_none()

    if other_item_exists is None and file_path.is_file():
        try:
            file_path.unlink()
        except Exception:
            pass
    if other_item_exists is None:
        remove_labeling_proxy_video(item)

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="item",
        object_id=item.id,
        action="item_deleted",
        payload={
            "path": item.path,
            "kind": item.kind.value,
            "status": item.status.value,
        },
    )

    db.execute(delete(Annotation).where(Annotation.item_id == item.id))
    db.delete(item)
    db.commit()

    return RedirectResponse(
        url=request.url_for("project_items", project_id=project_id),
        status_code=303,
    )
