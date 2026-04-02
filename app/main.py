from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from urllib.parse import quote
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exception_handlers import (
    http_exception_handler as fastapi_http_exception_handler,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .config import (
    configured_trusted_proxy_hosts,
    normalized_session_cookie_same_site,
    settings,
)
from .csrf import ensure_csrf_token, request_passes_csrf
from .database import Base, db_session, engine
from .extensions import apply_configured_extension_hooks
from .models import Annotation, Item, ItemKind, Team, User, UserRole
from .routers import (
    api_annotations,
    api_export,
    api_notifications,
    api_sam,
    api_v1,
    auth,
    web_items,
    web_projects,
    web_review,
    web_teams,
    ws_collaboration,
)
from .security import hash_password, request_uses_api_key_auth, verify_password_and_rehash
from .services.sam2_jobs import sam2_track_job_runner


logger = structlog.get_logger(__name__)


def _configured_bootstrap_default_admin_accounts() -> tuple[tuple[str, str], ...]:
    if not settings.bootstrap_default_admin_enabled:
        return ()

    email = (settings.bootstrap_default_admin_email or "").strip().lower()
    password = settings.bootstrap_default_admin_password or ""
    if not email or not password:
        logger.warning(
            "Bootstrap admin is enabled but credentials are incomplete; skipping bootstrap admin creation"
        )
        return ()

    return ((email, password),)


def validate_runtime_security_settings() -> None:
    insecure_secret_keys = {
        "CHANGE_ME",
        "change_me_dev",
        "change_me_stg",
        "change_me_prod",
        "dev-insecure-session-key-change-before-production",
    }
    insecure_password_salts = {
        "frame-pin-salt",
        "dev-insecure-password-salt-change-before-production",
    }
    session_cookie_same_site = normalized_session_cookie_same_site(
        settings.session_cookie_same_site
    )

    if settings.trust_proxy_headers:
        configured_trusted_proxy_hosts(settings.trusted_proxy_ips)

    if session_cookie_same_site == "none" and not settings.session_cookie_https_only:
        raise RuntimeError(
            "SESSION_COOKIE_SAME_SITE=none requires SESSION_COOKIE_HTTPS_ONLY=1."
        )

    if settings.env != "dev":
        if settings.secret_key in insecure_secret_keys:
            raise RuntimeError(
                "SECRET_KEY must be overridden before running outside dev."
            )
        if settings.password_salt and settings.password_salt in insecure_password_salts:
            raise RuntimeError(
                "PASSWORD_SALT must be overridden before using legacy SHA-256 password verification outside dev."
            )
        if not settings.session_cookie_https_only:
            logger.warning(
                "SESSION_COOKIE_HTTPS_ONLY is disabled outside dev; secure cookies are strongly recommended for self-hosted deployments"
            )


def configured_cors_allow_origins() -> list[str]:
    origins = [
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    ]
    if "*" in origins:
        raise RuntimeError(
            "CORS_ALLOW_ORIGINS must use an explicit allowlist. Wildcard origins are not supported."
        )
    return origins


STARTUP_DB_MAX_WAIT_SECONDS = 60
STARTUP_DB_RETRY_INTERVAL_SECONDS = 2
_database_initialized = False
_database_init_lock = Lock()


def bootstrap_default_admin() -> None:
    default_admin_accounts = _configured_bootstrap_default_admin_accounts()
    if not default_admin_accounts:
        return

    with db_session() as db:
        default_team = db.execute(
            select(Team).where(Team.name == "Default team").limit(1)
        ).scalar_one_or_none()

        admin_users = {
            email: db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            for email, _password in default_admin_accounts
        }

        reference_team_id = next(
            (
                user.team_id
                for user in admin_users.values()
                if user is not None and user.team_id is not None
            ),
            None,
        )

        if reference_team_id is None:
            if default_team is None:
                default_team = Team(
                    name="Default team",
                    is_active=True,
                )
                db.add(default_team)
                db.flush()
            reference_team_id = default_team.id

        created_admins: list[str] = []
        updated_admins: list[str] = []

        for email, password in default_admin_accounts:
            user = admin_users[email]
            if user is None:
                user = User(
                    email=email,
                    password_hash=hash_password(password),
                    role=UserRole.system_admin,
                    team_id=reference_team_id,
                    is_active=True,
                )
                db.add(user)
                created_admins.append(email)
                continue

            changed = False
            if user.role != UserRole.system_admin:
                user.role = UserRole.system_admin
                changed = True
            if user.team_id != reference_team_id:
                user.team_id = reference_team_id
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
            password_verified, upgraded_password_hash = verify_password_and_rehash(
                password,
                user.password_hash,
            )
            if not password_verified:
                user.password_hash = hash_password(password)
                changed = True
            elif upgraded_password_hash is not None:
                user.password_hash = upgraded_password_hash
                changed = True
            if changed:
                db.add(user)
                updated_admins.append(email)

        if created_admins or updated_admins:
            logger.warning(
                "Ensured bootstrap admin accounts",
                created_admins=created_admins,
                updated_admins=updated_admins,
                team_id=reference_team_id,
            )


def _ensure_missing_column(
    *,
    connection,
    table_name: str,
    column_name: str,
    ddl: str,
) -> None:
    existing_columns = {
        column["name"] for column in inspect(connection).get_columns(table_name)
    }
    if column_name in existing_columns:
        return
    connection.execute(text(ddl))


def _annotation_compaction_signature(annotation: Annotation) -> tuple:
    return (
        annotation.label_class_id,
        annotation.status.value,
        annotation.x1,
        annotation.y1,
        annotation.x2,
        annotation.y2,
        annotation.points_json or "",
    )


def ensure_runtime_schema() -> None:
    with engine.begin() as conn:
        table_names = set(inspect(conn).get_table_names())

        if "user" in table_names:
            _ensure_missing_column(
                connection=conn,
                table_name="user",
                column_name="name",
                ddl='ALTER TABLE "user" ADD COLUMN name VARCHAR(255)',
            )
            _ensure_missing_column(
                connection=conn,
                table_name="user",
                column_name="department",
                ddl='ALTER TABLE "user" ADD COLUMN department VARCHAR(255)',
            )

        if "label_class" in table_names:
            _ensure_missing_column(
                connection=conn,
                table_name="label_class",
                column_name="default_use_fixed_box",
                ddl="ALTER TABLE label_class ADD COLUMN default_use_fixed_box BOOLEAN DEFAULT FALSE NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="label_class",
                column_name="default_box_w",
                ddl="ALTER TABLE label_class ADD COLUMN default_box_w INTEGER",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="label_class",
                column_name="default_box_h",
                ddl="ALTER TABLE label_class ADD COLUMN default_box_h INTEGER",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="label_class",
                column_name="default_propagation_frames",
                ddl="ALTER TABLE label_class ADD COLUMN default_propagation_frames INTEGER DEFAULT 0 NOT NULL",
            )

        if "annotation" in table_names:
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="points_json",
                ddl="ALTER TABLE annotation ADD COLUMN points_json TEXT",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="client_uid",
                ddl="ALTER TABLE annotation ADD COLUMN client_uid VARCHAR(64)",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="is_occluded",
                ddl="ALTER TABLE annotation ADD COLUMN is_occluded BOOLEAN DEFAULT FALSE NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="is_truncated",
                ddl="ALTER TABLE annotation ADD COLUMN is_truncated BOOLEAN DEFAULT FALSE NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="is_outside",
                ddl="ALTER TABLE annotation ADD COLUMN is_outside BOOLEAN DEFAULT FALSE NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="is_lost",
                ddl="ALTER TABLE annotation ADD COLUMN is_lost BOOLEAN DEFAULT FALSE NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="annotation",
                column_name="updated_by",
                ddl="ALTER TABLE annotation ADD COLUMN updated_by INTEGER",
            )

        if "item" in table_names:
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="annotation_revision",
                ddl="ALTER TABLE item ADD COLUMN annotation_revision INTEGER DEFAULT 0 NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="media_conversion_status",
                ddl="ALTER TABLE item ADD COLUMN media_conversion_status VARCHAR(32) DEFAULT 'not_required' NOT NULL",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="media_conversion_error",
                ddl="ALTER TABLE item ADD COLUMN media_conversion_error TEXT",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="media_conversion_profile",
                ddl="ALTER TABLE item ADD COLUMN media_conversion_profile VARCHAR(255)",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="media_conversion_size_bytes",
                ddl="ALTER TABLE item ADD COLUMN media_conversion_size_bytes BIGINT",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="media_conversion_last_accessed_at",
                ddl="ALTER TABLE item ADD COLUMN media_conversion_last_accessed_at TIMESTAMP WITH TIME ZONE",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="item",
                column_name="frame_rate_mode",
                ddl="ALTER TABLE item ADD COLUMN frame_rate_mode VARCHAR(16)",
            )

        if "review_comment" in table_names:
            _ensure_missing_column(
                connection=conn,
                table_name="review_comment",
                column_name="mentions_json",
                ddl="ALTER TABLE review_comment ADD COLUMN mentions_json TEXT",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="review_comment",
                column_name="annotation_revision",
                ddl="ALTER TABLE review_comment ADD COLUMN annotation_revision INTEGER",
            )
            _ensure_missing_column(
                connection=conn,
                table_name="review_comment",
                column_name="snapshot_json",
                ddl="ALTER TABLE review_comment ADD COLUMN snapshot_json TEXT",
            )

        if "region_comment" in table_names:
            _ensure_missing_column(
                connection=conn,
                table_name="region_comment",
                column_name="mentions_json",
                ddl="ALTER TABLE region_comment ADD COLUMN mentions_json TEXT",
            )


def backfill_runtime_annotation_metadata() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "annotation" not in table_names:
            return

        annotation_columns = {
            column["name"] for column in inspector.get_columns("annotation")
        }

        if "client_uid" in annotation_columns:
            annotation_ids_missing_uid = conn.execute(
                text(
                    "SELECT id FROM annotation "
                    "WHERE client_uid IS NULL OR client_uid = ''"
                )
            ).scalars().all()
            if annotation_ids_missing_uid:
                conn.execute(
                    text(
                        "UPDATE annotation "
                        "SET client_uid = :client_uid "
                        "WHERE id = :annotation_id"
                    ),
                    [
                        {
                            "annotation_id": annotation_id,
                            "client_uid": uuid4().hex,
                        }
                        for annotation_id in annotation_ids_missing_uid
                    ],
                )

        if {"created_by", "updated_by"}.issubset(annotation_columns):
            conn.execute(
                text(
                    "UPDATE annotation "
                    "SET updated_by = created_by "
                    "WHERE updated_by IS NULL AND created_by IS NOT NULL"
                )
            )


def backfill_runtime_item_media_metadata() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "item" not in table_names:
            return

        item_columns = {column["name"] for column in inspector.get_columns("item")}
        required_columns = {
            "kind",
            "media_conversion_status",
            "frame_rate_mode",
        }
        if not required_columns.issubset(item_columns):
            return

        conn.execute(
            text(
                "UPDATE item "
                "SET media_conversion_status = 'not_required' "
                "WHERE kind != 'video' "
                "AND (media_conversion_status IS NULL OR media_conversion_status = '')"
            )
        )
        conn.execute(
            text(
                "UPDATE item "
                "SET media_conversion_status = 'pending' "
                "WHERE kind = 'video' "
                "AND (media_conversion_status IS NULL OR media_conversion_status = '' OR media_conversion_status = 'not_required')"
            )
        )
        conn.execute(
            text(
                "UPDATE item "
                "SET frame_rate_mode = 'unknown' "
                "WHERE kind = 'video' "
                "AND (frame_rate_mode IS NULL OR frame_rate_mode = '')"
            )
        )
        if "media_conversion_last_accessed_at" in item_columns:
            conn.execute(
                text(
                    "UPDATE item "
                    "SET media_conversion_last_accessed_at = COALESCE(updated_at, created_at) "
                    "WHERE kind = 'video' "
                    "AND media_conversion_status = 'ready' "
                    "AND media_conversion_last_accessed_at IS NULL"
                )
            )


def validate_legacy_password_hash_support() -> None:
    if (settings.password_salt or "").strip():
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        if "user" not in table_names:
            return

        user_columns = {column["name"] for column in inspector.get_columns("user")}
        if "password_hash" not in user_columns:
            return

        legacy_user_id = conn.execute(
            text(
                'SELECT id FROM "user" '
                "WHERE password_hash IS NOT NULL "
                "AND password_hash <> '' "
                "AND password_hash NOT LIKE :argon2_prefix "
                "LIMIT 1"
            ),
            {"argon2_prefix": "$argon2%"},
        ).scalar_one_or_none()
        if legacy_user_id is not None:
            raise RuntimeError(
                "PASSWORD_SALT must be configured before upgrading deployments that still store legacy SHA-256 password hashes."
            )


def _run_startup_database_tasks() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    backfill_runtime_annotation_metadata()
    backfill_runtime_item_media_metadata()
    validate_legacy_password_hash_support()
    bootstrap_default_admin()


def initialize_database(*, retry_on_failure: bool = True) -> None:
    global _database_initialized

    if _database_initialized:
        return

    with _database_init_lock:
        if _database_initialized:
            return

        attempt = 1
        deadline = time.monotonic() + STARTUP_DB_MAX_WAIT_SECONDS

        while True:
            try:
                _run_startup_database_tasks()
                _database_initialized = True
                return
            except OperationalError as exc:
                if not retry_on_failure or time.monotonic() >= deadline:
                    raise

                logger.warning(
                    "Database unavailable during startup; retrying",
                    attempt=attempt,
                    retry_in_seconds=STARTUP_DB_RETRY_INTERVAL_SECONDS,
                    error=str(getattr(exc, "orig", exc)),
                )
                time.sleep(STARTUP_DB_RETRY_INTERVAL_SECONDS)
                attempt += 1


def _database_unavailable_response(request: Request):
    detail = "Database temporarily unavailable. Retry in a moment."
    if request.url.path.startswith("/api"):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": detail},
        )
    return PlainTextResponse(
        content=detail,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def compact_legacy_video_annotations() -> None:
    with db_session() as db:
        video_items = db.execute(
            select(Item).where(Item.kind == ItemKind.video)
        ).scalars().all()

        for item in video_items:
            annotations = db.execute(
                select(Annotation)
                .where(Annotation.item_id == item.id)
                .order_by(Annotation.track_id, Annotation.frame_index, Annotation.id)
            ).scalars().all()

            if not annotations:
                continue

            if any((annotation.propagation_frames or 0) > 0 for annotation in annotations):
                continue

            tracked_annotations: dict[int, list[Annotation]] = {}
            changed = False
            to_delete: list[Annotation] = []

            for annotation in annotations:
                if annotation.track_id is None or annotation.frame_index is None:
                    if annotation.propagation_frames not in {None, 0}:
                        annotation.propagation_frames = 0
                        db.add(annotation)
                        changed = True
                    continue
                tracked_annotations.setdefault(annotation.track_id, []).append(annotation)

            for track_annotations in tracked_annotations.values():
                track_annotations.sort(
                    key=lambda current: (current.frame_index or 0, current.id)
                )
                run_start = track_annotations[0]
                prev = track_annotations[0]
                run_annotations = [track_annotations[0]]

                for current in track_annotations[1:]:
                    same_run = (
                        current.frame_index == (prev.frame_index or 0) + 1
                        and _annotation_compaction_signature(current)
                        == _annotation_compaction_signature(prev)
                    )

                    if same_run:
                        run_annotations.append(current)
                        prev = current
                        continue

                    new_propagation_frames = (
                        (prev.frame_index or 0) - (run_start.frame_index or 0)
                    )
                    if (run_start.propagation_frames or 0) != new_propagation_frames:
                        run_start.propagation_frames = new_propagation_frames
                        db.add(run_start)
                    if len(run_annotations) > 1:
                        to_delete.extend(run_annotations[1:])
                        changed = True

                    run_start = current
                    prev = current
                    run_annotations = [current]

                new_propagation_frames = (
                    (prev.frame_index or 0) - (run_start.frame_index or 0)
                )
                if (run_start.propagation_frames or 0) != new_propagation_frames:
                    run_start.propagation_frames = new_propagation_frames
                    db.add(run_start)
                if len(run_annotations) > 1:
                    to_delete.extend(run_annotations[1:])
                    changed = True

            for redundant_annotation in to_delete:
                db.delete(redundant_annotation)

            if changed:
                logger.info(
                    "Compacted legacy expanded video annotations",
                    item_id=item.id,
                    removed_rows=len(to_delete),
                )


def create_core_app() -> FastAPI:
    return FastAPI(
        title="FramePin",
        description=(
            "Image & Video Annotation for Computer Vision. "
            "Pin decisions to the exact frame."
        ),
    )


class RequestGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/static/uploads" or request.url.path.startswith(
            "/static/uploads/"
        ):
            return PlainTextResponse(
                content="Not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if request.url.path.startswith("/static") or request.url.path == "/healthz":
            return await call_next(request)

        api_key_request = request.url.path.startswith("/api/v1") and request_uses_api_key_auth(request)
        if not api_key_request:
            ensure_csrf_token(request)
        if not api_key_request and not request_passes_csrf(request):
            detail = "CSRF validation failed."
            if request.url.path.startswith("/api"):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": detail},
                )
            return PlainTextResponse(
                content=detail,
                status_code=status.HTTP_403_FORBIDDEN,
            )

        try:
            initialize_database(retry_on_failure=False)
        except OperationalError as exc:
            logger.warning(
                "Database unavailable before request",
                method=request.method,
                path=request.url.path,
                error=str(getattr(exc, "orig", exc)),
            )
            return _database_unavailable_response(request)

        return await call_next(request)


def register_core_middlewares(app: FastAPI) -> None:
    cors_allow_origins = configured_cors_allow_origins()
    session_cookie_same_site = normalized_session_cookie_same_site(
        settings.session_cookie_same_site
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestGuardMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_cookie_max_age_seconds,
        same_site=session_cookie_same_site,
        https_only=settings.session_cookie_https_only,
    )
    if settings.trust_proxy_headers:
        app.add_middleware(
            ProxyHeadersMiddleware,
            trusted_hosts=configured_trusted_proxy_hosts(settings.trusted_proxy_ips),
        )


def register_core_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FastAPIHTTPException)
    async def auth_exception_handler(
        request: Request,
        exc: FastAPIHTTPException,
    ):
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            if request.url.path.startswith("/api"):
                return await fastapi_http_exception_handler(request, exc)

            login_url = request.url_for("login")
            next_url = str(request.url)
            redirect_target = f"{login_url}?next={quote(next_url, safe='')}"

            return RedirectResponse(
                url=redirect_target,
                status_code=status.HTTP_302_FOUND,
            )

        return await fastapi_http_exception_handler(request, exc)

    @app.exception_handler(OperationalError)
    async def database_operational_error_handler(
        request: Request,
        exc: OperationalError,
    ):
        logger.warning(
            "Database unavailable while handling request",
            method=request.method,
            path=request.url.path,
            error=str(getattr(exc, "orig", exc)),
        )
        return _database_unavailable_response(request)


def register_core_static_mounts(app: FastAPI) -> None:
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def register_core_routers(app: FastAPI) -> None:
    app.include_router(auth.router)
    app.include_router(web_teams.router)
    app.include_router(web_projects.router)
    app.include_router(web_items.router)
    app.include_router(web_review.router)
    app.include_router(api_annotations.router, prefix="/api")
    app.include_router(api_notifications.router, prefix="/api")
    app.include_router(api_sam.router, prefix="/api")
    app.include_router(api_export.router, prefix="/api")
    app.include_router(api_v1.router)
    app.include_router(ws_collaboration.router)


def register_core_routes(app: FastAPI) -> None:
    register_core_static_mounts(app)
    register_core_routers(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}


def apply_extension_hooks(app: FastAPI) -> None:
    apply_configured_extension_hooks(app)


def create_app() -> FastAPI:
    validate_runtime_security_settings()

    try:
        initialize_database()
    except OperationalError as exc:
        logger.warning(
            "Database unavailable after startup retries; serving 503 until it recovers",
            error=str(getattr(exc, "orig", exc)),
        )

    app = create_core_app()
    register_core_middlewares(app)
    register_core_exception_handlers(app)
    register_core_routes(app)
    apply_extension_hooks(app)

    @app.on_event("startup")
    def start_background_workers() -> None:
        sam2_track_job_runner.start()

    @app.on_event("shutdown")
    def stop_background_workers() -> None:
        sam2_track_job_runner.stop()

    logger.info("FramePin app initialized", env=settings.env)
    return app


app = create_app()
