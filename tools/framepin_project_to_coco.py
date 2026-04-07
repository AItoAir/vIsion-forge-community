# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STATUSES = ("pending", "approved", "rejected", "mixed")
EXPORTABLE_GEOMETRIES = {"bbox", "polygon"}


@dataclass(frozen=True)
class LabelClassInfo:
    framepin_id: int
    framepin_idx: int
    coco_id: int | None
    name: str
    geometry_kind: str
    color_hex: str | None
    shortcut_key: str | None


@dataclass(frozen=True)
class ConversionStats:
    image_count: int
    annotation_count: int
    skipped_non_image_items: int
    skipped_filtered_statuses: int
    skipped_unknown_classes: int
    skipped_unsupported_geometries: int
    skipped_invalid_annotations: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="framepin_project_to_coco",
        description=(
            "Convert a FramePin lf_project_v2 JSON export into a COCO annotations JSON file."
        ),
    )
    parser.add_argument(
        "input",
        help="Path to the FramePin lf_project_v2 JSON file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Output COCO JSON path. Defaults to replacing '_lf_project.json' with "
            "'_coco.json' next to the input file."
        ),
    )
    parser.add_argument(
        "--statuses",
        default="",
        help=(
            "Comma-separated annotation statuses to include. "
            "Default: include every status listed in the input export."
        ),
    )
    parser.add_argument(
        "--clip-to-image",
        action="store_true",
        help="Clip bbox and polygon coordinates into the image bounds before export.",
    )
    parser.add_argument(
        "--file-name-mode",
        choices=("path", "basename"),
        default="path",
        help=(
            "How to populate COCO image.file_name. "
            "'path' keeps the exported FramePin path, 'basename' keeps only the filename."
        ),
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation width. Use 0 for compact output.",
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must be a single object.")
    return payload


def _default_output_path(input_path: Path) -> Path:
    name = input_path.name
    if name.endswith("_lf_project.json"):
        return input_path.with_name(name[: -len("_lf_project.json")] + "_coco.json")
    if name.endswith(".json"):
        return input_path.with_name(input_path.stem + ".coco.json")
    return input_path.with_name(input_path.name + ".coco.json")


def _normalize_statuses(payload: dict[str, Any]) -> list[str]:
    raw_statuses = payload.get("statuses")
    if not isinstance(raw_statuses, list):
        return list(DEFAULT_STATUSES)

    statuses = [str(value).strip() for value in raw_statuses if str(value).strip()]
    return statuses or list(DEFAULT_STATUSES)


def _parse_status_filter(raw_value: str, available_statuses: list[str]) -> set[str] | None:
    requested = {
        status.strip()
        for status in str(raw_value or "").split(",")
        if status.strip()
    }
    if not requested:
        return None

    unknown = sorted(requested.difference(available_statuses))
    if unknown:
        raise ValueError(
            "Unknown annotation status filter(s): "
            + ", ".join(unknown)
            + ". Available: "
            + ", ".join(available_statuses)
        )
    return requested


def _require_lf_project_v2(payload: dict[str, Any]) -> None:
    schema = str(payload.get("schema") or "").strip()
    if schema != "lf_project_v2":
        raise ValueError(
            f"Unsupported schema {schema!r}. Expected 'lf_project_v2'."
        )


def _coerce_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer for {field_name}: {value!r}") from exc


def _coerce_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float for {field_name}: {value!r}") from exc


def _clip_value(value: float, max_value: float) -> float:
    return max(0.0, min(float(max_value), float(value)))


def _safe_list_index(values: list[Any] | None, index: int) -> Any | None:
    if not isinstance(values, list):
        return None
    if index < 0 or index >= len(values):
        return None
    return values[index]


def _normalize_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    width: float,
    height: float,
    clip_to_image: bool,
) -> list[float] | None:
    left = min(float(x1), float(x2))
    top = min(float(y1), float(y2))
    right = max(float(x1), float(x2))
    bottom = max(float(y1), float(y2))

    if clip_to_image:
        left = _clip_value(left, width)
        top = _clip_value(top, height)
        right = _clip_value(right, width)
        bottom = _clip_value(bottom, height)

    box_width = right - left
    box_height = bottom - top
    if box_width <= 0.0 or box_height <= 0.0:
        return None
    return [left, top, box_width, box_height]


def _normalize_polygon(
    raw_points: Any,
    *,
    width: float,
    height: float,
    clip_to_image: bool,
) -> list[float] | None:
    if not isinstance(raw_points, list) or len(raw_points) < 6 or len(raw_points) % 2 != 0:
        return None

    points: list[tuple[float, float]] = []
    for index in range(0, len(raw_points), 2):
        x = _coerce_float(raw_points[index], f"polygon[{index}]")
        y = _coerce_float(raw_points[index + 1], f"polygon[{index + 1}]")
        if clip_to_image:
            x = _clip_value(x, width)
            y = _clip_value(y, height)
        point = (x, y)
        if not points or points[-1] != point:
            points.append(point)

    if len(points) > 1 and points[0] == points[-1]:
        points.pop()

    if len(points) < 3:
        return None

    flattened: list[float] = []
    for x, y in points:
        flattened.extend([x, y])
    return flattened


def _bbox_from_polygon(flattened_points: list[float]) -> list[float] | None:
    if not flattened_points:
        return None
    xs = flattened_points[0::2]
    ys = flattened_points[1::2]
    left = min(xs)
    top = min(ys)
    right = max(xs)
    bottom = max(ys)
    box_width = right - left
    box_height = bottom - top
    if box_width <= 0.0 or box_height <= 0.0:
        return None
    return [left, top, box_width, box_height]


def _polygon_area(flattened_points: list[float]) -> float:
    if len(flattened_points) < 6:
        return 0.0

    xs = flattened_points[0::2]
    ys = flattened_points[1::2]
    area = 0.0
    for index, current_x in enumerate(xs):
        next_index = (index + 1) % len(xs)
        area += current_x * ys[next_index]
        area -= xs[next_index] * ys[index]
    return abs(area) / 2.0


def _build_label_maps(
    payload: dict[str, Any],
) -> tuple[dict[int, LabelClassInfo], list[dict[str, Any]]]:
    raw_label_classes = payload.get("label_classes")
    if not isinstance(raw_label_classes, list):
        raise ValueError("Input JSON is missing label_classes.")

    labels_by_idx: dict[int, LabelClassInfo] = {}
    exportable_categories: list[dict[str, Any]] = []
    next_coco_id = 1

    sorted_label_classes = sorted(
        (entry for entry in raw_label_classes if isinstance(entry, dict)),
        key=lambda entry: (_coerce_int(entry.get("idx"), "label_classes[idx]"), _coerce_int(entry.get("id"), "label_classes[id]")),
    )

    for raw_label in sorted_label_classes:
        framepin_id = _coerce_int(raw_label.get("id"), "label_classes[id]")
        framepin_idx = _coerce_int(raw_label.get("idx"), "label_classes[idx]")
        if framepin_idx in labels_by_idx:
            raise ValueError(f"Duplicate label class idx detected: {framepin_idx}")

        geometry_kind = str(raw_label.get("geom") or "bbox").strip().lower()
        coco_id = next_coco_id if geometry_kind in EXPORTABLE_GEOMETRIES else None
        label_info = LabelClassInfo(
            framepin_id=framepin_id,
            framepin_idx=framepin_idx,
            coco_id=coco_id,
            name=str(raw_label.get("name") or f"class_{framepin_idx}"),
            geometry_kind=geometry_kind,
            color_hex=(str(raw_label.get("color")).strip() if raw_label.get("color") is not None else None),
            shortcut_key=(str(raw_label.get("key")).strip() if raw_label.get("key") is not None else None),
        )
        labels_by_idx[framepin_idx] = label_info

        if coco_id is None:
            continue

        exportable_categories.append(
            {
                "id": coco_id,
                "name": label_info.name,
                "supercategory": "framepin",
                "framepin_label_class_id": label_info.framepin_id,
                "framepin_label_class_index": label_info.framepin_idx,
                "framepin_geometry_kind": label_info.geometry_kind,
                "framepin_color": label_info.color_hex,
                "framepin_shortcut_key": label_info.shortcut_key,
            }
        )
        next_coco_id += 1

    if not exportable_categories:
        raise ValueError(
            "No COCO-exportable label classes found. Supported geometries: bbox, polygon."
        )

    return labels_by_idx, exportable_categories


def convert_lf_project_to_coco(
    payload: dict[str, Any],
    *,
    include_statuses: set[str] | None = None,
    clip_to_image: bool = False,
    file_name_mode: str = "path",
) -> tuple[dict[str, Any], ConversionStats]:
    _require_lf_project_v2(payload)
    if file_name_mode not in {"path", "basename"}:
        raise ValueError("file_name_mode must be either 'path' or 'basename'.")

    statuses = _normalize_statuses(payload)
    labels_by_idx, categories = _build_label_maps(payload)

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Input JSON is missing items.")

    project = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError("Input JSON is missing project metadata.")

    images: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []

    skipped_non_image_items = 0
    skipped_filtered_statuses = 0
    skipped_unknown_classes = 0
    skipped_unsupported_geometries = 0
    skipped_invalid_annotations = 0
    next_annotation_id = 1

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        item_kind = str(raw_item.get("kind") or "").strip().lower()
        if item_kind != "image":
            skipped_non_image_items += 1
            continue

        item_id = _coerce_int(raw_item.get("id"), "items[id]")
        item_path = str(raw_item.get("path") or "")
        width = _coerce_int(raw_item.get("w"), f"items[{item_id}].w")
        height = _coerce_int(raw_item.get("h"), f"items[{item_id}].h")
        file_name = item_path if file_name_mode == "path" else (Path(item_path).name or item_path)

        images.append(
            {
                "id": item_id,
                "width": width,
                "height": height,
                "file_name": file_name,
                "framepin_item_path": item_path,
                "framepin_display_path": raw_item.get("display_path"),
                "framepin_source_media_type": raw_item.get("source_media_type"),
                "framepin_item_status": raw_item.get("status"),
            }
        )

        raw_anns = raw_item.get("anns")
        ann_polygons = raw_item.get("ann_polygons")
        ann_flags = raw_item.get("ann_flags")
        if not isinstance(raw_anns, list):
            continue

        for ann_index, raw_ann in enumerate(raw_anns):
            if not isinstance(raw_ann, list) or len(raw_ann) < 6:
                skipped_invalid_annotations += 1
                continue

            label_idx = _coerce_int(raw_ann[0], f"items[{item_id}].anns[{ann_index}][0]")
            label_info = labels_by_idx.get(label_idx)
            if label_info is None:
                skipped_unknown_classes += 1
                continue
            if label_info.coco_id is None:
                skipped_unsupported_geometries += 1
                continue

            status_idx = _coerce_int(raw_ann[5], f"items[{item_id}].anns[{ann_index}][5]")
            annotation_status = (
                statuses[status_idx] if 0 <= status_idx < len(statuses) else str(status_idx)
            )
            if include_statuses is not None and annotation_status not in include_statuses:
                skipped_filtered_statuses += 1
                continue

            bbox = _normalize_bbox(
                _coerce_float(raw_ann[1], f"items[{item_id}].anns[{ann_index}][1]"),
                _coerce_float(raw_ann[2], f"items[{item_id}].anns[{ann_index}][2]"),
                _coerce_float(raw_ann[3], f"items[{item_id}].anns[{ann_index}][3]"),
                _coerce_float(raw_ann[4], f"items[{item_id}].anns[{ann_index}][4]"),
                width=float(width),
                height=float(height),
                clip_to_image=clip_to_image,
            )

            flattened_polygon = _normalize_polygon(
                _safe_list_index(ann_polygons, ann_index),
                width=float(width),
                height=float(height),
                clip_to_image=clip_to_image,
            )
            if bbox is None and flattened_polygon is not None:
                bbox = _bbox_from_polygon(flattened_polygon)

            if bbox is None:
                skipped_invalid_annotations += 1
                continue

            segmentation: list[list[float]] = []
            area = bbox[2] * bbox[3]
            if label_info.geometry_kind == "polygon" and flattened_polygon is not None:
                polygon_area = _polygon_area(flattened_polygon)
                if polygon_area > 0.0:
                    segmentation = [flattened_polygon]
                    area = polygon_area

            annotation_payload: dict[str, Any] = {
                "id": next_annotation_id,
                "image_id": item_id,
                "category_id": label_info.coco_id,
                "bbox": bbox,
                "area": area,
                "iscrowd": 0,
                "segmentation": segmentation,
                "framepin_annotation_status": annotation_status,
                "framepin_annotation_status_index": status_idx,
                "framepin_label_class_id": label_info.framepin_id,
                "framepin_label_class_index": label_info.framepin_idx,
                "framepin_geometry_kind": label_info.geometry_kind,
            }

            flags = _safe_list_index(ann_flags, ann_index)
            if isinstance(flags, dict) and flags:
                annotation_payload["framepin_flags"] = flags

            annotations.append(annotation_payload)
            next_annotation_id += 1

    now = datetime.now(timezone.utc).isoformat()
    coco_payload = {
        "info": {
            "description": "COCO annotations converted from FramePin lf_project_v2 export",
            "version": "1.0",
            "year": datetime.now(timezone.utc).year,
            "date_created": now,
        },
        "licenses": [],
        "images": images,
        "annotations": annotations,
        "categories": categories,
        "framepin": {
            "schema": payload.get("schema"),
            "project": {
                "id": project.get("id"),
                "name": project.get("name"),
            },
            "annotation_statuses": statuses,
            "included_annotation_statuses": (
                sorted(include_statuses) if include_statuses is not None else statuses
            ),
        },
    }

    stats = ConversionStats(
        image_count=len(images),
        annotation_count=len(annotations),
        skipped_non_image_items=skipped_non_image_items,
        skipped_filtered_statuses=skipped_filtered_statuses,
        skipped_unknown_classes=skipped_unknown_classes,
        skipped_unsupported_geometries=skipped_unsupported_geometries,
        skipped_invalid_annotations=skipped_invalid_annotations,
    )
    return coco_payload, stats


def main() -> int:
    args = _parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else _default_output_path(input_path).resolve()
    )

    try:
        payload = _read_json(input_path)
        statuses = _normalize_statuses(payload)
        include_statuses = _parse_status_filter(args.statuses, statuses)
        coco_payload, stats = convert_lf_project_to_coco(
            payload,
            include_statuses=include_statuses,
            clip_to_image=bool(args.clip_to_image),
            file_name_mode=str(args.file_name_mode),
        )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.indent and args.indent > 0:
        rendered = json.dumps(coco_payload, ensure_ascii=False, indent=args.indent)
    else:
        rendered = json.dumps(coco_payload, ensure_ascii=False, separators=(",", ":"))
    output_path.write_text(rendered, encoding="utf-8")

    print(f"Wrote COCO JSON: {output_path}")
    print(
        "Summary: "
        f"{stats.image_count} image(s), "
        f"{stats.annotation_count} annotation(s), "
        f"skipped non-image items={stats.skipped_non_image_items}, "
        f"skipped filtered statuses={stats.skipped_filtered_statuses}, "
        f"skipped unknown classes={stats.skipped_unknown_classes}, "
        f"skipped unsupported geometries={stats.skipped_unsupported_geometries}, "
        f"skipped invalid annotations={stats.skipped_invalid_annotations}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
