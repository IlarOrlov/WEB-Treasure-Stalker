"""Colour tokens and the Qt style sheet."""
from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).parent / "assets"

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0B0E14", "side": "#0E121A", "card": "#131824", "card2": "#1A2131", "hover": "#202940",
        "border": "#232C3E", "border2": "#2E3950", "text": "#E7EBF3", "muted": "#8B95AA", "faint": "#5E6880",
        "input": "#0F141F", "sel": "#2A2560", "shadow": "#000000",
    },
    "light": {
        "bg": "#F4F6FB", "side": "#FFFFFF", "card": "#FFFFFF", "card2": "#F1F4FA", "hover": "#E9EDF7",
        "border": "#E2E7F0", "border2": "#D3DAE6", "text": "#0F172A", "muted": "#5B6780", "faint": "#94A0B8",
        "input": "#F8FAFD", "sel": "#E4E0FF", "shadow": "#8090B0",
    },
}
ACCENT = "#7C5CFF"
ACCENT2 = "#22D3EE"
GOLD = "#F5B83D"
SEV = {"critical": "#F43F5E", "high": "#F97316", "medium": "#F59E0B", "low": "#38BDF8", "info": "#94A3B8"}
VERDICT = {"LIKELY PIRATED": "#F43F5E", "SUSPICIOUS": "#F59E0B", "NO STRONG INDICATORS": "#10B981"}
OK = "#10B981"
BAD = "#F43F5E"

FONT_FAMILY = "Inter"


def qss(mode: str) -> str:
    p = PALETTES[mode]
    grad = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT2})"
    grad_h = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8F74FF, stop:1 #4BE0F5)"
    return f"""
* {{ font-family: "{FONT_FAMILY}"; font-size: 13px; color: {p['text']}; outline: 0; }}
QMainWindow, #root {{ background: {p['bg']}; }}
QWidget#page {{ background: {p['bg']}; }}
QToolTip {{ background: {p['card2']}; color: {p['text']}; border: 1px solid {p['border2']}; padding: 6px 8px; border-radius: 6px; }}

/* sidebar */
#sidebar {{ background: {p['side']}; border-right: 1px solid {p['border']}; }}
#brand {{ font-size: 15px; font-weight: 700; letter-spacing: 0.2px; }}
#brandSub {{ color: {p['faint']}; font-size: 11px; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 10px; color: {p['muted']};
    font-size: 13px; font-weight: 500; background: transparent; }}
QPushButton#nav:hover {{ background: {p['hover']}; color: {p['text']}; }}
QPushButton#nav:checked {{ background: {p['sel']}; color: {p['text']}; font-weight: 600; }}

/* typography */
#h1 {{ font-size: 26px; font-weight: 700; letter-spacing: -0.4px; }}
#h2 {{ font-size: 18px; font-weight: 700; letter-spacing: -0.2px; }}
#h3 {{ font-size: 14px; font-weight: 600; }}
#muted {{ color: {p['muted']}; }}
#faint {{ color: {p['faint']}; font-size: 12px; }}
#mono {{ font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 12px; }}

/* cards */
QFrame#card {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 16px; }}
QFrame#card2 {{ background: {p['card2']}; border: 1px solid {p['border']}; border-radius: 12px; }}
QFrame#banner {{ background: {p['card2']}; border: 1px solid {p['border2']}; border-radius: 12px; }}
QFrame#divider {{ background: {p['border']}; max-height: 1px; min-height: 1px; border: none; }}

/* inputs */
QLineEdit, QSpinBox, QComboBox {{ background: {p['input']}; border: 1px solid {p['border2']}; border-radius: 10px;
    padding: 8px 12px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#url {{ font-size: 15px; padding: 13px 16px; border-radius: 14px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p['faint']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {p['card2']}; border: 1px solid {p['border2']}; border-radius: 8px;
    padding: 4px; selection-background-color: {p['sel']}; selection-color: {p['text']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}

/* buttons */
QPushButton {{ background: {p['card2']}; border: 1px solid {p['border2']}; border-radius: 10px; padding: 8px 16px;
    font-weight: 600; }}
QPushButton:hover {{ background: {p['hover']}; }}
QPushButton:pressed {{ background: {p['border']}; }}
QPushButton:disabled {{ color: {p['faint']}; background: {p['card2']}; }}
QPushButton#primary {{ background: {grad}; border: none; color: white; padding: 10px 22px; border-radius: 12px; font-size: 14px; }}
QPushButton#primary:hover {{ background: {grad_h}; }}
QPushButton#primary:disabled {{ background: {p['border2']}; color: {p['faint']}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {BAD}; color: {BAD}; }}
QPushButton#danger:hover {{ background: rgba(244,63,94,0.12); }}
QPushButton#ghost {{ background: transparent; border: none; color: {p['muted']}; padding: 6px 10px; }}
QPushButton#ghost:hover {{ color: {p['text']}; background: {p['hover']}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 8px; padding: 7px 14px; color: {p['muted']}; }}
QPushButton#seg:checked {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border2']}; }}
QFrame#segbox {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 10px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {p['border2']}; background: {p['input']}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; image: url("{(ASSETS / 'check.svg').as_posix()}"); }}

/* progress */
QProgressBar {{ background: {p['input']}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {grad}; border-radius: 5px; }}

/* lists / tables */
QTreeWidget, QTableWidget, QListWidget {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 12px;
    alternate-background-color: {p['card2']}; padding: 4px; }}
QTreeWidget::item, QListWidget::item {{ padding: 6px 4px; border: none; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {p['sel']}; color: {p['text']}; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {p['hover']}; }}
QHeaderView::section {{ background: {p['card']}; color: {p['muted']}; border: none; border-bottom: 1px solid {p['border']};
    padding: 8px 6px; font-weight: 600; font-size: 12px; }}
QTreeWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p['border2']}; background: {p['input']}; }}
QTreeWidget::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; image: url("{(ASSETS / 'check.svg').as_posix()}"); }}
QPlainTextEdit {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 12px; padding: 8px;
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 12px; color: {p['muted']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 9px 16px; margin-right: 4px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {p['text']}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['border2']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['border2']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0; background: none; }}

QLabel#chip {{ border-radius: 9px; padding: 3px 9px; font-size: 11px; font-weight: 700; }}
QLabel#toast {{ background: {p['card2']}; border: 1px solid {p['border2']}; border-radius: 12px; padding: 12px 18px; font-weight: 600; }}
QMessageBox {{ background: {p['card']}; }}
"""
