from __future__ import annotations

from pathlib import Path

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
    "Team creation and member invitations are currently reserved for system admins.",
    "Reviewer can open the labeling page, but it is read-only in the current implementation.",
]


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
    role_value = (role or "annotator").strip()
    try:
        user_role = UserRole(role_value)
    except ValueError:
        user_role = UserRole.annotator

    user = (
        db.execute(select(User).where(User.email == email_norm))
        .scalar_one_or_none()
    )

    if user:
        user.team_id = team.id
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
        url=request.url_for("team_settings", team_id=team.id),
        status_code=303,
    )
