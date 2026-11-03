"""Material Design Icons as inline SVG strings.

Usage:
    from core.icons import make_icon, make_pixmap, get_check_svg_path
    btn.setIcon(make_icon("folder-open", 16, "#656d76"))
"""
import os
import tempfile
import hashlib
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer

# Write checkmark SVGs to temp on first use
_CHECK_SVG_PATH: str = ""
_UNCHECK_SVG_PATH: str = ""
_PARTIAL_SVG_PATH: str = ""
_CHECKBOX_STYLE_KEY: tuple[str, str, str] | None = None


def _ensure_checkbox_svgs(accent: str = "#0969da",
                          box_fill: str = "#21262d",
                          box_border: str = "#8b949e") -> tuple[str, str, str]:
    """Write checkbox state SVGs to temp dir and return their paths."""
    global _CHECK_SVG_PATH, _UNCHECK_SVG_PATH, _PARTIAL_SVG_PATH, _CHECKBOX_STYLE_KEY
    style_key = (accent, box_fill, box_border)
    if (_CHECKBOX_STYLE_KEY == style_key and _CHECK_SVG_PATH and
            os.path.exists(_CHECK_SVG_PATH)):
        return _CHECK_SVG_PATH, _UNCHECK_SVG_PATH, _PARTIAL_SVG_PATH

    tmp = tempfile.gettempdir()
    checked_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<rect x="0.75" y="0.75" width="14.5" height="14.5" rx="3" '
        f'fill="{accent}" stroke="{accent}" stroke-width="1.5"/>'
        f'<polyline points="3.5,8.5 6.5,11.5 12.5,5" '
        f'fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )
    unchecked_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<rect x="0.75" y="0.75" width="14.5" height="14.5" rx="3" '
        f'fill="{box_fill}" stroke="{box_border}" stroke-width="1.5"/>'
        f'</svg>'
    )
    partial_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        f'<rect x="0.75" y="0.75" width="14.5" height="14.5" rx="3" '
        f'fill="{accent}" stroke="{accent}" stroke-width="1.5"/>'
        f'<line x1="4" y1="8" x2="12" y2="8" '
        f'stroke="white" stroke-width="2" stroke-linecap="round"/>'
        f'</svg>'
    )

    style_id = hashlib.sha1("|".join(style_key).encode("ascii")).hexdigest()[:10]
    _CHECK_SVG_PATH = os.path.join(tmp, f"mpp_check_{style_id}.svg")
    _UNCHECK_SVG_PATH = os.path.join(tmp, f"mpp_uncheck_{style_id}.svg")
    _PARTIAL_SVG_PATH = os.path.join(tmp, f"mpp_partial_{style_id}.svg")
    with open(_CHECK_SVG_PATH, "w") as f:
        f.write(checked_svg)
    with open(_UNCHECK_SVG_PATH, "w") as f:
        f.write(unchecked_svg)
    with open(_PARTIAL_SVG_PATH, "w") as f:
        f.write(partial_svg)
    _CHECKBOX_STYLE_KEY = style_key
    return _CHECK_SVG_PATH, _UNCHECK_SVG_PATH, _PARTIAL_SVG_PATH


def checkbox_qss(accent: str = "#0969da",
                 box_fill: str = "#21262d",
                 box_border: str = "#8b949e") -> str:
    """Return QSS snippet for a pixel-perfect checkbox using temp SVG files."""
    checked, unchecked, partial = _ensure_checkbox_svgs(accent, box_fill, box_border)
    c = checked.replace("\\", "/")
    u = unchecked.replace("\\", "/")
    p = partial.replace("\\", "/")

    # Radio button SVGs
    radio_on = os.path.join(tempfile.gettempdir(), "mpp_radio_on.svg")
    radio_off = os.path.join(tempfile.gettempdir(), "mpp_radio_off.svg")
    with open(radio_on, "w") as f:
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">'
            f'<circle cx="9" cy="9" r="8" fill="{box_fill}" stroke="{accent}" stroke-width="2"/>'
            f'<circle cx="9" cy="9" r="4.5" fill="{accent}"/>'
            f'</svg>'
        )
    with open(radio_off, "w") as f:
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18">'
            f'<circle cx="9" cy="9" r="8" fill="{box_fill}" stroke="{box_border}" stroke-width="1.5"/>'
            f'</svg>'
        )
    ron = radio_on.replace("\\", "/")
    roff = radio_off.replace("\\", "/")

    # Tree expand/collapse chevrons (Qt stops drawing native arrows once
    # QTreeWidget::branch is styled, so we must supply our own).
    chev_closed = os.path.join(tempfile.gettempdir(), "mpp_chev_closed.svg")
    chev_open = os.path.join(tempfile.gettempdir(), "mpp_chev_open.svg")
    with open(chev_closed, "w") as f:
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<polyline points="6,4 10,8 6,12" fill="none" '
            f'stroke="{box_border}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    with open(chev_open, "w") as f:
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            f'<polyline points="4,6 8,10 12,6" fill="none" '
            f'stroke="{accent}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    cc = chev_closed.replace("\\", "/")
    co = chev_open.replace("\\", "/")

    return f"""
QCheckBox {{ spacing: 8px; font-size: 13px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{ image: url({u}); }}
QCheckBox::indicator:checked {{ image: url({c}); }}
QTreeWidget::indicator {{ width: 16px; height: 16px; }}
QTreeWidget::indicator:unchecked {{ image: url({u}); }}
QTreeWidget::indicator:checked {{ image: url({c}); }}
QTreeWidget::indicator:indeterminate {{ image: url({p}); }}
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings {{ image: url({cc}); }}
QTreeWidget::branch:open:has-children:!has-siblings,
QTreeWidget::branch:open:has-children:has-siblings {{ image: url({co}); }}
QRadioButton {{ spacing: 8px; font-size: 13px; }}
QRadioButton::indicator {{ width: 18px; height: 18px; }}
QRadioButton::indicator:unchecked {{ image: url({roff}); }}
QRadioButton::indicator:unchecked:hover {{ image: url({roff}); }}
QRadioButton::indicator:checked {{ image: url({ron}); }}
"""

# ── MDI path data (viewBox 0 0 24 24) ────────────────────────────────
_PATHS: dict[str, str] = {
    # Disc plus eight detached rays with round caps. The previous path was a
    # radius-10 disc - nearly the whole 24px box - with the rays overlapping
    # its edge, which collapsed into a blob at the 16px button size.
    "weather-sunny": (
        "M12,7A5,5 0 1,1 12,17A5,5 0 1,1 12,7ZM13.05,4.8L13.05,1.6A1.05,1.05 0 0,0 10.95,1.6L10.95,4.8A1.05,1.05 0 0,0 13.05,4.8ZM17.83,7.65L20.1,5.39A1.05,1.05 0 0,0 18.61,3.9L16.35,6.17A1.05,1.05 0 0,0 17.83,7.65ZM19.2,13.05L22.4,13.05A1.05,1.05 0 0,0 22.4,10.95L19.2,10.95A1.05,1.05 0 0,0 19.2,13.05ZM16.35,17.83L18.61,20.1A1.05,1.05 0 0,0 20.1,18.61L17.83,16.35A1.05,1.05 0 0,0 16.35,17.83ZM10.95,19.2L10.95,22.4A1.05,1.05 0 0,0 13.05,22.4L13.05,19.2A1.05,1.05 0 0,0 10.95,19.2ZM6.17,16.35L3.9,18.61A1.05,1.05 0 0,0 5.39,20.1L7.65,17.83A1.05,1.05 0 0,0 6.17,16.35ZM4.8,10.95L1.6,10.95A1.05,1.05 0 0,0 1.6,13.05L4.8,13.05A1.05,1.05 0 0,0 4.8,10.95ZM7.65,6.17L5.39,3.9A1.05,1.05 0 0,0 3.9,5.39L6.17,7.65A1.05,1.05 0 0,0 7.65,6.17Z"
    ),
    # One sickle: the outer arc of a r9.5 disc meeting the inner arc of the
    # r8 disc that bites it, joined at the two intersection points. Drawn
    # this way it needs no fill rule, unlike the former crescent-plus-full-
    # circle pair that rendered as overlapping shapes.
    "weather-night": (
        "M20.52,16.21A9.5,9.5 0 1,1 12.55,2.52A8,8 0 0,0 20.52,16.21Z"
    ),
    "monitor": (
        "M4,3H20C21.1,3 22,3.9 22,5V15C22,16.1 21.1,17 20,17H14V19H17V21H7V19H10V17H4C2.9,17 2,16.1 2,15V5C2,3.9 2.9,3 4,3M4,5V15H20V5H4Z"
    ),
    "tshirt": (
        "M16,3L18.5,5.5L22,7V20H15V13H9V20H2V7L5.5,5.5L8,3C9,4.25 10.4,5 12,5C13.6,5 15,4.25 16,3Z"
    ),
    "face-man": (
        "M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,4C16.42,4 20,7.58 20,12C20,16.42 16.42,20 12,20C7.58,20 4,16.42 4,12C4,7.58 7.58,4 12,4M8,10.5A1.5,1.5 0 0,0 9.5,12A1.5,1.5 0 0,0 11,10.5A1.5,1.5 0 0,0 9.5,9A1.5,1.5 0 0,0 8,10.5M13,10.5A1.5,1.5 0 0,0 14.5,12A1.5,1.5 0 0,0 16,10.5A1.5,1.5 0 0,0 14.5,9A1.5,1.5 0 0,0 13,10.5M7.5,15C8.6,16.3 10.1,17 12,17C13.9,17 15.4,16.3 16.5,15C15.3,14.1 13.8,13.5 12,13.5C10.2,13.5 8.7,14.1 7.5,15Z"
    ),
    "archive": (
        "M4,3H20C21.1,3 22,3.9 22,5V7H2V5C2,3.9 2.9,3 4,3M2,9H22V19C22,20.1 21.1,21 20,21H4C2.9,21 2,20.1 2,19V9M9,12V14H15V12H9Z"
    ),
    "cog": (
        "M12,8A4,4 0 0,0 8,12A4,4 0 0,0 12,16A4,4 0 0,0 16,12A4,4 0 0,0 12,8M3.05,10.5L5.12,10.15C5.35,9.45 5.65,8.82 6.05,8.24L4.82,6.55L6.55,4.82L8.24,6.05C8.82,5.65 9.45,5.35 10.15,5.12L10.5,3.05H13.5L13.85,5.12C14.55,5.35 15.18,5.65 15.76,6.05L17.45,4.82L19.18,6.55L17.95,8.24C18.35,8.82 18.65,9.45 18.88,10.15L20.95,10.5V13.5L18.88,13.85C18.65,14.55 18.35,15.18 17.95,15.76L19.18,17.45L17.45,19.18L15.76,17.95C15.18,18.35 14.55,18.65 13.85,18.88L13.5,20.95H10.5L10.15,18.88C9.45,18.65 8.82,18.35 8.24,17.95L6.55,19.18L4.82,17.45L6.05,15.76C5.65,15.18 5.35,14.55 5.12,13.85L3.05,13.5V10.5Z"
    ),
    "arrow-left": "M20,11V13H7L12.5,18.5L11.08,19.92L3.16,12L11.08,4.08L12.5,5.5L7,11H20Z",
    "arrow-right": "M4,11V13H17L11.5,18.5L12.92,19.92L20.84,12L12.92,4.08L11.5,5.5L17,11H4Z",
    "folder-open": (
        "M19,20H4C2.89,20 2,19.1 2,18V6C2,4.89 2.89,4 4,4H10L12,6H19"
        "A2,2 0 0,1 21,8H21L19,20M19,8H4V18L6.14,10H21.07L19,8Z"
    ),
    "play": (
        "M8,5.14V19.14L19,12.14L8,5.14Z"
    ),
    "pause": (
        "M14,19H18V5H14M6,19H10V5H6V19Z"
    ),
    "check-circle": (
        "M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z"
    ),
    "close-circle": (
        "M12,2C17.53,2 22,6.47 22,12C22,17.53 17.53,22 12,22C6.47,22 2,17.53 2,12C2,6.47 6.47,2 12,2M15.59,7L12,10.59L8.41,7L7,8.41L10.59,12L7,15.59L8.41,17L12,13.41L15.59,17L17,15.59L13.41,12L17,8.41L15.59,7Z"
    ),
    "help-circle": (
        "M15.07,11.25L14.17,12.17C13.45,12.89 13,13.5 13,15H11V14.5C11,13.39 11.45,12.39 12.17,11.67L13.41,10.41C13.78,10.05 14,9.55 14,9C14,7.89 13.1,7 12,7A2,2 0 0,0 10,9H8A4,4 0 0,1 12,5A4,4 0 0,1 16,9C16,9.88 15.64,10.67 15.07,11.25M13,19H11V17H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12C22,6.47 17.5,2 12,2Z"
    ),
    "information-outline": (
        "M11,17H13V11H11V17M12,2C6.48,2 2,6.48 2,12S6.48,22 12,22 22,17.52 22,12 17.52,2 12,2M12,20C7.58,20 4,16.42 4,12S7.58,4 12,4 20,7.58 20,12 16.42,20 12,20M11,9H13V7H11V9Z"
    ),
    "checkbox-blank-outline": (
        "M19,3H5C3.89,3 3,3.89 3,5V19C3,20.1 3.9,21 5,21H19C20.1,21 21,20.1 21,19V5C21,3.9 20.1,3 19,3M19,19H5V5H19V19Z"
    ),
    "checkbox-marked": (
        "M19,3H5C3.89,3 3,3.89 3,5V19C3,20.1 3.9,21 5,21H19C20.1,21 21,20.1 21,19V5C21,3.9 20.1,3 19,3M10,17L5,12L6.41,10.59L10,14.17L17.59,6.58L19,8L10,17Z"
    ),
    "lightbulb-on-outline": (
        "M9,21C9,21.55 9.45,22 10,22H14C14.55,22 15,21.55 15,21V20H9V21M12,2C8.14,2 5,5.14 5,9C5,11.38 6.19,13.47 8,14.74V18H16V14.74C17.81,13.47 19,11.38 19,9C19,5.14 15.86,2 12,2M14.85,13.1L14,13.69V16H10V13.69L9.15,13.1C7.8,12.15 7,10.62 7,9C7,6.24 9.24,4 12,4S17,6.24 17,9C17,10.62 16.2,12.15 14.85,13.1Z"
    ),
    "trash-can": (
        "M9,3V4H4V6H5V19A2,2 0 0,0 7,21H17A2,2 0 0,0 19,19V6H20V4H15V3H9M9,8H11V17H9V8M13,8H15V17H13V8Z"
    ),
    "clock-outline": (
        "M12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22C6.47,22 2,17.5 2,12A10,10 0 0,1 12,2M12.5,7V12.25L17,14.92L16.25,16.15L11,13V7H12.5Z"
    ),
    "format-list": (
        "M9,5V9H21V5M9,19H21V15H9M9,14H21V10H9M4,9H8V5H4M4,19H8V15H4M4,14H8V10H4V14Z"
    ),
    "account-group": (
        "M12,5.5A3.5,3.5 0 0,1 15.5,9A3.5,3.5 0 0,1 12,12.5A3.5,3.5 0 0,1 8.5,9A3.5,3.5 0 0,1 12,5.5M5,8C5.56,8 6.08,8.15 6.53,8.42C6.38,9.85 6.8,11.27 7.66,12.38C7.16,13.34 6.16,14 5,14A3,3 0 0,1 2,11A3,3 0 0,1 5,8M19,8A3,3 0 0,1 22,11A3,3 0 0,1 19,14C17.84,14 16.84,13.34 16.34,12.38C17.2,11.27 17.62,9.85 17.47,8.42C17.92,8.15 18.44,8 19,8M5.5,18.25C5.5,16.18 8.41,14.5 12,14.5C15.59,14.5 18.5,16.18 18.5,18.25V20H5.5V18.25M0,20V18.5C0,17.11 1.89,15.94 4.45,15.6C3.86,16.28 3.5,17.22 3.5,18.25V20H0M24,20H20.5V18.25C20.5,17.22 20.14,16.28 19.55,15.6C22.11,15.94 24,17.11 24,18.5V20Z"
    ),
    "magnify": (
        "M9.5,3A6.5,6.5 0 0,1 16,9.5C16,11.11 15.41,12.59 14.44,13.73L14.71,14H15.5L20.5,19L19,20.5L14,15.5V14.71L13.73,14.44C12.59,15.41 11.11,16 9.5,16A6.5,6.5 0 0,1 3,9.5A6.5,6.5 0 0,1 9.5,3M9.5,5C7,5 5,7 5,9.5C5,12 7,14 9.5,14C12,14 14,12 14,9.5C14,7 12,5 9.5,5Z"
    ),
    "restore": (
        "M13,3A9,9 0 0,0 4,12H1L4.89,15.89L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,19.99 10.51,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3Z"
    ),
    "checkbox-outline": (
        "M19,3H5C3.89,3 3,3.89 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5C21,3.89 20.1,3 19,3M19,5V19H5V5H19M10,17L5,12L6.41,10.58L10,14.17L17.59,6.58L19,8L10,17Z"
    ),
}


def make_pixmap(name: str, size: int = 16, color: str = "#1f2328") -> QPixmap:
    path = _PATHS.get(name, _PATHS["help-circle"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" height="{size}">'
        f'<path fill="{color}" d="{path}"/>'
        f'</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def make_icon(name: str, size: int = 16, color: str = "#1f2328") -> QIcon:
    return QIcon(make_pixmap(name, size, color))
