from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    DEFAULT_BOOTSTRAP_ADMIN_EMAIL,
    DEFAULT_BOOTSTRAP_ADMIN_PASSWORD,
    settings,
)
from ..database import get_db
from ..models import ApiKey, User
from ..security import (
    create_api_key,
    get_current_user,
    get_optional_user,
    hash_password,
    verify_password,
    verify_password_and_rehash,
)

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent.parent / "templates")
)

MY_PAGE_API_KEY_TOKEN_SESSION_KEY = "_my_page_api_key_token"
MY_PAGE_API_KEY_NAME_SESSION_KEY = "_my_page_api_key_name"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _bootstrap_admin_is_configured() -> bool:
    if not settings.bootstrap_default_admin_enabled:
        return False
    email = (settings.bootstrap_default_admin_email or "").strip()
    password = settings.bootstrap_default_admin_password or ""
    return bool(email and password)


def _uses_repository_default_bootstrap_admin() -> bool:
    if not _bootstrap_admin_is_configured():
        return False
    email = (settings.bootstrap_default_admin_email or "").strip().lower()
    password = settings.bootstrap_default_admin_password or ""
    return (
        email == DEFAULT_BOOTSTRAP_ADMIN_EMAIL
        and password == DEFAULT_BOOTSTRAP_ADMIN_PASSWORD
    )


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False

    normalized = host.strip().lower()
    if normalized == "localhost":
        return True

    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _request_is_local(request: Request) -> bool:
    if settings.env == "dev":
        return True

    client_host = request.client.host if request.client else None
    return _is_loopback_host(request.url.hostname) or _is_loopback_host(client_host)


def _show_default_admin_hint(request: Request) -> bool:
    return _uses_repository_default_bootstrap_admin() and _request_is_local(request)


def _login_template_context(
    request: Request,
    *,
    error: str | None,
    email: str,
    next_url: str,
) -> dict[str, object]:
    return {
        "request": request,
        "error": error,
        "email": email,
        "next": next_url,
        "bootstrap_admin_configured": _bootstrap_admin_is_configured(),
        "show_default_admin_hint": _show_default_admin_hint(request),
        "default_admin_email": DEFAULT_BOOTSTRAP_ADMIN_EMAIL,
        "default_admin_password": DEFAULT_BOOTSTRAP_ADMIN_PASSWORD,
    }


def _my_page_template_context(
    request: Request,
    *,
    current_user: User,
    api_keys: list[ApiKey] | None = None,
    profile_error: str | None = None,
    profile_success: str | None = None,
    password_error: str | None = None,
    password_success: str | None = None,
    api_key_error: str | None = None,
    api_key_success: str | None = None,
    api_key_name_value: str | None = None,
    generated_api_key_token: str | None = None,
    generated_api_key_name: str | None = None,
    profile_name_value: str | None = None,
    profile_department_value: str | None = None,
) -> dict[str, object]:
    return {
        "request": request,
        "current_user": current_user,
        "api_keys": api_keys or [],
        "profile_error": profile_error,
        "profile_success": profile_success,
        "password_error": password_error,
        "password_success": password_success,
        "api_key_error": api_key_error,
        "api_key_success": api_key_success,
        "api_key_name_value": api_key_name_value or "",
        "generated_api_key_token": generated_api_key_token,
        "generated_api_key_name": generated_api_key_name,
        "developer_api_base_url": f"{str(request.base_url).rstrip('/')}/api/v1",
        "profile_name_value": (
            current_user.name if profile_name_value is None else profile_name_value
        )
        or "",
        "profile_department_value": (
            current_user.department
            if profile_department_value is None
            else profile_department_value
        )
        or "",
        "role_label": current_user.role.value.replace("_", " ").title(),
    }


def _normalize_profile_field(
    value: str | None,
    *,
    field_label: str,
) -> tuple[str | None, str | None]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None, None
    if len(cleaned) > 255:
        return None, f"{field_label} must be 255 characters or fewer."
    return cleaned, None


def _list_user_api_keys(db: Session, user_id: int) -> list[ApiKey]:
    return (
        db.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        )
        .scalars()
        .all()
    )


@router.get("/login", response_class=HTMLResponse, name="login")
def login_page(
    request: Request, current_user: User | None = Depends(get_optional_user)
):
    if current_user:
        return RedirectResponse(url="/", status_code=302)

    next_url = request.query_params.get("next") or "/"
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=_login_template_context(
            request,
            error=None,
            email="",
            next_url=next_url,
        ),
    )


@router.post("/login", response_class=HTMLResponse)
def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next_url: str | None = Form(None),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    user = db.execute(select(User).where(User.email == email_norm)).scalar_one_or_none()
    redirect_target = next_url or "/"
    if not redirect_target.startswith("/"):
        redirect_target = "/"

    password_valid = False
    upgraded_password_hash: str | None = None
    if user is not None:
        password_valid, upgraded_password_hash = verify_password_and_rehash(
            password,
            user.password_hash,
        )

    if not user or not password_valid:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=_login_template_context(
                request,
                error="Invalid email or password.",
                email=email_norm,
                next_url=redirect_target,
            ),
            status_code=401,
        )

    if upgraded_password_hash is not None:
        user.password_hash = upgraded_password_hash
        db.add(user)
        db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(url=redirect_target, status_code=302)


@router.get("/my-page", response_class=HTMLResponse, name="my_page")
def my_page(
    request: Request,
    profile: str | None = None,
    password: str | None = None,
    api_key: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile_success = (
        "Your profile has been updated." if profile == "updated" else None
    )
    password_success = (
        "Your password has been updated." if password == "updated" else None
    )
    api_key_success = None
    if api_key == "created":
        api_key_success = "Developer API key created."
    elif api_key == "revoked":
        api_key_success = "Developer API key revoked."
    return templates.TemplateResponse(
        request=request,
        name="my_page.html",
        context=_my_page_template_context(
            request,
            current_user=current_user,
            api_keys=_list_user_api_keys(db, current_user.id),
            profile_success=profile_success,
            password_success=password_success,
            api_key_success=api_key_success,
            generated_api_key_token=request.session.pop(
                MY_PAGE_API_KEY_TOKEN_SESSION_KEY, None
            ),
            generated_api_key_name=request.session.pop(
                MY_PAGE_API_KEY_NAME_SESSION_KEY, None
            ),
        ),
    )


@router.post(
    "/my-page/profile",
    response_class=HTMLResponse,
    name="update_my_page_profile",
)
def update_my_page_profile(
    request: Request,
    name: str | None = Form(None),
    department: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    clean_name_input = (name or "").strip()
    clean_department_input = (department or "").strip()

    normalized_name, name_error = _normalize_profile_field(
        clean_name_input,
        field_label="Name",
    )
    normalized_department, department_error = _normalize_profile_field(
        clean_department_input,
        field_label="Department",
    )
    profile_error = name_error or department_error
    if profile_error:
        return templates.TemplateResponse(
            request=request,
            name="my_page.html",
            context=_my_page_template_context(
                request,
                current_user=current_user,
                profile_error=profile_error,
                profile_name_value=clean_name_input,
                profile_department_value=clean_department_input,
            ),
            status_code=400,
        )

    current_user.name = normalized_name
    current_user.department = normalized_department
    db.add(current_user)
    db.commit()

    return RedirectResponse(
        url=f"{request.url_for('my_page')}?profile=updated",
        status_code=303,
    )


@router.post(
    "/my-page/password",
    response_class=HTMLResponse,
    name="update_my_page_password",
)
def update_my_page_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    password_error: str | None = None
    if not verify_password(current_password, current_user.password_hash):
        password_error = "Current password is incorrect."
    elif not new_password:
        password_error = "New password is required."
    elif new_password != confirm_password:
        password_error = "New password and confirmation do not match."

    if password_error:
        return templates.TemplateResponse(
            request=request,
            name="my_page.html",
            context=_my_page_template_context(
                request,
                current_user=current_user,
                password_error=password_error,
            ),
            status_code=400,
        )

    current_user.password_hash = hash_password(new_password)
    db.add(current_user)
    db.commit()

    return RedirectResponse(
        url=f"{request.url_for('my_page')}?password=updated",
        status_code=303,
    )


@router.post(
    "/my-page/api-keys",
    response_class=HTMLResponse,
    name="create_my_page_api_key",
)
def create_my_page_api_key(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleaned_name = name.strip()
    if not cleaned_name:
        return templates.TemplateResponse(
            request=request,
            name="my_page.html",
            context=_my_page_template_context(
                request,
                current_user=current_user,
                api_keys=_list_user_api_keys(db, current_user.id),
                api_key_error="API key name is required.",
                api_key_name_value=cleaned_name,
            ),
            status_code=400,
        )
    if len(cleaned_name) > 255:
        return templates.TemplateResponse(
            request=request,
            name="my_page.html",
            context=_my_page_template_context(
                request,
                current_user=current_user,
                api_keys=_list_user_api_keys(db, current_user.id),
                api_key_error="API key name must be 255 characters or fewer.",
                api_key_name_value=cleaned_name,
            ),
            status_code=400,
        )

    _api_key, raw_token = create_api_key(
        db,
        user=current_user,
        name=cleaned_name,
    )
    db.commit()
    request.session[MY_PAGE_API_KEY_TOKEN_SESSION_KEY] = raw_token
    request.session[MY_PAGE_API_KEY_NAME_SESSION_KEY] = cleaned_name
    return RedirectResponse(
        url=f"{request.url_for('my_page')}?api_key=created",
        status_code=303,
    )


@router.post(
    "/my-page/api-keys/{api_key_id}/revoke",
    response_class=HTMLResponse,
    name="revoke_my_page_api_key",
)
def revoke_my_page_api_key(
    request: Request,
    api_key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api_key = db.get(ApiKey, api_key_id)
    if api_key is None or api_key.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.is_active = False
    if api_key.revoked_at is None:
        api_key.revoked_at = _utcnow()
    db.add(api_key)
    db.commit()
    return RedirectResponse(
        url=f"{request.url_for('my_page')}?api_key=revoked",
        status_code=303,
    )


@router.post("/logout", include_in_schema=False, name="logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
