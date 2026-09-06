"""Shared pytest fixtures for GUI-frame tests.

Repeatedly creating/destroying separate CTk() roots across test modules in
one process is a known source of flaky TclError teardown timing on this
platform. Frame-level screen tests (screens built as a child of a root,
not MainWindow itself) share one session-scoped hidden root instead.
"""

import pytest


@pytest.fixture(scope="session")
def hidden_root():
    tkinter = pytest.importorskip("tkinter")
    import customtkinter as ctk

    try:
        root = ctk.CTk()
        root.withdraw()
    except tkinter.TclError as exc:
        pytest.skip(f"No display/Tk backend available for GUI test: {exc}")

    yield root
    root.destroy()
