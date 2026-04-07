# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from tempfile import SpooledTemporaryFile

from fastapi import UploadFile
from starlette.datastructures import Headers

from app.models import ItemKind
from app.services.uploads import (
    UploadPreparationError,
    normalize_requested_item_kind,
    prepare_uploaded_media,
)


def _build_upload(
    *,
    filename: str,
    payload: bytes = b"upload-bytes",
    content_type: str | None = None,
) -> UploadFile:
    temp_file = SpooledTemporaryFile(max_size=1024 * 1024)
    temp_file.write(payload)
    temp_file.seek(0)
    headers = Headers({"content-type": content_type}) if content_type else None
    return UploadFile(filename=filename, file=temp_file, headers=headers)


class UploadValidationTests(unittest.TestCase):
    def test_normalize_requested_item_kind_guesses_video_from_filename_when_content_type_is_missing(self) -> None:
        upload = _build_upload(filename="demo.mp4", content_type=None)
        self.addCleanup(upload.file.close)

        detected_kind = normalize_requested_item_kind(None, upload)

        self.assertEqual(ItemKind.video, detected_kind)

    def test_prepare_uploaded_media_rejects_unsupported_standard_image_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            static_dir = Path(temp_dir) / "static"
            upload = _build_upload(
                filename="IMG_0125.HEIC",
                payload=b"fake-heic",
                content_type="image/heic",
            )

            with self.assertRaises(UploadPreparationError) as raised:
                prepare_uploaded_media(
                    file=upload,
                    project_id=12,
                    static_dir=static_dir,
                )

            self.assertFalse(
                (static_dir / "uploads" / "project_12" / "IMG_0125.HEIC").exists()
            )

        self.assertIn("Unsupported image format", str(raised.exception))
        self.assertIn("JPEG, PNG, GIF, WebP, and BMP", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
