"""The main 'Grab' page: paste link -> inspect -> choose -> run -> results."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import history
from ..platforms import Inspection, classify
from ..prompts import FORMAT_PRESETS, FormatChoice
from . import icons
from .theme import ACCENT, ACCENT2, BAD, GOLD, OK, PALETTES, SEV, VERDICT
from .widgets import (Card, Chip, IconBadge, IconLabel, ScoreGauge, Segmented, Spinner, StatTile, hbox, label,
                      placeholder_pixmap, rounded_pixmap, vbox)
from .workers import ImageLoader, Job

MUTED = "#8B95AA"
SEV_ORDER = ["critical", "high", "medium", "low", "info"]


def fmt_dur(sec: Any) -> str:
    try:
        s = int(float(sec))
    except (TypeError, ValueError):
        return ""
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def fmt_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def open_path(p: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


def default_download_dir() -> str:
    return str(Path.home() / "Downloads" / "WEB-Treasure-Stalker")


# --------------------------------------------------------------------------- options bar


class OptionsBar(Card):
    go = Signal()

    def __init__(self, settings: QSettings):
        super().__init__("card")
        self.settings = settings
        self.path = QLineEdit(settings.value("download_dir", default_download_dir()))
        self.path.setMinimumWidth(240)
        browse = QPushButton(icons.icon("folder", MUTED, 16), "")
        browse.setToolTip("Choose folder")
        browse.setFixedWidth(42)
        browse.clicked.connect(self._browse)
        self.fmt = QComboBox()
        for key, (kind, _c, text) in FORMAT_PRESETS.items():
            if key == "audio":
                self.fmt.insertSeparator(self.fmt.count())
            self.fmt.addItem(icons.icon("play" if kind == "video" else "music", MUTED, 16), text, key)
        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self.res = QComboBox()
        for h in (0, 2160, 1440, 1080, 720, 480, 360):
            self.res.addItem("Best resolution" if h == 0 else f"Up to {h}p", h)
        self.btn = QPushButton(icons.icon("download", "#ffffff", 18), "  Download")
        self.btn.setObjectName("primary")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.go)
        lay = QGridLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(6)
        lay.addWidget(label("SAVE TO", "faint"), 0, 0)
        lay.addWidget(label("FORMAT", "faint"), 0, 2)
        lay.addWidget(label("QUALITY", "faint"), 0, 3)
        lay.addLayout(hbox(self.path, browse, spacing=6), 1, 0, 1, 2)
        lay.addWidget(self.fmt, 1, 2)
        lay.addWidget(self.res, 1, 3)
        lay.addWidget(self.btn, 1, 4)
        lay.setColumnStretch(0, 1)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose download folder", self.path.text())
        if d:
            self.path.setText(d)

    def _fmt_changed(self):
        key = self.fmt.currentData()
        self.res.setEnabled(bool(key) and FORMAT_PRESETS[key][0] == "video")

    def set_format(self, key: str) -> None:
        i = self.fmt.findData(key)
        if i >= 0:
            self.fmt.setCurrentIndex(i)

    def set_downloads_enabled(self, on: bool) -> None:
        for w in (self.fmt, self.res):
            w.setEnabled(on)
        if on:
            self._fmt_changed()

    def choice(self) -> FormatChoice:
        kind, container, _ = FORMAT_PRESETS[self.fmt.currentData()]
        h = self.res.currentData() if kind == "video" else 0
        return FormatChoice(kind=kind, container=container, max_height=h or None)

    def folder(self) -> Path:
        p = Path(self.path.text().strip() or default_download_dir()).expanduser()
        self.settings.setValue("download_dir", str(p))
        return p


# --------------------------------------------------------------------------- page


class GrabPage(QWidget):
    toast = Signal(str, str)
    request_tools = Signal()
    busy_changed = Signal(bool)

    def __init__(self, settings: QSettings, mode_getter):
        super().__init__()
        self.setObjectName("page")
        self.settings = settings
        self.mode = mode_getter  # returns "dark"/"light"
        self.job: Job | None = None
        self.ins: Inspection | None = None
        self.site_url = ""
        self.last_result: Any = None
        self._job_items = 0
        self.images = ImageLoader(self)
        self.images.loaded.connect(self._image_loaded)

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 36, 24)
        root.setSpacing(18)

        title = label("Find the treasure. Check it for traps.", "h1")
        sub = label("Paste a web page, YouTube or Spotify link. Every video is located, downloaded in the best quality, "
                    "scanned for malware and attacks, and checked for piracy.", "muted", wrap=True)
        root.addLayout(vbox(title, sub, spacing=4))

        # URL bar
        self.url = QLineEdit()
        self.url.setObjectName("url")
        self.url.setPlaceholderText("https://…   web page · YouTube video/playlist · Spotify track/album/playlist")
        self.url.addAction(icons.icon("link", MUTED, 18), QLineEdit.LeadingPosition)
        self.url.returnPressed.connect(self.inspect)
        self.url.setClearButtonEnabled(True)
        paste = QPushButton(icons.icon("clipboard", MUTED, 16), " Paste")
        paste.setMinimumHeight(48)
        paste.clicked.connect(self._paste)
        self.inspect_btn = QPushButton(icons.icon("search", "#ffffff", 18), "  Inspect")
        self.inspect_btn.setObjectName("primary")
        self.inspect_btn.setMinimumHeight(48)
        self.inspect_btn.setCursor(Qt.PointingHandCursor)
        self.inspect_btn.clicked.connect(self.inspect)
        root.addLayout(hbox(self.url, paste, self.inspect_btn, spacing=10))

        # banner (missing tools)
        self.banner = Card("banner")
        self.banner_text = label("", wrap=True)
        bb = QPushButton("Open Tools")
        bb.clicked.connect(self.request_tools)
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.addWidget(IconLabel("alert", GOLD, 18))
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(bb)
        self.banner.hide()
        root.addWidget(self.banner)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.pages: dict[str, QWidget] = {}
        for name, builder in (("empty", self._build_empty), ("loading", self._build_loading),
                              ("message", self._build_message), ("preview", self._build_preview),
                              ("site", self._build_site), ("running", self._build_running),
                              ("result_platform", self._build_result_platform), ("result_site", self._build_result_site)):
            w = builder()
            self.pages[name] = w
            self.stack.addWidget(w)
        self.options = OptionsBar(settings)
        self.options.go.connect(self.start)
        root.addWidget(self.options)
        self.show_state("empty")

    # ------------------------------------------------------------------ helpers
    def show_state(self, name: str) -> None:
        self.stack.setCurrentWidget(self.pages[name])
        self.options.setVisible(name in ("preview", "site"))

    def _scroll(self, inner: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        inner.setObjectName("page")
        return sa

    def _paste(self):
        self.url.setText(QGuiApplication.clipboard().text().strip())
        self.inspect()

    def set_banner(self, text: str | None) -> None:
        self.banner_text.setText(text or "")
        self.banner.setVisible(bool(text))

    def is_busy(self) -> bool:
        return self.job is not None and self.job.isRunning()

    # ------------------------------------------------------------------ states
    def _build_empty(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setSpacing(16)
        feats = [
            ("radar", ACCENT, "Locate every video", "Video tags, embeds (Instagram, YouTube, TikTok…), hidden HLS/DASH "
             "streams and every season/episode behind menus."),
            ("download", ACCENT2, "Best quality, your format", "Merges the best video and audio tracks, or converts to "
             "MP4, MKV, MP3, FLAC… Playlists and series get their own folder."),
            ("shield", OK, "Scan for traps", "Malvertising, crypto-miners, drive-by downloads, obfuscated scripts, "
             "payloads hidden inside media files, ClamAV/YARA."),
            ("flag", GOLD, "Flag piracy", "An explainable 0-100 piracy score per site and per video, with "
             "every reason listed."),
        ]
        for i, (ic, color, head, text) in enumerate(feats):
            c = Card("card")
            lay = QVBoxLayout(c)
            lay.setContentsMargins(20, 20, 20, 20)
            lay.setSpacing(10)
            lay.addWidget(IconBadge(ic, color, 44))
            lay.addWidget(label(head, "h3"))
            lay.addWidget(label(text, "muted", wrap=True))
            lay.addStretch(1)
            grid.addWidget(c, i // 2, i % 2)
        tip = label("Tip: YouTube channel or home pages contain no main video, so nothing is downloaded from them. "
                    "Use a video or playlist link.", "faint", wrap=True)
        grid.addWidget(tip, 2, 0, 1, 2)
        grid.setRowStretch(3, 1)
        return w

    def _build_loading(self) -> QWidget:
        w = QWidget()
        self.spinner = Spinner(46)
        self.loading_text = label("Inspecting link…", "h3")
        self.loading_sub = label("", "muted")
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        lay.addWidget(self.spinner, 0, Qt.AlignHCenter)
        lay.addSpacing(12)
        lay.addWidget(self.loading_text, 0, Qt.AlignHCenter)
        lay.addWidget(self.loading_sub, 0, Qt.AlignHCenter)
        lay.addStretch(2)
        return w

    def _build_message(self) -> QWidget:
        w = QWidget()
        c = Card("card")
        self.msg_badge = IconBadge("alert", GOLD, 52)
        self.msg_title = label("Nothing to download", "h2")
        self.msg_text = label("", "muted", wrap=True, selectable=True)
        again = QPushButton(icons.icon("refresh", MUTED, 16), "  Try another link")
        again.clicked.connect(lambda: (self.url.selectAll(), self.url.setFocus(), self.show_state("empty")))
        lay = QVBoxLayout(c)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(12)
        lay.addWidget(self.msg_badge)
        lay.addWidget(self.msg_title)
        lay.addWidget(self.msg_text)
        lay.addSpacing(6)
        lay.addLayout(hbox(again, None))
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(c)
        outer.addStretch(1)
        return w

    def _build_preview(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        card = Card("card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(18, 18, 18, 18)
        cl.setSpacing(18)
        self.thumb = QLabel()
        self.thumb.setFixedSize(256, 144)
        cl.addWidget(self.thumb, 0, Qt.AlignTop)
        self.pv_platform = Chip("YOUTUBE", BAD)
        self.pv_kind = Chip("VIDEO", ACCENT)
        self.pv_title = label("", "h2", wrap=True, selectable=True)
        self.pv_meta = label("", "muted", wrap=True)
        self.pv_quality = label("", "faint", wrap=True)
        self.pv_mode = Segmented([("video", "Only this video"), ("playlist", "Playlist items")])
        self.pv_mode.changed.connect(self._mode_changed)
        self.pv_note = label("", "faint", wrap=True)
        info = vbox(hbox(self.pv_platform, self.pv_kind, None, spacing=6), self.pv_title, self.pv_meta,
                    self.pv_quality, 4, hbox(self.pv_mode, None), self.pv_note, None, spacing=6)
        cl.addLayout(info, 1)
        lay.addWidget(card)

        # entry list
        self.list_box = Card("card")
        ll = QVBoxLayout(self.list_box)
        ll.setContentsMargins(14, 12, 14, 14)
        ll.setSpacing(10)
        self.sel_label = label("", "h3")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter…")
        self.filter.setMaximumWidth(220)
        self.filter.textChanged.connect(self._filter_entries)
        b_all, b_none, b_inv = QPushButton("All"), QPushButton("None"), QPushButton("Invert")
        for b in (b_all, b_none, b_inv):
            b.setObjectName("ghost")
        b_all.clicked.connect(lambda: self._set_checks(True))
        b_none.clicked.connect(lambda: self._set_checks(False))
        b_inv.clicked.connect(lambda: self._set_checks(None))
        ll.addLayout(hbox(self.sel_label, None, self.filter, b_all, b_none, b_inv, spacing=4))
        self.entries = QTreeWidget()
        self.entries.setHeaderLabels(["Title", "Artist / channel", "Length"])
        self.entries.setRootIsDecorated(False)
        self.entries.setAlternatingRowColors(True)
        self.entries.setUniformRowHeights(True)
        hdr = self.entries.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.entries.itemChanged.connect(lambda *_: self._update_sel())
        ll.addWidget(self.entries, 1)
        lay.addWidget(self.list_box, 1)
        self.list_spacer = QWidget()
        lay.addWidget(self.list_spacer, 1)
        return w

    def _build_site(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        card = Card("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 22)
        cl.setSpacing(14)
        self.site_title = label("", "h2", wrap=True)
        self.site_url_lbl = label("", "faint", wrap=True, selectable=True)
        self.site_status = Chip("", OK)
        head = hbox(IconBadge("globe", ACCENT2, 48), vbox(self.site_title, self.site_url_lbl, spacing=2),
                    self.site_status, spacing=14)
        head.setStretch(1, 1)
        head.setAlignment(self.site_status, Qt.AlignTop)
        cl.addLayout(head)
        self.site_mode = Segmented([("download", "Scan + download videos"), ("scan", "Security scan only")])
        self.site_mode.changed.connect(lambda k: self.options.set_downloads_enabled(k == "download"))
        self.opt_depth = QSpinBox()
        self.opt_depth.setRange(0, 3)
        self.opt_depth.setValue(1)
        self.opt_pages = QSpinBox()
        self.opt_pages.setRange(1, 200)
        self.opt_pages.setValue(25)
        self.opt_menus = QCheckBox("Walk season / episode menus and buttons")
        self.opt_menus.setChecked(True)
        self.opt_browser = QCheckBox("Render with browser engine")
        self.opt_browser.setChecked(True)
        self.opt_headful = QCheckBox("Show the browser window")
        self.opt_av = QCheckBox("ClamAV / YARA scan (if installed)")
        self.opt_av.setChecked(True)
        self.opt_deep = QCheckBox("Deep decode every video (slow)")
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(10)
        g.addWidget(label("Follow episode links (depth)", "muted"), 0, 0)
        g.addWidget(self.opt_depth, 0, 1)
        g.addWidget(label("Max pages to visit", "muted"), 1, 0)
        g.addWidget(self.opt_pages, 1, 1)
        g.addWidget(self.opt_menus, 0, 2)
        g.addWidget(self.opt_browser, 1, 2)
        g.addWidget(self.opt_headful, 2, 2)
        g.addWidget(self.opt_av, 0, 3)
        g.addWidget(self.opt_deep, 1, 3)
        g.setColumnStretch(2, 1)
        g.setColumnStretch(3, 1)
        cl.addLayout(hbox(self.site_mode, None))
        cl.addLayout(g)
        cl.addWidget(label("Analyse hostile sites from a disposable VM. The page is rendered in an isolated headless "
                           "browser with downloads, notifications and service workers blocked.", "faint", wrap=True))
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _build_running(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        card = Card("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 20)
        cl.setSpacing(10)
        self.run_spinner = Spinner(30)
        self.run_title = label("Working…", "h2")
        self.cancel_btn = QPushButton(icons.icon("stop", BAD, 16), "  Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.clicked.connect(self.cancel)
        cl.addLayout(hbox(self.run_spinner, self.run_title, None, self.cancel_btn, spacing=12))
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall_lbl = label("", "muted")
        self.item_lbl = label("", "h3", wrap=True)
        self.item_bar = QProgressBar()
        self.item_bar.setRange(0, 1000)
        self.item_stats = label("", "faint")
        cl.addWidget(self.overall_lbl)
        cl.addWidget(self.overall)
        cl.addSpacing(8)
        cl.addWidget(self.item_lbl)
        cl.addWidget(self.item_bar)
        cl.addWidget(self.item_stats)
        lay.addWidget(card)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        lay.addWidget(label("ACTIVITY", "faint"))
        lay.addWidget(self.log_view, 1)
        return w

    def _build_result_platform(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        card = Card("card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 20)
        cl.setSpacing(16)
        self.rp_badge = IconBadge("check", OK, 52)
        self.rp_title = label("", "h2")
        self.rp_sub = label("", "muted", wrap=True, selectable=True)
        self.rp_open = QPushButton(icons.icon("folder", MUTED, 16), "  Open folder")
        self.rp_open.clicked.connect(lambda: open_path(self.last_result.get("destination", "")))
        new = QPushButton(icons.icon("refresh", "#ffffff", 16), "  New link")
        new.setObjectName("primary")
        new.clicked.connect(self.reset)
        cl.addWidget(self.rp_badge)
        cl.addLayout(vbox(self.rp_title, self.rp_sub, spacing=4), 1)
        cl.addWidget(self.rp_open)
        cl.addWidget(new)
        lay.addWidget(card)
        self.rp_files = QTreeWidget()
        self.rp_files.setHeaderLabels(["File", "Status", "Size", "Security"])
        self.rp_files.setRootIsDecorated(False)
        self.rp_files.setAlternatingRowColors(True)
        h = self.rp_files.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.rp_files.itemDoubleClicked.connect(self._open_item_file)
        lay.addWidget(self.rp_files, 1)
        return w

    def _build_result_site(self) -> QWidget:
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(14)
        vcard = Card("card")
        vl = QHBoxLayout(vcard)
        vl.setContentsMargins(20, 18, 20, 18)
        vl.setSpacing(18)
        self.gauge = ScoreGauge(140)
        self.rs_verdict = label("", "h2")
        self.rs_host = label("", "faint", wrap=True, selectable=True)
        self.rs_reasons = label("", "muted", wrap=True)
        self.rs_reasons.setTextFormat(Qt.RichText)
        vl.addWidget(self.gauge, 0, Qt.AlignTop)
        vl.addLayout(vbox(self.rs_verdict, self.rs_host, 4, self.rs_reasons, None, spacing=2), 1)
        top.addWidget(vcard, 3)
        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.t_pages = StatTile("pages analysed", "globe", ACCENT2)
        self.t_cands = StatTile("videos located", "radar", ACCENT)
        self.t_files = StatTile("files downloaded", "download", OK)
        self.t_drm = StatTile("DRM-protected", "shield", MUTED)
        self.t_sev = StatTile("highest severity", "alert", GOLD)
        self.t_find = StatTile("security findings", "bug", BAD)
        for i, t in enumerate((self.t_pages, self.t_cands, self.t_files, self.t_drm, self.t_sev, self.t_find)):
            tiles.addWidget(t, i // 2, i % 2)
        top.addLayout(tiles, 2)
        lay.addLayout(top)

        actions = QHBoxLayout()
        self.rs_report = QPushButton(icons.icon("report", MUTED, 16), "  Open HTML report")
        self.rs_report.clicked.connect(lambda: open_path(Path(self.last_result["run_dir"]) / "report.html"))
        self.rs_folder = QPushButton(icons.icon("folder", MUTED, 16), "  Open folder")
        self.rs_folder.clicked.connect(lambda: open_path(self.last_result["run_dir"]))
        new = QPushButton(icons.icon("refresh", "#ffffff", 16), "  New scan")
        new.setObjectName("primary")
        new.clicked.connect(self.reset)
        actions.addWidget(self.rs_report)
        actions.addWidget(self.rs_folder)
        actions.addStretch(1)
        actions.addWidget(new)
        lay.addLayout(actions)

        self.tabs = QTabWidget()
        self.th_tree = self._tree(["Severity", "Category", "Finding", "Evidence"], stretch=2)
        self.sev_filter = QComboBox()
        self.sev_filter.addItems(["All severities", "Critical", "High", "Medium", "Low", "Info"])
        self.sev_filter.currentIndexChanged.connect(self._apply_sev_filter)
        th = QWidget()
        thl = QVBoxLayout(th)
        thl.setContentsMargins(0, 10, 0, 0)
        thl.addLayout(hbox(None, self.sev_filter))
        thl.addWidget(self.th_tree)
        self.vid_tree = self._tree(["Video", "Status", "Quality", "Security", "File"], stretch=0)
        self.vid_tree.itemDoubleClicked.connect(self._open_item_file)
        self.cand_tree = self._tree(["Kind", "Found via", "Label", "URL"], stretch=3)
        self.res_log = QPlainTextEdit()
        self.res_log.setReadOnly(True)
        self.tabs.addTab(th, icons.icon("shield", MUTED, 16), "Threats")
        self.tabs.addTab(self.vid_tree, icons.icon("play", MUTED, 16), "Videos")
        self.tabs.addTab(self.cand_tree, icons.icon("radar", MUTED, 16), "Located")
        self.tabs.addTab(self.res_log, icons.icon("activity", MUTED, 16), "Log")
        self.tabs.setMinimumHeight(360)
        lay.addWidget(self.tabs, 1)
        return self._scroll(inner)

    def _tree(self, headers: list[str], stretch: int) -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels(headers)
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(True)
        t.setWordWrap(False)
        h = t.header()
        for i in range(len(headers)):
            h.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch else QHeaderView.ResizeToContents)
        return t

    # ------------------------------------------------------------------ inspect
    def inspect(self) -> None:
        if self.is_busy():
            return
        url = self.url.text().strip()
        if not url:
            self.url.setFocus()
            return
        if not url.startswith(("http://", "https://", "spotify:")):
            url = "https://" + url
            self.url.setText(url)
        self.loading_text.setText("Inspecting link…")
        self.loading_sub.setText(urlparse(url).hostname or url)
        self.spinner.start()
        self.show_state("loading")
        self.inspect_btn.setEnabled(False)

        def work(ctx):
            target = classify(url)
            if target is not None:
                from ..platforms import inspect as do_inspect

                return ("platform", do_inspect(target))
            import requests
            from bs4 import BeautifulSoup

            from ..discovery import DEFAULT_UA
            try:
                r = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=20)
                soup = BeautifulSoup(r.text[:400_000], "html.parser")
                t = soup.title.get_text(strip=True) if soup.title else ""
                return ("site", {"url": url, "final": r.url, "status": r.status_code, "title": t})
            except Exception as exc:  # noqa: BLE001
                return ("site", {"url": url, "final": url, "status": None, "title": "", "error": str(exc)[:200]})

        self._run_job(work, self._inspected, self._inspect_failed, show_running=False)

    def _inspect_failed(self, err: str) -> None:
        self.spinner.stop()
        self._message("Could not inspect this link", err, "alert", BAD)

    def _message(self, title: str, text: str, icon: str = "alert", color: str = GOLD) -> None:
        self.msg_title.setText(title)
        self.msg_text.setText(text)
        self.msg_badge.set(icon, color)
        self.show_state("message")

    def _inspected(self, res) -> None:
        self.spinner.stop()
        kind, data = res
        if kind == "site":
            self._show_site(data)
            return
        ins: Inspection = data
        self.ins = ins
        if not ins.ok:
            self._message("Nothing to download", ins.message, "info", GOLD)
            return
        t = ins.target
        is_sp = t.platform == "spotify"
        self.pv_platform.set("SPOTIFY" if is_sp else "YOUTUBE", "#1DB954" if is_sp else "#FF3B3B")
        kind_txt = {"video": "VIDEO", "video-in-playlist": "VIDEO IN PLAYLIST", "playlist": "PLAYLIST",
                    "track": "TRACK", "album": "ALBUM"}.get(t.kind, t.kind.upper())
        self.pv_kind.set(kind_txt, ACCENT)
        self.pv_title.setText(ins.title or ins.playlist_title or "(untitled)")
        meta = [x for x in (ins.uploader, fmt_dur(ins.duration),
                            f"{len(ins.entries)} items" if ins.entries and t.kind != "track" else "") if x]
        if ins.is_live:
            meta.append("LIVE: records until the stream ends")
        self.pv_meta.setText("  ·  ".join(meta))
        self.pv_quality.setText(("Best available: " + ins.quality) if ins.quality and not is_sp else ins.quality)
        self.thumb.setPixmap(placeholder_pixmap(256, 144, "music" if is_sp else "play", self.mode() == "dark"))
        self.images.fetch(ins.thumbnail, "thumb")
        in_pl = t.kind == "video-in-playlist" and bool(ins.entries)
        self.pv_mode.setVisible(in_pl)
        self.pv_mode.set_value("video")
        self.pv_note.setText("Spotify audio is DRM-protected and never downloaded. Each track is matched to its "
                             "YouTube upload, and that upload is downloaded." if is_sp else
                             ("A sub-folder named after the playlist is created." if t.kind == "playlist" else ""))
        self._fill_entries()
        self._mode_changed("playlist" if t.kind in ("playlist", "album") or (is_sp and t.kind == "playlist") else "video")
        self.options.set_format("mp3" if is_sp else self.settings.value("last_format", "best"))
        self.options.set_downloads_enabled(True)
        self.options.btn.setText("  Download")
        self.options.btn.setIcon(icons.icon("download", "#ffffff", 18))
        self.show_state("preview")

    def _fill_entries(self) -> None:
        self.entries.blockSignals(True)
        self.entries.clear()
        ins = self.ins
        width = len(str(len(ins.entries)))
        for i, e in enumerate(ins.entries, 1):
            it = QTreeWidgetItem([f"{i:0{width}d}   {e.get('title') or '?'}", e.get("artists") or e.get("uploader") or "",
                                  fmt_dur(e.get("duration"))])
            it.setCheckState(0, Qt.Checked)
            it.setData(0, Qt.UserRole, i)
            it.setToolTip(0, e.get("title") or "")
            self.entries.addTopLevelItem(it)
        self.entries.blockSignals(False)
        self._update_sel()

    def _mode_changed(self, mode: str) -> None:
        t = self.ins.target if self.ins else None
        show_list = bool(self.ins and self.ins.entries) and (mode == "playlist" or (t and t.kind in ("playlist", "album")))
        if t and t.kind == "track":
            show_list = False
        self.list_box.setVisible(show_list)
        self.list_spacer.setVisible(not show_list)
        if mode != self.pv_mode.value() and self.pv_mode.isVisible():
            self.pv_mode.set_value(mode)

    def _set_checks(self, state: bool | None) -> None:
        self.entries.blockSignals(True)
        for i in range(self.entries.topLevelItemCount()):
            it = self.entries.topLevelItem(i)
            if it.isHidden():
                continue
            on = (it.checkState(0) != Qt.Checked) if state is None else state
            it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.entries.blockSignals(False)
        self._update_sel()

    def _filter_entries(self, text: str) -> None:
        text = text.lower()
        for i in range(self.entries.topLevelItemCount()):
            it = self.entries.topLevelItem(i)
            it.setHidden(bool(text) and text not in (it.text(0) + " " + it.text(1)).lower())

    def _checked(self) -> list[int]:
        return [self.entries.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.entries.topLevelItemCount())
                if self.entries.topLevelItem(i).checkState(0) == Qt.Checked]

    def _update_sel(self) -> None:
        n = len(self._checked())
        total = self.entries.topLevelItemCount()
        self.sel_label.setText(f"{n} of {total} selected")
        if self.stack.currentWidget() is self.pages["preview"]:
            self.options.btn.setEnabled(n > 0 or self.list_box.isHidden())

    def _image_loaded(self, data: bytes, tag) -> None:
        if tag == "thumb":
            pm = QPixmap()
            if pm.loadFromData(data):
                self.thumb.setPixmap(rounded_pixmap(pm, 256, 144, 12))

    def _show_site(self, d: dict) -> None:
        self.site_url = d["url"]
        host = urlparse(d["final"]).hostname or d["url"]
        self.site_title.setText(d.get("title") or host)
        self.site_url_lbl.setText(d["final"])
        if d.get("error"):
            self.site_status.set("UNREACHABLE", BAD)
            self.site_status.setToolTip(d["error"])
        else:
            st = d.get("status")
            self.site_status.set(f"HTTP {st}", OK if st and st < 400 else GOLD)
        self.site_mode.set_value("download")
        self.options.set_format(self.settings.value("last_format", "best"))
        self.options.set_downloads_enabled(True)
        self.options.btn.setEnabled(True)
        self.options.btn.setText("  Start analysis")
        self.options.btn.setIcon(icons.icon("radar", "#ffffff", 18))
        self.show_state("site")

    # ------------------------------------------------------------------ run
    def start(self) -> None:
        if self.is_busy():
            return
        from ..tools import find_browser

        site = self.stack.currentWidget() is self.pages["site"]
        downloading = not site or self.site_mode.value() == "download"
        if downloading and not self._ensure_ffmpeg():
            return
        if site and self.opt_browser.isChecked() and find_browser() is None:
            r = QMessageBox.question(
                self, "Browser engine missing",
                "Site analysis works best with the Chromium engine (renders players, captures streams, walks episode "
                "menus).\n\nInstall it now from the Tools page? Choose No to continue in static-HTML mode.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                self.request_tools.emit()
                return
            self.opt_browser.setChecked(False)
        key = self.options.fmt.currentData()
        self.settings.setValue("last_format", key)
        if site:
            self._start_site(downloading)
        else:
            self._start_platform()

    def _ensure_ffmpeg(self) -> bool:
        import shutil

        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            return True
        r = QMessageBox.question(
            self, "FFmpeg not found",
            "FFmpeg is needed to merge the best video + audio streams, convert formats and inspect media.\n\n"
            "Open the Tools page to install it automatically? Choose No to continue with reduced quality.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Yes:
            self.request_tools.emit()
            return False
        return r == QMessageBox.No

    def _prep_running(self, title: str) -> None:
        self.log_view.clear()
        self.run_title.setText(title)
        self.overall.setRange(0, 1000)
        self.overall.setValue(0)
        self.item_bar.setRange(0, 1000)
        self.item_bar.setValue(0)
        self.item_lbl.setText("")
        self.item_stats.setText("")
        self.overall_lbl.setText("Starting…")
        self.cancel_btn.setEnabled(True)
        self.run_spinner.start()
        self.show_state("running")

    def _start_platform(self) -> None:
        ins = self.ins
        t = ins.target
        mode = "video"
        items = None
        if t.kind in ("playlist", "album") or (t.kind == "video-in-playlist" and self.pv_mode.value() == "playlist"):
            mode = "playlist"
            chosen = self._checked()
            items = None if len(chosen) == len(ins.entries) else chosen
        choice = self.options.choice()
        base = self.options.folder()
        self._job_items = len(items) if items else (len(ins.entries) if mode == "playlist" else 1)
        self._prep_running(f"Downloading {self._job_items} item(s)" if mode == "playlist" else "Downloading")
        self.overall_lbl.setText(f"{choice.describe()}  →  {base}")
        use_av = True

        def work(ctx):
            from ..platforms import download

            return download(ins, choice, base, mode, items, log=ctx.log, on_progress=ctx.progress,
                            cancel=ctx.cancel, use_av=use_av)

        self._run_job(work, self._platform_done, self._job_failed)

    def _start_site(self, downloading: bool) -> None:
        from ..pipeline import Options

        opts = Options(out=self.options.folder(), browser=self.opt_browser.isChecked(), download=downloading,
                       max_pages=self.opt_pages.value(), depth=self.opt_depth.value(),
                       max_combinations=300 if self.opt_menus.isChecked() else 0,
                       choice=self.options.choice() if downloading else None, deep=self.opt_deep.isChecked(),
                       headful=self.opt_headful.isChecked(), av=self.opt_av.isChecked())
        url = self.site_url
        self._job_items = 0
        self._prep_running("Analysing site")

        def work(ctx):
            from ..pipeline import run

            report, _ = run(url, opts, log=ctx.log, cancel=ctx.cancel, on_progress=ctx.progress, on_stage=ctx.stage)
            return report

        self._run_job(work, self._site_done, self._job_failed)

    def _run_job(self, fn, on_done, on_fail, show_running: bool = True) -> None:
        self.job = Job(fn, self)
        self.job.log.connect(self._log)
        self.job.progress.connect(self._progress)
        self.job.stage.connect(self._stage)
        self.job.done.connect(on_done)
        self.job.failed.connect(on_fail)
        self.job.finished.connect(self._job_finished)
        self.busy_changed.emit(True)
        self.job.start()

    def _job_finished(self) -> None:
        self.inspect_btn.setEnabled(True)
        self.run_spinner.stop()
        self.busy_changed.emit(False)

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.cancel_btn.setEnabled(False)
            self.run_title.setText("Cancelling…")

    def _log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def _stage(self, name: str, frac: float) -> None:
        self.run_title.setText(name)
        self.overall.setValue(int(frac * 1000))
        self.overall_lbl.setText(f"{int(frac * 100)}% overall")

    def _progress(self, d: dict) -> None:
        st = d.get("status")
        title = d.get("title") or Path(d.get("filename") or "").name
        idx, cnt = d.get("index"), d.get("count") or self._job_items
        if idx and cnt and self._job_items:
            self.overall_lbl.setText(f"Item {idx} of {cnt}")
            self.overall.setValue(int((idx - 1) / max(cnt, 1) * 1000))
        if st == "searching":
            self.item_lbl.setText(title)
            self.item_bar.setRange(0, 0)
            self.item_stats.setText("Finding the matching YouTube upload…")
        elif st == "scanning":
            self.item_lbl.setText(title)
            self.item_bar.setRange(0, 0)
            self.item_stats.setText("Scanning file for threats…")
        elif st == "downloading":
            self.item_bar.setRange(0, 1000)
            self.item_lbl.setText(title)
            done, total = d.get("downloaded") or 0, d.get("total") or 0
            self.item_bar.setValue(int(done / total * 1000) if total else 0)
            parts = [fmt_bytes(done) + (f" of {fmt_bytes(total)}" if total else "")]
            if d.get("speed"):
                parts.append(f"{fmt_bytes(d['speed'])}/s")
            if d.get("eta") is not None:
                parts.append(f"ETA {fmt_dur(d['eta'])}")
            self.item_stats.setText("  ·  ".join(parts))
        elif st == "finished":
            self.item_bar.setRange(0, 1000)
            self.item_bar.setValue(1000)
            self.item_stats.setText("Processing (merge / convert)…")

    def _job_failed(self, err: str) -> None:
        self._message("Something went wrong", err + "\n\nSee the Activity log for details.", "alert", BAD)
        self.toast.emit("Job failed", BAD)

    # ------------------------------------------------------------------ results
    def _platform_done(self, rep: dict) -> None:
        self.last_result = rep
        n = rep["downloaded_files"]
        failed = [d for d in rep["downloads"] if d["status"] not in ("downloaded",)]
        cancelled = any(d["status"] == "cancelled" for d in rep["downloads"])
        sev = rep["highest_severity"]
        if n:
            self.rp_badge.set("check", OK if sev not in ("high", "critical") else BAD)
            self.rp_title.setText(f"Saved {n} file{'s' if n != 1 else ''}" + (" (cancelled early)" if cancelled else ""))
        else:
            self.rp_badge.set("x" if not cancelled else "stop", BAD if not cancelled else GOLD)
            self.rp_title.setText("Cancelled" if cancelled else "Nothing was downloaded")
        msg = rep["destination"]
        if failed and n:
            msg += f"\n{len(failed)} item(s) could not be downloaded."
        if sev in ("high", "critical"):
            msg += f"\n⚠ Security scan found {sev.upper()} findings. Review before opening the files."
        self.rp_sub.setText(msg)
        self.rp_files.clear()
        for d in rep["downloads"]:
            media = {m["file"]: m for m in d.get("media", [])}
            if not d.get("files"):
                it = QTreeWidgetItem([d["candidate"].get("label") or d["candidate"].get("url"), d["status"], "",
                                      d.get("error", "")[:120]])
                it.setIcon(0, icons.icon("x", BAD, 16))
                it.setToolTip(3, d.get("error", ""))
                self.rp_files.addTopLevelItem(it)
                continue
            for f in d["files"]:
                m = media.get(f, {})
                worst = min((x["severity"] for x in m.get("findings", [])), key=SEV_ORDER.index, default="info")
                serious = [x for x in m.get("findings", []) if x["severity"] in ("critical", "high", "medium")]
                p = Path(f)
                size = p.stat().st_size if p.exists() else 0
                sec = "Clean" if not serious else f"{worst.upper()} · {len(serious)} finding(s)"
                it = QTreeWidgetItem([p.name, "saved", fmt_bytes(size), sec])
                it.setIcon(0, icons.icon("music" if p.suffix.lower() in (".mp3", ".m4a", ".opus", ".flac", ".wav", ".ogg")
                                         else "play", MUTED, 16))
                it.setIcon(3, icons.dot(SEV[worst] if serious else OK))
                it.setToolTip(3, "\n".join(f"[{x['severity']}] {x['message']}" for x in m.get("findings", [])) or "No findings")
                it.setData(0, Qt.UserRole, f)
                self.rp_files.addTopLevelItem(it)
        history.add({"time": time.time(), "url": rep["target"], "kind": f"{rep['platform']} {rep['kind']}",
                     "title": (self.ins.title or self.ins.playlist_title) if self.ins else "", "files": n,
                     "severity": sev, "verdict": "", "score": None, "folder": rep["destination"], "report": ""})
        self.show_state("result_platform")
        self.toast.emit(self.rp_title.text(), OK if n else GOLD)
        QApplication.alert(self.window())

    def _site_done(self, rep: dict) -> None:
        self.last_result = rep
        s = rep["summary"]
        p = PALETTES[self.mode()]
        vcolor = VERDICT.get(s["piracy_verdict"], MUTED)
        self.gauge.set_score(s["piracy_score"], vcolor, p["border"], p["text"])
        self.rs_verdict.setText(s["piracy_verdict"].title())
        self.rs_verdict.setStyleSheet(f"color: {vcolor};")
        self.rs_host.setText(rep["target"])
        reasons = rep["piracy"]["reasons"] or ["No piracy indicators found."]
        self.rs_reasons.setText("<br>".join(f"• {r}" for r in reasons[:10]))
        self.t_pages.set(s["pages_analyzed"])
        self.t_cands.set(s["candidates"])
        self.t_files.set(s["downloaded_files"])
        self.t_drm.set(s["drm_protected"])
        self.t_sev.set(s["highest_severity"].upper(), SEV.get(s["highest_severity"]))
        self.t_find.set(s["security_findings"])
        # threats
        self.th_tree.clear()
        findings = [(f, "site") for f in rep["site"]["findings"]]
        for d in rep["downloads"]:
            for m in d.get("media", []):
                findings += [(f, Path(m["file"]).name) for f in m["findings"]]
        findings.sort(key=lambda x: SEV_ORDER.index(x[0]["severity"]) if x[0]["severity"] in SEV_ORDER else 9)
        for f, where in findings:
            ev = (f.get("evidence") or "").strip()
            it = QTreeWidgetItem([f["severity"].upper(), f["category"] + ("" if where == "site" else f" · {where}"),
                                  f["message"], ev.splitlines()[0][:140] if ev else ""])
            it.setIcon(0, icons.dot(SEV.get(f["severity"], MUTED)))
            from PySide6.QtGui import QBrush, QColor

            it.setForeground(0, QBrush(QColor(SEV.get(f["severity"], MUTED))))
            bold = it.font(0)
            bold.setBold(True)
            it.setFont(0, bold)
            it.setToolTip(2, f["message"])
            it.setToolTip(3, ev[:2000])
            it.setData(0, Qt.UserRole, f["severity"])
            self.th_tree.addTopLevelItem(it)
        self._apply_sev_filter()
        # videos
        self.vid_tree.clear()
        for d in rep["downloads"]:
            c = d["candidate"]
            info = d.get("info", {})
            q = " ".join(x for x in (f"{info['height']}p" if info.get("height") else "", info.get("vcodec") or "") if x)
            media = d.get("media", [])
            allf = [x for m in media for x in m["findings"]]
            worst = min((x["severity"] for x in allf), key=SEV_ORDER.index, default="info")
            serious = [x for x in allf if x["severity"] in ("critical", "high", "medium")]
            name = (c.get("label") + " · " if c.get("label") else "") + (info.get("title") or c["url"])
            it = QTreeWidgetItem([name, d["status"], q, "—" if not media else ("Clean" if not serious else worst.upper()),
                                  ", ".join(Path(f).name for f in d.get("files", []))])
            it.setIcon(0, icons.icon("play", MUTED, 16))
            if media:
                it.setIcon(3, icons.dot(SEV[worst] if serious else OK))
            pr = d.get("piracy")
            tip = c["url"] + (f"\nPiracy: {pr['verdict']} ({pr['score']})\n" + "\n".join(pr["reasons"]) if pr else "")
            it.setToolTip(0, tip)
            if d.get("files"):
                it.setData(0, Qt.UserRole, d["files"][0])
            self.vid_tree.addTopLevelItem(it)
        self.cand_tree.clear()
        for c in rep["candidates"]:
            it = QTreeWidgetItem([c["kind"], c["source"], c["label"], c["url"]])
            it.setToolTip(3, c["url"])
            self.cand_tree.addTopLevelItem(it)
        self.res_log.setPlainText(self.log_view.toPlainText())
        self.tabs.setCurrentIndex(0)
        history.add({"time": time.time(), "url": rep["target"], "kind": "web site", "title": rep["piracy"].get("title", ""),
                     "files": s["downloaded_files"], "severity": s["highest_severity"], "verdict": s["piracy_verdict"],
                     "score": s["piracy_score"], "folder": rep["run_dir"],
                     "report": str(Path(rep["run_dir"]) / "report.html")})
        self.show_state("result_site")
        self.toast.emit(f"Analysis complete: {s['piracy_verdict'].title()}", vcolor)
        QApplication.alert(self.window())

    def _apply_sev_filter(self) -> None:
        sel = self.sev_filter.currentText().lower()
        for i in range(self.th_tree.topLevelItemCount()):
            it = self.th_tree.topLevelItem(i)
            it.setHidden(sel != "all severities" and it.data(0, Qt.UserRole) != sel)

    def _open_item_file(self, item: QTreeWidgetItem, _col: int) -> None:
        f = item.data(0, Qt.UserRole)
        if f:
            open_path(Path(f).parent)

    def reset(self) -> None:
        self.url.clear()
        self.url.setFocus()
        self.show_state("empty")

    def refresh_theme(self) -> None:
        rep = self.last_result
        if rep and "summary" in rep:
            p = PALETTES[self.mode()]
            s = rep["summary"]
            self.gauge.set_score(s["piracy_score"], VERDICT.get(s["piracy_verdict"], MUTED), p["border"], p["text"])
        if self.ins and self.stack.currentWidget() is self.pages["preview"] and not self.ins.thumbnail:
            self.thumb.setPixmap(placeholder_pixmap(256, 144, "music" if self.ins.target.platform == "spotify" else "play",
                                                    self.mode() == "dark"))
