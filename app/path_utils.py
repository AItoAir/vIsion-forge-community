# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

from pathlib import PurePosixPath


def export_item_path_basename(path: str) -> str:
    normalized_path = str(path or "").replace("\\", "/")
    basename = PurePosixPath(normalized_path).name
    return basename or normalized_path
