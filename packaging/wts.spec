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
    # Optional native accelerators that ship without a code signature (Windows Smart App Control blocks
    # unsigned modules from unknown publishers). Each has a pure-Python fallback:
    #   yt-dlp -> its own AES and no brotli; websockets -> pure-Python frame masking.
    "Cryptodome", "Crypto", "brotli", "_brotli", "brotlicffi", "websockets.speedups",
    "greenlet.tests", "greenlet.tests._test_extension", "greenlet.tests._test_extension_cpp",
]

a = Analysis(
    [str(ROOT / "wts_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)

if sys.platform == "win32":
    # Report every native module still bundled without an Authenticode signature (see packaging/SIGNING.md).
    import struct

    def _signed(path):
        try:
            data = Path(path).read_bytes()
            pe = struct.unpack_from("<I", data, 0x3C)[0]
            opt = pe + 24
            dd = opt + (96 if struct.unpack_from("<H", data, opt)[0] == 0x10B else 112)
            off, size = struct.unpack_from("<II", data, dd + 8 * 4)
            return bool(off and size)
        except Exception:  # noqa: BLE001
            return True  # not a PE file

    unsigned = sorted(dest for dest, src, kind in a.binaries if not _signed(src))
    print(f"[signing] {len(unsigned)} unsigned native module(s) bundled:")
    for u in unsigned:
        print(f"[signing]   {u}")

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
