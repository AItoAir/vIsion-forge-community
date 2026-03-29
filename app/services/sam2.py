from __future__ import annotations

import inspect
import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from ..config import settings
from ..models import Item, ItemKind


logger = structlog.get_logger(__name__)

class Sam2Error(RuntimeError):
    """Base class for SAM2 integration errors."""


class Sam2UnavailableError(Sam2Error):
    """Raised when SAM2 runtime dependencies are not available."""


@dataclass(slots=True)
class Sam2PointPrompt:
    x: float
    y: float
    label: int


@dataclass(slots=True)
class Sam2PromptPayload:
    label_class_id: int
    frame_index: int | None
    box_xyxy: tuple[float, float, float, float] | None
    prompt_points: list[Sam2PointPrompt]
    track_id: int | None
    track_start_frame: int | None
    track_end_frame: int | None
    include_reverse: bool = True
    simplify_tolerance: float | None = None


@dataclass(slots=True)
class Sam2Suggestion:
    label_class_id: int
    frame_index: int | None
    track_id: int | None
    x1: float
    y1: float
    x2: float
    y2: float
    polygon_points: list[list[float]]


_IMAGE_RUNTIME: dict[str, Any] | None = None
_VIDEO_RUNTIME: dict[str, Any] | None = None
_CV_RUNTIME: dict[str, Any] | None = None
_IMAGE_RUNTIME_LOCK = threading.Lock()
_VIDEO_RUNTIME_LOCK = threading.Lock()
_CV_RUNTIME_LOCK = threading.Lock()
_FRAME_CACHE_LOCK = threading.Lock()


def sam2_feature_enabled() -> bool:
    return bool(settings.sam2_enabled)


def sam2_feature_configured() -> bool:
    checkpoint = (settings.sam2_checkpoint or "").strip()
    model_cfg = (settings.sam2_model_cfg or "").strip()
    if not sam2_feature_enabled() or not checkpoint or not model_cfg:
        return False
    return Path(checkpoint).expanduser().is_file()


def make_prompt_payload(
    *,
    label_class_id: int,
    frame_index: int | None,
    box_xyxy: list[float] | None,
    prompt_points: list[dict[str, Any]] | list[Sam2PointPrompt],
    track_id: int | None,
    track_start_frame: int | None,
    track_end_frame: int | None,
    include_reverse: bool,
    simplify_tolerance: float | None,
) -> Sam2PromptPayload:
    normalized_points: list[Sam2PointPrompt] = []
    for point in prompt_points or []:
        if isinstance(point, Sam2PointPrompt):
            normalized_points.append(point)
            continue
        normalized_points.append(
            Sam2PointPrompt(
                x=float(point["x"]),
                y=float(point["y"]),
                label=1 if int(point["label"]) == 1 else 0,
            )
        )

    normalized_box = None
    if box_xyxy is not None:
        normalized_box = tuple(float(value) for value in box_xyxy)

    return Sam2PromptPayload(
        label_class_id=int(label_class_id),
        frame_index=None if frame_index is None else int(frame_index),
        box_xyxy=normalized_box,
        prompt_points=normalized_points,
        track_id=None if track_id is None else int(track_id),
        track_start_frame=(
            None if track_start_frame is None else int(track_start_frame)
        ),
        track_end_frame=None if track_end_frame is None else int(track_end_frame),
        include_reverse=bool(include_reverse),
        simplify_tolerance=simplify_tolerance,
    )


def get_current_frame_suggestions(item: Item, prompt: Sam2PromptPayload) -> list[Sam2Suggestion]:
    if not sam2_feature_enabled():
        raise Sam2UnavailableError("SAM2 is disabled in the current server configuration.")
    if not sam2_feature_configured():
        raise Sam2UnavailableError(
            "SAM2 is not fully configured. Set SAM2_CHECKPOINT and SAM2_MODEL_CFG before using this endpoint."
        )

    rgb_image = _load_item_rgb(item, prompt.frame_index)
    return [_predict_mask_for_frame(rgb_image, prompt)]


def get_video_track_suggestions(item: Item, prompt: Sam2PromptPayload) -> list[Sam2Suggestion]:
    if item.kind != ItemKind.video:
        raise Sam2Error("Video tracking is only available for video items.")
    if prompt.frame_index is None:
        raise Sam2Error("frame_index is required for SAM2 video tracking.")
    if not sam2_feature_enabled():
        raise Sam2UnavailableError("SAM2 is disabled in the current server configuration.")
    if not sam2_feature_configured():
        raise Sam2UnavailableError(
            "SAM2 is not fully configured. Set SAM2_CHECKPOINT and SAM2_MODEL_CFG before using this endpoint."
        )

    runtime = _get_video_runtime()
    predictor = runtime["predictor"]
    frame_store = _ensure_frame_store(item)

    if _should_use_chunked_tracking(frame_store):
        if _supports_chunked_tracking(predictor):
            return _get_video_track_suggestions_chunked(
                item=item,
                prompt=prompt,
                runtime=runtime,
                predictor=predictor,
                frame_store=frame_store,
            )

        logger.warning(
            "SAM2 chunked tracking is not supported by the installed predictor; falling back to single-pass mode.",
            item_id=item.id,
            frame_count=int(frame_store.frame_count),
        )

    return _get_video_track_suggestions_single_pass(
        item=item,
        prompt=prompt,
        runtime=runtime,
        predictor=predictor,
        frame_store=frame_store,
    )


def _resolve_track_frame_range(
    prompt: Sam2PromptPayload,
    total_frames: int,
) -> tuple[int, int]:
    total_frames = int(total_frames)
    if total_frames <= 0:
        raise Sam2Error("SAM2 video tracking could not determine the total frame count.")

    max_frame = total_frames - 1
    if prompt.frame_index is None:
        raise Sam2Error("frame_index is required for SAM2 video tracking.")

    seed_frame = int(prompt.frame_index)
    start_frame = (
        0 if prompt.track_start_frame is None else int(prompt.track_start_frame)
    )
    end_frame = (
        max_frame if prompt.track_end_frame is None else int(prompt.track_end_frame)
    )

    if start_frame < 0:
        raise Sam2Error("track_start_frame must be >= 0.")
    if end_frame < 0:
        raise Sam2Error("track_end_frame must be >= 0.")
    if start_frame > max_frame:
        raise Sam2Error(f"track_start_frame must be <= {max_frame}.")
    if end_frame > max_frame:
        raise Sam2Error(f"track_end_frame must be <= {max_frame}.")
    if start_frame > end_frame:
        raise Sam2Error("track_start_frame must be <= track_end_frame.")
    if seed_frame < start_frame or seed_frame > end_frame:
        raise Sam2Error("frame_index must be within the requested tracking range.")

    return start_frame, end_frame


def _get_video_track_suggestions_single_pass(
    *,
    item: Item,
    prompt: Sam2PromptPayload,
    runtime: dict[str, Any],
    predictor,
    frame_store: _CachedFrameStore,
) -> list[Sam2Suggestion]:
    torch = runtime["torch"]
    state = _init_video_state(predictor, frame_store.frames_dir)
    obj_id = int(prompt.track_id or 1)
    track_start_frame, track_end_frame = _resolve_track_frame_range(
        prompt,
        int(frame_store.frame_count),
    )
    suggestions_by_frame: dict[int, Sam2Suggestion] = {}

    logger.info(
        "Running SAM2 video tracking in single-pass mode",
        item_id=item.id,
        frame_count=int(frame_store.frame_count),
        track_start_frame=track_start_frame,
        track_end_frame=track_end_frame,
    )

    try:
        with _VIDEO_RUNTIME_LOCK, torch.inference_mode():
            current_frame_idx, object_ids, mask_logits = _add_video_prompts(
                predictor,
                state,
                obj_id,
                prompt,
            )
            current_suggestion = _mask_logits_to_suggestion(
                frame_index=int(current_frame_idx),
                object_ids=object_ids,
                mask_logits=mask_logits,
                target_object_id=obj_id,
                label_class_id=prompt.label_class_id,
                track_id=prompt.track_id,
                simplify_tolerance=prompt.simplify_tolerance,
            )
            if current_suggestion is not None:
                current_frame_idx = int(current_frame_idx)
                if track_start_frame <= current_frame_idx <= track_end_frame:
                    suggestions_by_frame[current_frame_idx] = current_suggestion

            propagate_signature = inspect.signature(predictor.propagate_in_video)
            supports_start_frame = "start_frame_idx" in propagate_signature.parameters
            supports_reverse = "reverse" in propagate_signature.parameters

            propagation_runs: list[dict[str, Any]] = []
            if supports_start_frame and supports_reverse:
                if prompt.include_reverse and track_start_frame < int(prompt.frame_index):
                    propagation_runs.append(
                        {
                            "start_frame_idx": int(prompt.frame_index),
                            "reverse": True,
                        }
                    )
                if track_end_frame > int(prompt.frame_index):
                    propagation_runs.append(
                        {
                            "start_frame_idx": int(prompt.frame_index),
                            "reverse": False,
                        }
                    )
            else:
                propagation_runs.append({})

            for kwargs in propagation_runs:
                reverse = kwargs.get("reverse")
                for frame_index, object_ids, mask_logits in predictor.propagate_in_video(
                    state,
                    **kwargs,
                ):
                    frame_index = int(frame_index)
                    if frame_index < track_start_frame or frame_index > track_end_frame:
                        if reverse is True and frame_index < track_start_frame:
                            break
                        if reverse is False and frame_index > track_end_frame:
                            break
                        continue
                    suggestion = _mask_logits_to_suggestion(
                        frame_index=frame_index,
                        object_ids=object_ids,
                        mask_logits=mask_logits,
                        target_object_id=obj_id,
                        label_class_id=prompt.label_class_id,
                        track_id=prompt.track_id,
                        simplify_tolerance=prompt.simplify_tolerance,
                    )
                    if suggestion is not None:
                        suggestions_by_frame[frame_index] = suggestion
    finally:
        del state
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return [suggestions_by_frame[index] for index in sorted(suggestions_by_frame)]


def _supports_chunked_tracking(predictor) -> bool:
    try:
        propagate_signature = inspect.signature(predictor.propagate_in_video)
    except (TypeError, ValueError):
        return False

    return (
        "start_frame_idx" in propagate_signature.parameters
        and "reverse" in propagate_signature.parameters
    )


def _should_use_chunked_tracking(frame_store: _CachedFrameStore) -> bool:
    frame_count = int(frame_store.frame_count)
    if frame_count <= 0:
        return False

    threshold = int(getattr(settings, "sam2_video_chunk_threshold_frames", 0) or 0)
    chunk_size, _chunk_overlap = _get_chunk_settings(frame_count)

    if frame_count <= chunk_size:
        return False
    if threshold > 0 and frame_count < threshold:
        return False
    return True


def _get_chunk_settings(frame_count: int) -> tuple[int, int]:
    chunk_size = int(getattr(settings, "sam2_video_chunk_size", 240) or 240)
    chunk_overlap = int(getattr(settings, "sam2_video_chunk_overlap", 32) or 32)

    chunk_size = max(32, min(int(frame_count), chunk_size))
    chunk_overlap = max(1, chunk_overlap)

    if chunk_overlap >= chunk_size:
        chunk_overlap = max(1, chunk_size // 4)

    return chunk_size, chunk_overlap


def _get_seed_chunk_range(
    total_frames: int,
    seed_frame: int,
    chunk_size: int,
    chunk_overlap: int,
    min_frame: int = 0,
    max_frame: int | None = None,
) -> tuple[int, int]:
    if max_frame is None:
        max_frame = total_frames - 1
    min_frame = max(0, int(min_frame))
    max_frame = min(total_frames - 1, int(max_frame))

    max_start = max(min_frame, max_frame - chunk_size + 1)
    start_frame = max(min_frame, min(seed_frame - chunk_overlap, max_start))
    end_frame = min(max_frame, start_frame + chunk_size - 1)

    if seed_frame > end_frame:
        start_frame = max(min_frame, seed_frame - chunk_size + 1)
        end_frame = min(max_frame, start_frame + chunk_size - 1)

    return start_frame, end_frame


def _iter_forward_chunk_ranges(
    total_frames: int,
    seed_start: int,
    seed_end: int,
    chunk_size: int,
    chunk_overlap: int,
    min_frame: int = 0,
    max_frame: int | None = None,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    current_start = seed_start
    current_end = seed_end
    lower_bound = max(0, int(min_frame))
    upper_bound = (
        total_frames - 1 if max_frame is None else min(total_frames - 1, int(max_frame))
    )

    while current_end < upper_bound:
        next_start = max(current_start + 1, current_end - chunk_overlap + 1, lower_bound)
        next_end = min(upper_bound, next_start + chunk_size - 1)
        if next_start > next_end or next_end <= current_end:
            break

        ranges.append((next_start, next_end))
        current_start, current_end = next_start, next_end

    return ranges


def _iter_backward_chunk_ranges(
    total_frames: int,
    seed_start: int,
    seed_end: int,
    chunk_size: int,
    chunk_overlap: int,
    min_frame: int = 0,
    max_frame: int | None = None,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    current_start = seed_start
    current_end = seed_end
    lower_bound = max(0, int(min_frame))
    upper_bound = (
        total_frames - 1 if max_frame is None else min(total_frames - 1, int(max_frame))
    )

    while current_start > lower_bound:
        prev_end = min(upper_bound, current_start + chunk_overlap - 1, current_end)
        prev_start = max(lower_bound, prev_end - chunk_size + 1)
        if prev_start >= current_start:
            break

        ranges.append((prev_start, prev_end))
        current_start, current_end = prev_start, prev_end

    return ranges


def _get_video_track_suggestions_chunked(
    *,
    item: Item,
    prompt: Sam2PromptPayload,
    runtime: dict[str, Any],
    predictor,
    frame_store: _CachedFrameStore,
) -> list[Sam2Suggestion]:
    torch = runtime["torch"]
    obj_id = int(prompt.track_id or 1)
    total_frames = int(frame_store.frame_count)
    track_start_frame, track_end_frame = _resolve_track_frame_range(
        prompt,
        total_frames,
    )
    chunk_size, chunk_overlap = _get_chunk_settings(total_frames)

    seed_start, seed_end = _get_seed_chunk_range(
        total_frames,
        int(prompt.frame_index),
        chunk_size,
        chunk_overlap,
        min_frame=track_start_frame,
        max_frame=track_end_frame,
    )
    forward_ranges = _iter_forward_chunk_ranges(
        total_frames,
        seed_start,
        seed_end,
        chunk_size,
        chunk_overlap,
        min_frame=track_start_frame,
        max_frame=track_end_frame,
    )
    backward_ranges = (
        _iter_backward_chunk_ranges(
            total_frames,
            seed_start,
            seed_end,
            chunk_size,
            chunk_overlap,
            min_frame=track_start_frame,
            max_frame=track_end_frame,
        )
        if prompt.include_reverse and track_start_frame < int(prompt.frame_index)
        else []
    )
    run_seed_forward = track_end_frame > int(prompt.frame_index)
    run_seed_reverse = bool(
        prompt.include_reverse and track_start_frame < int(prompt.frame_index)
    )

    logger.info(
        "Running SAM2 video tracking in chunked mode",
        item_id=item.id,
        frame_count=total_frames,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        seed_start=seed_start,
        seed_end=seed_end,
        track_start_frame=track_start_frame,
        track_end_frame=track_end_frame,
    )

    suggestions_by_frame: dict[int, Sam2Suggestion] = {}

    with _VIDEO_RUNTIME_LOCK, torch.inference_mode():
        seed_boundary_frames: list[int] = []
        if forward_ranges:
            seed_boundary_frames.append(forward_ranges[0][0])
        if backward_ranges:
            seed_boundary_frames.append(backward_ranges[0][1])

        seed_suggestions, seed_boundaries = _run_video_tracking_chunk(
            item=item,
            predictor=predictor,
            torch=torch,
            frame_store=frame_store,
            chunk_start=seed_start,
            chunk_end=seed_end,
            obj_id=obj_id,
            label_class_id=prompt.label_class_id,
            track_id=prompt.track_id,
            seed_frame_global=int(prompt.frame_index),
            prompt=prompt,
            seed_mask=None,
            run_forward=run_seed_forward,
            run_reverse=run_seed_reverse,
            carry_frames_global=seed_boundary_frames,
        )
        suggestions_by_frame.update(seed_suggestions)

        forward_seed_mask = (
            seed_boundaries.get(forward_ranges[0][0]) if forward_ranges else None
        )
        backward_seed_mask = (
            seed_boundaries.get(backward_ranges[0][1]) if backward_ranges else None
        )

        for index, (chunk_start, chunk_end) in enumerate(forward_ranges):
            if forward_seed_mask is None:
                logger.warning(
                    "Stopping SAM2 forward chunk propagation because the boundary mask is missing",
                    item_id=item.id,
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                )
                break

            next_boundary_frames = (
                [forward_ranges[index + 1][0]]
                if index + 1 < len(forward_ranges)
                else []
            )
            chunk_suggestions, chunk_boundaries = _run_video_tracking_chunk(
                item=item,
                predictor=predictor,
                torch=torch,
                frame_store=frame_store,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                obj_id=obj_id,
                label_class_id=prompt.label_class_id,
                track_id=prompt.track_id,
                seed_frame_global=chunk_start,
                prompt=prompt,
                seed_mask=forward_seed_mask,
                run_forward=True,
                run_reverse=False,
                carry_frames_global=next_boundary_frames,
            )
            suggestions_by_frame.update(chunk_suggestions)
            forward_seed_mask = (
                chunk_boundaries.get(next_boundary_frames[0])
                if next_boundary_frames
                else None
            )

        for index, (chunk_start, chunk_end) in enumerate(backward_ranges):
            if backward_seed_mask is None:
                logger.warning(
                    "Stopping SAM2 reverse chunk propagation because the boundary mask is missing",
                    item_id=item.id,
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                )
                break

            next_boundary_frames = (
                [backward_ranges[index + 1][1]]
                if index + 1 < len(backward_ranges)
                else []
            )
            chunk_suggestions, chunk_boundaries = _run_video_tracking_chunk(
                item=item,
                predictor=predictor,
                torch=torch,
                frame_store=frame_store,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                obj_id=obj_id,
                label_class_id=prompt.label_class_id,
                track_id=prompt.track_id,
                seed_frame_global=chunk_end,
                prompt=prompt,
                seed_mask=backward_seed_mask,
                run_forward=False,
                run_reverse=True,
                carry_frames_global=next_boundary_frames,
            )
            suggestions_by_frame.update(chunk_suggestions)
            backward_seed_mask = (
                chunk_boundaries.get(next_boundary_frames[0])
                if next_boundary_frames
                else None
            )

    return [suggestions_by_frame[index] for index in sorted(suggestions_by_frame)]


def _run_video_tracking_chunk(
    *,
    item: Item,
    predictor,
    torch,
    frame_store: _CachedFrameStore,
    chunk_start: int,
    chunk_end: int,
    obj_id: int,
    label_class_id: int,
    track_id: int | None,
    seed_frame_global: int,
    prompt: Sam2PromptPayload,
    seed_mask,
    run_forward: bool,
    run_reverse: bool,
    carry_frames_global: list[int],
) -> tuple[dict[int, Sam2Suggestion], dict[int, Any]]:
    chunk_store = _ensure_chunk_frame_store(
        item=item,
        frame_store=frame_store,
        start_frame=chunk_start,
        end_frame=chunk_end,
    )
    state = _init_video_state(predictor, chunk_store.frames_dir)
    local_seed_frame = int(seed_frame_global - chunk_start)

    if local_seed_frame < 0 or local_seed_frame >= int(chunk_store.frame_count):
        raise Sam2Error(
            f"Chunk seed frame {seed_frame_global} is out of range for chunk {chunk_start}-{chunk_end}"
        )

    carry_frames = {
        int(frame_index)
        for frame_index in carry_frames_global
        if chunk_start <= int(frame_index) <= chunk_end
    }
    local_prompt = Sam2PromptPayload(
        label_class_id=prompt.label_class_id,
        frame_index=local_seed_frame,
        box_xyxy=prompt.box_xyxy,
        prompt_points=prompt.prompt_points,
        track_id=prompt.track_id,
        track_start_frame=prompt.track_start_frame,
        track_end_frame=prompt.track_end_frame,
        include_reverse=prompt.include_reverse,
        simplify_tolerance=prompt.simplify_tolerance,
    )

    direction = "both"
    if run_forward and not run_reverse:
        direction = "forward"
    elif run_reverse and not run_forward:
        direction = "reverse"

    logger.info(
        "Running SAM2 tracking chunk",
        item_id=item.id,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
        seed_frame=seed_frame_global,
        direction=direction,
    )

    suggestions_by_frame: dict[int, Sam2Suggestion] = {}
    boundary_masks: dict[int, Any] = {}

    try:
        if seed_mask is None:
            current_frame_idx, object_ids, mask_logits = _add_video_prompts(
                predictor,
                state,
                obj_id,
                local_prompt,
            )
        else:
            current_frame_idx, object_ids, mask_logits = _add_video_mask_seed(
                predictor,
                state,
                obj_id,
                local_seed_frame,
                seed_mask,
            )

        _record_chunk_tracking_result(
            suggestions_by_frame=suggestions_by_frame,
            boundary_masks=boundary_masks,
            chunk_start=chunk_start,
            frame_index_local=int(current_frame_idx),
            object_ids=object_ids,
            mask_logits=mask_logits,
            target_object_id=obj_id,
            carry_frames_global=carry_frames,
            label_class_id=label_class_id,
            track_id=track_id,
            simplify_tolerance=prompt.simplify_tolerance,
        )

        if run_reverse:
            for frame_index_local, object_ids, mask_logits in _iterate_chunk_propagation(
                predictor,
                state,
                local_seed_frame,
                reverse=True,
            ):
                _record_chunk_tracking_result(
                    suggestions_by_frame=suggestions_by_frame,
                    boundary_masks=boundary_masks,
                    chunk_start=chunk_start,
                    frame_index_local=frame_index_local,
                    object_ids=object_ids,
                    mask_logits=mask_logits,
                    target_object_id=obj_id,
                    carry_frames_global=carry_frames,
                    label_class_id=label_class_id,
                    track_id=track_id,
                    simplify_tolerance=prompt.simplify_tolerance,
                )

        if run_forward:
            for frame_index_local, object_ids, mask_logits in _iterate_chunk_propagation(
                predictor,
                state,
                local_seed_frame,
                reverse=False,
            ):
                _record_chunk_tracking_result(
                    suggestions_by_frame=suggestions_by_frame,
                    boundary_masks=boundary_masks,
                    chunk_start=chunk_start,
                    frame_index_local=frame_index_local,
                    object_ids=object_ids,
                    mask_logits=mask_logits,
                    target_object_id=obj_id,
                    carry_frames_global=carry_frames,
                    label_class_id=label_class_id,
                    track_id=track_id,
                    simplify_tolerance=prompt.simplify_tolerance,
                )

        return suggestions_by_frame, boundary_masks
    finally:
        del state
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _record_chunk_tracking_result(
    *,
    suggestions_by_frame: dict[int, Sam2Suggestion],
    boundary_masks: dict[int, Any],
    chunk_start: int,
    frame_index_local: int,
    object_ids,
    mask_logits,
    target_object_id: int,
    carry_frames_global: set[int],
    label_class_id: int,
    track_id: int | None,
    simplify_tolerance: float | None,
) -> None:
    mask_array = _extract_object_mask_array(
        object_ids=object_ids,
        mask_logits=mask_logits,
        target_object_id=target_object_id,
    )
    if mask_array is None:
        return

    frame_index_global = int(chunk_start + frame_index_local)
    if frame_index_global in carry_frames_global:
        boundary_masks[frame_index_global] = mask_array.copy()

    suggestion = _mask_array_to_suggestion(
        mask_array=mask_array,
        frame_index=frame_index_global,
        label_class_id=label_class_id,
        track_id=track_id,
        simplify_tolerance=simplify_tolerance,
    )
    if suggestion is not None:
        suggestions_by_frame[frame_index_global] = suggestion


def _iterate_chunk_propagation(
    predictor,
    state,
    seed_frame_local: int,
    *,
    reverse: bool,
):
    for frame_index, object_ids, mask_logits in predictor.propagate_in_video(
        state,
        start_frame_idx=int(seed_frame_local),
        reverse=bool(reverse),
    ):
        yield int(frame_index), object_ids, mask_logits


def _import_runtime() -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise Sam2UnavailableError(
            "SAM2 runtime dependencies are missing. Install numpy, opencv-python-headless, and torch in the API environment."
        ) from exc

    try:
        from sam2.build_sam import build_sam2, build_sam2_video_predictor  # type: ignore
        from sam2.sam2_image_predictor import SAM2ImagePredictor  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise Sam2UnavailableError(
            "The sam2 package is not installed in the API environment. Install the official SAM2 package before using this endpoint."
        ) from exc

    return {
        "cv2": cv2,
        "np": np,
        "torch": torch,
        "build_sam2": build_sam2,
        "build_sam2_video_predictor": build_sam2_video_predictor,
        "SAM2ImagePredictor": SAM2ImagePredictor,
    }


@dataclass(slots=True)
class _CachedFrameStore:
    frames_dir: Path
    image_ext: str
    digits: int
    frame_count: int
    fps: float
    width: int
    height: int


def _get_cv_runtime() -> dict[str, Any]:
    global _CV_RUNTIME
    if _CV_RUNTIME is not None:
        return _CV_RUNTIME

    with _CV_RUNTIME_LOCK:
        if _CV_RUNTIME is not None:
            return _CV_RUNTIME

        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise Sam2UnavailableError(
                "SAM2 helper dependencies are missing. Install numpy and opencv-python-headless in the API environment."
            ) from exc

        _CV_RUNTIME = {
            "cv2": cv2,
            "np": np,
        }
        return _CV_RUNTIME


def _get_image_runtime() -> dict[str, Any]:
    global _IMAGE_RUNTIME
    if _IMAGE_RUNTIME is not None:
        return _IMAGE_RUNTIME

    with _IMAGE_RUNTIME_LOCK:
        if _IMAGE_RUNTIME is not None:
            return _IMAGE_RUNTIME

        runtime = _import_runtime()
        build_sam2 = runtime["build_sam2"]
        predictor_class = runtime["SAM2ImagePredictor"]
        torch = runtime["torch"]

        predictor = predictor_class(
            _build_model(
                build_fn=build_sam2,
                torch=torch,
                allow_vos_optimized=False,
            )
        )
        runtime["predictor"] = predictor
        _IMAGE_RUNTIME = runtime
        return runtime


def _get_video_runtime() -> dict[str, Any]:
    global _VIDEO_RUNTIME
    if _VIDEO_RUNTIME is not None:
        return _VIDEO_RUNTIME

    with _VIDEO_RUNTIME_LOCK:
        if _VIDEO_RUNTIME is not None:
            return _VIDEO_RUNTIME

        runtime = _import_runtime()
        build_video_predictor = runtime["build_sam2_video_predictor"]
        torch = runtime["torch"]
        predictor = _build_model(
            build_fn=build_video_predictor,
            torch=torch,
            allow_vos_optimized=True,
        )
        runtime["predictor"] = predictor
        _VIDEO_RUNTIME = runtime
        return runtime


def _build_model(*, build_fn, torch, allow_vos_optimized: bool):
    model_cfg = (settings.sam2_model_cfg or "").strip()
    checkpoint_path = _resolve_checkpoint_path()

    kwargs: dict[str, Any] = {}
    signature = inspect.signature(build_fn)
    if "device" in signature.parameters:
        kwargs["device"] = _resolve_device(torch)
    if "apply_postprocessing" in signature.parameters:
        kwargs["apply_postprocessing"] = bool(settings.sam2_apply_postprocessing)
    if allow_vos_optimized and "vos_optimized" in signature.parameters:
        kwargs["vos_optimized"] = bool(settings.sam2_vos_optimized)

    return build_fn(model_cfg, str(checkpoint_path), **kwargs)


def _resolve_checkpoint_path() -> Path:
    checkpoint = Path((settings.sam2_checkpoint or "").strip()).expanduser()
    if not checkpoint.is_file():
        raise Sam2UnavailableError(
            f"SAM2 checkpoint was not found: {checkpoint}"
        )
    return checkpoint


def _resolve_device(torch) -> str:
    choice = (settings.sam2_device or "auto").strip().lower()
    require_gpu = bool(getattr(settings, "sam2_require_gpu", False))

    if require_gpu:
        if choice == "cpu":
            raise Sam2UnavailableError(
                "SAM2 is configured to require GPU, but SAM2_DEVICE=cpu was requested."
            )
        if getattr(torch.version, "cuda", None) is None:
            raise Sam2UnavailableError(
                "SAM2 requires a CUDA-enabled PyTorch build, but this container has a CPU-only PyTorch installation."
            )
        if not torch.cuda.is_available():
            raise Sam2UnavailableError(
                "SAM2 requires an NVIDIA GPU, but CUDA is not available inside the container."
            )
        return "cuda"

    if choice == "cpu":
        return "cpu"
    if choice == "cuda":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _static_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "static"


def _item_media_path(item: Item) -> Path:
    media_path = _static_root() / item.path
    if not media_path.is_file():
        raise Sam2Error(f"Item media file was not found: {media_path}")
    return media_path


def _load_item_rgb(item: Item, frame_index: int | None):
    runtime = _get_cv_runtime()
    cv2 = runtime["cv2"]
    media_path = _item_media_path(item)

    if item.kind == ItemKind.image:
        bgr = cv2.imread(str(media_path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise Sam2Error(f"Failed to read image data from: {media_path}")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    if frame_index is None:
        raise Sam2Error("frame_index is required for SAM2 video frame prompts.")

    cap = cv2.VideoCapture(str(media_path))
    if not cap.isOpened():
        raise Sam2Error(f"Failed to open video data from: {media_path}")
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
        ok, bgr = cap.read()
    finally:
        cap.release()

    if not ok or bgr is None:
        raise Sam2Error(f"Failed to decode frame {frame_index} from: {media_path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _predict_mask_for_frame(rgb_image, prompt: Sam2PromptPayload) -> Sam2Suggestion:
    runtime = _get_image_runtime()
    np = runtime["np"]
    torch = runtime["torch"]
    predictor = runtime["predictor"]

    point_coords, point_labels = _point_arrays(np, prompt.prompt_points)
    with _IMAGE_RUNTIME_LOCK, torch.inference_mode():
        predictor.set_image(rgb_image)
        predict_signature = inspect.signature(predictor.predict)
        kwargs: dict[str, Any] = {}
        if point_coords is not None and "point_coords" in predict_signature.parameters:
            kwargs["point_coords"] = point_coords
        if point_labels is not None and "point_labels" in predict_signature.parameters:
            kwargs["point_labels"] = point_labels
        if prompt.box_xyxy is not None and "box" in predict_signature.parameters:
            kwargs["box"] = np.asarray(prompt.box_xyxy, dtype=np.float32)
        if "multimask_output" in predict_signature.parameters:
            kwargs["multimask_output"] = False

        prediction = predictor.predict(**kwargs)

    masks, scores = _unpack_prediction(prediction)
    mask_array = _pick_best_mask(masks=masks, scores=scores)
    polygon_points = _mask_to_polygon_points(
        mask_array,
        simplify_tolerance=prompt.simplify_tolerance,
    )
    x1, y1, x2, y2 = _polygon_bounds(polygon_points)

    return Sam2Suggestion(
        label_class_id=prompt.label_class_id,
        frame_index=prompt.frame_index,
        track_id=prompt.track_id,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        polygon_points=polygon_points,
    )


def _point_arrays(np, prompt_points: list[Sam2PointPrompt]):
    if not prompt_points:
        return None, None

    coords = np.asarray(
        [[point.x, point.y] for point in prompt_points],
        dtype=np.float32,
    )
    labels = np.asarray(
        [point.label for point in prompt_points],
        dtype=np.int32,
    )
    return coords, labels


def _unpack_prediction(prediction):
    if isinstance(prediction, tuple):
        if len(prediction) >= 2:
            return prediction[0], prediction[1]
        return prediction[0], None
    return prediction, None


def _pick_best_mask(*, masks, scores):
    runtime = _get_cv_runtime()
    np = runtime["np"]

    mask_array = np.asarray(masks)
    if mask_array.ndim == 2:
        return mask_array > 0
    if mask_array.ndim != 3 or mask_array.shape[0] == 0:
        raise Sam2Error("SAM2 did not return a usable mask for the current prompt.")

    if scores is None:
        return mask_array[0] > 0

    score_array = np.asarray(scores)
    best_index = int(score_array.argmax())
    return mask_array[best_index] > 0


def _mask_to_polygon_points(mask, *, simplify_tolerance: float | None) -> list[list[float]]:
    runtime = _get_cv_runtime()
    cv2 = runtime["cv2"]
    np = runtime["np"]

    mask_u8 = (np.asarray(mask).astype(np.uint8) * 255)
    contours, _ = cv2.findContours(
        mask_u8,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        raise Sam2Error("SAM2 returned an empty mask for the current prompt.")

    contour = max(contours, key=cv2.contourArea)
    perimeter = float(cv2.arcLength(contour, True))
    epsilon_ratio = simplify_tolerance
    if epsilon_ratio is None:
        epsilon_ratio = float(settings.sam2_polygon_epsilon)
    epsilon = max(0.5, perimeter * float(epsilon_ratio))
    simplified = cv2.approxPolyDP(contour, epsilon, True)
    points = [
        [float(point[0][0]), float(point[0][1])]
        for point in simplified
    ]

    if len(points) < 3:
        dense_points = [
            [float(point[0][0]), float(point[0][1])]
            for point in contour
        ]
        if len(dense_points) >= 3:
            step = max(1, len(dense_points) // 96)
            points = dense_points[::step]

    if len(points) < 3:
        raise Sam2Error("SAM2 returned a mask contour that could not be converted to a polygon.")
    return points


def _polygon_bounds(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _frame_cache_root(item: Item) -> Path:
    return Path(settings.sam2_cache_dir).expanduser() / f"item_{item.id}_{item.sha256[:16]}"


def _extract_video_frames(
    *,
    video_path: Path,
    frames_dir: Path,
    image_ext: str = "jpg",
    jpg_quality: int = 95,
) -> _CachedFrameStore:
    runtime = _get_cv_runtime()
    cv2 = runtime["cv2"]

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise Sam2Error(f"Failed to open video data from: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    reported_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    digits = max(5, len(str(max(1, reported_total)))) if reported_total > 0 else 5

    frame_count = 0
    try:
        while True:
            ok, bgr = cap.read()
            if not ok or bgr is None:
                break

            if frame_count == 0 and (width <= 0 or height <= 0):
                height, width = bgr.shape[:2]

            out_path = frames_dir / f"{frame_count:0{digits}d}.{image_ext}"
            if image_ext.lower() in {"jpg", "jpeg"}:
                cv2.imwrite(str(out_path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpg_quality)])
            else:
                cv2.imwrite(str(out_path), bgr)
            frame_count += 1
    finally:
        cap.release()

    if frame_count <= 0:
        raise Sam2Error("Frame extraction failed for the requested video item.")

    return _CachedFrameStore(
        frames_dir=frames_dir,
        image_ext=image_ext,
        digits=digits,
        frame_count=frame_count,
        fps=fps if fps > 1e-3 else 30.0,
        width=width,
        height=height,
    )


def _ensure_frame_store(item: Item):
    cache_root = _frame_cache_root(item)
    frames_dir = cache_root / "frames"
    meta_path = cache_root / "frames_meta.json"

    cache_root.mkdir(parents=True, exist_ok=True)

    with _FRAME_CACHE_LOCK:
        if meta_path.is_file():
            cached = _load_frame_store_from_meta(
                frames_dir=frames_dir,
                item=item,
                meta_path=meta_path,
            )
            if cached is not None:
                return cached

        media_path = _item_media_path(item)
        frames_dir.mkdir(parents=True, exist_ok=True)
        store = _extract_video_frames(
            video_path=media_path,
            frames_dir=frames_dir,
            image_ext="jpg",
            jpg_quality=95,
        )

        meta_path.write_text(
            json.dumps(
                {
                    "sha256": item.sha256,
                    "frame_count": int(store.frame_count),
                    "digits": int(store.digits),
                    "image_ext": str(store.image_ext),
                    "fps": float(store.fps),
                    "width": int(store.width),
                    "height": int(store.height),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return store


def _load_frame_store_from_meta(*, frames_dir: Path, item: Item, meta_path: Path):
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if str(meta.get("sha256") or "") != item.sha256:
        return None

    try:
        frame_count = int(meta["frame_count"])
        digits = int(meta["digits"])
        image_ext = str(meta["image_ext"])
        fps = float(meta["fps"])
        width = int(meta["width"])
        height = int(meta["height"])
    except Exception:
        return None

    first_path = frames_dir / f"{0:0{digits}d}.{image_ext}"
    last_path = frames_dir / f"{max(0, frame_count - 1):0{digits}d}.{image_ext}"
    if not first_path.is_file() or not last_path.is_file():
        return None

    return _CachedFrameStore(
        frames_dir=frames_dir,
        image_ext=image_ext,
        digits=digits,
        frame_count=frame_count,
        fps=fps,
        width=width,
        height=height,
    )


def _ensure_chunk_frame_store(
    *,
    item: Item,
    frame_store: _CachedFrameStore,
    start_frame: int,
    end_frame: int,
) -> _CachedFrameStore:
    chunk_root = (
        _frame_cache_root(item)
        / "chunks"
        / f"{start_frame:08d}_{end_frame:08d}"
    )
    frames_dir = chunk_root / "frames"
    meta_path = chunk_root / "chunk_meta.json"
    expected_frame_count = end_frame - start_frame + 1
    digits = max(5, len(str(max(1, expected_frame_count - 1))))

    chunk_root.mkdir(parents=True, exist_ok=True)

    with _FRAME_CACHE_LOCK:
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = None

            if (
                meta
                and str(meta.get("sha256") or "") == item.sha256
                and int(meta.get("start_frame", -1)) == start_frame
                and int(meta.get("end_frame", -1)) == end_frame
            ):
                first_path = frames_dir / f"{0:0{digits}d}.{frame_store.image_ext}"
                last_path = frames_dir / (
                    f"{max(0, expected_frame_count - 1):0{digits}d}.{frame_store.image_ext}"
                )
                if first_path.is_file() and last_path.is_file():
                    return _CachedFrameStore(
                        frames_dir=frames_dir,
                        image_ext=frame_store.image_ext,
                        digits=digits,
                        frame_count=expected_frame_count,
                        fps=frame_store.fps,
                        width=frame_store.width,
                        height=frame_store.height,
                    )

        if frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir(parents=True, exist_ok=True)

        for local_index, global_index in enumerate(range(start_frame, end_frame + 1)):
            source_path = frame_store.frames_dir / (
                f"{global_index:0{frame_store.digits}d}.{frame_store.image_ext}"
            )
            if not source_path.is_file():
                raise Sam2Error(f"Cached frame was not found: {source_path}")

            target_path = frames_dir / f"{local_index:0{digits}d}.{frame_store.image_ext}"
            _link_or_copy_file(source_path, target_path)

        meta_path.write_text(
            json.dumps(
                {
                    "sha256": item.sha256,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "frame_count": expected_frame_count,
                    "digits": digits,
                    "image_ext": frame_store.image_ext,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    return _CachedFrameStore(
        frames_dir=frames_dir,
        image_ext=frame_store.image_ext,
        digits=digits,
        frame_count=expected_frame_count,
        fps=frame_store.fps,
        width=frame_store.width,
        height=frame_store.height,
    )


def _link_or_copy_file(source_path: Path, target_path: Path) -> None:
    if target_path.exists():
        return

    try:
        os.link(source_path, target_path)
        return
    except Exception:
        pass

    try:
        target_path.symlink_to(source_path)
        return
    except Exception:
        pass

    shutil.copy2(source_path, target_path)


def _init_video_state(predictor, frames_dir: Path):
    init_signature = inspect.signature(predictor.init_state)
    kwargs: dict[str, Any] = {}
    if "offload_video_to_cpu" in init_signature.parameters:
        kwargs["offload_video_to_cpu"] = bool(settings.sam2_offload_video_to_cpu)
    if "offload_state_to_cpu" in init_signature.parameters:
        kwargs["offload_state_to_cpu"] = bool(settings.sam2_offload_state_to_cpu)
    if "async_loading_frames" in init_signature.parameters:
        kwargs["async_loading_frames"] = bool(settings.sam2_async_loading_frames)
    return predictor.init_state(str(frames_dir), **kwargs)


def _add_video_prompts(predictor, state, obj_id: int, prompt: Sam2PromptPayload):
    runtime = _get_video_runtime()
    np = runtime["np"]

    point_coords, point_labels = _point_arrays(np, prompt.prompt_points)
    add_signature = inspect.signature(predictor.add_new_points_or_box)
    kwargs: dict[str, Any] = {}
    if point_coords is not None and "points" in add_signature.parameters:
        kwargs["points"] = point_coords
    if point_labels is not None and "labels" in add_signature.parameters:
        kwargs["labels"] = point_labels
    if prompt.box_xyxy is not None and "box" in add_signature.parameters:
        kwargs["box"] = np.asarray(prompt.box_xyxy, dtype=np.float32)
    if "clear_old_points" in add_signature.parameters:
        kwargs["clear_old_points"] = True
    if "normalize_coords" in add_signature.parameters:
        kwargs["normalize_coords"] = True

    return predictor.add_new_points_or_box(
        state,
        int(prompt.frame_index),
        int(obj_id),
        **kwargs,
    )


def _add_video_mask_seed(predictor, state, obj_id: int, frame_index: int, seed_mask):
    runtime = _get_video_runtime()
    np = runtime["np"]

    mask_input = np.asarray(seed_mask > 0, dtype=np.uint8)
    if mask_input.ndim != 2 or not mask_input.any():
        raise Sam2Error("SAM2 could not prepare a valid boundary mask for chunked tracking.")

    add_new_mask = getattr(predictor, "add_new_mask", None)
    if callable(add_new_mask):
        try:
            return add_new_mask(state, int(frame_index), int(obj_id), mask_input)
        except Exception:
            logger.warning(
                "Falling back to box-and-point chunk seeding because predictor.add_new_mask failed"
            )

    seed_box = _mask_array_to_box(mask_input)
    if seed_box is None:
        raise Sam2Error("SAM2 could not derive a valid box seed from the boundary mask.")

    seed_prompt = Sam2PromptPayload(
        label_class_id=0,
        frame_index=int(frame_index),
        box_xyxy=seed_box,
        prompt_points=_mask_array_to_point_prompts(mask_input),
        track_id=int(obj_id),
        track_start_frame=None,
        track_end_frame=None,
        include_reverse=False,
        simplify_tolerance=None,
    )
    return _add_video_prompts(predictor, state, int(obj_id), seed_prompt)


def _mask_array_to_box(mask_array) -> tuple[float, float, float, float] | None:
    runtime = _get_cv_runtime()
    np = runtime["np"]

    ys, xs = np.nonzero(np.asarray(mask_array) > 0)
    if xs.size == 0 or ys.size == 0:
        return None

    return (
        float(xs.min()),
        float(ys.min()),
        float(xs.max()),
        float(ys.max()),
    )


def _mask_array_to_point_prompts(mask_array) -> list[Sam2PointPrompt]:
    runtime = _get_cv_runtime()
    np = runtime["np"]

    ys, xs = np.nonzero(np.asarray(mask_array) > 0)
    if xs.size == 0 or ys.size == 0:
        return []

    return [
        Sam2PointPrompt(
            x=float(xs.mean()),
            y=float(ys.mean()),
            label=1,
        )
    ]


def _extract_object_mask_array(
    *,
    object_ids,
    mask_logits,
    target_object_id: int,
):
    runtime = _get_video_runtime()
    np = runtime["np"]

    object_id_list = list(object_ids) if not isinstance(object_ids, list) else object_ids
    if int(target_object_id) not in object_id_list:
        return None

    object_index = object_id_list.index(int(target_object_id))
    mask_tensor = mask_logits[object_index]

    try:
        mask_array = ((mask_tensor > 0.0).to("cpu").numpy()).astype(np.uint8)
    except Exception:
        mask_array = (np.asarray(mask_tensor) > 0.0).astype(np.uint8)

    if mask_array.ndim == 3:
        mask_array = mask_array.squeeze()
    if mask_array.ndim != 2 or not mask_array.any():
        return None

    return mask_array


def _mask_array_to_suggestion(
    *,
    mask_array,
    frame_index: int,
    label_class_id: int,
    track_id: int | None,
    simplify_tolerance: float | None,
) -> Sam2Suggestion | None:
    try:
        polygon_points = _mask_to_polygon_points(
            mask_array > 0,
            simplify_tolerance=simplify_tolerance,
        )
    except Sam2Error:
        return None

    x1, y1, x2, y2 = _polygon_bounds(polygon_points)
    return Sam2Suggestion(
        label_class_id=int(label_class_id),
        frame_index=int(frame_index),
        track_id=track_id,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        polygon_points=polygon_points,
    )


def _mask_logits_to_suggestion(
    *,
    frame_index: int,
    object_ids,
    mask_logits,
    target_object_id: int,
    label_class_id: int,
    track_id: int | None,
    simplify_tolerance: float | None,
) -> Sam2Suggestion | None:
    mask_array = _extract_object_mask_array(
        object_ids=object_ids,
        mask_logits=mask_logits,
        target_object_id=target_object_id,
    )
    if mask_array is None:
        return None

    return _mask_array_to_suggestion(
        mask_array=mask_array,
        frame_index=frame_index,
        label_class_id=label_class_id,
        track_id=track_id,
        simplify_tolerance=simplify_tolerance,
    )
