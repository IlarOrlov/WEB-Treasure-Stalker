"""Reusable custom widgets."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Property, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import icons
from .theme import ACCENT, ACCENT2, SEV


def label(text: str = "", obj: str = "", wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


class Card(QFrame):
    def __init__(self, obj: str = "card", shadow: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName(obj)
        if shadow:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(32)
            eff.setOffset(0, 8)
            eff.setColor(QColor(0, 0, 0, 70))
            self.setGraphicsEffect(eff)


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    return f


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


class IconLabel(QLabel):
    def __init__(self, name: str, color: str, size: int = 20):
        super().__init__()
        self.setPixmap(icons.pixmap(name, color, size))
        self.setFixedSize(size, size)


class IconBadge(QWidget):
    """Rounded square with a gradient tint and an icon."""

    def __init__(self, name: str, color: str = ACCENT, size: int = 40):
        super().__init__()
        self._name, self._color, self._size = name, color, size
        self.setFixedSize(size, size)

    def set(self, name: str, color: str) -> None:
        self._name, self._color = name, color
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(40)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, self._size, self._size), self._size * 0.3, self._size * 0.3)
        s = int(self._size * 0.5)
        p.drawPixmap((self._size - s) // 2, (self._size - s) // 2, icons.pixmap(self._name, self._color, s))


class Chip(QLabel):
    def __init__(self, text: str = "", color: str = "#94A3B8"):
        super().__init__()
        self.setObjectName("chip")
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.set(text, color)

    def set(self, text: str, color: str) -> None:
        self.setText(text)
        c = QColor(color)
        self.setStyleSheet(f"QLabel#chip {{ color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},0.15);"
                           f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.35); }}")


def sev_chip(sev: str) -> Chip:
    return Chip(sev.upper(), SEV.get(sev, "#94A3B8"))


class Spinner(QWidget):
    def __init__(self, size: int = 36, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start(16)
        self.show()

    def stop(self):
        self._timer.stop()

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        pen = QPen(QColor(ACCENT), max(3, w // 10))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        m = pen.width()
        rect = QRectF(m, m, w - 2 * m, w - 2 * m)
        p.drawArc(rect, -self._angle * 16, 100 * 16)
        pen.setColor(QColor(ACCENT2))
        p.setPen(pen)
        p.drawArc(rect, (-self._angle + 180) * 16, 60 * 16)


class ScoreGauge(QWidget):
    """Animated circular score 0-100."""

    def __init__(self, size: int = 150, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._value = 0.0
        self._target = 0
        self._color = QColor("#10B981")
        self._track = QColor("#232C3E")
        self._text = QColor("#E7EBF3")
        self._caption = "piracy score"
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def getValue(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = v
        self.update()

    value = Property(float, getValue, setValue)

    def set_score(self, score: int, color: str, track: str, text: str, caption: str = "piracy score") -> None:
        self._color, self._track, self._text, self._caption = QColor(color), QColor(track), QColor(text), caption
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(float(score))
        self._anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        thick = w * 0.085
        rect = QRectF(thick, thick, w - 2 * thick, w - 2 * thick)
        pen = QPen(self._track, thick)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 225 * 16, -270 * 16)
        pen.setColor(self._color)
        p.setPen(pen)
        p.drawArc(rect, 225 * 16, int(-270 * 16 * self._value / 100))
        p.setPen(self._text)
        f = QFont(self.font())
        f.setPixelSize(int(w * 0.27))
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.drawText(QRectF(0, -w * 0.04, w, w), Qt.AlignCenter, str(int(round(self._value))))
        f.setPixelSize(max(10, int(w * 0.075)))
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        c = QColor(self._text)
        c.setAlpha(150)
        p.setPen(c)
        p.drawText(QRectF(0, w * 0.6, w, w * 0.2), Qt.AlignHCenter | Qt.AlignTop, self._caption)


class StatTile(Card):
    def __init__(self, title: str, icon_name: str, color: str = ACCENT):
        super().__init__("card2")
        self.badge = IconBadge(icon_name, color, 34)
        self.num = label("0", "h2")
        self.cap = label(title, "faint")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        lay.addWidget(self.badge)
        lay.addLayout(vbox(self.num, self.cap, spacing=0))
        lay.addStretch(1)

    def set(self, value, color: str | None = None) -> None:
        self.num.setText(str(value))
        if color:
            self.num.setStyleSheet(f"color: {color};")


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("segbox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        if options:
            self.buttons[options[0][0]].setChecked(True)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def value(self) -> str:
        return next(k for k, b in self.buttons.items() if b.isChecked())

    def set_value(self, key: str) -> None:
        self.buttons[key].setChecked(True)


class Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self.hide()
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, color: str | None = None, ms: int = 3200) -> None:
        self.setText(text)
        self.setStyleSheet(f"border-left: 4px solid {color};" if color else "")
        self.adjustSize()
        par = self.parentWidget()
        self.move(par.width() - self.width() - 28, par.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self):
        self._anim.stop()
        self._anim.setDuration(400)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.hide)
        self._anim.start()


def rounded_pixmap(pm: QPixmap, w: int, h: int, radius: int = 12) -> QPixmap:
    scaled = pm.scaled(w * 2, h * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(w * 2, h * 2)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w * 2, h * 2), radius * 2, radius * 2)
    p.setClipPath(path)
    p.drawPixmap(QPointF((w * 2 - scaled.width()) / 2, (h * 2 - scaled.height()) / 2), scaled)
    p.end()
    out.setDevicePixelRatio(2)
    return out


def placeholder_pixmap(w: int, h: int, icon_name: str, dark: bool) -> QPixmap:
    from PySide6.QtGui import QLinearGradient

    out = QPixmap(w * 2, h * 2)
    out.setDevicePixelRatio(2)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), 12, 12)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(ACCENT).darker(160 if dark else 105))
    g.setColorAt(1, QColor(ACCENT2).darker(180 if dark else 110))
    p.fillPath(path, g)
    s = int(min(w, h) * 0.38)
    p.drawPixmap((w - s) // 2, (h - s) // 2, icons.pixmap(icon_name, "#ffffff", s))
    p.end()
    return out
