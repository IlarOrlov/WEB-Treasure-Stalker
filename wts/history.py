"""Run history. Kept in memory for this session; saved to disk only while 'remember' is turned on."""
from __future__ import annotations

import json
import threading
from typing import Any

from . import prefs

_LOCK = threading.Lock()
_session: list[dict[str, Any]] = []
MAX = 500


def load() -> list[dict[str, Any]]:
    if prefs.remembering():
        try:
            data = json.loads(prefs.history_file().read_text())
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
    return list(_session)


def add(entry: dict[str, Any]) -> None:
    with _LOCK:
        _session.insert(0, entry)
        if prefs.remembering():
            items = [entry] + [e for e in load() if e is not entry]
            prefs.history_file().write_text(json.dumps(items[:MAX], indent=1))


def persist_session() -> None:
    """Called when the user turns 'remember' on: save this session's runs."""
    with _LOCK:
        if prefs.remembering():
            prefs.history_file().write_text(json.dumps(_session[:MAX], indent=1))


def clear() -> None:
    with _LOCK:
        _session.clear()
        if prefs.history_file().exists():
            prefs.history_file().unlink()
