"""Loads tournament/branding configuration from data/settings.json.

Keeps JSON parsing out of the UI layer so widgets load configurable values
(app name, header title, logo path, position colors) instead of
hard-coding them, per PROJECT_CONTEXT.md's branding rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.runtime_paths import RESOURCE_ROOT

# `ROOT_DIR` is kept as the historical name for the *read-only resource*
# root (assets/, data/) — every existing importer of `ROOT_DIR` across
# this project reads canonical/bundled data, never writable state, so
# this alias preserves all existing behavior in source mode and now
# additionally resolves correctly inside a packaged .exe (see
# services/runtime_paths.py). Writable state (config/, saves/) uses
# `services.runtime_paths.WRITABLE_ROOT` directly instead.
ROOT_DIR = RESOURCE_ROOT
DEFAULT_SETTINGS_PATH = ROOT_DIR / "data" / "settings.json"

# A plain, manually-bumped project version string for the Settings
# screen's diagnostics section — not tied to git tags, bumped per
# packaging milestone (see PROJECT_CONTEXT.md's "Windows Packaging —
# RC0" and "Windows RC0 Packaged-Runtime Stabilization").
APP_VERSION = "0.9.0-rc2"


@dataclass(frozen=True, slots=True)
class BrandingConfig:
    app_name: str
    branding_name: str
    header_title: str
    logo_path: str
    theme_name: str

    @property
    def logo_full_path(self) -> Path:
        return ROOT_DIR / self.logo_path


def load_settings(path: Path | None = None) -> dict[str, Any]:
    settings_path = path or DEFAULT_SETTINGS_PATH
    return json.loads(settings_path.read_text(encoding="utf-8"))


def load_branding_config(path: Path | None = None) -> BrandingConfig:
    branding = load_settings(path)["branding"]
    return BrandingConfig(
        app_name=branding["app_name"],
        branding_name=branding["branding_name"],
        header_title=branding["header_title"],
        logo_path=branding["logo_path"],
        theme_name=branding["theme_name"],
    )


def load_position_colors(path: Path | None = None) -> dict[str, str]:
    return dict(load_settings(path)["position_colors"])
