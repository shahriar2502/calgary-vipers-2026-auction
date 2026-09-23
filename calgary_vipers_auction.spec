# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Calgary Vipers Auction 2026 — RC2 test build.

Run via `build_windows.bat`, which invokes:

    pyinstaller calgary_vipers_auction.spec --distpath dist --workpath build --noconfirm

This is a folder-style (onedir) build, not onefile — see PROJECT_CONTEXT.md's
"Windows Packaging — RC0" for why (many photos/assets, HTML, JSON; easier
troubleshooting; safer resource handling; faster startup).

RC2 supersedes RC1 (see PROJECT_CONTEXT.md's "RC1 Real-Laptop Server Start
Hang — Targeted Debug Pass") but does not overwrite RC0 or RC1: the
`DIST_FOLDER_NAME` below and `build_windows.bat`'s cleanup step are both
scoped to this RC's own output folder specifically, so an earlier RC's
folder under `dist/` (if kept around) survives a rebuild untouched. Bump
`DIST_FOLDER_NAME` (and the matching line in `build_windows.bat`) together
when cutting the next RC.

Only `assets/` and `data/` (read-only canonical resources) are bundled as
`datas` here. `config/` and `saves/` are deliberately NOT bundled — they
are writable runtime state that the app creates beside the .exe on first
use (see services/runtime_paths.py); bundling them into PyInstaller's
read-only payload would be exactly the mistake the ticket warned against.

Set the environment variable CVA_DEBUG_CONSOLE=1 before building to produce
a console-attached debug variant — output to "Calgary Vipers Auction 2026
RC2 Debug/" — instead of the normal windowed build, which outputs to
"Calgary Vipers Auction 2026 RC2/". The debug variant shows the fine-grained
SERVER_START_XX/SERVER_THREAD_XX/WATCHER_XX/LAN_XX startup trace in a live
console window in addition to the log file, for diagnosing a hung server
start on the organizer's own machine. The user-facing release build should
be windowed (the default, `CVA_DEBUG_CONSOLE` unset).
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH is injected by PyInstaller
EXE_NAME = "Calgary Vipers Auction 2026"
DEBUG_CONSOLE = os.environ.get("CVA_DEBUG_CONSOLE") == "1"
# The .exe itself keeps the clean final-release name; only the *folder*
# is RC-labeled, so a test build can never be mistaken for (or silently
# collide with) a future "dist/Calgary Vipers Auction 2026/" final release
# folder. The debug console build gets its own distinctly-named folder so
# it never overwrites the windowed RC2 release build (or vice versa).
DIST_FOLDER_NAME = "Calgary Vipers Auction 2026 RC2 Debug" if DEBUG_CONSOLE else "Calgary Vipers Auction 2026 RC2"

datas = [
    (str(ROOT / "data"), "data"),
    (str(ROOT / "assets"), "assets"),
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=DEBUG_CONSOLE,
    icon=None,  # no suitable .ico exists yet — a missing icon beats a bad one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=DIST_FOLDER_NAME,
)
