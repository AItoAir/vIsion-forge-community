# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import random
import shutil
from collections import defaultdict
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Annotation,
    Item,
    ItemKind,
    ItemStatus,
    LabelClass,
    LabelGeometryKind,
    Notification,
    Project,
    Sam2TrackJob,
    UserRole,
)
from ..security import ensure_project_team_access, require_roles
from ..services.audit import log_audit
from ..services.media import (
    annotation_source_relative_path,
    build_annotation_media_state,
    labeling_proxy_storage_summary_payload,
    media_storage_path,
    preferred_item_media_variant,
    remove_labeling_proxy_video,
    resolve_annotation_source_path,
    sync_item_media_conversion_state,
)
from ..services.uploads import (
    SUPPORTED_MEDICAL_UPLOAD_EXTENSIONS,
    SUPPORTED_STANDARD_IMAGE_EXTENSIONS,
    SUPPORTED_STANDARD_IMAGE_LABEL,
    UPLOAD_FILE_INPUT_ACCEPT,
)
from ..template_utils import create_templates

router = APIRouter(include_in_schema=False)

templates = create_templates()


DEFAULT_LABEL_COLORS = [
    "#ef4444",
    "#f97316",
    "#eab308",
    "#84cc16",
    "#22c55e",
    "#14b8a6",
    "#06b6d4",
    "#3b82f6",
    "#6366f1",
    "#8b5cf6",
    "#d946ef",
    "#ec4899",
]

ITEM_LABEL_SUMMARY_PREVIEW_LIMIT = 3
PROJECT_DELETE_SAMPLE_LIMIT = 5

PROJECTS_INDEX_NOTICE_MESSAGES = {
    "project_deleted": "Project was deleted.",
}

PROJECT_SETTINGS_DELETE_ERROR_MESSAGES = {
    "name_mismatch": "Type the project name exactly to confirm deletion.",
}


def _pick_default_label_color(label_classes: list[LabelClass]) -> str:
    used_colors = {(lc.color_hex or "").lower() for lc in label_classes}
    available_colors = [
        color for color in DEFAULT_LABEL_COLORS if color.lower() not in used_colors
    ]
    return random.choice(available_colors or DEFAULT_LABEL_COLORS)


def _normalize_project_name(raw_value: str | None) -> str:
    return (raw_value or "").strip()


def _projects_index_redirect_url(
    request: Request,
    *,
    notice: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if notice in PROJECTS_INDEX_NOTICE_MESSAGES:
        params["notice"] = str(notice)

    base_url = str(request.url_for("projects_index"))
    if not params:
        return base_url
    return f"{base_url}?{urlencode(params)}"


def _project_settings_redirect_url(
    request: Request,
    project_id: int,
    *,
    delete_error: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if delete_error in PROJECT_SETTINGS_DELETE_ERROR_MESSAGES:
        params["delete_error"] = str(delete_error)

    base_url = str(request.url_for("project_settings", project_id=project_id))
    if not params:
        return base_url
    return f"{base_url}?{urlencode(params)}"


def _build_project_delete_summary(
    db: Session,
    *,
    project_id: int,
    label_class_count: int,
) -> dict[str, object]:
    total_item_count = int(
        db.execute(select(func.count(Item.id)).where(Item.project_id == project_id))
        .scalar_one()
        or 0
    )
    total_annotation_count = int(
        db.execute(
            select(func.count(Annotation.id))
            .join(Item, Item.id == Annotation.item_id)
            .where(Item.project_id == project_id)
        )
        .scalar_one()
        or 0
    )
    sample_items = (
        db.execute(
            select(Item)
            .where(Item.project_id == project_id)
            .order_by(Item.id.asc())
            .limit(PROJECT_DELETE_SAMPLE_LIMIT)
        )
        .scalars()
        .all()
    )

    return {
        "item_count": total_item_count,
        "annotation_count": total_annotation_count,
        "label_class_count": label_class_count,
        "sample_items": sample_items,
        "sample_limit": PROJECT_DELETE_SAMPLE_LIMIT,
        "remaining_item_count": max(0, total_item_count - len(sample_items)),
    }


def _delete_project_related_records(db: Session, project: Project) -> None:
    items = (
        db.execute(select(Item).where(Item.project_id == project.id).order_by(Item.id.asc()))
        .scalars()
        .all()
    )
    item_ids = [item.id for item in items]

    if item_ids:
        sam2_job_ids = list(
            db.execute(select(Sam2TrackJob.id).where(Sam2TrackJob.item_id.in_(item_ids)))
            .scalars()
            .all()
        )
        if sam2_job_ids:
            db.execute(
                delete(Notification).where(
                    Notification.sam2_track_job_id.in_(sam2_job_ids)
                )
            )

        db.execute(delete(Notification).where(Notification.item_id.in_(item_ids)))
        db.execute(delete(Sam2TrackJob).where(Sam2TrackJob.item_id.in_(item_ids)))

    db.execute(delete(Notification).where(Notification.project_id == project.id))

    for item in items:
        remove_labeling_proxy_video(item)
        db.delete(item)

    db.delete(project)


def _parse_optional_positive_int(raw_value: str | None) -> int | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_non_negative_int(raw_value: str | None, default: int = 0) -> int:
    value = (raw_value or "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


def _normalize_label_presets(
    *,
    geometry_kind: LabelGeometryKind,
    default_use_fixed_box: bool | None,
    default_box_w: str | None,
    default_box_h: str | None,
    default_propagation_frames: str | None,
) -> dict[str, int | bool | None]:
    propagation_frames = _parse_non_negative_int(default_propagation_frames, default=0)
    box_w = _parse_optional_positive_int(default_box_w)
    box_h = _parse_optional_positive_int(default_box_h)
    use_fixed_box = bool(default_use_fixed_box) if geometry_kind == LabelGeometryKind.bbox else False

    if geometry_kind != LabelGeometryKind.bbox:
        box_w = None
        box_h = None

    if use_fixed_box and box_w is None:
        box_w = 64
    if use_fixed_box and box_h is None:
        box_h = 64

    return {
        "default_use_fixed_box": use_fixed_box,
        "default_box_w": box_w,
        "default_box_h": box_h,
        "default_propagation_frames": propagation_frames,
    }


def _normalize_item_search_query(raw_value: str | None) -> str:
    return (raw_value or "").strip()


def _parse_item_kind_filter(raw_value: str | None) -> ItemKind | None:
    value = (raw_value or "").strip().lower()
    if not value:
        return None
    try:
        return ItemKind(value)
    except ValueError:
        return None


def _parse_item_status_filter(raw_value: str | None) -> ItemStatus | None:
    value = (raw_value or "").strip().lower()
    if not value:
        return None
    try:
        return ItemStatus(value)
    except ValueError:
        return None


def _parse_label_class_filter(raw_value: str | None) -> int | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _load_project_label_classes(db: Session, project_id: int) -> list[LabelClass]:
    return (
        db.execute(
            select(LabelClass)
            .where(LabelClass.project_id == project_id)
            .order_by(LabelClass.name.asc(), LabelClass.id.asc())
        )
        .scalars()
        .all()
    )


def _resolve_project_label_filter(
    label_class_id: int | None,
    project_label_classes: list[LabelClass],
) -> LabelClass | None:
    if label_class_id is None:
        return None

    for label_class in project_label_classes:
        if label_class.id == label_class_id:
            return label_class
    return None


def _annotation_frame_span(
    item_kind: ItemKind,
    frame_index: int | None,
    propagation_frames: int | None,
) -> tuple[int, int]:
    if item_kind == ItemKind.video and frame_index is not None:
        extra_frames = max(0, propagation_frames or 0)
        return frame_index, frame_index + extra_frames

    start_frame = frame_index if frame_index is not None else 0
    return start_frame, start_frame


def _count_covered_frames(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0

    sorted_intervals = sorted(intervals)
    current_start, current_end = sorted_intervals[0]
    covered_frames = 0

    for next_start, next_end in sorted_intervals[1:]:
        if next_start <= current_end + 1:
            current_end = max(current_end, next_end)
            continue

        covered_frames += current_end - current_start + 1
        current_start, current_end = next_start, next_end

    covered_frames += current_end - current_start + 1
    return covered_frames


def _build_item_label_summaries(
    items: list[Item],
    annotation_rows: list[tuple[int, int, str, str | None, int | None, int | None]],
) -> dict[int, dict[str, object]]:
    item_kinds = {item.id: item.kind for item in items}
    grouped_stats: dict[int, dict[int, dict[str, object]]] = defaultdict(dict)

    for (
        item_id,
        label_class_id,
        label_name,
        color_hex,
        frame_index,
        propagation_frames,
    ) in annotation_rows:
        item_kind = item_kinds.get(item_id, ItemKind.image)
        start_frame, end_frame = _annotation_frame_span(
            item_kind,
            frame_index,
            propagation_frames,
        )
        label_stats = grouped_stats[item_id].setdefault(
            label_class_id,
            {
                "id": label_class_id,
                "name": label_name,
                "color_hex": color_hex or "#6c757d",
                "object_count": 0,
                "frame_intervals": [],
            },
        )
        label_stats["object_count"] += end_frame - start_frame + 1
        label_stats["frame_intervals"].append((start_frame, end_frame))

    summaries: dict[int, dict[str, object]] = {}
    for item in items:
        labels: list[dict[str, object]] = []
        for stats in grouped_stats.get(item.id, {}).values():
            frame_intervals = stats.pop("frame_intervals")
            labels.append(
                {
                    "id": stats["id"],
                    "name": stats["name"],
                    "color_hex": stats["color_hex"],
                    "object_count": stats["object_count"],
                    "frame_count": _count_covered_frames(frame_intervals),
                }
            )

        labels.sort(key=lambda entry: (str(entry["name"]).lower(), int(entry["id"])))
        summaries[item.id] = {
            "labels": labels,
            "preview_labels": labels[:ITEM_LABEL_SUMMARY_PREVIEW_LIMIT],
            "hidden_labels": labels[ITEM_LABEL_SUMMARY_PREVIEW_LIMIT:],
            "hidden_count": max(0, len(labels) - ITEM_LABEL_SUMMARY_PREVIEW_LIMIT),
        }

    return summaries


def _load_item_label_summaries(
    db: Session,
    items: list[Item],
) -> dict[int, dict[str, object]]:
    item_ids = [item.id for item in items]
    if not item_ids:
        return {}

    annotation_rows = db.execute(
        select(
            Annotation.item_id,
            Annotation.label_class_id,
            LabelClass.name,
            LabelClass.color_hex,
            Annotation.frame_index,
            Annotation.propagation_frames,
        )
        .join(LabelClass, LabelClass.id == Annotation.label_class_id)
        .where(Annotation.item_id.in_(item_ids))
        .order_by(
            Annotation.item_id.asc(),
            LabelClass.name.asc(),
            Annotation.frame_index.asc(),
            Annotation.id.asc(),
        )
    ).all()

    return _build_item_label_summaries(items, list(annotation_rows))


def _load_project_items(
    db: Session,
    *,
    project_id: int,
    query_text: str = "",
    kind: ItemKind | None = None,
    status: ItemStatus | None = None,
    label_class_id: int | None = None,
) -> list[Item]:
    stmt = select(Item).where(Item.project_id == project_id)

    if query_text:
        stmt = stmt.where(Item.path.ilike(f"%{query_text}%"))
    if kind is not None:
        stmt = stmt.where(Item.kind == kind)
    if status is not None:
        stmt = stmt.where(Item.status == status)
    if label_class_id is not None:
        stmt = stmt.where(Item.annotations.any(Annotation.label_class_id == label_class_id))

    return db.execute(stmt.order_by(Item.id.asc())).scalars().all()


def _build_project_items_context(
    *,
    request: Request,
    project: Project,
    db: Session,
    current_user,
    progress: float | None,
    query_text: str = "",
    kind: ItemKind | None = None,
    status: ItemStatus | None = None,
    label_class_id: int | None = None,
) -> dict[str, object]:
    project_label_classes = _load_project_label_classes(db, project.id)
    selected_label_class = _resolve_project_label_filter(
        label_class_id, project_label_classes
    )
    effective_label_class_id = (
        selected_label_class.id if selected_label_class is not None else None
    )
    normalized_query_text = _normalize_item_search_query(query_text)
    items = _load_project_items(
        db,
        project_id=project.id,
        query_text=normalized_query_text,
        kind=kind,
        status=status,
        label_class_id=effective_label_class_id,
    )
    item_preview_state: dict[int, dict[str, object]] = {}
    items_changed = False
    for item in items:
        if item.kind == ItemKind.video:
            if sync_item_media_conversion_state(item):
                db.add(item)
                items_changed = True
            media_state = build_annotation_media_state(item)
            source_available = resolve_annotation_source_path(item) is not None
            preview_available = media_state.ready or source_available
            item_preview_state[item.id] = {
                "available": preview_available,
                "variant": "display",
                "missing_reason": (
                    (item.media_conversion_error or "").strip()
                    or f"Video source was not found: {annotation_source_relative_path(item)}"
                ),
            }
            continue

        source_available = resolve_annotation_source_path(item) is not None
        item_preview_state[item.id] = {
            "available": source_available,
            "variant": preferred_item_media_variant(item),
            "missing_reason": (
                None if source_available else f"Image source was not found: {annotation_source_relative_path(item)}"
            ),
        }

    if items_changed:
        db.commit()

    total_item_count = len(project.items)
    filtered_item_count = len(items)
    filters_applied = bool(
        normalized_query_text or kind is not None or status is not None or effective_label_class_id is not None
    )

    return {
        "request": request,
        "project": project,
        "items": items,
        "item_preview_state": item_preview_state,
        "item_label_summaries": _load_item_label_summaries(db, items),
        "progress": progress,
        "status_filter": status,
        "storage_budget": labeling_proxy_storage_summary_payload(),
        "current_user": current_user,
        "upload_file_accept": UPLOAD_FILE_INPUT_ACCEPT,
        "supported_standard_image_extensions": list(SUPPORTED_STANDARD_IMAGE_EXTENSIONS),
        "supported_standard_image_label": SUPPORTED_STANDARD_IMAGE_LABEL,
        "supported_medical_upload_extensions": list(SUPPORTED_MEDICAL_UPLOAD_EXTENSIONS),
        "project_label_classes": project_label_classes,
        "selected_label_class": selected_label_class,
        "filtered_item_count": filtered_item_count,
        "total_item_count": total_item_count,
        "filters_applied": filters_applied,
        "filter_state": {
            "query": normalized_query_text,
            "kind": kind.value if kind is not None else "",
            "status": status.value if status is not None else "",
            "label_class_id": effective_label_class_id,
        },
    }


@router.get("/", response_class=HTMLResponse, name="projects_index")
def projects_index(
    request: Request,
    notice: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    projects = db.execute(select(Project).order_by(Project.updated_at.desc())).scalars().all()
    if current_user.role != UserRole.system_admin:
        projects = [
            p for p in projects if p.owner and p.owner.team_id == current_user.team_id
        ]
    return templates.TemplateResponse(
        request=request,
        name="projects_index.html",
        context={
            "request": request,
            "projects": projects,
            "projects_notice": PROJECTS_INDEX_NOTICE_MESSAGES.get(notice),
            "current_user": current_user,
        },
    )


@router.get(
    "/projects/{project_id}/settings",
    response_class=HTMLResponse,
    name="project_settings",
)
def project_settings(
    request: Request,
    project_id: int,
    delete_error: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    label_classes = (
        db.execute(
            select(LabelClass)
            .where(LabelClass.project_id == project_id)
            .order_by(LabelClass.id.asc())
        )
        .scalars()
        .all()
    )
    delete_summary = _build_project_delete_summary(
        db,
        project_id=project_id,
        label_class_count=len(label_classes),
    )

    return templates.TemplateResponse(
        request=request,
        name="project_settings.html",
        context={
            "request": request,
            "project": project,
            "label_classes": label_classes,
            "new_label_color": _pick_default_label_color(label_classes),
            "project_delete_summary": delete_summary,
            "project_delete_error": PROJECT_SETTINGS_DELETE_ERROR_MESSAGES.get(
                delete_error
            ),
            "project_delete_expected_name": _normalize_project_name(project.name),
            "current_user": current_user,
        },
    )


@router.post(
    "/projects/{project_id}/settings/update",
    response_class=HTMLResponse,
    name="update_project",
)
def update_project(
    request: Request,
    project_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    clean_name = name.strip()
    if not clean_name:
        return HTMLResponse(status_code=400, content="Project name is required")

    project.name = clean_name
    project.description = (description or "").strip() or None
    db.add(project)
    db.commit()

    return RedirectResponse(
        url=request.url_for("project_settings", project_id=project_id),
        status_code=303,
    )


@router.post(
    "/projects/{project_id}/settings/labels/create",
    response_class=HTMLResponse,
    name="create_label_class",
)
def create_label_class(
    request: Request,
    project_id: int,
    name: str = Form(...),
    geometry_kind: str = Form("bbox"),
    color_hex: str | None = Form(None),
    shortcut_key: str | None = Form(None),
    default_use_fixed_box: bool | None = Form(None),
    default_box_w: str | None = Form(None),
    default_box_h: str | None = Form(None),
    default_propagation_frames: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    try:
        geom = LabelGeometryKind(geometry_kind)
    except ValueError:
        geom = LabelGeometryKind.bbox

    sk = (shortcut_key or "").strip() or None

    resolved_color = (color_hex or "").strip() or _pick_default_label_color(
        project.label_classes
    )
    preset_values = _normalize_label_presets(
        geometry_kind=geom,
        default_use_fixed_box=default_use_fixed_box,
        default_box_w=default_box_w,
        default_box_h=default_box_h,
        default_propagation_frames=default_propagation_frames,
    )

    lc = LabelClass(
        project_id=project_id,
        name=name,
        geometry_kind=geom,
        color_hex=resolved_color,
        shortcut_key=sk,
        is_active=True,
        **preset_values,
    )
    db.add(lc)
    db.commit()

    return RedirectResponse(
        url=request.url_for("project_settings", project_id=project_id),
        status_code=303,
    )


@router.post(
    "/projects/{project_id}/settings/labels/{label_id}/update",
    response_class=HTMLResponse,
    name="update_label_class",
)
def update_label_class(
    request: Request,
    project_id: int,
    label_id: int,
    name: str = Form(...),
    geometry_kind: str = Form("bbox"),
    color_hex: str = Form("#00ff00"),
    shortcut_key: str | None = Form(None),
    default_use_fixed_box: bool | None = Form(None),
    default_box_w: str | None = Form(None),
    default_box_h: str | None = Form(None),
    default_propagation_frames: str | None = Form(None),
    is_active: bool | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    lc = db.get(LabelClass, label_id)
    if not lc or lc.project_id != project_id:
        return HTMLResponse(status_code=404, content="Label class not found")

    lc.name = name
    try:
        lc.geometry_kind = LabelGeometryKind(geometry_kind)
    except ValueError:
        lc.geometry_kind = LabelGeometryKind.bbox

    lc.color_hex = color_hex or "#00ff00"
    sk = (shortcut_key or "").strip()
    lc.shortcut_key = sk or None
    lc.is_active = bool(is_active)
    preset_values = _normalize_label_presets(
        geometry_kind=lc.geometry_kind,
        default_use_fixed_box=default_use_fixed_box,
        default_box_w=default_box_w,
        default_box_h=default_box_h,
        default_propagation_frames=default_propagation_frames,
    )
    lc.default_use_fixed_box = bool(preset_values["default_use_fixed_box"])
    lc.default_box_w = preset_values["default_box_w"]
    lc.default_box_h = preset_values["default_box_h"]
    lc.default_propagation_frames = int(
        preset_values["default_propagation_frames"] or 0
    )

    db.add(lc)
    db.commit()

    return RedirectResponse(
        url=request.url_for("project_settings", project_id=project_id),
        status_code=303,
    )


@router.post("/projects/create", response_class=HTMLResponse, name="create_project")
def create_project(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    clean_name = _normalize_project_name(name)
    if not clean_name:
        return HTMLResponse(status_code=400, content="Project name is required")

    project = Project(
        name=clean_name,
        description=(description or "").strip() or None,
        owner_user_id=current_user.id,
        is_archived=False,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return RedirectResponse(
        url=request.url_for("project_items", project_id=project.id),
        status_code=303,
    )


@router.post(
    "/projects/{project_id}/delete",
    response_class=HTMLResponse,
    name="delete_project",
)
def delete_project(
    request: Request,
    project_id: int,
    confirmation_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    expected_name = _normalize_project_name(project.name)
    if _normalize_project_name(confirmation_name) != expected_name:
        return RedirectResponse(
            url=_project_settings_redirect_url(
                request,
                project_id,
                delete_error="name_mismatch",
            ),
            status_code=303,
        )

    delete_summary = _build_project_delete_summary(
        db,
        project_id=project.id,
        label_class_count=len(project.label_classes),
    )
    project_name = project.name
    project_upload_dir = media_storage_path(f"uploads/project_{project.id}")

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="project",
        object_id=project.id,
        action="project_deleted",
        payload={
            "name": project_name,
            "item_count": delete_summary["item_count"],
            "annotation_count": delete_summary["annotation_count"],
            "label_class_count": delete_summary["label_class_count"],
        },
    )
    _delete_project_related_records(db, project)
    db.flush()

    if project_upload_dir.exists() and project_upload_dir.is_dir():
        shutil.rmtree(project_upload_dir, ignore_errors=True)

    db.commit()

    return RedirectResponse(
        url=_projects_index_redirect_url(request, notice="project_deleted"),
        status_code=303,
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse, name="project_detail")
def project_detail(
    request: Request,
    project_id: int,
    q: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    label_class_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    total_items = len(project.items)
    done_items = len([i for i in project.items if i.status == ItemStatus.done])
    progress = (done_items / total_items * 100) if total_items else 0.0

    return templates.TemplateResponse(
        request=request,
        name="items_list.html",
        context=_build_project_items_context(
            request=request,
            project=project,
            db=db,
            current_user=current_user,
            progress=progress,
            query_text=q,
            kind=_parse_item_kind_filter(kind),
            status=_parse_item_status_filter(status),
            label_class_id=_parse_label_class_filter(label_class_id),
        ),
    )


@router.get(
    "/projects/{project_id}/items",
    response_class=HTMLResponse,
    name="project_items",
)
def project_items(
    request: Request,
    project_id: int,
    q: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    label_class_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    return templates.TemplateResponse(
        request=request,
        name="items_list.html",
        context=_build_project_items_context(
            request=request,
            project=project,
            db=db,
            current_user=current_user,
            progress=None,
            query_text=q,
            kind=_parse_item_kind_filter(kind),
            status=_parse_item_status_filter(status),
            label_class_id=_parse_label_class_filter(label_class_id),
        ),
    )
