from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ExportJob, ExportJobStatus, Project
from ..routers.api_export import (
    _annotation_export_records,
    _build_original_media_zip,
    _build_yolo_zip,
    _export_lf_project,
    _export_lf_video_tracks,
    _fetch_project_data,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _artifact_root() -> Path:
    root = Path(settings.public_api_export_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_artifact(*, filename: str, payload: bytes) -> str:
    artifact_path = _artifact_root() / filename
    artifact_path.write_bytes(payload)
    return str(artifact_path)


def _build_export_payload(
    *,
    db: Session,
    project: Project,
    current_user,
    export_format: str,
) -> tuple[bytes, str, str]:
    _project, items, annotations, label_classes = _fetch_project_data(
        db, project.id, current_user
    )
    items_by_id = {item.id: item for item in items}
    label_map = {label_class.id: index for index, label_class in enumerate(sorted(label_classes, key=lambda current: current.id))}
    label_classes_by_id = {label_class.id: label_class for label_class in label_classes}

    if export_format == "json":
        records = _annotation_export_records(
            project,
            annotations,
            items_by_id,
            label_map,
            label_classes_by_id,
        )
        return (
            json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
            f"frame_pin_project_{project.id}_annotations.json",
        )

    if export_format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "project_id",
                "item_id",
                "item_path",
                "item_kind",
                "client_uid",
                "frame_index",
                "track_id",
                "propagation_frames",
                "is_occluded",
                "is_truncated",
                "is_outside",
                "is_lost",
                "label_class_id",
                "label_class_index",
                "geometry_kind",
                "x1",
                "y1",
                "x2",
                "y2",
                "polygon_points_json",
                "status",
            ]
        )
        for record in _annotation_export_records(
            project,
            annotations,
            items_by_id,
            label_map,
            label_classes_by_id,
        ):
            writer.writerow(
                [
                    record["project_id"],
                    record["item_id"],
                    record["item_path"],
                    record["item_kind"],
                    record["client_uid"],
                    record["frame_index"],
                    record["track_id"],
                    record["propagation_frames"],
                    record["is_occluded"],
                    record["is_truncated"],
                    record["is_outside"],
                    record["is_lost"],
                    record["label_class_id"],
                    record["label_class_index"],
                    record["geometry_kind"],
                    record["bbox"][0],
                    record["bbox"][1],
                    record["bbox"][2],
                    record["bbox"][3],
                    json.dumps(record["polygon_points"], ensure_ascii=False, separators=(",", ":"))
                    if record["polygon_points"]
                    else "",
                    record["status"],
                ]
            )
        return (
            buffer.getvalue().encode("utf-8"),
            "text/csv",
            f"frame_pin_project_{project.id}.csv",
        )

    if export_format == "yolo":
        return (
            _build_yolo_zip(project, items, annotations, label_classes),
            "application/zip",
            f"frame_pin_project_{project.id}_yolo.zip",
        )

    if export_format == "lf_video_tracks":
        payload = _export_lf_video_tracks(project, items, annotations, label_classes)
        return (
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
            f"frame_pin_project_{project.id}_lf_video_tracks.json",
        )

    if export_format == "lf_project":
        payload = _export_lf_project(project, items, annotations, label_classes)
        return (
            json.dumps(json.loads(payload), ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
            f"frame_pin_project_{project.id}_lf_project.json",
        )

    if export_format == "original_media":
        archive_file = _build_original_media_zip(project, items)
        try:
            return (
                archive_file.read(),
                "application/zip",
                f"frame_pin_project_{project.id}_original_media.zip",
            )
        finally:
            archive_file.close()

    raise HTTPException(status_code=400, detail="Unsupported export format")


def process_export_job(
    db: Session,
    *,
    job: ExportJob,
    project: Project,
    current_user,
) -> ExportJob:
    job.status = ExportJobStatus.running
    job.started_at = _utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        payload, content_type, download_name = _build_export_payload(
            db=db,
            project=project,
            current_user=current_user,
            export_format=job.format,
        )
        suffix = Path(download_name).suffix or ".bin"
        artifact_path = _write_artifact(
            filename=f"export_job_{job.id}_{uuid4().hex}{suffix}",
            payload=payload,
        )
        job.artifact_path = artifact_path
        job.content_type = content_type
        job.download_name = download_name
        job.status = ExportJobStatus.completed
        job.error_message = None
        job.completed_at = _utcnow()
    except HTTPException as exc:
        job.status = ExportJobStatus.failed
        job.error_message = str(exc.detail)
        job.completed_at = _utcnow()
    except Exception as exc:  # pragma: no cover - defensive fallback
        job.status = ExportJobStatus.failed
        job.error_message = str(exc)
        job.completed_at = _utcnow()

    db.add(job)
    db.commit()
    db.refresh(job)
    return job
