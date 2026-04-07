# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2  # type: ignore
import numpy as np

from ..models import ItemKind
from .media import FRAME_RATE_MODE_CFR, FRAME_RATE_MODE_UNKNOWN, MediaMetadata


SOURCE_MEDIA_TYPE_DICOM = "dicom"
SOURCE_MEDIA_TYPE_NIFTI = "nifti"
MEDICAL_PREVIEW_FPS = 12.0


class MedicalMediaError(RuntimeError):
    """Raised when a medical-media preview cannot be generated."""


@dataclass(slots=True)
class MedicalPreviewResult:
    kind: ItemKind
    display_path: Path
    metadata: MediaMetadata
    source_media_type: str


def detect_source_media_type(
    filename: str | None,
    *,
    source_path: Path | None = None,
) -> str | None:
    normalized = (filename or "").strip().lower()
    if normalized.endswith(".nii.gz") or normalized.endswith(".nii"):
        return SOURCE_MEDIA_TYPE_NIFTI
    if normalized.endswith(".dcm") or normalized.endswith(".dicom"):
        return SOURCE_MEDIA_TYPE_DICOM
    if normalized.endswith(".zip") and source_path and _archive_contains_dicom_series(source_path):
        return SOURCE_MEDIA_TYPE_DICOM
    return None


def build_medical_preview(source_path: Path) -> MedicalPreviewResult:
    source_media_type = detect_source_media_type(source_path.name, source_path=source_path)
    if source_media_type is None:
        raise MedicalMediaError("Unsupported medical media format")

    if source_media_type == SOURCE_MEDIA_TYPE_DICOM:
        if source_path.suffix.lower() == ".zip":
            frames = _load_dicom_archive_frames(source_path)
        else:
            frames = _load_dicom_frames(source_path)
    else:
        frames = _load_nifti_frames(source_path)

    if not frames:
        raise MedicalMediaError("No previewable frames were extracted from the uploaded file")

    width = int(frames[0].shape[1])
    height = int(frames[0].shape[0])
    if len(frames) == 1:
        display_path = _reserve_preview_path(source_path, ".png")
        _write_preview_image(display_path, frames[0])
        metadata = MediaMetadata(
            width=width,
            height=height,
            duration_sec=None,
            fps=None,
            frame_rate_mode=FRAME_RATE_MODE_UNKNOWN,
        )
        return MedicalPreviewResult(
            kind=ItemKind.image,
            display_path=display_path,
            metadata=metadata,
            source_media_type=source_media_type,
        )

    display_path = _reserve_preview_path(source_path, ".mp4")
    _write_preview_video(display_path, frames)
    metadata = MediaMetadata(
        width=width,
        height=height,
        duration_sec=round(len(frames) / MEDICAL_PREVIEW_FPS, 6),
        fps=MEDICAL_PREVIEW_FPS,
        frame_rate_mode=FRAME_RATE_MODE_CFR,
    )
    return MedicalPreviewResult(
        kind=ItemKind.video,
        display_path=display_path,
        metadata=metadata,
        source_media_type=source_media_type,
    )


def _load_dicom_frames(source_path: Path) -> list[np.ndarray]:
    pydicom, apply_modality_lut, apply_voi_lut = _import_pydicom()

    try:
        dataset = pydicom.dcmread(str(source_path))
    except Exception as exc:
        raise MedicalMediaError(f"Failed to read DICOM pixel data: {exc}") from exc

    return _extract_dicom_dataset_frames(
        dataset,
        apply_modality_lut=apply_modality_lut,
        apply_voi_lut=apply_voi_lut,
    )


def _import_pydicom():
    try:
        import pydicom  # type: ignore
        from pydicom.pixel_data_handlers.util import apply_modality_lut, apply_voi_lut  # type: ignore
    except ImportError as exc:
        raise MedicalMediaError(
            "DICOM support requires pydicom in the runtime environment."
        ) from exc
    return pydicom, apply_modality_lut, apply_voi_lut


def _load_dicom_archive_frames(source_path: Path) -> list[np.ndarray]:
    pydicom, apply_modality_lut, apply_voi_lut = _import_pydicom()

    try:
        archive = zipfile.ZipFile(source_path)
    except Exception as exc:
        raise MedicalMediaError(f"Failed to read DICOM ZIP archive: {exc}") from exc

    with archive:
        grouped_entries: dict[str, list[tuple[tuple, object, str]]] = {}
        for member in archive.infolist():
            if member.is_dir():
                continue
            if not _archive_member_looks_like_dicom(archive, member):
                continue

            try:
                with archive.open(member, "r") as handle:
                    dataset = pydicom.dcmread(io.BytesIO(handle.read()))
            except Exception:
                continue

            if not getattr(dataset, "PixelData", None):
                continue

            series_uid = str(getattr(dataset, "SeriesInstanceUID", "") or "")
            grouped_entries.setdefault(series_uid, []).append(
                (_dicom_archive_sort_key(dataset, member.filename), dataset, member.filename)
            )

    if not grouped_entries:
        raise MedicalMediaError(
            "No previewable DICOM images were found in the ZIP archive"
        )

    non_empty_series_uids = sorted({uid for uid in grouped_entries if uid})
    if len(non_empty_series_uids) > 1:
        raise MedicalMediaError(
            "ZIP archives must contain DICOM files from a single series. Upload one series per ZIP archive."
        )

    if non_empty_series_uids:
        selected_entries = list(grouped_entries.get(non_empty_series_uids[0], []))
        if "" in grouped_entries:
            selected_entries.extend(grouped_entries[""])
    else:
        selected_entries = list(grouped_entries.get("", []))

    if not selected_entries:
        raise MedicalMediaError(
            "No previewable DICOM images were found in the ZIP archive"
        )

    selected_entries.sort(key=lambda entry: entry[0])
    frames: list[np.ndarray] = []
    for _, dataset, _member_name in selected_entries:
        frames.extend(
            _extract_dicom_dataset_frames(
                dataset,
                apply_modality_lut=apply_modality_lut,
                apply_voi_lut=apply_voi_lut,
            )
        )

    return frames


def _extract_dicom_dataset_frames(
    dataset,
    *,
    apply_modality_lut,
    apply_voi_lut,
) -> list[np.ndarray]:
    try:
        pixels = dataset.pixel_array
    except Exception as exc:
        raise MedicalMediaError(f"Failed to read DICOM pixel data: {exc}") from exc

    try:
        pixels = apply_modality_lut(pixels, dataset)
    except Exception:
        pass
    try:
        pixels = apply_voi_lut(pixels, dataset)
    except Exception:
        pass

    invert = str(getattr(dataset, "PhotometricInterpretation", "") or "").upper() == "MONOCHROME1"
    number_of_frames = max(1, int(getattr(dataset, "NumberOfFrames", 1) or 1))
    array = np.asarray(pixels)
    if number_of_frames > 1:
        if array.ndim == 3:
            return [_normalize_frame(array[index], invert=invert) for index in range(array.shape[0])]
        if array.ndim == 4:
            return [_normalize_frame(array[index], invert=invert) for index in range(array.shape[0])]
        raise MedicalMediaError(
            f"Unsupported DICOM frame layout with shape {tuple(array.shape)}"
        )

    return [_normalize_frame(array, invert=invert)]


def _archive_contains_dicom_series(source_path: Path) -> bool:
    try:
        with zipfile.ZipFile(source_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if _archive_member_looks_like_dicom(archive, member):
                    return True
    except Exception:
        return False
    return False


def _reserve_preview_path(source_path: Path, extension: str) -> Path:
    preview_stem = f"{source_path.stem}.medical_preview"
    candidate = source_path.with_name(f"{preview_stem}{extension}")
    suffix_index = 2

    while True:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("xb"):
                pass
            return candidate
        except FileExistsError:
            candidate = source_path.with_name(
                f"{preview_stem}.{suffix_index}{extension}"
            )
            suffix_index += 1


def _archive_member_looks_like_dicom(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
) -> bool:
    normalized_name = member.filename.lower()
    if normalized_name.endswith(".dcm") or normalized_name.endswith(".dicom"):
        return True

    try:
        with archive.open(member, "r") as handle:
            header = handle.read(132)
    except Exception:
        return False
    return len(header) >= 132 and header[128:132] == b"DICM"


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dicom_archive_sort_key(dataset, member_name: str) -> tuple:
    position = getattr(dataset, "ImagePositionPatient", None)
    if position is not None:
        try:
            coordinates = tuple(float(position[index]) for index in range(min(3, len(position))))
        except Exception:
            coordinates = None
        if coordinates:
            return (
                0,
                coordinates,
                _safe_int(getattr(dataset, "InstanceNumber", None)) or 0,
                member_name,
            )

    instance_number = _safe_int(getattr(dataset, "InstanceNumber", None))
    if instance_number is not None:
        return (1, instance_number, member_name)

    slice_location = _safe_float(getattr(dataset, "SliceLocation", None))
    if slice_location is not None:
        return (2, slice_location, member_name)

    acquisition_number = _safe_int(getattr(dataset, "AcquisitionNumber", None))
    if acquisition_number is not None:
        return (3, acquisition_number, member_name)

    return (4, member_name)


def _load_nifti_frames(source_path: Path) -> list[np.ndarray]:
    try:
        import nibabel as nib  # type: ignore
    except ImportError as exc:
        raise MedicalMediaError(
            "NIfTI support requires nibabel in the runtime environment."
        ) from exc

    try:
        image = nib.load(str(source_path))
        image = nib.as_closest_canonical(image)
        data = np.asanyarray(image.dataobj)
    except Exception as exc:
        raise MedicalMediaError(f"Failed to read NIfTI volume data: {exc}") from exc

    volume = np.squeeze(np.asarray(data))
    if volume.ndim < 2:
        raise MedicalMediaError(
            f"Unsupported NIfTI payload with shape {tuple(volume.shape)}"
        )
    if volume.ndim == 2:
        return [_normalize_scalar_frame(volume)]

    while volume.ndim > 3:
        volume = volume[..., 0]

    if volume.ndim != 3:
        raise MedicalMediaError(
            f"Unsupported NIfTI payload with shape {tuple(volume.shape)}"
        )

    return [
        _normalize_scalar_frame(volume[:, :, frame_index])
        for frame_index in range(volume.shape[2])
    ]


def _normalize_frame(frame: np.ndarray, *, invert: bool = False) -> np.ndarray:
    array = np.asarray(frame)
    if array.ndim == 2:
        return _normalize_scalar_frame(array, invert=invert)

    if array.ndim == 3 and array.shape[-1] in {1, 3, 4}:
        if array.shape[-1] == 1:
            return _normalize_scalar_frame(np.squeeze(array, axis=-1), invert=invert)
        return _normalize_color_frame(array)

    if array.ndim == 3 and array.shape[0] in {1, 3, 4}:
        transposed = np.moveaxis(array, 0, -1)
        if transposed.shape[-1] == 1:
            return _normalize_scalar_frame(np.squeeze(transposed, axis=-1), invert=invert)
        return _normalize_color_frame(transposed)

    raise MedicalMediaError(f"Unsupported frame layout with shape {tuple(array.shape)}")


def _normalize_scalar_frame(frame: np.ndarray, *, invert: bool = False) -> np.ndarray:
    values = np.asarray(frame, dtype=np.float32)
    finite_mask = np.isfinite(values)
    if not finite_mask.any():
        normalized = np.zeros(values.shape, dtype=np.uint8)
    else:
        finite_values = values[finite_mask]
        low = float(np.percentile(finite_values, 1))
        high = float(np.percentile(finite_values, 99))
        if not np.isfinite(low):
            low = float(np.min(finite_values))
        if not np.isfinite(high):
            high = float(np.max(finite_values))
        if high <= low:
            high = low + 1.0
        clipped = np.clip(values, low, high)
        scaled = ((clipped - low) / (high - low)) * 255.0
        normalized = np.clip(scaled, 0.0, 255.0).astype(np.uint8)

    if invert:
        normalized = 255 - normalized
    return normalized


def _normalize_color_frame(frame: np.ndarray) -> np.ndarray:
    values = np.asarray(frame, dtype=np.float32)
    finite_mask = np.isfinite(values)
    if not finite_mask.any():
        normalized = np.zeros(values.shape, dtype=np.uint8)
    else:
        finite_values = values[finite_mask]
        low = float(np.percentile(finite_values, 1))
        high = float(np.percentile(finite_values, 99))
        if not np.isfinite(low):
            low = float(np.min(finite_values))
        if not np.isfinite(high):
            high = float(np.max(finite_values))
        if high <= low:
            high = low + 1.0
        clipped = np.clip(values, low, high)
        scaled = ((clipped - low) / (high - low)) * 255.0
        normalized = np.clip(scaled, 0.0, 255.0).astype(np.uint8)

    if normalized.shape[-1] == 4:
        normalized = normalized[:, :, :3]
    return normalized


def _write_preview_image(target_path: Path, frame: np.ndarray) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if frame.ndim == 3:
        encoded_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    else:
        encoded_frame = frame
    if not cv2.imwrite(str(target_path), encoded_frame):
        raise MedicalMediaError(f"Failed to write preview image: {target_path.name}")


def _write_preview_video(target_path: Path, frames: list[np.ndarray]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        for frame_index, frame in enumerate(frames):
            frame_path = temp_root / f"{frame_index:05d}.png"
            _write_preview_image(frame_path, frame)

        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            f"{MEDICAL_PREVIEW_FPS:g}",
            "-i",
            str(temp_root / "%05d.png"),
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2:color=black",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            if target_path.exists():
                target_path.unlink()
            raise MedicalMediaError(
                "ffmpeg is not available in the runtime environment"
            ) from exc

        if result.returncode != 0:
            if target_path.exists():
                target_path.unlink()
            detail = (result.stderr or "").strip() or "Unknown ffmpeg failure"
            raise MedicalMediaError(f"Failed to build preview video: {detail}")
