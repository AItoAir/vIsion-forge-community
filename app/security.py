from __future__ import annotations

import hashlib
from hmac import compare_digest
from typing import Callable

try:
    from argon2 import PasswordHasher, exceptions as argon2_exceptions
    from argon2.low_level import Type
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "argon2-cffi is required for password hashing. Rebuild the app image or reinstall requirements/base.txt."
    ) from exc
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Project, User, UserRole


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


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    request.state.user = user
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    request.state.user = user
    return user


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
