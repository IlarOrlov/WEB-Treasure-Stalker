# PyInstaller spec: single-file, windowed executable for Linux and Windows.
#   pyinstaller packaging/wts.spec --noconfirm
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
NAME = "WEB-Treasure-Stalker"

datas = [
    (str(ROOT / "wts" / "rules"), "wts/rules"),
    (str(ROOT / "wts" / "gui" / "assets"), "wts/gui/assets"),
]
datas += collect_data_files("yt_dlp_ejs")
datas += collect_data_files("certifi")

hiddenimports = collect_submodules("yt_dlp_ejs") + ["yt_dlp.compat._legacy", "yt_dlp.compat._deprecated"]

excludes = [
    "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest",
    "matplotlib", "numpy", "PIL", "pytest",
]

a = Analysis(
    [str(ROOT / "wts_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(ROOT / "wts" / "gui" / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.png"))

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    console=False,
    icon=icon,
    upx=False,
    strip=False,
    debug=False,
    runtime_tmpdir=None,
    version=str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None,
)
