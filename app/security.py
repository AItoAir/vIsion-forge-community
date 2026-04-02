from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any
from typing import Callable

try:
    from argon2 import PasswordHasher, exceptions as argon2_exceptions
    from argon2.low_level import Type
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "argon2-cffi is required for password hashing. Rebuild the app image or reinstall requirements/base.txt."
    ) from exc
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import ApiKey, Project, User, UserRole


_ARGON2_INVALID_HASH_ERRORS = tuple(
    error_type
    for error_type in (
        getattr(argon2_exceptions, "InvalidHashError", None),
        getattr(argon2_exceptions, "InvalidHash", None),
    )
    if isinstance(error_type, type)
)
_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
API_KEY_TOKEN_PREFIX = "fpk"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _legacy_password_salt() -> str:
    salt = (settings.password_salt or "").strip()
    if not salt:
        raise RuntimeError(
            "PASSWORD_SALT must be configured to verify legacy SHA-256 password hashes."
        )
    return salt


def _legacy_hash_password(password: str) -> str:
    data = f"{_legacy_password_salt()}:{password}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _is_argon2_password_hash(hashed: str) -> bool:
    return hashed.startswith("$argon2")


def _argon2_hash_needs_rehash(hashed: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(hashed)
    except _ARGON2_INVALID_HASH_ERRORS:
        return True


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password_and_rehash(password: str, hashed: str) -> tuple[bool, str | None]:
    normalized_hash = (hashed or "").strip()
    if not normalized_hash:
        return False, None

    if _is_argon2_password_hash(normalized_hash):
        try:
            password_valid = _PASSWORD_HASHER.verify(normalized_hash, password)
        except _ARGON2_INVALID_HASH_ERRORS:
            return False, None
        except argon2_exceptions.VerificationError:
            return False, None
        if not password_valid:
            return False, None
        if _argon2_hash_needs_rehash(normalized_hash):
            return True, hash_password(password)
        return True, None

    if compare_digest(_legacy_hash_password(password), normalized_hash):
        return True, hash_password(password)

    return False, None


def verify_password(password: str, hashed: str) -> bool:
    password_valid, _rehash = verify_password_and_rehash(password, hashed)
    return password_valid


def password_hash_needs_rehash(hashed: str) -> bool:
    normalized_hash = (hashed or "").strip()
    if not normalized_hash:
        return True
    if not _is_argon2_password_hash(normalized_hash):
        return True
    return _argon2_hash_needs_rehash(normalized_hash)


def _request_session(request: Request) -> dict[str, Any]:
    session = request.scope.get("session")
    return session if isinstance(session, dict) else {}


def api_key_from_request(request: Request) -> str | None:
    authorization = (request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None

    token = (request.headers.get("x-api-key") or "").strip()
    return token or None


def request_uses_api_key_auth(request: Request) -> bool:
    return api_key_from_request(request) is not None


def _split_api_key(raw_token: str) -> tuple[str, str]:
    normalized = (raw_token or "").strip()
    prefix = f"{API_KEY_TOKEN_PREFIX}_"
    if not normalized.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    body = normalized[len(prefix) :]

    key_id, separator, secret = body.partition(".")
    if not separator or not key_id or not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return key_id, secret


def hash_api_key_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_api_key_token() -> tuple[str, str, str, str]:
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    raw_token = f"{API_KEY_TOKEN_PREFIX}_{key_id}.{secret}"
    return raw_token, key_id, hash_api_key_secret(secret), secret[-4:]


def create_api_key(
    db: Session,
    *,
    user: User,
    name: str,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    raw_token, key_id, secret_hash, secret_last_four = generate_api_key_token()
    api_key = ApiKey(
        user_id=user.id,
        name=name.strip(),
        key_id=key_id,
        secret_hash=secret_hash,
        secret_last_four=secret_last_four,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(api_key)
    db.flush()
    return api_key, raw_token


def _api_key_not_found_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )


def authenticate_api_key(
    request: Request,
    db: Session,
) -> tuple[ApiKey | None, User | None]:
    raw_token = api_key_from_request(request)
    if not raw_token:
        return None, None

    key_id, provided_secret = _split_api_key(raw_token)
    api_key = db.execute(
        select(ApiKey).where(ApiKey.key_id == key_id, ApiKey.is_active.is_(True))
    ).scalar_one_or_none()
    if api_key is None:
        raise _api_key_not_found_error()

    if api_key.revoked_at is not None:
        raise _api_key_not_found_error()

    if api_key.expires_at is not None and api_key.expires_at <= _utcnow():
        raise _api_key_not_found_error()

    if not compare_digest(api_key.secret_hash, hash_api_key_secret(provided_secret)):
        raise _api_key_not_found_error()

    user = api_key.user
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    api_key.last_used_at = _utcnow()
    db.add(api_key)
    request.state.api_key = api_key
    request.state.user = user
    return api_key, user


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user_id = _request_session(request).get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    request.state.user = user
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = _request_session(request).get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    request.state.user = user
    return user


def get_api_or_session_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    _api_key, api_user = authenticate_api_key(request, db)
    if api_user is not None:
        return api_user
    return get_current_user(request, db)


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not roles:
            return current_user

        if current_user.role == UserRole.system_admin:
            return current_user

        allowed = any(current_user.role == r for r in roles)
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return current_user

    return dependency


def require_api_roles(*roles: UserRole) -> Callable[[User], User]:
    def dependency(current_user: User = Depends(get_api_or_session_user)) -> User:
        if not roles:
            return current_user

        if current_user.role == UserRole.system_admin:
            return current_user

        allowed = any(current_user.role == role for role in roles)
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return current_user

    return dependency


def ensure_project_team_access(project: Project, current_user: User) -> None:
    """
    Enforce that the project belongs to the same team as current_user
    (unless current_user is system_admin).
    """
    if current_user.role == UserRole.system_admin:
        return

    owner = project.owner
    if owner is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if owner.team_id is None or current_user.team_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if owner.team_id != current_user.team_id:
        # Hide existence from other teams
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
