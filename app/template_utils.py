# SPDX-FileCopyrightText: AItoAir, Inc.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for the project-specific license terms.

from __future__ import annotations

from pathlib import Path, PurePosixPath

from fastapi.templating import Jinja2Templates
from starlette.requests import Request


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
STATIC_DIR = REPO_ROOT / "static"


def static_asset_url(request: Request, path: str) -> str:
    asset_path = STATIC_DIR / Path(*PurePosixPath(path).parts)
    asset_url = str(request.url_for("static", path=path))

    try:
        resolved_asset_path = asset_path.resolve()
        resolved_asset_path.relative_to(STATIC_DIR.resolve())
    except (RuntimeError, ValueError):
        return asset_url

    if not resolved_asset_path.is_file():
        return asset_url

    version = resolved_asset_path.stat().st_mtime_ns
    separator = "&" if "?" in asset_url else "?"
    return f"{asset_url}{separator}v={version}"


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["static_asset_url"] = static_asset_url
    return templates
