from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..database import SessionLocal, db_session
from ..models import (
    Annotation,
    AnnotationStatus,
    Item,
    ItemStatus,
    LabelClass,
    Sam2JobStatus,
    Sam2TrackJob,
)
from ..schemas import AnnotationRead, Sam2TrackJobRead, Sam2TrackJobStatusResponse
from .audit import log_audit
from .collaboration import collaboration_hub
from .notifications import create_sam2_job_notifications
from .sam2 import Sam2Error, Sam2PromptPayload, Sam2Suggestion, get_video_track_suggestions


logger = structlog.get_logger(__name__)
_SAM2_QUEUE_ADVISORY_LOCK_ID = 2_024_032_801


class Sam2QueueFullError(RuntimeError):
    """Raised when the SAM2 background queue has reached its configured limit."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_job_error_message(error: Exception | str) -> str:
    text = str(error).strip() if error is not None else ""
    return text[:2000] if text else "SAM2 track job failed."


def _prompt_to_payload_json(prompt: Sam2PromptPayload) -> str:
    return json.dumps(
        {
            "label_class_id": int(prompt.label_class_id),
            "frame_index": prompt.frame_index,
            "box_xyxy": list(prompt.box_xyxy) if prompt.box_xyxy is not None else None,
            "prompt_points": [
                {
                    "x": float(point.x),
                    "y": float(point.y),
                    "label": 1 if int(point.label) == 1 else 0,
                }
                for point in prompt.prompt_points
            ],
            "track_id": prompt.track_id,
            "track_start_frame": prompt.track_start_frame,
            "track_end_frame": prompt.track_end_frame,
            "include_reverse": bool(prompt.include_reverse),
            "simplify_tolerance": prompt.simplify_tolerance,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

def _deserialize_prompt(payload_json: str) -> Sam2PromptPayload:
    payload = json.loads(payload_json)
    from .sam2 import Sam2PointPrompt

    return Sam2PromptPayload(
        label_class_id=int(payload["label_class_id"]),
        frame_index=(
            None if payload.get("frame_index") is None else int(payload["frame_index"])
        ),
        box_xyxy=(
            tuple(float(value) for value in payload["box_xyxy"])
            if payload.get("box_xyxy") is not None
            else None
        ),
        prompt_points=[
            Sam2PointPrompt(
                x=float(point["x"]),
                y=float(point["y"]),
                label=1 if int(point["label"]) == 1 else 0,
            )
            for point in payload.get("prompt_points") or []
        ],
        track_id=None if payload.get("track_id") is None else int(payload["track_id"]),
        track_start_frame=(
            None
            if payload.get("track_start_frame") is None
            else int(payload["track_start_frame"])
        ),
        track_end_frame=(
            None
            if payload.get("track_end_frame") is None
            else int(payload["track_end_frame"])
        ),
        include_reverse=bool(payload.get("include_reverse", True)),
        simplify_tolerance=payload.get("simplify_tolerance"),
    )


def _count_jobs_by_status(db: Session) -> tuple[int, int]:
    rows = db.execute(
        select(Sam2TrackJob.status, func.count())
        .group_by(Sam2TrackJob.status)
    ).all()
    counts = {
        status: int(count)
        for status, count in rows
    }
    return (
        counts.get(Sam2JobStatus.running, 0),
        counts.get(Sam2JobStatus.queued, 0),
    )


def _build_queue_position_map(db: Session) -> dict[int, int]:
    queued_job_ids = db.execute(
        select(Sam2TrackJob.id)
        .where(Sam2TrackJob.status == Sam2JobStatus.queued)
        .order_by(Sam2TrackJob.id.asc())
    ).scalars().all()
    return {job_id: index + 1 for index, job_id in enumerate(queued_job_ids)}


def _serialize_job(
    job: Sam2TrackJob,
    *,
    queue_positions: dict[int, int] | None = None,
) -> Sam2TrackJobRead:
    queue_position = None
    if job.status == Sam2JobStatus.queued:
        queue_position = (queue_positions or {}).get(job.id)

    return Sam2TrackJobRead(
        id=job.id,
        item_id=job.item_id,
        requested_by=job.requested_by,
        status=job.status,
        label_class_id=job.label_class_id,
        track_id=job.track_id,
        frame_index=job.frame_index,
        track_start_frame=job.track_start_frame,
        track_end_frame=job.track_end_frame,
        error_message=job.error_message,
        result_annotation_count=job.result_annotation_count,
        applied_revision=job.applied_revision,
        queue_position=queue_position,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _serialize_item_annotations(db: Session, item_id: int) -> list[dict[str, Any]]:
    annotations = db.execute(
        select(Annotation)
        .options(
            selectinload(Annotation.created_by_user),
            selectinload(Annotation.updated_by_user),
        )
        .where(Annotation.item_id == item_id)
        .order_by(Annotation.frame_index, Annotation.track_id, Annotation.id)
    ).scalars().all()
    return [
        AnnotationRead.model_validate(annotation).model_dump(mode="json")
        for annotation in annotations
    ]


def _next_track_id_for_item(db: Session, item_id: int) -> int:
    annotation_max = db.execute(
        select(func.max(Annotation.track_id)).where(Annotation.item_id == item_id)
    ).scalar_one()
    job_max = db.execute(
        select(func.max(Sam2TrackJob.track_id)).where(Sam2TrackJob.item_id == item_id)
    ).scalar_one()

    max_track_id = max(
        int(annotation_max or 0),
        int(job_max or 0),
    )
    return max_track_id + 1


def enqueue_track_job(
    *,
    db: Session,
    item: Item,
    label_class: LabelClass,
    prompt: Sam2PromptPayload,
    requested_by_user_id: int | None,
) -> tuple[Sam2TrackJobRead, int, int]:
    max_queue_size = max(1, int(getattr(settings, "sam2_max_queue_size", 8) or 8))
    running_count, queued_count = _count_jobs_by_status(db)
    if queued_count >= max_queue_size:
        raise Sam2QueueFullError(
            f"SAM2 queue is full ({queued_count}/{max_queue_size} waiting jobs). Retry later."
        )

    if prompt.track_id is None:
        prompt.track_id = _next_track_id_for_item(db, item.id)

    job = Sam2TrackJob(
        item_id=item.id,
        requested_by=requested_by_user_id,
        status=Sam2JobStatus.queued,
        label_class_id=label_class.id,
        track_id=prompt.track_id,
        frame_index=prompt.frame_index,
        track_start_frame=prompt.track_start_frame,
        track_end_frame=prompt.track_end_frame,
        payload_json=_prompt_to_payload_json(prompt),
        error_message=None,
        result_annotation_count=None,
        applied_revision=None,
        started_at=None,
        completed_at=None,
    )
    db.add(job)
    db.flush()
    db.refresh(job)

    log_audit(
        db,
        actor_id=requested_by_user_id,
        object_type="item",
        object_id=item.id,
        action="sam2_track_job_enqueued",
        payload={
            "job_id": job.id,
            "label_class_id": label_class.id,
            "track_id": prompt.track_id,
            "frame_index": prompt.frame_index,
            "track_start_frame": prompt.track_start_frame,
            "track_end_frame": prompt.track_end_frame,
        },
    )

    _running_count, queued_count_after = _count_jobs_by_status(db)
    queue_positions = _build_queue_position_map(db)
    return _serialize_job(job, queue_positions=queue_positions), running_count, queued_count_after


def build_track_job_status_response(
    *,
    db: Session,
    item: Item,
) -> Sam2TrackJobStatusResponse:
    running_count, queued_count = _count_jobs_by_status(db)
    queue_positions = _build_queue_position_map(db)

    active_jobs = db.execute(
        select(Sam2TrackJob)
        .where(
            Sam2TrackJob.item_id == item.id,
            Sam2TrackJob.status.in_([Sam2JobStatus.running, Sam2JobStatus.queued]),
        )
    ).scalars().all()
    active_jobs.sort(
        key=lambda job: (
            0 if job.status == Sam2JobStatus.running else 1,
            int(job.id),
        )
    )

    latest_finished_job = db.execute(
        select(Sam2TrackJob)
        .where(
            Sam2TrackJob.item_id == item.id,
            Sam2TrackJob.status.in_([Sam2JobStatus.completed, Sam2JobStatus.failed]),
        )
        .order_by(Sam2TrackJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    return Sam2TrackJobStatusResponse(
        item_id=item.id,
        item_annotation_revision=int(item.annotation_revision or 0),
        item_status=item.status,
        running_count=running_count,
        queued_count=queued_count,
        max_concurrent_jobs=max(
            1,
            int(getattr(settings, "sam2_max_concurrent_jobs", 1) or 1),
        ),
        max_queue_size=max(
            1,
            int(getattr(settings, "sam2_max_queue_size", 8) or 8),
        ),
        item_jobs=[
            _serialize_job(job, queue_positions=queue_positions) for job in active_jobs
        ],
        latest_finished_job=(
            _serialize_job(latest_finished_job, queue_positions=queue_positions)
            if latest_finished_job is not None
            else None
        ),
    )


class Sam2TrackJobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._dispatcher_thread: threading.Thread | None = None
        self._active_futures: dict[int, Future] = {}
        self._started = False

    @property
    def max_workers(self) -> int:
        return max(1, int(getattr(settings, "sam2_max_concurrent_jobs", 1) or 1))

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="sam2-track-job",
            )
            try:
                self._requeue_running_jobs()
            except OperationalError as error:
                logger.warning(
                    "SAM2 track job runner could not inspect startup jobs yet",
                    error=str(getattr(error, "orig", error)),
                )
            self._dispatcher_thread = threading.Thread(
                target=self._dispatch_loop,
                name="sam2-track-job-dispatcher",
                daemon=True,
            )
            self._dispatcher_thread.start()
            self._started = True

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._stop_event.set()
            self._wake_event.set()
            dispatcher_thread = self._dispatcher_thread
            executor = self._executor
            self._dispatcher_thread = None
            self._executor = None
            self._started = False

        if dispatcher_thread is not None:
            dispatcher_thread.join(timeout=3)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=False)

    def wake(self) -> None:
        self._wake_event.set()

    def _requeue_running_jobs(self) -> None:
        with db_session() as db:
            running_jobs = db.execute(
                select(Sam2TrackJob).where(Sam2TrackJob.status == Sam2JobStatus.running)
            ).scalars().all()
            if not running_jobs:
                return

            for job in running_jobs:
                job.status = Sam2JobStatus.queued
                job.started_at = None
                job.completed_at = None
                job.error_message = None
                db.add(job)

            logger.warning(
                "Re-queued stale SAM2 track jobs after startup",
                requeued_job_count=len(running_jobs),
            )

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._reap_completed_futures()

                while (
                    not self._stop_event.is_set()
                    and len(self._active_futures) < self.max_workers
                ):
                    job_id = self._claim_next_job_id()
                    if job_id is None:
                        break

                    executor = self._executor
                    if executor is None:
                        return

                    future = executor.submit(self._run_job, job_id)
                    self._active_futures[job_id] = future
                    future.add_done_callback(
                        lambda _future, _job_id=job_id: self.wake()
                    )
            except OperationalError as error:
                logger.warning(
                    "SAM2 track job dispatcher could not reach the database",
                    error=str(getattr(error, "orig", error)),
                )
            except Exception:
                logger.exception("SAM2 track job dispatcher loop crashed")

            self._wake_event.wait(timeout=1.0)
            self._wake_event.clear()

    def _reap_completed_futures(self) -> None:
        finished_job_ids = [
            job_id
            for job_id, future in list(self._active_futures.items())
            if future.done()
        ]
        for job_id in finished_job_ids:
            future = self._active_futures.pop(job_id, None)
            if future is None:
                continue
            try:
                future.result()
            except Exception:
                logger.exception(
                    "SAM2 track job worker crashed unexpectedly",
                    job_id=job_id,
                )

    def _claim_next_job_id(self) -> int | None:
        with db_session() as db:
            dialect_name = getattr(getattr(db, "bind", None), "dialect", None)
            dialect_name = getattr(dialect_name, "name", "")

            if dialect_name == "postgresql":
                lock_acquired = bool(
                    db.execute(
                        text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
                        {"lock_id": _SAM2_QUEUE_ADVISORY_LOCK_ID},
                    ).scalar_one()
                )
                if not lock_acquired:
                    return None

            running_count = int(
                db.execute(
                    select(func.count())
                    .select_from(Sam2TrackJob)
                    .where(Sam2TrackJob.status == Sam2JobStatus.running)
                ).scalar_one()
            )
            if running_count >= self.max_workers:
                return None

            job = db.execute(
                select(Sam2TrackJob)
                .where(Sam2TrackJob.status == Sam2JobStatus.queued)
                .order_by(Sam2TrackJob.id.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            ).scalar_one_or_none()
            if job is None:
                return None

            job.status = Sam2JobStatus.running
            job.started_at = _utcnow()
            job.completed_at = None
            job.error_message = None
            db.add(job)
            db.flush()
            logger.info("Claimed SAM2 track job", job_id=job.id, item_id=job.item_id)
            return int(job.id)

    def _run_job(self, job_id: int) -> None:
        try:
            with SessionLocal() as db:
                job = db.get(Sam2TrackJob, job_id)
                if job is None:
                    return
                item = db.get(Item, job.item_id)
                if item is None:
                    raise RuntimeError("Item not found for SAM2 track job.")
                prompt = _deserialize_prompt(job.payload_json)
                if prompt.track_id is None and job.track_id is not None:
                    prompt.track_id = int(job.track_id)

            suggestions = get_video_track_suggestions(item, prompt)
            if not suggestions:
                raise Sam2Error("The mask assistant did not return any tracked masks.")
            self._mark_job_completed(
                job_id=job_id,
                prompt=prompt,
                suggestions=suggestions,
            )
        except Exception as error:
            logger.exception("SAM2 track job failed", job_id=job_id)
            self._mark_job_failed(job_id=job_id, error=error)
        finally:
            self.wake()

    def _mark_job_completed(
        self,
        *,
        job_id: int,
        prompt: Sam2PromptPayload,
        suggestions: list[Sam2Suggestion],
    ) -> None:
        annotations_payload: list[dict[str, Any]] = []
        final_revision = 0
        final_item_status = ItemStatus.unlabeled.value
        actor_user_id: int | None = None
        item_id: int | None = None

        with db_session() as db:
            job = db.get(Sam2TrackJob, job_id)
            if job is None:
                return
            item = db.get(Item, job.item_id)
            if item is None:
                raise RuntimeError("Item not found for SAM2 track job completion.")

            track_id = prompt.track_id
            if track_id is None:
                track_id = _next_track_id_for_item(db, item.id)
                job.track_id = track_id

            db.execute(
                delete(Annotation).where(
                    Annotation.item_id == item.id,
                    Annotation.track_id == track_id,
                )
            )

            ordered_suggestions = sorted(
                suggestions,
                key=lambda suggestion: (suggestion.frame_index or 0, suggestion.label_class_id),
            )

            for suggestion in ordered_suggestions:
                annotation = Annotation(
                    item_id=item.id,
                    label_class_id=suggestion.label_class_id,
                    frame_index=suggestion.frame_index,
                    track_id=track_id,
                    propagation_frames=0,
                    x1=suggestion.x1,
                    y1=suggestion.y1,
                    x2=suggestion.x2,
                    y2=suggestion.y2,
                    status=AnnotationStatus.pending,
                    created_by=job.requested_by,
                    updated_by=job.requested_by,
                )
                annotation.polygon_points = suggestion.polygon_points
                db.add(annotation)

            annotation_count = int(
                db.execute(
                    select(func.count())
                    .select_from(Annotation)
                    .where(Annotation.item_id == item.id)
                ).scalar_one()
            )

            item.status = (
                ItemStatus.in_progress if annotation_count else ItemStatus.unlabeled
            )
            item.annotation_revision += 1

            job.status = Sam2JobStatus.completed
            job.result_annotation_count = len(ordered_suggestions)
            job.applied_revision = int(item.annotation_revision or 0)
            job.completed_at = _utcnow()
            job.error_message = None
            db.add(item)
            db.add(job)

            log_audit(
                db,
                actor_id=job.requested_by,
                object_type="item",
                object_id=item.id,
                action="sam2_track_job_completed",
                payload={
                    "job_id": job.id,
                    "track_id": track_id,
                    "label_class_id": job.label_class_id,
                    "frame_index": job.frame_index,
                    "track_start_frame": job.track_start_frame,
                    "track_end_frame": job.track_end_frame,
                    "result_annotation_count": len(ordered_suggestions),
                    "annotation_revision": item.annotation_revision,
                },
            )
            create_sam2_job_notifications(
                db=db,
                project=item.project,
                job=job,
            )

            db.flush()
            annotations_payload = _serialize_item_annotations(db, item.id)
            final_revision = int(item.annotation_revision or 0)
            final_item_status = item.status.value
            actor_user_id = job.requested_by
            item_id = item.id

        if item_id is not None:
            collaboration_hub.publish_annotation_commit(
                item_id=item_id,
                revision=final_revision,
                annotations=annotations_payload,
                item_status=final_item_status,
                actor_user_id=actor_user_id,
            )
            logger.info(
                "Completed SAM2 track job",
                job_id=job_id,
                item_id=item_id,
                result_annotation_count=len(suggestions),
                revision=final_revision,
            )

    def _mark_job_failed(self, *, job_id: int, error: Exception | str) -> None:
        error_message = _normalize_job_error_message(error)
        with db_session() as db:
            job = db.get(Sam2TrackJob, job_id)
            if job is None:
                return
            item = db.get(Item, job.item_id) if job.item_id is not None else None

            job.status = Sam2JobStatus.failed
            job.error_message = error_message
            job.completed_at = _utcnow()
            db.add(job)

            log_audit(
                db,
                actor_id=job.requested_by,
                object_type="item",
                object_id=job.item_id,
                action="sam2_track_job_failed",
                payload={
                    "job_id": job.id,
                    "track_id": job.track_id,
                    "label_class_id": job.label_class_id,
                    "frame_index": job.frame_index,
                    "track_start_frame": job.track_start_frame,
                    "track_end_frame": job.track_end_frame,
                    "error": error_message,
                },
            )
            if item is not None:
                create_sam2_job_notifications(
                    db=db,
                    project=item.project,
                    job=job,
                )


sam2_track_job_runner = Sam2TrackJobRunner()
