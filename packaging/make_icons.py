"""Render wts/gui/assets/icon.svg to icon.png and a multi-size icon.ico (needs PySide6 + Pillow)."""
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "wts" / "gui" / "assets"


def render(size: int) -> Image.Image:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray((ASSETS / "icon.svg").read_bytes())).render(p, QRectF(0, 0, size, size))
    p.end()
    tmp = ASSETS / f".tmp{size}.png"
    img.save(str(tmp))
    out = Image.open(tmp).copy()
    tmp.unlink()
    return out


if __name__ == "__main__":
    app = QGuiApplication([])
    big = render(512)
    big.save(ASSETS / "icon.png")
    render(256).save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icons written to", ASSETS)
