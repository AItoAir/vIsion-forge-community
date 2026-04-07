# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from ..models import ItemKind
from .medical_media import MedicalMediaError, build_medical_preview, detect_source_media_type
from .media import FRAME_RATE_MODE_VFR, MediaMetadata, MediaProbeError, probe_media_metadata


class UploadPreparationError(RuntimeError):
    """Raised when an uploaded file cannot be prepared as an item."""


SUPPORTED_STANDARD_IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
)
SUPPORTED_STANDARD_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/pjpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/x-ms-bmp",
}
SUPPORTED_MEDICAL_UPLOAD_EXTENSIONS = (
    ".dcm",
    ".dicom",
    ".zip",
    ".nii",
    ".nii.gz",
)
SUPPORTED_STANDARD_IMAGE_LABEL = "JPEG, PNG, GIF, WebP, and BMP"
UPLOAD_FILE_INPUT_ACCEPT = ",".join(
    [
        *SUPPORTED_STANDARD_IMAGE_EXTENSIONS,
        "video/*",
        *SUPPORTED_MEDICAL_UPLOAD_EXTENSIONS,
    ]
)


@dataclass(slots=True)
class PreparedUpload:
    kind: ItemKind
    path: str
    display_path: str | None
    source_media_type: str | None
    sha256: str
    metadata: MediaMetadata


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _has_allowed_extension(filename: str | None, allowed_extensions: tuple[str, ...]) -> bool:
    normalized = (filename or "").strip().lower()
    return any(normalized.endswith(extension) for extension in allowed_extensions)


def _effective_upload_content_type(file: UploadFile) -> str:
    declared = _normalize_content_type(file.content_type)
    if declared:
        return declared
    guessed, _encoding = mimetypes.guess_type(file.filename or "")
    return _normalize_content_type(guessed)


def is_supported_standard_image_upload(
    filename: str | None,
    content_type: str | None,
) -> bool:
    normalized_content_type = _normalize_content_type(content_type)
    if normalized_content_type.startswith("image/"):
        return normalized_content_type in SUPPORTED_STANDARD_IMAGE_MIME_TYPES
    return _has_allowed_extension(filename, SUPPORTED_STANDARD_IMAGE_EXTENSIONS)


def unsupported_standard_image_message(filename: str | None) -> str:
    display_name = Path(filename or "uploaded").name
    return (
        f"Unsupported image format for browser-based labeling: {display_name}. "
        f"Standard image uploads must be {SUPPORTED_STANDARD_IMAGE_LABEL}."
    )


def normalize_requested_item_kind(kind: str | None, file: UploadFile) -> ItemKind:
    normalized = (kind or "").strip().lower()
    if normalized in {ItemKind.image.value, ItemKind.video.value}:
        return ItemKind(normalized)
    content_type = _effective_upload_content_type(file)
    return ItemKind.video if content_type.startswith("video/") else ItemKind.image


def prepare_uploaded_media(
    *,
    file: UploadFile,
    project_id: int,
    static_dir: Path,
    requested_kind: str | None = None,
) -> PreparedUpload:
    uploads_root = static_dir / "uploads"
    project_dir = uploads_root / f"project_{project_id}"
    project_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(file.filename or "uploaded").name
    target_path = project_dir / filename

    hasher = hashlib.sha256()
    try:
        with target_path.open("xb") as output:
            while True:
                chunk = file.file.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
                output.write(chunk)
    except FileExistsError as exc:
        raise UploadPreparationError(
            f"File already exists in this project: {filename}"
        ) from exc
    finally:
        file.file.close()

    display_path: Path | None = None
    try:
        source_media_type = detect_source_media_type(filename, source_path=target_path)
        if source_media_type is not None:
            medical_preview = build_medical_preview(target_path)
            display_path = medical_preview.display_path
            return PreparedUpload(
                kind=medical_preview.kind,
                path=str(target_path.relative_to(static_dir)).replace("\\", "/"),
                display_path=str(display_path.relative_to(static_dir)).replace("\\", "/"),
                source_media_type=medical_preview.source_media_type,
                sha256=hasher.hexdigest(),
                metadata=medical_preview.metadata,
            )

        item_kind = normalize_requested_item_kind(requested_kind, file)
        if item_kind == ItemKind.image and not is_supported_standard_image_upload(
            filename,
            _effective_upload_content_type(file),
        ):
            raise UploadPreparationError(unsupported_standard_image_message(filename))
        metadata = probe_media_metadata(target_path, item_kind)
        if item_kind == ItemKind.video and metadata.frame_rate_mode == FRAME_RATE_MODE_VFR:
            raise UploadPreparationError(
                "Variable frame rate videos are not supported for frame-accurate labeling yet. "
                "Please convert this video to constant frame rate (CFR) before uploading."
            )

        return PreparedUpload(
            kind=item_kind,
            path=str(target_path.relative_to(static_dir)).replace("\\", "/"),
            display_path=None,
            source_media_type=None,
            sha256=hasher.hexdigest(),
            metadata=metadata,
        )
    except (MedicalMediaError, MediaProbeError, UploadPreparationError) as exc:
        _cleanup_failed_upload(target_path, display_path)
        raise UploadPreparationError(str(exc)) from exc
    except Exception:
        _cleanup_failed_upload(target_path, display_path)
        raise


def _cleanup_failed_upload(source_path: Path, display_path: Path | None) -> None:
    for candidate in (display_path, source_path):
        if candidate is None:
            continue
        try:
            candidate.unlink(missing_ok=True)
        except TypeError:
            if candidate.exists():
                candidate.unlink()
