from __future__ import annotations

from collections.abc import Callable, Iterator
from importlib import import_module
from types import ModuleType

import structlog
from fastapi import FastAPI

from .config import settings


logger = structlog.get_logger(__name__)

DEFAULT_OPTIONAL_EXTENSION_MODULES = ("vision_forge_enterprise.app_hooks",)
ExtensionHook = Callable[[FastAPI], None]


def configured_extension_modules() -> tuple[str, ...]:
    configured = tuple(
        module_name.strip()
        for module_name in settings.app_extension_hooks.split(",")
        if module_name.strip()
    )
    if configured:
        return configured
    return DEFAULT_OPTIONAL_EXTENSION_MODULES


def _is_missing_requested_module(module_name: str, exc: ModuleNotFoundError) -> bool:
    missing_name = exc.name or ""
    return module_name == missing_name or module_name.startswith(f"{missing_name}.")


def _load_extension_module(module_name: str, *, required: bool) -> ModuleType | None:
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        if required or not _is_missing_requested_module(module_name, exc):
            raise

        logger.debug(
            "Optional app extension module not installed",
            module_name=module_name,
        )
        return None


def _resolve_extension_hook(module: ModuleType, module_name: str) -> ExtensionHook:
    hook = getattr(module, "apply_extension_hooks", None)
    if hook is None or not callable(hook):
        raise RuntimeError(
            f"Extension module '{module_name}' must define apply_extension_hooks(app)."
        )
    return hook


def iter_extension_hooks() -> Iterator[tuple[str, ExtensionHook]]:
    extension_modules = configured_extension_modules()
    explicit_modules_configured = bool(settings.app_extension_hooks.strip())

    for module_name in extension_modules:
        module = _load_extension_module(
            module_name,
            required=explicit_modules_configured,
        )
        if module is None:
            continue
        yield module_name, _resolve_extension_hook(module, module_name)


def apply_configured_extension_hooks(app: FastAPI) -> None:
    for module_name, hook in iter_extension_hooks():
        hook(app)
        logger.info(
            "Applied app extension hooks",
            module_name=module_name,
        )
