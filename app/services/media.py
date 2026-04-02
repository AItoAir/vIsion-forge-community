from __future__ import annotations

import json
import logging
import shutil
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path, PurePosixPath
from threading import Lock

from ..config import settings
from ..database import SessionLocal
from ..models import Item, ItemKind


logger = logging.getLogger(__name__)
_LABELING_PROXY_PROFILE_VERSION = "v2"
_LABELING_PROXY_LOCKS: dict[str, Lock] = {}
_LABELING_PROXY_LOCKS_GUARD = Lock()
_LABELING_PROXY_EXECUTOR: ThreadPoolExecutor | None = None
_LABELING_PROXY_EXECUTOR_GUARD = Lock()
_LABELING_PROXY_JOBS: dict[int, Future] = {}
_LABELING_PROXY_JOBS_GUARD = Lock()
_LABELING_PROXY_STORAGE_GUARD = Lock()

MEDIA_CONVERSION_STATUS_NOT_REQUIRED = "not_required"
MEDIA_CONVERSION_STATUS_PENDING = "pending"
MEDIA_CONVERSION_STATUS_PROCESSING = "processing"
MEDIA_CONVERSION_STATUS_READY = "ready"
MEDIA_CONVERSION_STATUS_FAILED = "failed"

FRAME_RATE_MODE_UNKNOWN = "unknown"
FRAME_RATE_MODE_CFR = "cfr"
FRAME_RATE_MODE_VFR = "vfr"


class MediaProbeError(RuntimeError):
    """Raised when media metadata cannot be extracted."""


@dataclass(slots=True)
class MediaMetadata:
    width: int
    height: int
    duration_sec: float | None
    fps: float | None
    frame_rate_mode: str = FRAME_RATE_MODE_UNKNOWN


@dataclass(slots=True)
class AnnotationMediaState:
    status: str
    status_label: str
    status_badge_class: str
    message: str
    detail: str | None
    ready: bool
    failed: bool
    pending: bool
    frame_rate_mode: str | None
    display_media_path: str


@dataclass(slots=True)
class LabelingProxyStorageCandidate:
    proxy_path: Path
    size_bytes: int
    item: Item | None
    last_accessed_at: datetime | None
    orphaned: bool


@dataclass(slots=True)
class LabelingProxyStorageSummary:
    enabled: bool
    budget_bytes: int | None
    used_bytes: int
    available_budget_bytes: int | None
    usage_ratio: float | None
    over_budget_bytes: int
    filesystem_total_bytes: int | None
    filesystem_free_bytes: int | None
    proxy_file_count: int
    managed_proxy_count: int
    stale_proxy_count: int
    orphan_proxy_count: int
    ttl_days: int | None


class LabelingProxyError(RuntimeError):
    """Raised when a labeling proxy video cannot be generated."""


def _parse_frame_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        fps = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    if fps <= 0:
        return None
    return round(fps, 6)


def _detect_frame_rate_mode(stream: dict) -> str:
    avg_fps = _parse_frame_rate(stream.get("avg_frame_rate"))
    real_fps = _parse_frame_rate(stream.get("r_frame_rate"))
    if avg_fps is None and real_fps is None:
        return FRAME_RATE_MODE_UNKNOWN
    if avg_fps is not None and real_fps is not None:
        tolerance = max(0.01, max(avg_fps, real_fps) * 0.001)
        if abs(avg_fps - real_fps) > tolerance:
            return FRAME_RATE_MODE_VFR
    return FRAME_RATE_MODE_CFR


def probe_media_metadata(file_path: Path, kind: ItemKind) -> MediaMetadata:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise MediaProbeError("ffprobe is not available in the runtime environment") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise MediaProbeError(stderr or "Failed to extract media metadata")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaProbeError("ffprobe returned invalid JSON") from exc

    streams = payload.get("streams") or []
    target_stream = None

    for stream in streams:
        codec_type = stream.get("codec_type")
        if kind == ItemKind.video and codec_type == "video":
            target_stream = stream
            break
        if kind == ItemKind.image and codec_type in {"video", "image"}:
            target_stream = stream
            break

    if target_stream is None and streams:
        target_stream = streams[0]

    if target_stream is None:
        raise MediaProbeError("No media stream found")

    width = int(target_stream.get("width") or 0)
    height = int(target_stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise MediaProbeError("Failed to read media dimensions")

    duration_value = target_stream.get("duration")
    if duration_value in {None, "", "N/A"}:
        duration_value = (payload.get("format") or {}).get("duration")

    duration_sec = None
    if duration_value not in {None, "", "N/A"}:
        try:
            duration_sec = float(duration_value)
        except ValueError:
            duration_sec = None

    fps = None
    frame_rate_mode = FRAME_RATE_MODE_UNKNOWN
    if kind == ItemKind.video:
        fps = _parse_frame_rate(target_stream.get("avg_frame_rate"))
        if fps is None:
            fps = _parse_frame_rate(target_stream.get("r_frame_rate"))
        frame_rate_mode = _detect_frame_rate_mode(target_stream)

    return MediaMetadata(
        width=width,
        height=height,
        duration_sec=duration_sec,
        fps=fps,
        frame_rate_mode=frame_rate_mode,
    )


def static_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "static"


def media_storage_path(relative_path: str) -> Path:
    return _safe_static_path(relative_path)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _labeling_proxy_storage_budget_bytes() -> int | None:
    raw_value = float(getattr(settings, "labeling_proxy_storage_budget_gb", 0) or 0)
    if raw_value <= 0:
        return None
    return int(raw_value * 1024 * 1024 * 1024)


def _labeling_proxy_storage_ttl_days() -> int | None:
    raw_value = int(getattr(settings, "labeling_proxy_storage_ttl_days", 0) or 0)
    return raw_value if raw_value > 0 else None


def _format_storage_bytes(value: int | None) -> str:
    if value is None:
        return "Unlimited"
    if value < 0:
        value = 0
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    unit_index = 0
    while number >= 1024 and unit_index < len(units) - 1:
        number /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(number)} {units[unit_index]}"
    return f"{number:.1f} {units[unit_index]}"


def _normalize_relative_media_path(relative_path: str) -> str:
    return str(relative_path or "").replace("\\", "/").lstrip("/")


def _safe_static_path(relative_path: str) -> Path:
    root = static_root().resolve()
    candidate = (root / _normalize_relative_media_path(relative_path)).resolve()
    if candidate != root and root not in candidate.parents:
        raise MediaProbeError("Media path points outside the static directory")
    return candidate


def resolve_media_source_path(item: Item) -> Path | None:
    try:
        canonical_path = _safe_static_path(item.path)
    except MediaProbeError:
        return None
    return canonical_path if canonical_path.is_file() else None


def _normalize_media_conversion_status(value: str | None, *, kind: ItemKind) -> str:
    allowed = {
        MEDIA_CONVERSION_STATUS_NOT_REQUIRED,
        MEDIA_CONVERSION_STATUS_PENDING,
        MEDIA_CONVERSION_STATUS_PROCESSING,
        MEDIA_CONVERSION_STATUS_READY,
        MEDIA_CONVERSION_STATUS_FAILED,
    }
    normalized = (value or "").strip().lower()
    if normalized in allowed:
        return normalized
    if kind == ItemKind.video and settings.labeling_proxy_enabled:
        return MEDIA_CONVERSION_STATUS_PENDING
    return MEDIA_CONVERSION_STATUS_NOT_REQUIRED


def _normalize_frame_rate_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {FRAME_RATE_MODE_CFR, FRAME_RATE_MODE_VFR}:
        return normalized
    return FRAME_RATE_MODE_UNKNOWN


def _variable_frame_rate_error_message() -> str:
    return (
        "Variable frame rate videos are not supported for frame-accurate labeling yet. "
        "Please convert this video to constant frame rate (CFR) before uploading."
    )


def _missing_media_source_error_message(item: Item) -> str:
    return f"Video source was not found: {item.path}"


def _job_failure_message(exc: Exception) -> str:
    message = str(exc or "").strip()
    return message or "Video conversion failed."


def _labeling_proxy_gop_size() -> int:
    return max(1, int(getattr(settings, "labeling_proxy_gop_size", 1) or 1))


def _labeling_proxy_b_frames() -> int:
    return max(0, int(getattr(settings, "labeling_proxy_b_frames", 0) or 0))


def labeling_proxy_profile_token() -> str:
    crf = max(0, int(getattr(settings, "labeling_proxy_crf", 12) or 12))
    max_width = max(0, int(getattr(settings, "labeling_proxy_max_width", 0) or 0))
    gop_size = _labeling_proxy_gop_size()
    b_frames = _labeling_proxy_b_frames()
    return f"{_LABELING_PROXY_PROFILE_VERSION}.q{crf}.g{gop_size}.b{b_frames}.w{max_width}"


def labeling_proxy_relative_path(relative_media_path: str) -> str:
    rel_path = PurePosixPath(_normalize_relative_media_path(relative_media_path))
    proxy_name = f"{rel_path.stem}.label_proxy.{labeling_proxy_profile_token()}.mp4"
    if str(rel_path.parent) in {"", "."}:
        return proxy_name
    return str(rel_path.parent / proxy_name)


def _current_labeling_proxy_path(relative_media_path: str) -> Path:
    return _safe_static_path(labeling_proxy_relative_path(relative_media_path))


def _labeling_proxy_scale_filter(width: int) -> str | None:
    max_width = int(getattr(settings, "labeling_proxy_max_width", 0) or 0)
    if max_width <= 0 or width <= 0 or width <= max_width:
        return None
    return f"scale='min({max_width},iw)':-2:flags=lanczos"


def _iter_labeling_proxy_files(relative_media_path: str) -> list[Path]:
    rel_path = PurePosixPath(_normalize_relative_media_path(relative_media_path))
    parent_rel = "" if str(rel_path.parent) in {"", "."} else str(rel_path.parent)
    parent_path = _safe_static_path(parent_rel)
    if not parent_path.exists() or not parent_path.is_dir():
        return []

    proxy_prefix = f"{rel_path.stem}.label_proxy"
    return sorted(
        child
        for child in parent_path.iterdir()
        if child.is_file() and child.name.startswith(proxy_prefix) and child.suffix.lower() == ".mp4"
    )


def _remove_obsolete_labeling_proxy_files(
    relative_media_path: str,
    keep_proxy_path: Path | None = None,
) -> None:
    for candidate in _iter_labeling_proxy_files(relative_media_path):
        if keep_proxy_path is not None and candidate == keep_proxy_path:
            continue
        try:
            candidate.unlink()
        except Exception:
            logger.exception(
                "Failed to delete obsolete labeling proxy video",
                extra={"item_path": relative_media_path, "proxy_path": str(candidate)},
            )


def _iter_all_labeling_proxy_files() -> list[Path]:
    uploads_root = static_root() / "uploads"
    if not uploads_root.exists() or not uploads_root.is_dir():
        return []
    return sorted(
        child
        for child in uploads_root.rglob("*.mp4")
        if child.is_file() and ".label_proxy." in child.name
    )


def _collect_labeling_proxy_storage_candidates(
    db,
) -> list[LabelingProxyStorageCandidate]:
    video_items = (
        db.query(Item)
        .filter(Item.kind == ItemKind.video)
        .all()
    )
    item_by_proxy_path: dict[str, Item] = {}
    for item in video_items:
        try:
            proxy_path = _current_labeling_proxy_path(item.path).resolve()
        except MediaProbeError:
            continue
        item_by_proxy_path[str(proxy_path)] = item

    candidates: list[LabelingProxyStorageCandidate] = []
    for proxy_path in _iter_all_labeling_proxy_files():
        try:
            stat = proxy_path.stat()
        except FileNotFoundError:
            continue
        item = item_by_proxy_path.get(str(proxy_path.resolve()))
        last_accessed_at = None
        if item is not None:
            last_accessed_at = _coerce_utc_datetime(
                item.media_conversion_last_accessed_at or item.updated_at or item.created_at
            )
        candidates.append(
            LabelingProxyStorageCandidate(
                proxy_path=proxy_path,
                size_bytes=max(0, int(stat.st_size or 0)),
                item=item,
                last_accessed_at=last_accessed_at,
                orphaned=item is None,
            )
        )
    return candidates


def _candidate_sort_key(candidate: LabelingProxyStorageCandidate) -> tuple[datetime, int, str]:
    last_access = _coerce_utc_datetime(candidate.last_accessed_at)
    if last_access is None:
        last_access = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (last_access, candidate.size_bytes, str(candidate.proxy_path))


def _build_labeling_proxy_storage_summary(
    candidates: list[LabelingProxyStorageCandidate],
) -> LabelingProxyStorageSummary:
    budget_bytes = _labeling_proxy_storage_budget_bytes()
    ttl_days = _labeling_proxy_storage_ttl_days()
    now = _utcnow()
    ttl_cutoff = now - timedelta(days=ttl_days) if ttl_days is not None else None
    used_bytes = sum(max(0, candidate.size_bytes) for candidate in candidates)
    proxy_file_count = len(candidates)
    managed_candidates = [candidate for candidate in candidates if not candidate.orphaned]
    stale_candidates = [
        candidate
        for candidate in managed_candidates
        if ttl_cutoff is not None
        and _coerce_utc_datetime(candidate.last_accessed_at) is not None
        and _coerce_utc_datetime(candidate.last_accessed_at) <= ttl_cutoff
    ]

    filesystem_total_bytes = None
    filesystem_free_bytes = None
    try:
        disk_usage = shutil.disk_usage(static_root())
        filesystem_total_bytes = int(disk_usage.total)
        filesystem_free_bytes = int(disk_usage.free)
    except FileNotFoundError:
        filesystem_total_bytes = None
        filesystem_free_bytes = None

    available_budget_bytes = None
    usage_ratio = None
    over_budget_bytes = 0
    if budget_bytes is not None:
        available_budget_bytes = max(0, budget_bytes - used_bytes)
        usage_ratio = min(1.0, used_bytes / budget_bytes) if budget_bytes > 0 else 0.0
        over_budget_bytes = max(0, used_bytes - budget_bytes)

    return LabelingProxyStorageSummary(
        enabled=settings.labeling_proxy_enabled,
        budget_bytes=budget_bytes,
        used_bytes=used_bytes,
        available_budget_bytes=available_budget_bytes,
        usage_ratio=usage_ratio,
        over_budget_bytes=over_budget_bytes,
        filesystem_total_bytes=filesystem_total_bytes,
        filesystem_free_bytes=filesystem_free_bytes,
        proxy_file_count=proxy_file_count,
        managed_proxy_count=len(managed_candidates),
        stale_proxy_count=len(stale_candidates),
        orphan_proxy_count=len([candidate for candidate in candidates if candidate.orphaned]),
        ttl_days=ttl_days,
    )


def plan_labeling_proxy_storage_evictions(
    candidates: list[LabelingProxyStorageCandidate],
    *,
    budget_bytes: int | None,
    reserve_bytes: int = 0,
    ttl_days: int | None,
    exclude_item_ids: set[int] | None = None,
    now: datetime | None = None,
) -> list[tuple[LabelingProxyStorageCandidate, str]]:
    eviction_plan: list[tuple[LabelingProxyStorageCandidate, str]] = []
    excluded_ids = exclude_item_ids or set()
    selected_proxy_paths: set[str] = set()
    remaining_used_bytes = sum(max(0, candidate.size_bytes) for candidate in candidates)
    reference_now = _coerce_utc_datetime(now) or _utcnow()
    ttl_cutoff = (
        reference_now - timedelta(days=ttl_days)
        if ttl_days is not None
        else None
    )

    orphan_candidates = sorted(
        [candidate for candidate in candidates if candidate.orphaned],
        key=_candidate_sort_key,
    )
    for candidate in orphan_candidates:
        eviction_plan.append((candidate, "orphan"))
        selected_proxy_paths.add(str(candidate.proxy_path))
        remaining_used_bytes -= max(0, candidate.size_bytes)

    ttl_candidates = []
    if ttl_cutoff is not None:
        ttl_candidates = sorted(
            [
                candidate
                for candidate in candidates
                if not candidate.orphaned
                and candidate.item is not None
                and candidate.item.id not in excluded_ids
                and _coerce_utc_datetime(candidate.last_accessed_at) is not None
                and _coerce_utc_datetime(candidate.last_accessed_at) <= ttl_cutoff
            ],
            key=_candidate_sort_key,
        )
    for candidate in ttl_candidates:
        proxy_key = str(candidate.proxy_path)
        if proxy_key in selected_proxy_paths:
            continue
        eviction_plan.append((candidate, "ttl"))
        selected_proxy_paths.add(proxy_key)
        remaining_used_bytes -= max(0, candidate.size_bytes)

    if budget_bytes is None:
        return eviction_plan

    lru_candidates = sorted(
        [
            candidate
            for candidate in candidates
            if not candidate.orphaned
            and candidate.item is not None
            and candidate.item.id not in excluded_ids
        ],
        key=_candidate_sort_key,
    )
    for candidate in lru_candidates:
        if remaining_used_bytes + max(0, reserve_bytes) <= budget_bytes:
            break
        proxy_key = str(candidate.proxy_path)
        if proxy_key in selected_proxy_paths:
            continue
        eviction_plan.append((candidate, "budget"))
        selected_proxy_paths.add(proxy_key)
        remaining_used_bytes -= max(0, candidate.size_bytes)

    return eviction_plan


def _evict_labeling_proxy_candidate(
    db,
    candidate: LabelingProxyStorageCandidate,
    *,
    reason: str,
) -> None:
    if candidate.item is not None:
        _remove_obsolete_labeling_proxy_files(candidate.item.path, keep_proxy_path=None)
        remaining_proxy_files = _iter_labeling_proxy_files(candidate.item.path)
        if remaining_proxy_files:
            logger.warning(
                "Converted video eviction left files behind",
                extra={
                    "item_id": candidate.item.id,
                    "item_path": candidate.item.path,
                    "reason": reason,
                    "remaining_proxy_files": [str(path) for path in remaining_proxy_files],
                },
            )
            return
        candidate.item.media_conversion_status = MEDIA_CONVERSION_STATUS_PENDING
        candidate.item.media_conversion_error = None
        candidate.item.media_conversion_size_bytes = None
        db.add(candidate.item)
        logger.info(
            "Evicted converted video from local storage",
            extra={
                "item_id": candidate.item.id,
                "item_path": candidate.item.path,
                "proxy_path": str(candidate.proxy_path),
                "reason": reason,
            },
        )
        return

    try:
        candidate.proxy_path.unlink(missing_ok=True)
    except TypeError:
        if candidate.proxy_path.exists():
            candidate.proxy_path.unlink()
    except Exception:
        logger.exception(
            "Failed to delete orphaned converted video",
            extra={"proxy_path": str(candidate.proxy_path), "reason": reason},
        )
        return
    logger.info(
        "Deleted orphaned converted video",
        extra={"proxy_path": str(candidate.proxy_path), "reason": reason},
    )


def maintain_labeling_proxy_storage_budget(
    *,
    reserve_bytes: int = 0,
    exclude_item_ids: set[int] | None = None,
) -> LabelingProxyStorageSummary:
    with _LABELING_PROXY_STORAGE_GUARD:
        with SessionLocal() as db:
            candidates = _collect_labeling_proxy_storage_candidates(db)
            eviction_plan = plan_labeling_proxy_storage_evictions(
                candidates,
                budget_bytes=_labeling_proxy_storage_budget_bytes(),
                reserve_bytes=max(0, int(reserve_bytes or 0)),
                ttl_days=_labeling_proxy_storage_ttl_days(),
                exclude_item_ids=exclude_item_ids,
            )
            for candidate, reason in eviction_plan:
                _evict_labeling_proxy_candidate(db, candidate, reason=reason)
            if eviction_plan:
                db.commit()
            return _build_labeling_proxy_storage_summary(
                _collect_labeling_proxy_storage_candidates(db)
            )


def get_labeling_proxy_storage_summary() -> LabelingProxyStorageSummary:
    with SessionLocal() as db:
        return _build_labeling_proxy_storage_summary(
            _collect_labeling_proxy_storage_candidates(db)
        )


def labeling_proxy_storage_summary_payload() -> dict:
    summary = get_labeling_proxy_storage_summary()
    usage_percent = None
    if summary.usage_ratio is not None:
        usage_percent = round(summary.usage_ratio * 100, 1)
    if summary.ttl_days is not None:
        policy_message = (
            f"Least recently opened converted videos are removed first, and idle conversions "
            f"older than {summary.ttl_days} days are deleted automatically."
        )
    else:
        policy_message = "Least recently opened converted videos are removed first when the local budget is exceeded."
    return {
        "enabled": summary.enabled,
        "budget_bytes": summary.budget_bytes,
        "budget_label": _format_storage_bytes(summary.budget_bytes),
        "used_bytes": summary.used_bytes,
        "used_label": _format_storage_bytes(summary.used_bytes),
        "available_budget_bytes": summary.available_budget_bytes,
        "available_budget_label": _format_storage_bytes(summary.available_budget_bytes),
        "usage_ratio": summary.usage_ratio,
        "usage_percent": usage_percent,
        "over_budget_bytes": summary.over_budget_bytes,
        "over_budget_label": _format_storage_bytes(summary.over_budget_bytes),
        "filesystem_total_bytes": summary.filesystem_total_bytes,
        "filesystem_total_label": (
            _format_storage_bytes(summary.filesystem_total_bytes)
            if summary.filesystem_total_bytes is not None
            else "Unavailable"
        ),
        "filesystem_free_bytes": summary.filesystem_free_bytes,
        "filesystem_free_label": (
            _format_storage_bytes(summary.filesystem_free_bytes)
            if summary.filesystem_free_bytes is not None
            else "Unavailable"
        ),
        "proxy_file_count": summary.proxy_file_count,
        "managed_proxy_count": summary.managed_proxy_count,
        "stale_proxy_count": summary.stale_proxy_count,
        "orphan_proxy_count": summary.orphan_proxy_count,
        "ttl_days": summary.ttl_days,
        "policy_message": policy_message,
    }


def _labeling_proxy_lock_for(relative_media_path: str) -> Lock:
    normalized_path = _normalize_relative_media_path(relative_media_path)
    with _LABELING_PROXY_LOCKS_GUARD:
        lock = _LABELING_PROXY_LOCKS.get(normalized_path)
        if lock is None:
            lock = Lock()
            _LABELING_PROXY_LOCKS[normalized_path] = lock
        return lock


def ensure_labeling_proxy_video(
    item: Item,
    *,
    metadata: MediaMetadata | None = None,
) -> str:
    if item.kind != ItemKind.video or not settings.labeling_proxy_enabled:
        return item.path

    source_path = resolve_media_source_path(item)
    if source_path is None or not source_path.is_file():
        raise LabelingProxyError(_missing_media_source_error_message(item))

    proxy_relative_path = labeling_proxy_relative_path(item.path)
    proxy_path = _safe_static_path(proxy_relative_path)
    proxy_path.parent.mkdir(parents=True, exist_ok=True)

    if proxy_path.is_file():
        _remove_obsolete_labeling_proxy_files(item.path, keep_proxy_path=proxy_path)
        return proxy_relative_path

    if metadata is None:
        try:
            metadata = probe_media_metadata(source_path, ItemKind.video)
        except MediaProbeError as exc:
            raise LabelingProxyError(str(exc)) from exc

    if metadata.frame_rate_mode == FRAME_RATE_MODE_VFR:
        raise LabelingProxyError(_variable_frame_rate_error_message())

    item_lock = _labeling_proxy_lock_for(item.path)
    with item_lock:
        if proxy_path.is_file():
            _remove_obsolete_labeling_proxy_files(item.path, keep_proxy_path=proxy_path)
            return proxy_relative_path

        temp_proxy_path = proxy_path.with_name(f"{proxy_path.stem}.tmp{proxy_path.suffix}")
        scale_filter = _labeling_proxy_scale_filter(metadata.width)
        gop_size = _labeling_proxy_gop_size()
        b_frames = _labeling_proxy_b_frames()
        if temp_proxy_path.exists():
            temp_proxy_path.unlink()

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            settings.labeling_proxy_preset,
            "-crf",
            str(settings.labeling_proxy_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-g",
            str(gop_size),
            "-keyint_min",
            str(gop_size),
            "-sc_threshold",
            "0",
            "-bf",
            str(b_frames),
        ]
        if scale_filter:
            cmd.extend(["-vf", scale_filter])
        cmd.append(str(temp_proxy_path))

        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise LabelingProxyError(
                "ffmpeg is not available in the runtime environment"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            try:
                temp_proxy_path.unlink(missing_ok=True)
            except TypeError:
                if temp_proxy_path.exists():
                    temp_proxy_path.unlink()
            raise LabelingProxyError(
                stderr or f"Failed to generate labeling proxy for {item.path}"
            )

        temp_proxy_path.replace(proxy_path)
        logger.info(
            "Generated labeling proxy video",
            extra={
                "item_id": item.id,
                "source_path": item.path,
                "proxy_path": proxy_relative_path,
                "proxy_profile": labeling_proxy_profile_token(),
            },
        )
        _remove_obsolete_labeling_proxy_files(item.path, keep_proxy_path=proxy_path)

    return proxy_relative_path


def sync_item_media_conversion_state(item: Item) -> bool:
    changed = False
    desired_profile = labeling_proxy_profile_token() if item.kind == ItemKind.video else None
    current_status = _normalize_media_conversion_status(item.media_conversion_status, kind=item.kind)
    current_rate_mode = _normalize_frame_rate_mode(item.frame_rate_mode)

    def assign(attr_name: str, value) -> None:
        nonlocal changed
        if getattr(item, attr_name) != value:
            setattr(item, attr_name, value)
            changed = True

    if item.kind != ItemKind.video or not settings.labeling_proxy_enabled:
        assign("media_conversion_status", MEDIA_CONVERSION_STATUS_NOT_REQUIRED)
        assign("media_conversion_error", None)
        assign("media_conversion_profile", desired_profile)
        assign("media_conversion_size_bytes", None)
        if item.kind != ItemKind.video:
            assign("frame_rate_mode", None)
            assign("media_conversion_last_accessed_at", None)
        return changed

    proxy_exists = False
    proxy_size_bytes = None
    try:
        proxy_path = _current_labeling_proxy_path(item.path)
        proxy_exists = proxy_path.is_file()
        if proxy_exists:
            proxy_size_bytes = max(0, int(proxy_path.stat().st_size or 0))
    except MediaProbeError:
        proxy_exists = False

    assign("media_conversion_profile", desired_profile)
    assign("frame_rate_mode", current_rate_mode)
    assign("media_conversion_size_bytes", proxy_size_bytes)

    if proxy_exists:
        assign("media_conversion_status", MEDIA_CONVERSION_STATUS_READY)
        assign("media_conversion_error", None)
        return changed

    source_path = resolve_media_source_path(item)
    if source_path is None or not source_path.is_file():
        assign("media_conversion_status", MEDIA_CONVERSION_STATUS_FAILED)
        assign("media_conversion_error", _missing_media_source_error_message(item))
        return changed

    if current_rate_mode == FRAME_RATE_MODE_VFR:
        assign("media_conversion_status", MEDIA_CONVERSION_STATUS_FAILED)
        assign("media_conversion_error", _variable_frame_rate_error_message())
        return changed

    if current_status == MEDIA_CONVERSION_STATUS_FAILED:
        current_error = (item.media_conversion_error or "").strip()
        if current_error == _missing_media_source_error_message(item):
            assign("media_conversion_status", MEDIA_CONVERSION_STATUS_PENDING)
            assign("media_conversion_error", None)
            return changed
        assign("media_conversion_status", MEDIA_CONVERSION_STATUS_FAILED)
        return changed

    if current_status == MEDIA_CONVERSION_STATUS_PROCESSING:
        assign("media_conversion_status", MEDIA_CONVERSION_STATUS_PROCESSING)
        return changed

    assign("media_conversion_status", MEDIA_CONVERSION_STATUS_PENDING)
    assign("media_conversion_error", None)
    return changed


def touch_media_conversion_access(item: Item) -> bool:
    if item.kind != ItemKind.video or not settings.labeling_proxy_enabled:
        return False
    current_status = _normalize_media_conversion_status(
        item.media_conversion_status,
        kind=item.kind,
    )
    if current_status != MEDIA_CONVERSION_STATUS_READY:
        return False

    try:
        proxy_path = _current_labeling_proxy_path(item.path)
    except MediaProbeError:
        return False
    if not proxy_path.is_file():
        return False

    changed = False
    size_bytes = max(0, int(proxy_path.stat().st_size or 0))
    if item.media_conversion_size_bytes != size_bytes:
        item.media_conversion_size_bytes = size_bytes
        changed = True

    now = _utcnow()
    current_last_accessed = _coerce_utc_datetime(item.media_conversion_last_accessed_at)
    if current_last_accessed is None or abs((now - current_last_accessed).total_seconds()) >= 1:
        item.media_conversion_last_accessed_at = now
        changed = True

    return changed


def build_annotation_media_state(item: Item) -> AnnotationMediaState:
    status = _normalize_media_conversion_status(item.media_conversion_status, kind=item.kind)
    frame_rate_mode = (
        None if item.kind != ItemKind.video else _normalize_frame_rate_mode(item.frame_rate_mode)
    )

    if item.kind != ItemKind.video or not settings.labeling_proxy_enabled:
        return AnnotationMediaState(
            status=MEDIA_CONVERSION_STATUS_NOT_REQUIRED,
            status_label="Not required",
            status_badge_class="text-bg-secondary",
            message="No video conversion is required for this item.",
            detail=None,
            ready=True,
            failed=False,
            pending=False,
            frame_rate_mode=frame_rate_mode,
            display_media_path=item.path,
        )

    display_media_path = item.path
    if status == MEDIA_CONVERSION_STATUS_READY:
        display_media_path = labeling_proxy_relative_path(item.path)

    detail = (item.media_conversion_error or "").strip() or None
    if status == MEDIA_CONVERSION_STATUS_READY:
        return AnnotationMediaState(
            status=status,
            status_label="Ready",
            status_badge_class="text-bg-success",
            message="Video conversion is complete. Frame-accurate playback is ready.",
            detail=None,
            ready=True,
            failed=False,
            pending=False,
            frame_rate_mode=frame_rate_mode,
            display_media_path=display_media_path,
        )

    if status == MEDIA_CONVERSION_STATUS_PROCESSING:
        return AnnotationMediaState(
            status=status,
            status_label="Converting",
            status_badge_class="text-bg-primary",
            message="Converting video for smoother frame-accurate labeling.",
            detail=detail,
            ready=False,
            failed=False,
            pending=True,
            frame_rate_mode=frame_rate_mode,
            display_media_path=display_media_path,
        )

    if status == MEDIA_CONVERSION_STATUS_FAILED:
        return AnnotationMediaState(
            status=status,
            status_label="Failed",
            status_badge_class="text-bg-danger",
            message="Video conversion failed.",
            detail=detail,
            ready=False,
            failed=True,
            pending=False,
            frame_rate_mode=frame_rate_mode,
            display_media_path=display_media_path,
        )

    return AnnotationMediaState(
        status=MEDIA_CONVERSION_STATUS_PENDING,
        status_label="Queued",
        status_badge_class="text-bg-secondary",
        message="Upload complete. Waiting to start video conversion.",
        detail=detail,
        ready=False,
        failed=False,
        pending=True,
        frame_rate_mode=frame_rate_mode,
        display_media_path=display_media_path,
    )


def resolve_annotation_media_path(item: Item) -> str:
    if item.kind != ItemKind.video:
        return item.path

    try:
        proxy_path = _current_labeling_proxy_path(item.path)
    except MediaProbeError:
        return item.path
    return labeling_proxy_relative_path(item.path) if proxy_path.is_file() else item.path


def _labeling_proxy_executor() -> ThreadPoolExecutor:
    global _LABELING_PROXY_EXECUTOR
    with _LABELING_PROXY_EXECUTOR_GUARD:
        if _LABELING_PROXY_EXECUTOR is None:
            max_workers = max(
                1, int(getattr(settings, "labeling_proxy_max_concurrent_jobs", 2) or 2)
            )
            _LABELING_PROXY_EXECUTOR = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="labeling-proxy",
            )
        return _LABELING_PROXY_EXECUTOR


def _clear_completed_conversion_job(item_id: int, future: Future | None = None) -> None:
    with _LABELING_PROXY_JOBS_GUARD:
        current = _LABELING_PROXY_JOBS.get(item_id)
        if current is None:
            return
        if future is not None and current is not future:
            return
        if current.done():
            _LABELING_PROXY_JOBS.pop(item_id, None)


def enqueue_media_conversion(item_id: int) -> bool:
    if not settings.labeling_proxy_enabled:
        return False

    with SessionLocal() as db:
        item = db.get(Item, item_id)
        if not item or item.kind != ItemKind.video:
            return False
        changed = sync_item_media_conversion_state(item)
        if changed:
            db.add(item)
            db.commit()
            db.refresh(item)
        if item.media_conversion_status in {
            MEDIA_CONVERSION_STATUS_READY,
            MEDIA_CONVERSION_STATUS_FAILED,
        }:
            return False

    with _LABELING_PROXY_JOBS_GUARD:
        existing_future = _LABELING_PROXY_JOBS.get(item_id)
        if existing_future is not None:
            if not existing_future.done():
                return False
            _LABELING_PROXY_JOBS.pop(item_id, None)

        future = _labeling_proxy_executor().submit(_run_media_conversion_job, item_id)
        _LABELING_PROXY_JOBS[item_id] = future
        future.add_done_callback(lambda done_future, queued_item_id=item_id: _clear_completed_conversion_job(queued_item_id, done_future))
        return True


def _mark_item_conversion_failed(item_id: int, error_message: str) -> None:
    with SessionLocal() as db:
        item = db.get(Item, item_id)
        if item is None:
            return
        item.media_conversion_status = MEDIA_CONVERSION_STATUS_FAILED
        item.media_conversion_error = (error_message or "Video conversion failed.").strip()
        item.media_conversion_profile = labeling_proxy_profile_token()
        item.media_conversion_size_bytes = None
        db.add(item)
        db.commit()


def _run_media_conversion_job(item_id: int) -> None:
    try:
        with SessionLocal() as db:
            item = db.get(Item, item_id)
            if item is None or item.kind != ItemKind.video:
                return

            source_path = resolve_media_source_path(item)
            if source_path is None or not source_path.is_file():
                raise LabelingProxyError(_missing_media_source_error_message(item))
            estimated_reserve_bytes = max(0, int(source_path.stat().st_size or 0))
            maintain_labeling_proxy_storage_budget(
                reserve_bytes=estimated_reserve_bytes,
                exclude_item_ids={item_id},
            )

            metadata = probe_media_metadata(source_path, ItemKind.video)
            item.frame_rate_mode = metadata.frame_rate_mode
            item.media_conversion_profile = labeling_proxy_profile_token()
            if metadata.frame_rate_mode == FRAME_RATE_MODE_VFR:
                raise LabelingProxyError(_variable_frame_rate_error_message())

            item.media_conversion_status = MEDIA_CONVERSION_STATUS_PROCESSING
            item.media_conversion_error = None
            db.add(item)
            db.commit()

            ensure_labeling_proxy_video(item, metadata=metadata)

            refreshed_item = db.get(Item, item_id)
            if refreshed_item is None:
                return
            proxy_path = _current_labeling_proxy_path(refreshed_item.path)
            proxy_size_bytes = None
            if proxy_path.is_file():
                proxy_size_bytes = max(0, int(proxy_path.stat().st_size or 0))
            refreshed_item.frame_rate_mode = metadata.frame_rate_mode
            refreshed_item.media_conversion_status = MEDIA_CONVERSION_STATUS_READY
            refreshed_item.media_conversion_error = None
            refreshed_item.media_conversion_profile = labeling_proxy_profile_token()
            refreshed_item.media_conversion_size_bytes = proxy_size_bytes
            refreshed_item.media_conversion_last_accessed_at = _utcnow()
            db.add(refreshed_item)
            db.commit()
            maintain_labeling_proxy_storage_budget(exclude_item_ids={item_id})
    except Exception as exc:
        logger.exception(
            "Video conversion job failed",
            extra={"item_id": item_id},
        )
        _mark_item_conversion_failed(item_id, _job_failure_message(exc))


def refresh_annotation_media_state(
    item: Item,
    *,
    auto_enqueue: bool = False,
) -> AnnotationMediaState:
    sync_item_media_conversion_state(item)
    state = build_annotation_media_state(item)
    if auto_enqueue and item.kind == ItemKind.video and state.pending and not state.failed:
        enqueue_media_conversion(item.id)
    return state


def media_conversion_payload(item: Item, *, auto_enqueue: bool = False) -> dict:
    state = refresh_annotation_media_state(item, auto_enqueue=auto_enqueue)
    return {
        "item_id": item.id,
        "kind": item.kind.value,
        "status": state.status,
        "label": state.status_label,
        "badge_class": state.status_badge_class,
        "message": state.message,
        "detail": state.detail,
        "ready": state.ready,
        "failed": state.failed,
        "pending": state.pending,
        "frame_rate_mode": state.frame_rate_mode,
    }


def remove_labeling_proxy_video(item: Item) -> None:
    if item.kind != ItemKind.video:
        return

    try:
        proxy_paths = _iter_labeling_proxy_files(item.path)
    except MediaProbeError:
        logger.exception(
            "Failed to resolve labeling proxy path during item cleanup",
            extra={"item_id": item.id, "item_path": item.path},
        )
        return

    if not proxy_paths:
        return

    for proxy_path in proxy_paths:
        try:
            proxy_path.unlink()
        except Exception:
            logger.exception(
                "Failed to delete labeling proxy video",
                extra={"item_id": item.id, "item_path": item.path, "proxy_path": str(proxy_path)},
            )
