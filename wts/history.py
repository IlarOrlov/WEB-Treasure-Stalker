"""Small persistent history of runs (JSON in the app data folder)."""
from __future__ import annotations

import json
import threading
from typing import Any

from .tools import data_dir

_LOCK = threading.Lock()
MAX = 500


def _file():
    return data_dir() / "history.json"


def load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_file().read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add(entry: dict[str, Any]) -> None:
    with _LOCK:
        items = [entry] + load()
        _file().write_text(json.dumps(items[:MAX], indent=1))


def clear() -> None:
    with _LOCK:
        _file().write_text("[]")
