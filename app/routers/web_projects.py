from __future__ import annotations

import random
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Item,
    ItemStatus,
    LabelClass,
    LabelGeometryKind,
    Project,
    UserRole,
)
from ..security import ensure_project_team_access, require_roles
from ..services.media import labeling_proxy_storage_summary_payload

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


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


def _pick_default_label_color(label_classes: list[LabelClass]) -> str:
    used_colors = {(lc.color_hex or "").lower() for lc in label_classes}
    available_colors = [
        color for color in DEFAULT_LABEL_COLORS if color.lower() not in used_colors
    ]
    return random.choice(available_colors or DEFAULT_LABEL_COLORS)


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


@router.get("/", response_class=HTMLResponse, name="projects_index")
def projects_index(
    request: Request,
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

    return templates.TemplateResponse(
        request=request,
        name="project_settings.html",
        context={
            "request": request,
            "project": project,
            "label_classes": label_classes,
            "new_label_color": _pick_default_label_color(label_classes),
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
    project = Project(
        name=name,
        description=description,
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


@router.get("/projects/{project_id}", response_class=HTMLResponse, name="project_detail")
def project_detail(
    request: Request,
    project_id: int,
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
        context={
            "request": request,
            "project": project,
            "items": project.items,
            "progress": progress,
            "status_filter": None,
            "storage_budget": labeling_proxy_storage_summary_payload(),
            "current_user": current_user,
        },
    )


@router.get(
    "/projects/{project_id}/items",
    response_class=HTMLResponse,
    name="project_items",
)
def project_items(
    request: Request,
    project_id: int,
    status: ItemStatus | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.annotator, UserRole.reviewer, UserRole.project_admin)),
):
    project = db.get(Project, project_id)
    if not project:
        return HTMLResponse(status_code=404, content="Project not found")

    ensure_project_team_access(project, current_user)

    stmt = select(Item).where(Item.project_id == project_id)
    if status:
        stmt = stmt.where(Item.status == status)

    items = db.execute(stmt.order_by(Item.id.asc())).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="items_list.html",
        context={
            "request": request,
            "project": project,
            "items": items,
            "progress": None,
            "status_filter": status,
            "storage_budget": labeling_proxy_storage_summary_payload(),
            "current_user": current_user,
        },
    )
