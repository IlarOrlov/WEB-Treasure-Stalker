"""History, Tools and About pages."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__, history, prefs
from ..tools import bin_dir, status
from . import icons
from .grab_page import open_path
from .theme import ACCENT, ACCENT2, BAD, GOLD, OK, SEV, VERDICT
from .widgets import Card, Chip, IconBadge, hbox, label, vbox
from .workers import Job

MUTED = "#8B95AA"
REPO = "https://github.com/IlarOrlov/WEB-Treasure-Stalker"


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("page")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(36, 30, 36, 24)
    lay.setSpacing(16)
    return w, lay


class HistoryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        _, lay = None, QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 24)
        lay.setSpacing(16)
        clear = QPushButton(icons.icon("trash", MUTED, 16), "  Clear history")
        clear.setObjectName("ghost")
        clear.clicked.connect(self._clear)
        head = hbox(vbox(label("History", "h1"), label("Previous downloads and analyses. Double-click to open "
                                                        "the report or folder.", "muted", wrap=True), spacing=4), clear)
        head.setStretch(0, 1)
        lay.addLayout(head)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["When", "Target", "Type", "Files", "Piracy", "Security"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        h = self.tree.header()
        for i in range(6):
            h.setSectionResizeMode(i, QHeaderView.Stretch if i == 1 else QHeaderView.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self._open)
        store = Card("card2")
        sl = QHBoxLayout(store)
        sl.setContentsMargins(16, 12, 16, 12)
        self.remember = QCheckBox("Remember preferences and history on this computer")
        self.remember.setChecked(prefs.remembering())
        self.remember.toggled.connect(self._toggle_remember)
        self.store_note = label("", "faint", wrap=True)
        sl.addLayout(vbox(self.remember, self.store_note, spacing=4), 1)
        lay.addWidget(store)
        self._update_note()
        lay.addWidget(self.tree, 1)
        self.empty = label("No history yet. Your downloads and scans will appear here.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)

    def _update_note(self) -> None:
        if prefs.remembering():
            self.store_note.setText(f"On: saved in {prefs.prefs_file().parent} (prefs.json, history.json). "
                                    "Turning this off deletes them.")
        else:
            self.store_note.setText("Off (default): nothing is saved. Theme, folder and history are forgotten "
                                    "when you close the app.")

    def _toggle_remember(self, on: bool) -> None:
        if on:
            where = prefs.prefs_file().parent
            if QMessageBox.question(self, "Remember on this computer",
                                    f"Create the folder\n{where}\nand save your preferences and history there?") \
                    != QMessageBox.Yes:
                self.remember.blockSignals(True)
                self.remember.setChecked(False)
                self.remember.blockSignals(False)
                return
            prefs.enable()
            history.persist_session()
        else:
            removed = prefs.disable()
            if removed:
                QMessageBox.information(self, "Deleted", "Deleted:\n" + "\n".join(str(r) for r in removed))
        self._update_note()
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        items = history.load()
        for e in items:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("time", 0)))
            target = e.get("title") or e.get("url", "")
            pir = f"{e['verdict'].title()} ({e['score']})" if e.get("verdict") else "—"
            it = QTreeWidgetItem([when, target, e.get("kind", ""), str(e.get("files", 0)), pir,
                                  (e.get("severity") or "info").upper()])
            it.setToolTip(1, e.get("url", ""))
            it.setIcon(5, icons.dot(SEV.get(e.get("severity") or "info", MUTED)))
            if e.get("verdict"):
                it.setIcon(4, icons.dot(VERDICT.get(e["verdict"], MUTED)))
            it.setData(0, Qt.UserRole, e)
            self.tree.addTopLevelItem(it)
        self.empty.setVisible(not items)
        self.tree.setVisible(bool(items))

    def _open(self, item: QTreeWidgetItem, _col: int) -> None:
        e = item.data(0, Qt.UserRole)
        target = e.get("report") if e.get("report") and Path(e["report"]).exists() else e.get("folder")
        if target and Path(target).exists():
            open_path(target)
        else:
            QMessageBox.information(self, "Not found", "The files of this run were moved or deleted.")

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear history", "Remove all history entries? (Downloaded files are kept.)") \
                == QMessageBox.Yes:
            history.clear()
            self.refresh()


class ToolCard(Card):
    install = Signal(str)

    def __init__(self, st):
        super().__init__("card")
        self.key = st.key
        ic = {"ffmpeg": "play", "js": "settings", "browser": "globe", "clamav": "shield", "yara": "bug"}.get(st.key, "tools")
        self.badge = IconBadge(ic, ACCENT2 if st.installable else ACCENT, 42)
        self.name = label(st.name, "h3")
        self.purpose = label(st.purpose, "muted", wrap=True)
        self.detail = label("", "faint", wrap=True, selectable=True)
        self.chip = Chip("", OK)
        self.btn = QPushButton(icons.icon("download", "#ffffff", 16), "  Install")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(lambda: self.install.emit(self.key))
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(16)
        lay.addWidget(self.badge, 0, Qt.AlignTop)
        lay.addLayout(vbox(hbox(self.name, self.chip, None, spacing=10), self.purpose, self.detail, self.bar, spacing=4), 1)
        lay.addWidget(self.btn, 0, Qt.AlignVCenter)
        self.update_status(st)

    def update_status(self, st) -> None:
        self.detail.setText(st.detail)
        if st.found:
            self.chip.set("READY", OK)
        elif st.installable:
            self.chip.set("MISSING", BAD if st.key == "ffmpeg" else GOLD)
        else:
            self.chip.set("OPTIONAL", MUTED)
        self.btn.setVisible(st.installable and not st.found)
        if not st.installable and not st.found:
            hint = {"clamav": "Linux: sudo apt install clamav && sudo freshclam  ·  Windows: install ClamAV from clamav.net",
                    "yara": "Linux: sudo apt install yara  ·  Windows: download yara from github.com/VirusTotal/yara"}
            self.detail.setText(hint.get(st.key, st.detail))


class ToolsPage(QWidget):
    toast = Signal(str, str)
    tools_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 24)
        lay.setSpacing(16)
        refresh = QPushButton(icons.icon("refresh", MUTED, 16), "  Re-check")
        refresh.clicked.connect(self.refresh)
        head = hbox(vbox(label("Tools", "h1"), label(
            "External engines used by the app. They are taken from your system when available, otherwise "
            "downloaded on demand (checksums verified).", "muted", wrap=True), spacing=4), refresh)
        head.setStretch(0, 1)
        lay.addLayout(head)
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(12)
        inner = QWidget()
        inner.setObjectName("page")
        inner.setLayout(self.cards_box)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        lay.addWidget(sa, 1)
        folder = QPushButton(icons.icon("folder", MUTED, 16), "  Open tools folder")
        folder.setObjectName("ghost")
        folder.clicked.connect(lambda: open_path(bin_dir()))
        lay.addLayout(hbox(label(f"Installed tools live in {bin_dir()}", "faint"), None, folder))
        self.cards: dict[str, ToolCard] = {}
        self.jobs: dict[str, Job] = {}
        self.refresh()

    def refresh(self) -> None:
        for st in status():
            if st.key in self.cards:
                self.cards[st.key].update_status(st)
            else:
                c = ToolCard(st)
                c.install.connect(self.install)
                self.cards[st.key] = c
                self.cards_box.addWidget(c)
        if self.cards_box.itemAt(self.cards_box.count() - 1).spacerItem() is None:
            self.cards_box.addStretch(1)
        self.tools_changed.emit()

    def missing(self, key: str) -> bool:
        return any(st.key == key and not st.found for st in status())

    def install(self, key: str) -> None:
        if key in self.jobs and self.jobs[key].isRunning():
            return
        from .. import tools

        card = self.cards[key]
        dest = {"ffmpeg": f"{bin_dir()}  (ffmpeg + ffprobe, about 200 MB unpacked)",
                "js": f"{bin_dir()}  (deno, about 100 MB unpacked)",
                "browser": f"{tools.browsers_dir()}  (Chromium headless shell, about 250 MB)"}[key]
        if QMessageBox.question(self, f"Install {card.name.text()}",
                                f"This downloads {card.name.text()} from its official GitHub release, verifies the "
                                f"checksum and installs it to:\n\n{dest}\n\nContinue?") != QMessageBox.Yes:
            return
        card.btn.setEnabled(False)
        card.btn.setText("  Installing…")
        card.bar.show()
        card.bar.setRange(0, 1000)
        card.bar.setValue(0)

        def work(ctx):
            prog = lambda done, total: ctx.progress({"status": "dl", "downloaded": done, "total": total})  # noqa: E731
            if key == "ffmpeg":
                return tools.install_ffmpeg(prog, ctx.cancel)
            if key == "js":
                return tools.install_deno(prog, ctx.cancel)
            if key == "browser":
                return tools.install_browser(ctx.log, ctx.cancel)
            raise ValueError(key)

        job = Job(work, self)
        self.jobs[key] = job

        def on_prog(d):
            if d.get("total"):
                card.bar.setRange(0, 1000)
                card.bar.setValue(int(d["downloaded"] / d["total"] * 1000))
                card.detail.setText(f"Downloading… {d['downloaded'] / 1048576:.1f} / {d['total'] / 1048576:.1f} MB")

        def on_log(line):
            if line.strip():
                card.bar.setRange(0, 0)
                card.detail.setText(line.strip()[:140])

        def done(_res):
            self.toast.emit(f"{card.name.text()} installed", OK)
            self._finish(card)

        def failed(err):
            self.toast.emit(f"Install failed: {err[:80]}", BAD)
            card.detail.setText(err)
            self._finish(card, ok=False)

        job.progress.connect(on_prog)
        job.log.connect(on_log)
        job.done.connect(done)
        job.failed.connect(failed)
        job.start()

    def _finish(self, card: ToolCard, ok: bool = True) -> None:
        card.bar.hide()
        card.btn.setEnabled(True)
        card.btn.setText("  Install")
        if ok:
            self.refresh()


class AboutPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 24)
        lay.setSpacing(16)
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QLabel

        from .theme import ASSETS

        logo = QLabel()
        logo.setPixmap(QIcon(str(ASSETS / "icon.svg")).pixmap(88, 88))
        lay.addLayout(hbox(logo, vbox(label("WEB-Treasure-Stalker", "h1"), label(f"Version {__version__}", "muted"),
                                      spacing=2), None, spacing=18))
        card = Card("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 20)
        cl.setSpacing(10)
        cl.addWidget(label("A blue-team and OSINT tool. It locates and downloads every video on a web page in the "
                           "best quality, scans the site and the files for malware and attacks, and flags likely "
                           "piracy. YouTube and Spotify links are handled directly.", wrap=True))
        cl.addWidget(label("Safety", "h3"))
        cl.addWidget(label("• Analyse hostile sites from a disposable VM. Pages are rendered in an isolated headless "
                           "browser, and media files are parsed by FFprobe.\n"
                           "• Downloaded files are made read-only and non-executable.\n"
                           "• DRM is never circumvented. DRM-protected streams are reported and skipped. Spotify tracks "
                           "are matched to YouTube uploads.\n"
                           "• The piracy verdict is a heuristic analyst aid, not a legal determination.",
                           "muted", wrap=True))
        cl.addWidget(label("Built with", "h3"))
        cl.addWidget(label("yt-dlp · Playwright · FFmpeg · Qt for Python (PySide6) · Beautiful Soup · Inter typeface",
                           "muted", wrap=True))
        lay.addWidget(card)
        repo = QPushButton(icons.icon("external", "#ffffff", 16), "  Project on GitHub")
        repo.setObjectName("primary")
        repo.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO)))
        lay.addLayout(hbox(repo, None))
        lay.addStretch(1)
