from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..database import get_db
from ..models import (
    Annotation,
    Item,
    ItemKind,
    LabelClass,
    LabelGeometryKind,
    Project,
    UserRole,
)
from ..security import ensure_project_team_access, require_roles
from ..services.audit import log_audit

router = APIRouter(tags=["export"])

YOLO_EXPORTABLE_GEOMETRIES = {
    LabelGeometryKind.bbox,
    LabelGeometryKind.polygon,
}


def _fetch_project_data(
    db: Session, project_id: int, current_user
) -> tuple[Project, list[Item], list[Annotation], list[LabelClass]]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ensure_project_team_access(project, current_user)

    items = db.execute(select(Item).where(Item.project_id == project_id)).scalars().all()
    annotations = (
        db.execute(
            select(Annotation)
            .join(Item, Annotation.item_id == Item.id)
            .where(Item.project_id == project_id)
        )
        .scalars()
        .all()
    )
    label_classes = (
        db.execute(select(LabelClass).where(LabelClass.project_id == project_id))
        .scalars()
        .all()
    )
    return project, items, annotations, label_classes


def _yolo_bbox(ann: Annotation, item: Item) -> tuple[float, float, float, float]:
    x_center = ((ann.x1 + ann.x2) / 2.0) / item.w
    y_center = ((ann.y1 + ann.y2) / 2.0) / item.h
    width = abs(ann.x2 - ann.x1) / item.w
    height = abs(ann.y2 - ann.y1) / item.h
    return x_center, y_center, width, height




def _clip_to_image_range(value: float, max_value: float) -> float:
    return max(0.0, min(float(max_value), float(value)))


def _normalize_bbox_for_export(
    ann: Annotation, item: Item
) -> tuple[float, float, float, float] | None:
    x1 = _clip_to_image_range(min(ann.x1, ann.x2), item.w)
    y1 = _clip_to_image_range(min(ann.y1, ann.y2), item.h)
    x2 = _clip_to_image_range(max(ann.x1, ann.x2), item.w)
    y2 = _clip_to_image_range(max(ann.y1, ann.y2), item.h)

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2

def _clone_polygon_points(
    points: list[list[float]] | None,
) -> list[list[float]] | None:
    if not points:
        return None
    return [[float(x), float(y)] for x, y in points]


def _flatten_polygon_points(points: list[list[float]] | None) -> list[float] | None:
    if not points:
        return None
    return [coordinate for point in points for coordinate in point]


def _polygon_points_json(points: list[list[float]] | None) -> str:
    if not points:
        return ""
    return json.dumps(points, ensure_ascii=False, separators=(",", ":"))


def _annotation_flags_dict(ann: Annotation) -> dict[str, bool]:
    return {
        "occluded": bool(getattr(ann, "is_occluded", False)),
        "truncated": bool(getattr(ann, "is_truncated", False)),
        "outside": bool(getattr(ann, "is_outside", False)),
        "lost": bool(getattr(ann, "is_lost", False)),
    }


def _yolo_polygon(ann: Annotation, item: Item) -> list[float] | None:
    points = ann.polygon_points
    if not points:
        return None

    normalized_points: list[float] = []
    for x, y in points:
        normalized_points.extend([x / item.w, y / item.h])
    return normalized_points




def _normalize_polygon_for_export(
    ann: Annotation, item: Item
) -> list[tuple[float, float]] | None:
    points = ann.polygon_points
    if not points:
        return None

    normalized_points: list[tuple[float, float]] = []
    for x, y in points:
        clipped_point = (
            _clip_to_image_range(x, item.w),
            _clip_to_image_range(y, item.h),
        )
        if not normalized_points or normalized_points[-1] != clipped_point:
            normalized_points.append(clipped_point)

    if len(normalized_points) > 1 and normalized_points[0] == normalized_points[-1]:
        normalized_points.pop()

    if len(normalized_points) < 3:
        return None

    return normalized_points


def _yolo_bbox_line(ann: Annotation, item: Item, class_idx: int) -> str | None:
    bbox = _normalize_bbox_for_export(ann, item)
    if not bbox:
        return None

    x1, y1, x2, y2 = bbox
    x_center = ((x1 + x2) / 2.0) / item.w
    y_center = ((y1 + y2) / 2.0) / item.h
    width = (x2 - x1) / item.w
    height = (y2 - y1) / item.h
    return f"{class_idx} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}"


def _yolo_polygon_line(ann: Annotation, item: Item, class_idx: int) -> str | None:
    points = _normalize_polygon_for_export(ann, item)
    if not points:
        return None

    normalized_values: list[str] = []
    for x, y in points:
        normalized_values.append(f"{x / item.w:.6f}")
        normalized_values.append(f"{y / item.h:.6f}")

    return f"{class_idx} {' '.join(normalized_values)}"


def _safe_yolo_label_stem(item: Item) -> str:
    stem = Path(item.path).stem or f"item_{item.id}"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return safe_stem or f"item_{item.id}"


def _static_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "static"


def _item_storage_path(item: Item) -> Path:
    path_parts = PurePosixPath(str(item.path).replace("\\", "/")).parts
    return _static_root().joinpath(*path_parts)


def _original_media_archive_name(project_id: int, item: Item) -> str:
    raw_path = PurePosixPath(str(item.path).replace("\\", "/"))
    project_prefix = PurePosixPath("uploads") / f"project_{project_id}"
    try:
        archive_path = raw_path.relative_to(project_prefix)
    except ValueError:
        archive_path = raw_path

    archive_name = archive_path.as_posix().strip("/")
    if not archive_name:
        raise HTTPException(
            status_code=409,
            detail=f"Original media export aborted because item {item.id} has an empty archive path.",
        )
    return archive_name


def _build_original_media_zip(
    project: Project,
    items: list[Item],
) -> tempfile.SpooledTemporaryFile[bytes]:
    archive_names_by_item_id: dict[int, str] = {}
    seen_archive_names: dict[str, int] = {}
    missing_files: list[str] = []
    duplicate_names: list[str] = []

    for item in sorted(items, key=lambda current_item: current_item.id):
        file_path = _item_storage_path(item)
        if not file_path.is_file():
            missing_files.append(f"item {item.id}: {item.path}")
            continue

        archive_name = _original_media_archive_name(project.id, item)
        previous_item_id = seen_archive_names.get(archive_name)
        if previous_item_id is not None:
            duplicate_names.append(
                f"{archive_name} (items {previous_item_id} and {item.id})"
            )
            continue

        seen_archive_names[archive_name] = item.id
        archive_names_by_item_id[item.id] = archive_name

    if missing_files:
        missing_summary = "; ".join(missing_files[:5])
        raise HTTPException(
            status_code=409,
            detail=(
                "Original media export aborted because some source files are missing on disk: "
                f"{missing_summary}"
            ),
        )

    if duplicate_names:
        duplicate_summary = "; ".join(duplicate_names[:5])
        raise HTTPException(
            status_code=409,
            detail=(
                "Original media export aborted because duplicate archive paths would overwrite files: "
                f"{duplicate_summary}"
            ),
        )

    archive_file: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(
        max_size=64 * 1024 * 1024
    )
    # ZIP_STORED keeps the original bytes verbatim inside the archive.
    with zipfile.ZipFile(
        archive_file,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        for item in sorted(items, key=lambda current_item: current_item.id):
            archive_name = archive_names_by_item_id.get(item.id)
            if not archive_name:
                continue
            archive.write(_item_storage_path(item), arcname=archive_name)

    archive_file.seek(0)
    return archive_file


def _build_yolo_zip(
    project: Project,
    items: list[Item],
    annotations: list[Annotation],
    label_classes: list[LabelClass],
) -> bytes:
    if any(item.kind != ItemKind.image for item in items):
        raise HTTPException(
            status_code=400,
            detail=(
                "YOLO export currently supports image items only. "
                "Use lf_video_tracks or lf_project for video annotations."
            ),
        )

    exportable_label_classes = [
        label_class
        for label_class in sorted(label_classes, key=lambda lc: lc.id)
        if label_class.geometry_kind in YOLO_EXPORTABLE_GEOMETRIES
    ]
    if not exportable_label_classes:
        raise HTTPException(
            status_code=400,
            detail="YOLO export requires bbox or polygon label classes",
        )

    label_map = {
        label_class.id: index
        for index, label_class in enumerate(exportable_label_classes)
    }
    label_classes_by_id = {label_class.id: label_class for label_class in label_classes}

    annotations_by_item_id: dict[int, list[Annotation]] = {}
    for annotation in annotations:
        annotations_by_item_id.setdefault(annotation.item_id, []).append(annotation)

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(
        archive_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "classes.txt",
            "\n".join(label_class.name for label_class in exportable_label_classes),
        )
        archive.writestr(
            "README.txt",
            "\n".join(
                [
                    f"FramePin YOLO export for project {project.id}",
                    "",
                    "- One label file per image item",
                    "- bbox -> YOLO detection line format",
                    "- polygon -> YOLO segmentation line format",
                    "- tag geometry is skipped because YOLO does not support it",
                    "- Out-of-bounds coordinates are clipped during export",
                ]
            ),
        )

        manifest_rows = ["item_id\titem_path\tlabel_file"]
        for item in sorted(items, key=lambda current_item: current_item.id):
            label_filename = f"labels/{item.id:06d}_{_safe_yolo_label_stem(item)}.txt"
            label_lines: list[str] = []

            for annotation in sorted(
                annotations_by_item_id.get(item.id, []),
                key=lambda current_annotation: (current_annotation.id, current_annotation.created_at),
            ):
                label_class = label_classes_by_id.get(annotation.label_class_id)
                if not label_class:
                    continue
                if label_class.geometry_kind == LabelGeometryKind.tag:
                    continue

                class_idx = label_map.get(annotation.label_class_id)
                if class_idx is None:
                    continue

                if label_class.geometry_kind == LabelGeometryKind.polygon:
                    label_line = _yolo_polygon_line(annotation, item, class_idx)
                else:
                    label_line = _yolo_bbox_line(annotation, item, class_idx)

                if label_line:
                    label_lines.append(label_line)

            archive.writestr(label_filename, "\n".join(label_lines))
            manifest_rows.append(f"{item.id}\t{item.path}\t{label_filename}")

        archive.writestr("manifest.tsv", "\n".join(manifest_rows))

    return archive_buffer.getvalue()


def _annotations_iter(
    annotations: list[Annotation],
    items_by_id: dict[int, Item],
    label_map: dict[int, int],
    label_classes_by_id: dict[int, LabelClass],
) -> Iterable[str]:
    for ann in annotations:
        item = items_by_id.get(ann.item_id)
        if not item:
            continue
        class_idx = label_map.get(ann.label_class_id, ann.label_class_id)
        label_class = label_classes_by_id.get(ann.label_class_id)
        if (
            label_class
            and label_class.geometry_kind == LabelGeometryKind.polygon
            and ann.polygon_points
        ):
            polygon_values = _yolo_polygon(ann, item)
            if polygon_values:
                coords = " ".join(f"{value:.6f}" for value in polygon_values)
                frame = ann.frame_index if ann.frame_index is not None else 0
                yield f"{class_idx} {coords} {frame} # {item.path}"
                continue
        x_center, y_center, width, height = _yolo_bbox(ann, item)
        frame = ann.frame_index if ann.frame_index is not None else 0
        yield f"{class_idx} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f} {frame} # {item.path}"


def _build_track_segments(track_annotations: list[Annotation]) -> list[dict]:
    sparse_annotations = [
        annotation
        for annotation in track_annotations
        if annotation.frame_index is not None
    ]
    sparse_annotations.sort(
        key=lambda annotation: (annotation.frame_index or 0, annotation.id)
    )

    segments: list[dict] = []
    for annotation in sparse_annotations:
        start_frame = int(annotation.frame_index or 0)
        end_frame = int(start_frame + max(0, annotation.propagation_frames or 0))
        next_segment = {
            "start_frame": start_frame,
            "end_frame": end_frame,
            "bbox_xyxy": [annotation.x1, annotation.y1, annotation.x2, annotation.y2],
            "polygon_points": _clone_polygon_points(annotation.polygon_points),
            "flags": _annotation_flags_dict(annotation),
        }

        previous_segment = segments[-1] if segments else None
        if (
            previous_segment
            and previous_segment["end_frame"] + 1 == next_segment["start_frame"]
            and previous_segment["bbox_xyxy"] == next_segment["bbox_xyxy"]
            and previous_segment["polygon_points"] == next_segment["polygon_points"]
            and previous_segment["flags"] == next_segment["flags"]
        ):
            previous_segment["end_frame"] = next_segment["end_frame"]
            continue

        segments.append(next_segment)

    return segments


def _annotation_export_records(
    project: Project,
    annotations: list[Annotation],
    items_by_id: dict[int, Item],
    label_map: dict[int, int],
    label_classes_by_id: dict[int, LabelClass],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for ann in annotations:
        item = items_by_id.get(ann.item_id)
        if not item:
            continue
        label_class = label_classes_by_id.get(ann.label_class_id)
        payload = {
            "project_id": project.id,
            "item_id": ann.item_id,
            "item_path": item.path,
            "item_kind": item.kind.value,
            "client_uid": ann.client_uid,
            "frame_index": ann.frame_index,
            "track_id": ann.track_id,
            "propagation_frames": ann.propagation_frames,
            "is_occluded": ann.is_occluded,
            "is_truncated": ann.is_truncated,
            "is_outside": ann.is_outside,
            "is_lost": ann.is_lost,
            "label_class_id": ann.label_class_id,
            "label_class_index": label_map.get(ann.label_class_id, ann.label_class_id),
            "geometry_kind": (
                label_class.geometry_kind.value if label_class else "bbox"
            ),
            "bbox": [ann.x1, ann.y1, ann.x2, ann.y2],
            "polygon_points": _clone_polygon_points(ann.polygon_points),
            "status": ann.status.value,
        }
        records.append(payload)
    return records


def _export_lf_video_tracks(
    project: Project,
    items: list[Item],
    annotations: list[Annotation],
    label_classes: list[LabelClass],
) -> list[dict[str, Any]]:
    """
    Project-wide export in FramePin-specific lf_video_tracks JSON format.

    - 1 record per video item
    - Each record has: project, item, label_classes, tracks, single_frame_boxes
    """
    # Map label_class_id -> stable index (0..N-1)
    label_map = {
        lc.id: idx
        for idx, lc in enumerate(sorted(label_classes, key=lambda l: l.id))
    }
    label_classes_by_id = {lc.id: lc for lc in label_classes}

    # Group annotations by item_id
    anns_by_item: dict[int, list[Annotation]] = {}
    for ann in annotations:
        anns_by_item.setdefault(ann.item_id, []).append(ann)

    # Per-record label_classes payload (same for all items in this project)
    label_classes_payload = [
        {
            "id": lc.id,
            "index": label_map.get(lc.id, lc.id),
            "name": lc.name,
            "geometry_kind": lc.geometry_kind.value,
            "color_hex": lc.color_hex,
        }
        for lc in label_classes
    ]

    records: list[dict[str, Any]] = []

    for item in items:
        # Only export videos in this format
        if item.kind != ItemKind.video:
            continue

        item_anns = anns_by_item.get(item.id, [])

        tracks_by_id: dict[int, list[Annotation]] = {}
        single_frame_boxes: list[Annotation] = []

        for ann in item_anns:
            if ann.track_id is not None:
                tracks_by_id.setdefault(ann.track_id, []).append(ann)
            else:
                single_frame_boxes.append(ann)

        # Build track payloads
        tracks_payload: list[dict] = []
        for track_id, track_anns in sorted(tracks_by_id.items(), key=lambda kv: kv[0]):
            if not track_anns:
                continue

            # In current UI, all anns in a track share the same label_class_id;
            # if not, we fall back to the first.
            lc_id = track_anns[0].label_class_id
            label_class = label_classes_by_id.get(lc_id)
            segments = _build_track_segments(track_anns)
            if not segments:
                continue

            status_values = {a.status.value for a in track_anns}
            track_status = (
                status_values.pop() if len(status_values) == 1 else "mixed"
            )

            tracks_payload.append(
                {
                    "track_id": int(track_id),
                    "label_class_id": lc_id,
                    "label_class_index": label_map.get(lc_id, lc_id),
                    "geometry_kind": (
                        label_class.geometry_kind.value if label_class else "bbox"
                    ),
                    "status": track_status,
                    "segments": segments,
                }
            )

        # Annotations without track_id are exported as single_frame_boxes
        single_boxes_payload: list[dict] = []
        for ann in single_frame_boxes:
            frame_idx = ann.frame_index if ann.frame_index is not None else 0
            lc_id = ann.label_class_id
            label_class = label_classes_by_id.get(lc_id)
            single_boxes_payload.append(
                {
                    "frame_index": int(frame_idx),
                    "propagation_frames": int(max(0, ann.propagation_frames or 0)),
                    "label_class_id": lc_id,
                    "label_class_index": label_map.get(lc_id, lc_id),
                    "geometry_kind": (
                        label_class.geometry_kind.value if label_class else "bbox"
                    ),
                    "status": ann.status.value,
                    "bbox_xyxy": [ann.x1, ann.y1, ann.x2, ann.y2],
                    "polygon_points": _clone_polygon_points(ann.polygon_points),
                    "flags": _annotation_flags_dict(ann),
                }
            )

        record = {
            "project": {
                "id": project.id,
                "name": project.name,
            },
            "item": {
                "id": item.id,
                "path": item.path,
                "kind": item.kind.value,
                "width": item.w,
                "height": item.h,
                "fps": item.fps,
                "duration_sec": item.duration_sec,
            },
            "label_classes": label_classes_payload,
            "tracks": tracks_payload,
            "single_frame_boxes": single_boxes_payload,
        }

        records.append(record)

    return records


def _export_lf_project(
    project: Project,
    items: list[Item],
    annotations: list[Annotation],
    label_classes: list[LabelClass],
) -> str:
    """
    Compact, non-redundant project export (minified JSON).

    Top-level:
      - schema, project, statuses, label_classes, items

    Per item:
      - Images: anns = [ [class_idx, x1, y1, x2, y2, status_idx], ... ]
      - Videos:
          tracks = [ {id, c, s, seg}, ... ]
            where seg = [ [start_frame, end_frame, x1, y1, x2, y2], ... ]
          boxes  = [ [frame, c, x1, y1, x2, y2, status_idx], ... ]  # track_id is None
    """
    statuses = ["pending", "approved", "rejected", "mixed"]
    status_map = {s: i for i, s in enumerate(statuses)}

    label_classes_sorted = sorted(label_classes, key=lambda l: l.id)
    label_map = {lc.id: idx for idx, lc in enumerate(label_classes_sorted)}

    anns_by_item: dict[int, list[Annotation]] = {}
    for ann in annotations:
        anns_by_item.setdefault(ann.item_id, []).append(ann)

    label_classes_payload = [
        {
            "id": lc.id,
            "idx": label_map[lc.id],
            "name": lc.name,
            "geom": lc.geometry_kind.value,
            "color": lc.color_hex,
            "key": lc.shortcut_key,
        }
        for lc in label_classes_sorted
    ]

    items_payload: list[dict] = []

    for item in sorted(items, key=lambda i: i.id):
        rec: dict = {
            "id": item.id,
            "path": item.path,
            "kind": item.kind.value,
            "w": item.w,
            "h": item.h,
            "fps": item.fps,
            "dur": item.duration_sec,
            "status": item.status.value,
        }

        item_anns = anns_by_item.get(item.id, [])

        if item.kind == ItemKind.video:
            tracks_by_id: dict[int, list[Annotation]] = {}
            single_boxes: list[list] = []
            single_box_runs: list[int] = []
            single_box_polygons: list[list[float] | None] = []
            single_box_flags: list[dict[str, bool]] = []

            for ann in item_anns:
                if ann.track_id is not None:
                    tracks_by_id.setdefault(ann.track_id, []).append(ann)
                else:
                    frame = int(ann.frame_index) if ann.frame_index is not None else 0
                    c = label_map.get(ann.label_class_id, ann.label_class_id)
                    s = status_map.get(ann.status.value, 0)
                    single_boxes.append([frame, c, ann.x1, ann.y1, ann.x2, ann.y2, s])
                    single_box_runs.append(int(max(0, ann.propagation_frames or 0)))
                    single_box_polygons.append(
                        _flatten_polygon_points(ann.polygon_points)
                    )
                    single_box_flags.append(_annotation_flags_dict(ann))

            tracks_payload: list[dict] = []
            for track_id, track_anns in sorted(tracks_by_id.items(), key=lambda kv: kv[0]):
                if not track_anns:
                    continue

                lc_id = track_anns[0].label_class_id
                c = label_map.get(lc_id, lc_id)

                status_values = {a.status.value for a in track_anns}
                track_status = status_values.pop() if len(status_values) == 1 else "mixed"
                s = status_map.get(track_status, 0)

                segs: list[list] = []
                seg_polygons: list[list[float] | None] = []
                seg_flags: list[dict[str, bool]] = []
                for seg in _build_track_segments(track_anns):
                    x1, y1, x2, y2 = seg["bbox_xyxy"]
                    segs.append([seg["start_frame"], seg["end_frame"], x1, y1, x2, y2])
                    seg_polygons.append(
                        _flatten_polygon_points(seg.get("polygon_points"))
                    )
                    seg_flags.append(seg.get("flags") or {})

                if not segs:
                    continue

                track_payload = {"id": int(track_id), "c": c, "s": s, "seg": segs}
                if any(points is not None for points in seg_polygons):
                    track_payload["poly"] = seg_polygons
                if any(any(flags.values()) for flags in seg_flags):
                    track_payload["seg_flags"] = seg_flags
                tracks_payload.append(track_payload)

            rec["tracks"] = tracks_payload
            rec["boxes"] = single_boxes
            if any(run_length > 0 for run_length in single_box_runs):
                rec["box_runs"] = single_box_runs
            if any(points is not None for points in single_box_polygons):
                rec["box_polygons"] = single_box_polygons
            if any(any(flags.values()) for flags in single_box_flags):
                rec["box_flags"] = single_box_flags
        else:
            anns_payload: list[list] = []
            ann_polygons_payload: list[list[float] | None] = []
            ann_flags_payload: list[dict[str, bool]] = []
            for ann in item_anns:
                c = label_map.get(ann.label_class_id, ann.label_class_id)
                s = status_map.get(ann.status.value, 0)
                anns_payload.append([c, ann.x1, ann.y1, ann.x2, ann.y2, s])
                ann_polygons_payload.append(
                    _flatten_polygon_points(ann.polygon_points)
                )
                ann_flags_payload.append(_annotation_flags_dict(ann))
            rec["anns"] = anns_payload
            if any(points is not None for points in ann_polygons_payload):
                rec["ann_polygons"] = ann_polygons_payload
            if any(any(flags.values()) for flags in ann_flags_payload):
                rec["ann_flags"] = ann_flags_payload

        items_payload.append(rec)

    payload = {
        "schema": "lf_project_v2",
        "project": {"id": project.id, "name": project.name},
        "statuses": statuses,
        "label_classes": label_classes_payload,
        "items": items_payload,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@router.get("/export/project/{project_id}")
def export_project(
    project_id: int,
    format: str = Query(
        "json",
        pattern="^(json|csv|yolo|lf_video_tracks|lf_project|original_media)$",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(
        require_roles(UserRole.reviewer, UserRole.project_admin, UserRole.annotator)
    ),
):
    project, items, annotations, label_classes = _fetch_project_data(
        db, project_id, current_user
    )
    items_by_id = {item.id: item for item in items}
    label_map = {lc.id: idx for idx, lc in enumerate(sorted(label_classes, key=lambda l: l.id))}
    label_classes_by_id = {lc.id: lc for lc in label_classes}

    if format == "json":
        records = _annotation_export_records(
            project,
            annotations,
            items_by_id,
            label_map,
            label_classes_by_id,
        )
        response = PlainTextResponse(
            json.dumps(records, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    elif format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
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
        for ann in annotations:
            item = items_by_id.get(ann.item_id)
            if not item:
                continue
            label_class = label_classes_by_id.get(ann.label_class_id)
            writer.writerow(
                [
                    project.id,
                    ann.item_id,
                    item.path,
                    item.kind.value,
                    ann.client_uid,
                    ann.frame_index,
                    ann.track_id,
                    ann.propagation_frames,
                    ann.is_occluded,
                    ann.is_truncated,
                    ann.is_outside,
                    ann.is_lost,
                    ann.label_class_id,
                    label_map.get(ann.label_class_id, ann.label_class_id),
                    label_class.geometry_kind.value if label_class else "bbox",
                    ann.x1,
                    ann.y1,
                    ann.x2,
                    ann.y2,
                    _polygon_points_json(ann.polygon_points),
                    ann.status.value,
                ]
            )
        response = PlainTextResponse(buf.getvalue(), media_type="text/csv")
    elif format == "yolo":
        response = Response(
            content=_build_yolo_zip(project, items, annotations, label_classes),
            media_type="application/zip",
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="frame_pin_project_{project.id}_yolo.zip"'
        )
    elif format == "lf_video_tracks":
        records = _export_lf_video_tracks(project, items, annotations, label_classes)
        response = PlainTextResponse(
            json.dumps(records, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    elif format == "lf_project":
        payload = _export_lf_project(project, items, annotations, label_classes)
        payload = json.dumps(
            json.loads(payload),
            ensure_ascii=False,
            indent=2,
        )
        response = PlainTextResponse(payload, media_type="application/json")
    elif format == "original_media":
        archive_file = _build_original_media_zip(project, items)
        response = StreamingResponse(
            iter(lambda: archive_file.read(1024 * 1024), b""),
            media_type="application/zip",
            background=BackgroundTask(archive_file.close),
        )
        response.headers["Content-Disposition"] = (
            f'attachment; filename="frame_pin_project_{project.id}_original_media.zip"'
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported format")

    log_audit(
        db,
        actor_id=current_user.id,
        object_type="project",
        object_id=project.id,
        action="export_project",
        payload={
            "format": format,
            "item_count": len(items),
            "annotation_count": len(annotations),
        },
    )
    db.commit()
    return response
