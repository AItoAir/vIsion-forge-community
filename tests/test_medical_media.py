# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from tempfile import SpooledTemporaryFile
from unittest.mock import patch

import numpy as np
from fastapi import UploadFile
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

from app.models import ItemKind
from app.services.medical_media import MedicalMediaError, build_medical_preview, detect_source_media_type
from app.services.uploads import UploadPreparationError, prepare_uploaded_media


def _build_test_dicom_bytes(
    pixel_array: np.ndarray,
    *,
    series_uid: str,
    instance_number: int,
) -> bytes:
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    dataset = FileDataset(
        f"slice-{instance_number}.dcm",
        {},
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )
    dataset.is_little_endian = True
    dataset.is_implicit_VR = False
    dataset.SOPClassUID = file_meta.MediaStorageSOPClassUID
    dataset.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = series_uid
    dataset.PatientID = "TEST"
    dataset.Modality = "OT"
    dataset.InstanceNumber = instance_number
    dataset.ImagePositionPatient = [0.0, 0.0, float(instance_number)]
    dataset.Rows = int(pixel_array.shape[0])
    dataset.Columns = int(pixel_array.shape[1])
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.PixelRepresentation = 0
    dataset.BitsStored = 8
    dataset.BitsAllocated = 8
    dataset.HighBit = 7
    dataset.PixelData = np.asarray(pixel_array, dtype=np.uint8).tobytes()

    buffer = io.BytesIO()
    dataset.save_as(buffer, write_like_original=False)
    return buffer.getvalue()


def _build_dicom_zip_bytes(series_uids: list[str]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w") as zip_handle:
        for index, series_uid in enumerate(series_uids, start=1):
            pixels = np.full((4, 4), index * 32, dtype=np.uint8)
            zip_handle.writestr(
                f"nested/slice_{index:03d}.dcm",
                _build_test_dicom_bytes(
                    pixels,
                    series_uid=series_uid,
                    instance_number=index,
                ),
            )
        zip_handle.writestr("LICENSE", "public dataset license")
    return archive.getvalue()


class MedicalMediaTests(unittest.TestCase):
    def test_detect_source_media_type_recognizes_dicom_series_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "series.zip"
            archive_path.write_bytes(_build_dicom_zip_bytes([generate_uid()]))

            detected = detect_source_media_type(archive_path.name, source_path=archive_path)

        self.assertEqual("dicom", detected)

    def test_build_medical_preview_supports_single_series_dicom_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "series.zip"
            series_uid = generate_uid()
            archive_path.write_bytes(_build_dicom_zip_bytes([series_uid, series_uid]))

            def _fake_write_preview_video(target_path: Path, frames) -> None:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(b"preview-video")

            with patch("app.services.medical_media._write_preview_video", side_effect=_fake_write_preview_video):
                result = build_medical_preview(archive_path)

            self.assertTrue(result.display_path.is_file())

        self.assertEqual(ItemKind.video, result.kind)
        self.assertEqual("dicom", result.source_media_type)
        self.assertEqual(4, result.metadata.width)
        self.assertEqual(4, result.metadata.height)
        self.assertTrue(result.display_path.name.endswith(".medical_preview.mp4"))

    def test_build_medical_preview_rejects_multi_series_dicom_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "study.zip"
            archive_path.write_bytes(_build_dicom_zip_bytes([generate_uid(), generate_uid()]))

            with self.assertRaises(MedicalMediaError) as raised:
                build_medical_preview(archive_path)

        self.assertIn("single series", str(raised.exception).lower())

    def test_prepare_uploaded_media_accepts_dicom_series_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            series_uid = generate_uid()
            archive_bytes = _build_dicom_zip_bytes([series_uid, series_uid])

            temp_file = SpooledTemporaryFile(max_size=1024 * 1024)
            temp_file.write(archive_bytes)
            temp_file.seek(0)
            upload = UploadFile(filename="study.zip", file=temp_file)

            def _fake_write_preview_video(target_path: Path, frames) -> None:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(b"preview-video")

            with patch("app.services.medical_media._write_preview_video", side_effect=_fake_write_preview_video):
                prepared = prepare_uploaded_media(
                    file=upload,
                    project_id=7,
                    static_dir=static_dir,
                )

            self.assertTrue((static_dir / prepared.path).is_file())
            self.assertTrue((static_dir / prepared.display_path).is_file())

        self.assertEqual(ItemKind.video, prepared.kind)
        self.assertEqual("dicom", prepared.source_media_type)
        self.assertEqual("uploads/project_7/study.zip", prepared.path)
        self.assertEqual(
            "uploads/project_7/study.medical_preview.mp4",
            prepared.display_path,
        )

    def test_prepare_uploaded_media_cleans_up_invalid_multi_series_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            archive_bytes = _build_dicom_zip_bytes([generate_uid(), generate_uid()])
            temp_file = SpooledTemporaryFile(max_size=1024 * 1024)
            temp_file.write(archive_bytes)
            temp_file.seek(0)
            upload = UploadFile(filename="study.zip", file=temp_file)

            with self.assertRaises(UploadPreparationError) as raised:
                prepare_uploaded_media(
                    file=upload,
                    project_id=8,
                    static_dir=static_dir,
                )

            self.assertFalse((static_dir / "uploads" / "project_8" / "study.zip").exists())

        self.assertIn("single series", str(raised.exception).lower())

    def test_prepare_uploaded_media_avoids_existing_preview_filename_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            project_dir = static_dir / "uploads" / "project_9"
            project_dir.mkdir(parents=True, exist_ok=True)
            existing_preview = project_dir / "scan.medical_preview.png"
            existing_preview.write_bytes(b"existing-item-preview")

            dicom_bytes = _build_test_dicom_bytes(
                np.full((4, 4), 128, dtype=np.uint8),
                series_uid=generate_uid(),
                instance_number=1,
            )
            temp_file = SpooledTemporaryFile(max_size=1024 * 1024)
            temp_file.write(dicom_bytes)
            temp_file.seek(0)
            upload = UploadFile(filename="scan.dcm", file=temp_file)

            prepared = prepare_uploaded_media(
                file=upload,
                project_id=9,
                static_dir=static_dir,
            )

            self.assertEqual(b"existing-item-preview", existing_preview.read_bytes())
            self.assertEqual(
                "uploads/project_9/scan.medical_preview.2.png",
                prepared.display_path,
            )
            self.assertTrue((static_dir / prepared.display_path).is_file())

    def test_prepare_uploaded_media_cleans_up_reserved_preview_when_ffmpeg_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            series_uid = generate_uid()
            archive_bytes = _build_dicom_zip_bytes([series_uid, series_uid])
            temp_file = SpooledTemporaryFile(max_size=1024 * 1024)
            temp_file.write(archive_bytes)
            temp_file.seek(0)
            upload = UploadFile(filename="study.zip", file=temp_file)

            with patch(
                "app.services.medical_media.subprocess.run",
                side_effect=FileNotFoundError("ffmpeg"),
            ):
                with self.assertRaises(UploadPreparationError) as raised:
                    prepare_uploaded_media(
                        file=upload,
                        project_id=10,
                        static_dir=static_dir,
                    )

            project_dir = static_dir / "uploads" / "project_10"
            self.assertFalse((project_dir / "study.zip").exists())
            self.assertEqual([], list(project_dir.glob("*.medical_preview*.mp4")))

        self.assertIn("ffmpeg", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
