"""Preferences: kept in memory only, unless the user explicitly turns on 'remember'.

When remembering is on, preferences go to <app data>/prefs.json and run history to history.json.
Turning it off deletes both files. Nothing is ever written to the Windows registry.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .tools import data_dir

_LOCK = threading.Lock()
_mem: dict[str, Any] = {}
_loaded = False


def prefs_file() -> Path:
    return data_dir() / "prefs.json"


def history_file() -> Path:
    return data_dir() / "history.json"


def remembering() -> bool:
    return prefs_file().exists()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if remembering():
        try:
            data = json.loads(prefs_file().read_text())
            if isinstance(data, dict):
                _mem.update(data)
        except (OSError, ValueError):
            pass


def get(key: str, default: Any = None) -> Any:
    _load()
    return _mem.get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001
    _load()
    with _LOCK:
        _mem[key] = value
        if remembering():
            prefs_file().write_text(json.dumps(_mem, indent=1))


def enable() -> Path:
    """User turned 'remember' on: create the app folder and save current preferences."""
    _load()
    with _LOCK:
        data_dir(create=True)
        prefs_file().write_text(json.dumps(_mem, indent=1))
    return data_dir()


def disable() -> list[Path]:
    """User turned 'remember' off: delete saved preferences and history (and the folder if now empty)."""
    removed = []
    with _LOCK:
        for f in (prefs_file(), history_file()):
            if f.exists():
                f.unlink()
                removed.append(f)
        d = data_dir()
        try:
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    return removed


class Settings:
    """Drop-in for the subset of QSettings the GUI uses."""

    def value(self, key: str, default: Any = None) -> Any:
        return get(key, default)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802
        set(key, value)
