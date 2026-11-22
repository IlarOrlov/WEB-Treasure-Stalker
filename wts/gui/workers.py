"""Background jobs that report back to the GUI thread through Qt signals."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal


class JobContext:
    def __init__(self, job: "Job"):
        self._job = job
        self.cancel = threading.Event()
        self._last = 0.0

    def log(self, msg: str) -> None:
        self._job.log.emit(str(msg))

    def progress(self, d: dict) -> None:
        now = time.monotonic()
        if d.get("status") in ("downloading", None) and now - self._last < 0.12:
            return
        self._last = now
        self._job.progress.emit(d)

    def stage(self, name: str, frac: float) -> None:
        self._job.stage.emit(name, float(frac))


class Job(QThread):
    log = Signal(str)
    progress = Signal(dict)
    stage = Signal(str, float)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[JobContext], Any], parent=None):
        super().__init__(parent)
        self.fn = fn
        self.ctx = JobContext(self)

    def run(self) -> None:
        try:
            self.done.emit(self.fn(self.ctx))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(traceback.format_exc())
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def cancel(self) -> None:
        self.ctx.cancel.set()


class _ImgSignals(QObject):
    loaded = Signal(bytes, object)


class _ImgTask(QRunnable):
    def __init__(self, url: str, tag: object, sig: _ImgSignals):
        super().__init__()
        self.url, self.tag, self.sig = url, tag, sig

    def run(self) -> None:
        try:
            import requests

            r = requests.get(self.url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.ok and len(r.content) < 8_000_000:
                self.sig.loaded.emit(r.content, self.tag)
        except Exception:  # noqa: BLE001
            pass


class ImageLoader(QObject):
    loaded = Signal(bytes, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sig = _ImgSignals()
        self._sig.loaded.connect(self.loaded)

    def fetch(self, url: str, tag: object = None) -> None:
        if url:
            QThreadPool.globalInstance().start(_ImgTask(url, tag, self._sig))
