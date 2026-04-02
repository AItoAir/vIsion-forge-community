from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Team, User, UserRole
from ..security import require_roles, hash_password

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)


ROLE_DISPLAY_NAMES = {
    "annotator": "Annotator",
    "reviewer": "Reviewer",
    "project_admin": "Project admin",
    "system_admin": "System admin",
}

ROLE_MATRIX_COLUMNS = [
    {
        "key": "annotator",
        "label": "Annotator",
    },
    {
        "key": "reviewer",
        "label": "Reviewer",
    },
    {
        "key": "project_admin",
        "label": "Project admin",
    },
]

ROLE_MATRIX_ROWS = [
    {
        "capability": "Open projects and items",
        "detail": "Browse project lists, item lists, labeling pages, and review pages for the same team.",
        "annotator": True,
        "reviewer": True,
        "project_admin": True,
    },
    {
        "capability": "Edit annotations",
        "detail": "Create, update, and delete annotations from the labeling screen.",
        "annotator": True,
        "reviewer": False,
        "project_admin": True,
    },
    {
        "capability": "Use SAM assist",
        "detail": "Run current-frame and track suggestion tools while labeling.",
        "annotator": True,
        "reviewer": False,
        "project_admin": True,
    },
    {
        "capability": "Submit or reopen items",
        "detail": "Send work to review or reopen items for editing.",
        "annotator": True,
        "reviewer": False,
        "project_admin": True,
    },
    {
        "capability": "Approve, reject, or reset review",
        "detail": "Finalize review decisions and leave rejection comments.",
        "annotator": False,
        "reviewer": True,
        "project_admin": True,
    },
    {
        "capability": "Manage project settings and labels",
        "detail": "Create projects, edit project details, and manage label classes.",
        "annotator": False,
        "reviewer": False,
        "project_admin": True,
    },
    {
        "capability": "Upload or delete project items",
        "detail": "Add source media to a project and remove items when needed.",
        "annotator": False,
        "reviewer": False,
        "project_admin": True,
    },
    {
        "capability": "Export project data",
        "detail": "Download project exports and original media.",
        "annotator": True,
        "reviewer": True,
        "project_admin": True,
    },
    {
        "capability": "Manage teams and members",
        "detail": "Create teams, invite members, and change team roles.",
        "annotator": False,
        "reviewer": False,
        "project_admin": False,
    },
]

ROLE_MATRIX_NOTES = [
    "Team creation and member management are currently reserved for system admins.",
    "Reviewer can open the labeling page, but it is read-only in the current implementation.",
    "Removing a member deactivates the account. Re-invite the same email to restore access.",
]

TEAM_SETTINGS_NOTICE_MESSAGES = {
    "invited": "Member access was updated.",
    "role_updated": "Member role was updated.",
    "removed": "Member access was disabled.",
}

TEAM_SETTINGS_ERROR_MESSAGES = {
    "system_admin_managed_separately": "System admin accounts cannot be changed from team settings.",
}

MANAGED_TEAM_ROLES = {
    UserRole.annotator,
    UserRole.reviewer,
    UserRole.project_admin,
}


def _normalize_team_member_role(role: str | None) -> UserRole:
    role_value = (role or UserRole.annotator.value).strip().lower()
    try:
        user_role = UserRole(role_value)
    except ValueError:
        return UserRole.annotator
    if user_role not in MANAGED_TEAM_ROLES:
        return UserRole.annotator
    return user_role


def _team_settings_redirect_url(
    request: Request,
    team_id: int,
    *,
    notice: str | None = None,
    error: str | None = None,
) -> str:
    params: dict[str, str] = {}
    if notice in TEAM_SETTINGS_NOTICE_MESSAGES:
        params["notice"] = str(notice)
    if error in TEAM_SETTINGS_ERROR_MESSAGES:
        params["error"] = str(error)

    base_url = str(request.url_for("team_settings", team_id=team_id))
    if not params:
        return base_url
    return f"{base_url}?{urlencode(params)}"


@router.get("/teams", response_class=HTMLResponse, name="teams_index")
def teams_index(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.system_admin)),
):
    teams = db.execute(select(Team).order_by(Team.id.asc())).scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="teams_index.html",
        context={
            "request": request,
            "teams": teams,
            "current_user": current_user,
        },
    )


@router.post("/teams/create", response_class=HTMLResponse, name="create_team")
def create_team(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.system_admin)),
):
    team = Team(
        name=name.strip(),
        is_active=True,
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    return RedirectResponse(
        url=request.url_for("team_settings", team_id=team.id),
        status_code=303,
    )


@router.get(
    "/teams/{team_id}/settings",
    response_class=HTMLResponse,
    name="team_settings",
)
def team_settings(
    request: Request,
    team_id: int,
    notice: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.system_admin)),
):
    team = db.get(Team, team_id)
    if not team:
        return HTMLResponse(status_code=404, content="Team not found")

    members = (
        db.execute(
            select(User)
            .where(User.team_id == team.id)
            .order_by(User.id.asc())
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="team_settings.html",
        context={
            "request": request,
            "team": team,
            "members": members,
            "role_display_names": ROLE_DISPLAY_NAMES,
            "role_matrix_columns": ROLE_MATRIX_COLUMNS,
            "role_matrix_rows": ROLE_MATRIX_ROWS,
            "role_matrix_notes": ROLE_MATRIX_NOTES,
            "member_notice": TEAM_SETTINGS_NOTICE_MESSAGES.get(notice),
            "member_error": TEAM_SETTINGS_ERROR_MESSAGES.get(error),
            "current_user": current_user,
        },
    )


@router.post(
    "/teams/{team_id}/invite",
    response_class=HTMLResponse,
    name="invite_team_member",
)
def invite_team_member(
    request: Request,
    team_id: int,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("annotator"),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.system_admin)),
):
    team = db.get(Team, team_id)
    if not team:
        return HTMLResponse(status_code=404, content="Team not found")

    email_norm = email.strip().lower()
    user_role = _normalize_team_member_role(role)

    user = (
        db.execute(select(User).where(User.email == email_norm))
        .scalar_one_or_none()
    )

    if user:
        if user.role == UserRole.system_admin:
            return RedirectResponse(
                url=_team_settings_redirect_url(
                    request,
                    team.id,
                    error="system_admin_managed_separately",
                ),
                status_code=303,
            )
        user.team_id = team.id
        user.is_active = True
        if password:
            user.password_hash = hash_password(password)
        user.role = user_role
    else:
        user = User(
            email=email_norm,
            password_hash=hash_password(password),
            role=user_role,
            is_active=True,
            team_id=team.id,
        )
        db.add(user)

    db.commit()

    return RedirectResponse(
        url=_team_settings_redirect_url(request, team.id, notice="invited"),
        status_code=303,
    )


@router.post(
    "/teams/{team_id}/members/{user_id}/role",
    response_class=HTMLResponse,
    name="update_team_member_role",
)
def update_team_member_role(
    request: Request,
    team_id: int,
    user_id: int,
    role: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.system_admin)),
):
    team = db.get(Team, team_id)
    if not team:
        return HTMLResponse(status_code=404, content="Team not found")

    user = db.get(User, user_id)
    if not user or user.team_id != team.id or not user.is_active:
        return HTMLResponse(status_code=404, content="Member not found")

    if user.role == UserRole.system_admin:
        return RedirectResponse(
            url=_team_settings_redirect_url(
                request,
                team.id,
                error="system_admin_managed_separately",
            ),
            status_code=303,
        )

    user.role = _normalize_team_member_role(role)
    db.add(user)
    db.commit()

    return RedirectResponse(
        url=_team_settings_redirect_url(request, team.id, notice="role_updated"),
        status_code=303,
    )


@router.post(
    "/teams/{team_id}/members/{user_id}/remove",
    response_class=HTMLResponse,
    name="remove_team_member",
)
def remove_team_member(
    request: Request,
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(UserRole.system_admin)),
):
    team = db.get(Team, team_id)
    if not team:
        return HTMLResponse(status_code=404, content="Team not found")

    user = db.get(User, user_id)
    if not user or user.team_id != team.id or not user.is_active:
        return HTMLResponse(status_code=404, content="Member not found")

    if user.role == UserRole.system_admin:
        return RedirectResponse(
            url=_team_settings_redirect_url(
                request,
                team.id,
                error="system_admin_managed_separately",
            ),
            status_code=303,
        )

    user.is_active = False
    db.add(user)
    db.commit()

    return RedirectResponse(
        url=_team_settings_redirect_url(request, team.id, notice="removed"),
        status_code=303,
    )
