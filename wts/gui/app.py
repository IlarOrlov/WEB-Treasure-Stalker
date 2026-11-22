"""Application entry point and main window."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, QSize, Qt, QTimer

from ..prefs import Settings
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..tools import setup_environment
from . import icons
from .grab_page import GrabPage
from .pages import AboutPage, HistoryPage, ToolsPage
from .theme import ASSETS, FONT_FAMILY, GOLD, qss
from .widgets import Toast, label

MUTED = "#8B95AA"


def make_palette(mode: str):
    from PySide6.QtGui import QColor, QPalette

    from .theme import ACCENT, PALETTES

    t = PALETTES[mode]
    pal = QPalette()
    roles = {QPalette.Window: t["bg"], QPalette.WindowText: t["text"], QPalette.Base: t["input"],
             QPalette.AlternateBase: t["card2"], QPalette.Text: t["text"], QPalette.Button: t["card2"],
             QPalette.ButtonText: t["text"], QPalette.Highlight: ACCENT, QPalette.HighlightedText: "#ffffff",
             QPalette.ToolTipBase: t["card2"], QPalette.ToolTipText: t["text"], QPalette.PlaceholderText: t["faint"],
             QPalette.Link: ACCENT, QPalette.Mid: t["border"], QPalette.Dark: t["border2"], QPalette.Light: t["card"]}
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor(t["faint"]))
    return pal


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.settings = Settings()  # memory-only unless the user turns on "remember"
        self.theme = self.settings.value("theme", "dark")
        self.setWindowTitle("WEB-Treasure-Stalker")
        self.setWindowIcon(QIcon(str(ASSETS / "icon.svg")))
        self.resize(1220, 820)
        self.setMinimumSize(980, 680)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(228)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 22, 16, 16)
        sl.setSpacing(4)
        from PySide6.QtWidgets import QLabel

        logo = QLabel()
        logo.setPixmap(QIcon(str(ASSETS / "icon.svg")).pixmap(38, 38))
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(label("Treasure Stalker", "brand"))
        brand.addWidget(label("web video forensics", "brandSub"))
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(logo)
        top.addLayout(brand)
        top.addStretch(1)
        sl.addLayout(top)
        sl.addSpacing(26)

        self.stack = QStackedWidget()
        self.grab_page = GrabPage(self.settings, lambda: self.theme)
        self.history = HistoryPage()
        self.tools = ToolsPage()
        self.about = AboutPage()
        self.nav = QButtonGroup(self)
        self.nav_buttons: list[QPushButton] = []
        for i, (ic, text, page) in enumerate((("radar", "Grab && analyse", self.grab_page), ("report", "History", self.history),
                                              ("tools", "Tools", self.tools), ("info", "About", self.about))):
            self.stack.addWidget(page)
            b = QPushButton(icons.icon(ic, MUTED, 18), f"   {text}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(18, 18))
            b.clicked.connect(lambda _=False, idx=i: self.go(idx))
            self.nav.addButton(b)
            self.nav_buttons.append(b)
            sl.addWidget(b)
        self.nav_buttons[0].setChecked(True)
        sl.addStretch(1)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("nav")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self.toggle_theme)
        sl.addWidget(self.theme_btn)
        sl.addWidget(label(f"v{__version__}", "faint"))
        h.addWidget(side)
        h.addWidget(self.stack, 1)

        self.toast = Toast(root)
        self.grab_page.toast.connect(self.toast.show_message)
        self.tools.toast.connect(self.toast.show_message)
        self.grab_page.request_tools.connect(lambda: self.go(2))
        self.tools.tools_changed.connect(self.update_banner)
        self.apply_theme()
        QTimer.singleShot(200, self.update_banner)

    def go(self, idx: int) -> None:
        self.nav_buttons[idx].setChecked(True)
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self.history.refresh()

    def apply_theme(self) -> None:
        self.app.setPalette(make_palette(self.theme))
        self.app.setStyleSheet(qss(self.theme))
        dark = self.theme == "dark"
        self.theme_btn.setIcon(icons.icon("sun" if dark else "moon", MUTED, 18))
        self.theme_btn.setText("   Light mode" if dark else "   Dark mode")
        self.grab_page.refresh_theme()

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self.settings.setValue("theme", self.theme)
        self.apply_theme()

    def update_banner(self) -> None:
        if self.tools.missing("ffmpeg"):
            self.grab_page.set_banner("FFmpeg is not installed. It is needed to merge the best video + audio and to "
                                 "convert formats. Install it in one click from Tools.")
        else:
            self.grab_page.set_banner(None)

    def closeEvent(self, e) -> None:
        if self.grab_page.is_busy():
            if QMessageBox.question(self, "Job running", "A download/analysis is still running. Cancel it and quit?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.grab_page.cancel()
            self.grab_page.job.wait(8000)
        e.accept()


def main() -> int:
    setup_environment()
    # Qt itself stores small things (e.g. file-dialog state) via QSettings. Keep that out of the registry /
    # home folder: redirect it into the session temp folder, which is deleted on exit.
    from ..tools import session_temp

    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(session_temp() / "qt"))
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IlarOrlov.WEBTreasureStalker")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("WEB-Treasure-Stalker")
    app.setOrganizationName("WEB-Treasure-Stalker")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    for f in sorted((ASSETS / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    font = QFont(FONT_FAMILY)
    font.setPixelSize(13)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)
    app.setWindowIcon(QIcon(str(ASSETS / "icon.svg")))
    win = MainWindow(app)
    win.show()
    code = app.exec()
    from ..tools import cleanup_session_temp

    cleanup_session_temp()
    return code


if __name__ == "__main__":
    sys.exit(main())
