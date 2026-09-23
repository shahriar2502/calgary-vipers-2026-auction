"""Resource and writable-data path resolution that works identically
whether the app is run from source (`python main.py`) or as a
PyInstaller-packaged Windows build.

Two roots, deliberately kept separate, because a packaged build makes
them two different directories:

- `RESOURCE_ROOT`: where read-only bundled resources live — `assets/`
  and `data/`. In source mode this is the repository root. In a
  packaged build it's PyInstaller's extracted bundle directory
  (`sys._MEIPASS`), which for a onedir build is the `_internal/`
  folder next to the .exe, and for a onefile build is a temporary
  extraction directory that can differ between launches — either way,
  nothing here ever writes into it.
- `WRITABLE_ROOT`: where the app creates/reads/writes its own runtime
  state — `config/` and `saves/`. This must be a stable location beside
  the actual .exe that survives every relaunch, so it is never
  `RESOURCE_ROOT` in a packaged build (a onefile build's bundle
  directory is a fresh temp folder every launch, and even a onedir
  build's `_internal/` is meant to be treated as read-only, disposable
  application payload, not a place to keep an organizer's saves or PIN
  configuration). In source mode `WRITABLE_ROOT` and `RESOURCE_ROOT`
  are the same repository root, exactly matching this app's existing
  dev-mode behavior.

Every module that previously computed its own `Path(__file__).resolve()
.parent.parent` or otherwise hard-coded a root should import one of the
two roots (or the small helpers below) from here instead — this is the
one place that distinguishes "am I running from source or packaged."
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True only inside a PyInstaller-packaged executable."""
    return bool(getattr(sys, "frozen", False))


def _source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resource_root() -> Path:
    """The read-only resource root for the *current* process state.

    Kept as its own function (rather than inlined into the module-level
    constant below) so packaged-mode resolution is directly unit-testable
    via monkeypatching `sys.frozen`/`sys._MEIPASS` — re-evaluating this
    function reflects a hypothetical frozen state without needing to
    reimport this module or disturb `RESOURCE_ROOT` for the rest of a
    test run.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", ""))
    return _source_root()


def _writable_root() -> Path:
    """The writable-data root for the *current* process state — see
    `_resource_root`'s docstring for why this is a separate function."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _source_root()


RESOURCE_ROOT: Path = _resource_root()
WRITABLE_ROOT: Path = _writable_root()


def resource_path(*parts: str) -> Path:
    """A read-only bundled resource — e.g. `resource_path("assets", "branding", "calgary_vipers_logo.png")`."""
    return RESOURCE_ROOT.joinpath(*parts)


def writable_path(*parts: str) -> Path:
    """A path under the app's writable runtime-data root — e.g. `writable_path("saves")`."""
    return WRITABLE_ROOT.joinpath(*parts)


def ensure_writable_dir(*parts: str) -> Path:
    """`writable_path(*parts)`, guaranteed to exist as a directory afterward."""
    path = writable_path(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
