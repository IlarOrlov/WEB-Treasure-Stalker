"""Inline stroke icons (24x24 grid) rendered through QtSvg in any colour."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

P = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "flag": '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "tools": '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "file": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
    "report": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M8 13h8M8 17h5"/>',
    "play": '<rect x="3" y="5" width="18" height="14" rx="4"/><path d="M10 9l5 3-5 3z"/>',
    "music": '<circle cx="7" cy="17" r="3"/><circle cx="17" cy="15" r="3"/><path d="M10 17V5l10-2v12"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
    "check": '<path d="M5 12.5l4.2 4.2L19 7"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "alert": '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.7 3.7 5.7 3.7 9s-1.2 6.3-3.7 9c-2.5-2.7-3.7-5.7-3.7-9S9.5 5.7 12 3z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "clipboard": '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.5v.01"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "gem": '<path d="M6 3h12l3 6-9 12L3 9z"/><path d="M3 9h18M9 3l3 6 3-6M12 21L9 9M12 21l3-12"/>',
    "radar": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/>',
    "bug": '<rect x="7" y="8" width="10" height="12" rx="5"/><path d="M12 8V5M9 5l1.5 2M15 5l-1.5 2M3 13h4M17 13h4M4 19l3-2M20 19l-3-2M4 7l3 2M20 7l-3 2"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
}


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#E7EBF3", size: int = 20, stroke: float = 1.9) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{P[name]}</svg>')
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(svg.encode()))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str = "#E7EBF3", size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))


@lru_cache(maxsize=64)
def dot(color: str, size: int = 10) -> QIcon:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size * ratio, size * ratio)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)
