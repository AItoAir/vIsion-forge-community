# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

import unittest

from app.path_utils import export_item_path_basename


class PathUtilsTests(unittest.TestCase):
    def test_export_item_path_basename_keeps_filename_only_for_posix_path(self) -> None:
        self.assertEqual(
            "IMG_0125.png",
            export_item_path_basename("uploads/project_7/IMG_0125.png"),
        )

    def test_export_item_path_basename_keeps_filename_only_for_windows_path(self) -> None:
        self.assertEqual(
            "IMG_0125.png",
            export_item_path_basename(r"uploads\project_7\IMG_0125.png"),
        )

    def test_export_item_path_basename_preserves_plain_filename(self) -> None:
        self.assertEqual(
            "IMG_0125.png",
            export_item_path_basename("IMG_0125.png"),
        )


if __name__ == "__main__":
    unittest.main()
