import os
import ntpath
import re
import shutil
import subprocess
import sys
import tempfile
import html
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QEvent, QTimer, QSize, QRect, QRectF, QPoint, QPointF, QThread, Signal

# Count terminal events once per unique outfit. REBASE/CONFORM/CLIP lines are
# informational and must not advance progress.
# SKIPPED and FAILED are spelled in full by the headparts backend, and
# library mode skips routinely — six of fifty shapes on one measured mod.
# Without them the bar stalled and the summary claimed nothing was skipped.
_PROGRESS_RE = re.compile(
    r'^\s*\[(DONE|SKIP|SKIPPED|PATCHED|PATCH|ERROR|FAILED|WARN)\s*\]\s+(.+?)\s*$')
# The prefix the CLI gives every set it writes, so the patched project can
# sit beside the original in BodySlide. Progress lines carry it on one of
# the two reports each set produces.
OUTPUT_SET_PREFIX = "MPP_"
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QRadioButton, QCheckBox, QProgressBar, QGroupBox, QFileDialog,
    QMessageBox, QTextEdit, QFrame, QSizePolicy, QScrollArea,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QMenu, QStyledItemDelegate,
    QStyle, QStyleOptionButton, QStyleOptionComboBox, QStyleOptionViewItem, QDoubleSpinBox, QStackedWidget, QToolButton,
    QComboBox, QAbstractItemView,
)
from PySide6.QtGui import (
    QFont, QColor, QPalette, QTextCharFormat, QTextCursor, QBrush,
    QCursor, QAction, QIcon, QGuiApplication, QPainter, QPainterPath, QPen, QFontMetrics,
)

from core.config import Config, vfs_isdir, vfs_listdir
from core.source_staging import (
    extract_zip, find_bodyslide_root, find_tri_root,
    resolve_physical_bodyslide_project, normalize_output_ownership,
    mo2_texture_environment,
)
from core.drape_runtime import RUNTIME_DIRNAME as DRAPE_RUNTIME_DIRNAME, drape_environment
from core.output_archive import create_zip_archive
from core.headpart_patcher import discover_references
from core.slider_group_reader import SliderGroup, read_all as read_groups
from core.automation_xml_writer import detect_gender
from core.os_runner import OsRunner
from core import backup_manager
from core.icons import make_icon, make_pixmap, checkbox_qss
from core.outfit_scanner import (
    scan_all, OutfitInfo, GENDER_CYCLE, MESH_CYCLE,
    classify_metadata, SUPPORTED_BODY_FAMILIES, SUPPORTED_REFERENCES,
    SUPPORTED_MESH_PARTS,
)


# ── Badge colors ─────────────────────────────────────────────────────
GENDER_COLORS = {"Female": "#d946a0", "Male": "#3b82f6", "Unknown": "#94a3b8"}
GENDER_ICONS  = {"Female": "Female", "Male": "Male", "Unknown": "?"}
MESH_COLORS   = {"Body": "#8b5cf6", "Hands": "#22c55e", "Feet": "#f97316",
                 "Mixed": "#f59e0b", "Unknown": "#94a3b8",
                 "Not Applicable": "#64748b"}
MESH_LABELS   = {"Body": "Body", "Hands": "Hands", "Feet": "Feet",
                 "Mixed": "mix", "Unknown": "?", "Not Applicable": "N/A"}
MESH_ICONS    = {"Body": "⬤", "Hands": "✋", "Feet": "👟"}
PATCHED_COLOR = "#22c55e"
REVEAL_COLOR  = "#f59e0b"
GARMENT_COLORS = {"Hood": "#94a3b8", "Cape": "#14b8a6", "Cloak": "#06b6d4"}

COL_NAME   = 0
COL_GENDER = 1
COL_MESH   = 2
COL_STATUS = 3
COL_REVEAL = 4
COL_SMART  = 5
# Column 3 carries Patch Status in the selection tree and Output Reference
# in the review tree. Same index, different meaning - name both so a call
# site says which tree it is talking about.
COL_OUTPUT = 3

# The per-outfit boolean columns, and the OutfitInfo attribute each one owns.
# One mapping rather than a second copy of the delegate, badge painter and
# toggle handlers: the two flags differ only in which field they write and
# which accent they wear.
FLAG_COLUMNS = {
    COL_REVEAL: "revealing",
    COL_SMART:  "smart_refit",
}
FLAG_ACCENT_PROPERTY = {
    COL_REVEAL: "revealAccent",
    COL_SMART:  "smartRefitAccent",
}
SMART_COLOR = "#8b5cf6"
# (tooltip when on, tooltip when off) per flag column.
FLAG_TOOLTIPS = {
    COL_REVEAL: (
        "Revealing morph selected. Click to use the smoother Covered morph.",
        "Covered morph selected. Click to enable the detailed Revealing morph.",
    ),
    COL_SMART: (
        "Smart refit (Drape) enabled for this outfit. Expect roughly a minute "
        "per RM slider. Click to use the normal conform.",
        "Normal conform selected. Click to run the experimental Drape Smart "
        "refit on this outfit only.",
    ),
}
FLAG_ATTR_COLUMN = {attr: column for column, attr in FLAG_COLUMNS.items()}

SORT_ROLE   = Qt.UserRole + 2
GENDER_SORT = {"Female": 0, "Male": 1, "mix": 2, "?": 3}
MESH_SORT   = {"Body": 0, "Hands": 1, "Feet": 2, "Mixed": 3,
               "mix": 4, "Unknown": 5, "?": 5, "Not Applicable": 6,
               "N/A": 6}


class TreeItem(QTreeWidgetItem):
    """QTreeWidgetItem that sorts badge columns by numeric key (SORT_ROLE)."""
    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col in (COL_GENDER, COL_MESH, COL_STATUS, *FLAG_COLUMNS):
            a = self.data(col, SORT_ROLE)
            b = other.data(col, SORT_ROLE)
            if a is not None and b is not None:
                return a < b
        return super().__lt__(other)


class FlagDelegate(QStyledItemDelegate):
    """Paint a per-outfit flag checkbox at the visual center of its cell.

    QTreeWidget's built-in check indicator is always laid out at the leading
    edge of an item, even when the item's text alignment is centered.  This
    delegate owns the paint step for one flag column so an empty state is
    a genuinely centered checkbox, while checked/partial states keep their
    readable label centered beside it.

    One instance owns exactly one column, named at construction: Revealing and
    Smart refit are the same interaction over a different OutfitInfo field.
    """

    def __init__(self, parent=None, column: int = COL_REVEAL):
        super().__init__(parent)
        self.column = column
        self.attr = FLAG_COLUMNS[column]

    def paint(self, painter, option, index):
        if index.column() != self.column:
            return super().paint(painter, option, index)

        # Keep the checkbox legible without turning the entire narrow cell
        # into a bright selection slab. Row focus remains visible elsewhere.
        role = QPalette.AlternateBase if (index.row() % 2) else QPalette.Base
        painter.fillRect(option.rect, option.palette.brush(role))
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, option.palette.brush(QPalette.AlternateBase))

        state = index.data(Qt.CheckStateRole)
        label = str(index.data(Qt.DisplayRole) or "")
        state_opt = QStyleOptionButton()
        state_opt.palette = option.palette
        state_opt.state = QStyle.State_Enabled
        if state == Qt.Checked:
            state_opt.state |= QStyle.State_On
        elif state == Qt.PartiallyChecked:
            state_opt.state |= QStyle.State_NoChange
        else:
            state_opt.state |= QStyle.State_Off

        style = option.widget.style()
        indicator = style.subElementRect(QStyle.SE_CheckBoxIndicator, state_opt,
                                         option.widget)
        gap = 6 if label else 0
        text_width = option.fontMetrics.horizontalAdvance(label) if label else 0
        total_width = indicator.width() + gap + text_width
        left = option.rect.center().x() - total_width // 2
        top = option.rect.center().y() - indicator.height() // 2
        state_opt.rect = QRect(left, top, indicator.width(), indicator.height())
        style.drawPrimitive(QStyle.PE_IndicatorCheckBox, state_opt, painter,
                            option.widget)

        if label:
            # The group count is an accent state, not ordinary row text.  Read
            # the active theme color from the tree so the delegate remains
            # correct after an in-place Light/Dark switch.
            accent = (option.widget.property(FLAG_ACCENT_PROPERTY[self.column])
                      or (REVEAL_COLOR if self.column == COL_REVEAL else SMART_COLOR))
            painter.setPen(QColor(str(accent)))
            text_rect = QRect(left + indicator.width() + gap, option.rect.top(),
                              text_width, option.rect.height())
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, label)

    def toggle_index(self, tree, index) -> bool:
        """Toggle one review row through the window's model-backed handler."""
        if not index.isValid() or index.column() != self.column:
            return False
        owner = tree.window()
        item = tree.itemFromIndex(index)
        if item is None or not tree.isEnabled():
            return False
        if not hasattr(owner, "_toggle_execute_flag"):
            return False
        owner._toggle_execute_flag(item, self.attr)
        return True

    def editorEvent(self, event, model, option, index):
        """Own mouse and keyboard input for the custom checkbox exactly once."""
        if index.column() != self.column:
            return super().editorEvent(event, model, option, index)
        tree = option.widget
        if tree is None or not tree.isEnabled():
            return False
        if (event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
                and option.rect.contains(event.position().toPoint())):
            return self.toggle_index(tree, index)
        if (event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter)):
            return self.toggle_index(tree, index)
        return False

    def createEditor(self, parent, option, index):
        # A flag column is a checkbox interaction, never a text editor.
        return None


# Older name, kept because it reads better at the Revealing call sites.
RevealDelegate = FlagDelegate


class ReadOnlyReviewDelegate(QStyledItemDelegate):
    """Blocks accidental QLineEdit editors in non-editable review columns."""

    def createEditor(self, parent, option, index):
        return None


class RevealPopover(QFrame):
    """Small non-modal contextual help surface for the Revealing column."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("revealPopover")
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


class ReviewChoiceDelegate(QStyledItemDelegate):
    """Draw a consistent clickable choice field without a transient native combo.

    The matching menu is displayed by MainWindow._show_review_choice_menu.
    No row editor widgets are created (important for large BodySlide scans).
    """

    H_INSET, V_INSET, TEXT_PAD, ARROW_W = 5, 4, 10, 22

    def __init__(self, owner, kind: str, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.kind = kind

    def paint(self, painter, option, index):
        tree = option.widget
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        base.text = ""
        tree.style().drawControl(QStyle.CE_ItemViewItem, base, painter, tree)

        def c(key: str, fallback: str) -> QColor:
            return QColor(str(tree.property(key) or fallback))

        bg = c("comboBg", "#21262d")
        border = c("comboBorder", "#30363d")
        fg = c("comboText", "#c9d1d9")
        dim = c("comboDim", "#8b949e")
        accent = c("comboAccent", "#58a6ff")
        warning = c("reviewAttentionColor", "#d29922")
        attention = bool(index.data(Qt.UserRole + 12))
        manual = bool(index.data(Qt.UserRole + 11))
        # Qt marks every column in a hovered row with State_MouseOver. Check
        # the pointer too, so only the control under the cursor highlights.
        pointer = tree.viewport().mapFromGlobal(QCursor.pos())
        hovered = bool(option.state & QStyle.State_MouseOver)
        enabled = bool(option.state & QStyle.State_Enabled)

        box = QRectF(option.rect.adjusted(self.H_INSET, self.V_INSET,
                                         -self.H_INSET, -self.V_INSET))
        box.adjust(0.5, 0.5, -0.5, -0.5)
        hovered = hovered and box.toRect().contains(pointer)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(bg)
        painter.setPen(QPen(warning if attention else accent if hovered and enabled
                            else border, 1))
        painter.drawRoundedRect(box, 6, 6)

        cx = box.right() - self.ARROW_W / 2
        cy = box.center().y()
        chevron = QPainterPath(QPointF(cx - 4, cy - 2))
        chevron.lineTo(cx, cy + 2)
        chevron.lineTo(cx + 4, cy - 2)
        painter.setPen(QPen(accent if hovered and enabled else dim, 1.5,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(chevron)

        label = str(index.data(Qt.DisplayRole) or "")
        text_rect = box.toRect().adjusted(self.TEXT_PAD, 0, -self.ARROW_W, 0)
        available_width = max(0, text_rect.width() - (11 if manual else 0))
        elided = option.fontMetrics.elidedText(label, Qt.ElideRight, available_width)
        painter.setFont(option.font)
        painter.setPen(dim if not enabled else warning if attention else fg)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)
        if manual:
            dot_x = min(text_rect.right() - 3,
                        text_rect.left() + option.fontMetrics.horizontalAdvance(elided) + 7)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QPointF(dot_x, cy), 2.4, 2.4)
        painter.restore()

    def createEditor(self, parent, option, index):
        # Click/Enter/Space opens one QMenu; do not create a QComboBox editor.
        return None


class OutfitReviewTree(QTreeWidget):
    """Keyboard-accessible technical review using the same menu as mouse clicks."""

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key_Space
                and self.currentItem() is not None
                and self.currentColumn() in FLAG_COLUMNS):
            column = self.currentColumn()
            delegate = self.itemDelegateForColumn(column)
            if isinstance(delegate, FlagDelegate) and delegate.toggle_index(
                    self, self.currentIndex()):
                event.accept()
                return
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space, Qt.Key_F2)
                and self.currentItem() is not None
                and self.currentColumn() in (1, 2, 3)):
            parent = self.window()
            if not self.isEnabled() or not hasattr(parent, "_show_review_choice_menu"):
                event.ignore()
                return
            parent._show_review_choice_menu(self.currentItem(), self.currentColumn())
            event.accept()
            return
        super().keyPressEvent(event)

class ReviewHeaderPopover(QFrame):
    """Structured, theme-aware help for one technical-review column."""

    def __init__(self, title: str, description: str,
                 sections: list[tuple[str, str]], recommendation: str = "",
                 important: str = "", parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("reviewHeaderPopover")
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 15, 16, 16)
        lay.setSpacing(8)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(8)
        icon = QLabel()
        self.icon_label = icon
        effective = parent._effective_theme() if parent and hasattr(parent, "_effective_theme") else "dark"
        colors = LIGHT_THEME if effective == "light" else DARK_THEME
        icon.setPixmap(make_pixmap("information-outline", 17, colors["popup_accent"]))
        icon.setFixedSize(18, 18)
        heading_row.addWidget(icon, 0, Qt.AlignTop)
        heading = QLabel(title)
        heading.setObjectName("reviewPopoverTitle")
        heading_row.addWidget(heading, 1)
        lay.addLayout(heading_row)

        text = QLabel(description)
        text.setObjectName("reviewPopoverBody")
        text.setWordWrap(True)
        lay.addWidget(text)
        for section_title, section_body in sections:
            section_heading = QLabel(section_title)
            section_heading.setObjectName("reviewPopoverHeading")
            lay.addWidget(section_heading)
            section_text = QLabel(section_body)
            section_text.setObjectName("reviewPopoverBody")
            section_text.setWordWrap(True)
            lay.addWidget(section_text)
        if recommendation:
            recommendation_label = QLabel(recommendation)
            recommendation_label.setObjectName("reviewPopoverRecommendation")
            recommendation_label.setWordWrap(True)
            lay.addWidget(recommendation_label)
        if important:
            important_label = QLabel(important)
            important_label.setObjectName("reviewPopoverImportant")
            important_label.setWordWrap(True)
            lay.addWidget(important_label)
        self.setFixedWidth(340)

    def refresh_theme(self, colors: dict):
        self.icon_label.setPixmap(make_pixmap("information-outline", 17, colors["popup_accent"]))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


class ScanWorker(QThread):
    """Background thread that scans all .osp files for outfit metadata."""
    scanned = Signal(int, dict)    # emits (generation, {set_name: OutfitInfo})
    failed = Signal(int, str)

    def __init__(self, slider_sets_dir: str, generation: int, parent=None):
        super().__init__(parent)
        self._dir = slider_sets_dir
        self._generation = generation

    def run(self):
        try:
            results = scan_all(self._dir, cancel=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.scanned.emit(self._generation, results)
        except Exception as exc:
            self.failed.emit(self._generation, str(exc))


# ── Palette (dark) ───────────────────────────────────────────────────
DARK_THEME = {
    "bg": "#0d1117", "card": "#161b22", "card_alt": "#21262d",
    "border": "#30363d", "accent": "#58a6ff", "accent_hover": "#79b8ff",
    "success": "#3fb950", "warning": "#d29922", "error": "#f85149",
    "text": "#c9d1d9", "text_dim": "#8b949e", "highlight": "#1f2937",
    "input": "#21262d", "selection": "#21364e", "selection_border": "#315b83",
    "popup_bg": "#1b222c", "popup_border": "#3b4654",
    "popup_divider": "#303b49", "popup_text": "#e6edf3",
    "popup_secondary": "#9aa8b8", "popup_accent": "#58a6ff",
    "popup_warning": "#e3b341",
}
LIGHT_THEME = {
    "bg": "#f5f7fa", "card": "#ffffff", "card_alt": "#eef2f7",
    "border": "#c7d0da", "accent": "#2563eb", "accent_hover": "#1d4ed8",
    "success": "#15803d", "warning": "#a16207", "error": "#dc2626",
    "text": "#1f2937", "text_dim": "#5b6878", "highlight": "#dbeafe",
    "input": "#ffffff", "selection": "#dbeafe", "selection_border": "#93c5fd",
    "popup_bg": "#ffffff", "popup_border": "#c7d0da",
    "popup_divider": "#e2e8f0", "popup_text": "#1f2937",
    "popup_secondary": "#526173", "popup_accent": "#2563eb",
    "popup_warning": "#a16207",
}
BG         = DARK_THEME["bg"]
CARD       = DARK_THEME["card"]
CARD2      = DARK_THEME["card_alt"]
BORDER     = DARK_THEME["border"]
ACCENT     = DARK_THEME["accent"]
ACCENT_HOV = DARK_THEME["accent_hover"]
SUCCESS    = DARK_THEME["success"]
WARNING    = DARK_THEME["warning"]
ERROR      = DARK_THEME["error"]
TEXT       = DARK_THEME["text"]
TEXT_DIM   = DARK_THEME["text_dim"]
HIGHLIGHT  = DARK_THEME["highlight"]

APP_QSS = f"""
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QMainWindow, QMainWindow > QWidget {{
    background-color: {BG};
}}
QFrame#card {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLineEdit {{
    background-color: {CARD2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox {{
    background-color: {CARD2}; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 4px 24px 4px 8px; color: {TEXT};
}}
QComboBox:hover, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border-left: 1px solid {BORDER}; width: 20px; }}
QPushButton {{
    background-color: {CARD2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ background-color: #2d333b; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: #373e47; }}
QPushButton[outputMode="true"] {{
    background-color: #151b24; border: 1px solid #354252; border-radius: 0;
    padding: 6px 11px; color: {TEXT_DIM}; font-size: 12px;
}}
QPushButton[outputMode="true"][outputModeSide="left"] {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QPushButton[outputMode="true"][outputModeSide="right"] {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; margin-left: -1px; }}
QPushButton[outputMode="true"]:hover {{ background-color: #202b38; border-color: #5798dc; color: {TEXT}; }}
QPushButton[outputMode="true"]:checked {{
    background-color: #1d4b78; border-color: #58a5fa; color: #f4f9ff; font-weight: 700;
}}
QPushButton[navButton="true"] {{
    text-align: left;
    padding: 10px 12px;
    border: 1px solid transparent;
    background: transparent;
    color: {TEXT_DIM};
}}
QPushButton[navButton="true"]:hover {{ background: {CARD2}; color: {TEXT}; }}
QPushButton[navButton="true"]:checked {{
    background: #21364e;
    border-color: #315b83;
    color: #e9f2ff;
}}
QPushButton#primary {{
    background-color: {ACCENT};
    border: none;
    border-radius: 6px;
    padding: 10px 20px;
    color: {BG};
    font-size: 14px;
    font-weight: bold;
}}
QPushButton#primary:hover {{ background-color: {ACCENT_HOV}; }}
QPushButton#primary:disabled {{ background-color: #1c3454; color: #3a5a7a; }}
QListWidget {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    background: transparent;
    border: none;
}}
QListWidget::item:hover {{ background-color: {CARD2}; }}
QListWidget::item:selected {{ background-color: {HIGHLIGHT}; color: {TEXT}; }}
QTreeWidget {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
    alternate-background-color: {CARD2};
}}
QTreeWidget::item {{
    padding: 4px 4px;
    border: none;
}}
QTreeWidget::item:hover {{ background-color: {CARD2}; }}
QTreeWidget::item:selected {{ background-color: {HIGHLIGHT}; color: {TEXT}; }}
QTreeWidget#reviewTree::item {{ padding: 3px 6px; }}
QTreeWidget#reviewTree::item:hover, QTreeWidget#reviewTree::item:selected {{ background-color: transparent; color: {TEXT}; }}
QTreeWidget#reviewTree QComboBox {{ min-height: 0; padding: 0 22px 0 8px; border-radius: 5px; }}
QTreeWidget#reviewTree QComboBox::drop-down {{ border: none; width: 22px; }}
QTreeWidget#reviewTree QHeaderView::section {{ padding: 7px 28px 7px 9px; }}
QHeaderView::section {{
    background-color: {CARD2};
    color: {TEXT_DIM};
    padding: 6px 18px 6px 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {TEXT}; background-color: #2d333b; }}
QHeaderView::down-arrow {{ subcontrol-position: center right; right: 4px; width: 10px; height: 10px; }}
QHeaderView::up-arrow {{ subcontrol-position: center right; right: 4px; width: 10px; height: 10px; }}
QMenu {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; color: {TEXT}; }}
QMenu::item:selected {{ background-color: {HIGHLIGHT}; }}
QMenu::item:disabled {{ color: {TEXT_DIM}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QDialog, QMessageBox {{ background-color: {CARD}; color: {TEXT}; }}
QDialog QLabel, QMessageBox QLabel {{ color: {TEXT}; }}
QProgressBar {{
    background-color: {CARD2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}
QScrollBar:vertical {{
    background-color: {BG};
    width: 8px;
    border-radius: 4px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background-color: {TEXT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background-color: {BG};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background-color: {BORDER};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QTextEdit {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT};
    font-family: 'Consolas', monospace;
    font-size: 12px;
}}
QLabel#section {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel#dim {{ color: {TEXT_DIM}; font-size: 12px; }}
QToolTip {{
    background-color: {DARK_THEME["popup_bg"]};
    color: {DARK_THEME["popup_text"]};
    border: 1px solid {DARK_THEME["popup_border"]};
    padding: 9px 11px;
}}
QFrame#revealPopover {{
    background-color: {DARK_THEME["popup_bg"]};
    border: 1px solid {DARK_THEME["popup_border"]};
    border-radius: 10px;
}}
QLabel#revealPopoverTitle {{ color: {DARK_THEME["popup_text"]}; font-size: 15px; font-weight: 700; }}
QLabel#revealPopoverBody {{ color: {DARK_THEME["popup_secondary"]}; font-size: 12px; }}
QLabel#revealPopoverHeading {{ color: {DARK_THEME["popup_text"]}; font-size: 13px; font-weight: 650; }}
QLabel#revealPopoverRecommendation {{ color: {DARK_THEME["popup_warning"]}; font-size: 12px; }}
QFrame#revealPopoverDivider {{ color: {DARK_THEME["popup_divider"]}; background-color: {DARK_THEME["popup_divider"]}; max-height: 1px; }}
QToolButton#revealInfoButton {{ background: transparent; border: none; padding: 3px; border-radius: 12px; }}
QToolButton#revealInfoButton:hover {{ background: {DARK_THEME["selection"]}; }}
QToolButton#revealInfoButton:focus {{ border: 1px solid {DARK_THEME["popup_accent"]}; }}
QToolButton#reviewHeaderInfo {{
    color: {DARK_THEME["popup_accent"]}; background: transparent; border: none;
    border-radius: 9px; font-weight: 700; padding: 0px;
}}
QToolButton#reviewHeaderInfo:hover {{ background: {DARK_THEME["selection"]}; }}
QFrame#reviewHeaderPopover {{ background: {DARK_THEME["popup_bg"]}; border: 1px solid {DARK_THEME["popup_border"]}; border-radius: 8px; }}
QLabel#reviewPopoverTitle {{ color: {DARK_THEME["popup_text"]}; font-weight: 700; font-size: 13px; }}
QLabel#reviewPopoverBody {{ color: {DARK_THEME["popup_secondary"]}; font-size: 12px; }}
QLabel#reviewPopoverHeading {{ color: {DARK_THEME["popup_text"]}; font-weight: 650; font-size: 12px; margin-top: 2px; }}
QLabel#reviewPopoverRecommendation {{ color: {DARK_THEME["popup_warning"]}; font-size: 12px; margin-top: 3px; }}
QLabel#reviewPopoverImportant {{ color: {DARK_THEME["popup_text"]}; font-size: 12px; font-weight: 600; margin-top: 3px; }}
QLabel#sidebarBrand {{ color: {TEXT}; }}
QLabel#sidebarSubtitle, QLabel#sidebarStatus {{ color: {TEXT_DIM}; }}
QLabel#stat_num {{ font-size: 24px; font-weight: bold; color: {TEXT}; }}
QLabel#stat_lbl {{ font-size: 12px; color: {TEXT_DIM}; }}
QLabel#pageTitle {{ font-size: 25px; font-weight: 700; color: {TEXT}; }}
QLabel#statusPill {{
    color: #70dfaa; background-color: #17372e; border: 1px solid #285c49;
    border-radius: 12px; padding: 6px 12px; font-size: 12px;
}}
QLabel#statusPill[connected="false"] {{
    color: #f6c76b; background-color: #392d16; border-color: #6a5222;
}}
QPushButton[choiceCard="true"] {{
    background-color: #101721; border: 1px solid #334050; border-radius: 10px;
    color: {TEXT}; padding: 16px; text-align: left; font-size: 13px;
}}
QPushButton[choiceCard="true"]:hover {{ border-color: #75adf0; background-color: #142238; }}
QPushButton[choiceCard="true"][selected="true"] {{
    border: 1px solid #57a2fa; background-color: #182d45;
}}
QPushButton[choiceCard="true"]:disabled {{
    color: {TEXT_DIM}; border-color: #283241; background-color: #11161e;
}}
QPushButton[filterButton="true"] {{
    background-color: #273243; border: 1px solid #3a4859; border-radius: 12px;
    padding: 6px 11px; color: #b8c5d8; font-size: 12px;
}}
QPushButton[filterButton="true"]:checked {{
    background-color: #1e4c79; border-color: #58a5fa; color: #e9f2ff;
}}
QPushButton[filterButton="true"][activeFilter="true"] {{
    background-color: #285f91; border: 1px solid #79b8ff; color: #ffffff;
    font-weight: 700;
}}
QPushButton#primary {{
    background-color: #438fe4; border: 1px solid #408deb; border-radius: 7px;
    padding: 10px 18px; color: #081a2e; font-weight: 700;
}}
QPushButton#primary:hover {{ background-color: #5ba4f4; }}
QPushButton#navButton {{ text-align: left; padding: 11px 12px; }}
QPushButton#themeButton {{ padding: 4px; border-radius: 6px; color: {TEXT_DIM}; }}
QPushButton#themeButton:checked {{ background-color: #21364e; border-color: #58a6ff; color: #e9f2ff; }}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 8px;
    font-weight: 600;
    color: {TEXT};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT}; }}
QRadioButton {{ color: {TEXT}; spacing: 6px; }}
QCheckBox {{ color: {TEXT}; spacing: 6px; }}
QPushButton#stop {{
    background-color: {CARD2};
    border: 1px solid #5a2020;
    border-radius: 6px;
    padding: 10px 20px;
    color: {ERROR};
    font-size: 14px;
    font-weight: bold;
}}
QPushButton#stop:hover {{ background-color: #2d1515; border-color: {ERROR}; }}
QPushButton#stop:disabled {{ color: #4a3333; border-color: #3a2020; }}
"""

# Deliberately authored light palette.  This is an override layer rather than
# a filter/inversion so cards, disabled controls, selections and logs retain
# their intended semantic contrast.
LIGHT_QSS = f"""
QWidget {{ background: transparent; color: {LIGHT_THEME["text"]}; }}
QMainWindow, QMainWindow > QWidget {{ background-color: {LIGHT_THEME["bg"]}; }}
QFrame#card {{ background-color: {LIGHT_THEME["card"]}; border-color: {LIGHT_THEME["border"]}; }}
QLineEdit, QTextEdit {{ background-color: {LIGHT_THEME["input"]}; color: {LIGHT_THEME["text"]}; border-color: {LIGHT_THEME["border"]}; }}
QComboBox {{ background-color: {LIGHT_THEME["input"]}; color: {LIGHT_THEME["text"]}; border-color: {LIGHT_THEME["border"]}; }}
QComboBox:hover, QComboBox:focus {{ border-color: {LIGHT_THEME["accent"]}; }}
QLineEdit:focus {{ border-color: {LIGHT_THEME["accent"]}; }}
QPushButton {{ background-color: {LIGHT_THEME["card"]}; color: {LIGHT_THEME["text"]}; border-color: {LIGHT_THEME["border"]}; }}
QPushButton:hover {{ background-color: #eef5ff; border-color: {LIGHT_THEME["accent"]}; }}
QPushButton:pressed {{ background-color: #dbeafe; }}
QPushButton:disabled {{ color: #9aa5b1; background-color: #f0f2f5; border-color: #d8dee6; }}
QPushButton[outputMode="true"] {{ background-color: #f5f7fa; border-color: #c7d0da; color: #637286; }}
QPushButton[outputMode="true"]:hover {{ background-color: #eef5ff; border-color: #60a5fa; color: {LIGHT_THEME["text"]}; }}
QPushButton[outputMode="true"]:checked {{ background-color: #dbeafe; border-color: #3b82f6; color: #12345b; font-weight: 700; }}
QPushButton[navButton="true"] {{ color: {LIGHT_THEME["text_dim"]}; background: transparent; }}
QPushButton[navButton="true"]:hover {{ background: #eef2f7; color: {LIGHT_THEME["text"]}; }}
QPushButton[navButton="true"]:checked {{ background: #dbeafe; border-color: #93c5fd; color: #12345b; }}
QPushButton[choiceCard="true"] {{ background-color: #fbfcfe; border-color: #c7d0da; color: {LIGHT_THEME["text"]}; }}
QPushButton[choiceCard="true"]:hover {{ background-color: #f0f6ff; border-color: #60a5fa; }}
QPushButton[choiceCard="true"][selected="true"] {{ background-color: #e5f0ff; border-color: #3b82f6; }}
QPushButton[choiceCard="true"]:disabled {{ background-color: #f2f4f7; color: #9aa5b1; border-color: #d8dee6; }}
QPushButton[filterButton="true"] {{ background-color: #eef2f7; border-color: #c7d0da; color: #526173; }}
QPushButton[filterButton="true"]:checked {{ background-color: #dbeafe; border-color: #60a5fa; color: #12345b; }}
QPushButton[filterButton="true"][activeFilter="true"] {{ background-color: #bfdbfe; border: 2px solid #2563eb; color: #12345b; font-weight: 700; }}
QPushButton#primary {{ background-color: {LIGHT_THEME["accent"]}; border-color: {LIGHT_THEME["accent"]}; color: white; }}
QPushButton#primary:hover {{ background-color: {LIGHT_THEME["accent_hover"]}; }}
QPushButton#primary:disabled {{ background-color: #bfdbfe; color: #eff6ff; }}
QPushButton#stop {{ background-color: #fff7f7; border-color: #fecaca; color: {LIGHT_THEME["error"]}; }}
QPushButton#stop:hover {{ background-color: #fee2e2; border-color: {LIGHT_THEME["error"]}; }}
QPushButton#themeButton {{ color: {LIGHT_THEME["text_dim"]}; }}
QPushButton#themeButton:checked {{ background-color: #dbeafe; border-color: #60a5fa; color: #12345b; }}
QListWidget, QTreeWidget {{ background-color: {LIGHT_THEME["card"]}; color: {LIGHT_THEME["text"]}; border-color: {LIGHT_THEME["border"]}; alternate-background-color: #f3f6fa; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: #eef5ff; }}
QTreeWidget#reviewTree::item {{ padding: 3px 6px; }}
QTreeWidget#reviewTree::item:hover, QTreeWidget#reviewTree::item:selected {{ background-color: transparent; color: {LIGHT_THEME["text"]}; }}
QTreeWidget#reviewTree QComboBox {{ min-height: 0; padding: 0 22px 0 8px; border-radius: 5px; }}
QTreeWidget#reviewTree QComboBox::drop-down {{ border: none; width: 22px; }}
QTreeWidget#reviewTree QHeaderView::section {{ padding: 7px 28px 7px 9px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: {LIGHT_THEME["highlight"]}; color: #12345b; }}
QHeaderView::section {{ background-color: #eef2f7; color: {LIGHT_THEME["text_dim"]}; border-color: {LIGHT_THEME["border"]}; }}
QHeaderView::section:hover {{ background-color: #e5edf7; color: {LIGHT_THEME["text"]}; }}
QMenu {{ background-color: {LIGHT_THEME["card"]}; color: {LIGHT_THEME["text"]}; border-color: {LIGHT_THEME["border"]}; }}
QMenu::item {{ color: {LIGHT_THEME["text"]}; }}
QMenu::item:selected {{ background-color: {LIGHT_THEME["highlight"]}; }}
QMenu::separator {{ background: {LIGHT_THEME["border"]}; }}
QDialog, QMessageBox {{ background-color: {LIGHT_THEME["card"]}; color: {LIGHT_THEME["text"]}; }}
QDialog QLabel, QMessageBox QLabel {{ color: {LIGHT_THEME["text"]}; }}
QProgressBar {{ background-color: #eef2f7; color: {LIGHT_THEME["text"]}; border-color: {LIGHT_THEME["border"]}; }}
QProgressBar::chunk {{ background-color: {LIGHT_THEME["accent"]}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background-color: {LIGHT_THEME["bg"]}; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background-color: #c4ccd6; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background-color: #94a3b8; }}
QLabel#section, QLabel#pageTitle, QLabel#stat_num, QGroupBox, QGroupBox::title {{ color: {LIGHT_THEME["text"]}; }}
QLabel#dim, QLabel#stat_lbl {{ color: {LIGHT_THEME["text_dim"]}; }}
QToolTip {{ background-color: {LIGHT_THEME["popup_bg"]}; color: {LIGHT_THEME["popup_text"]}; border: 1px solid {LIGHT_THEME["popup_border"]}; padding: 9px 11px; }}
QFrame#revealPopover {{ background-color: {LIGHT_THEME["popup_bg"]}; border-color: {LIGHT_THEME["popup_border"]}; }}
QLabel#revealPopoverTitle, QLabel#revealPopoverHeading {{ color: {LIGHT_THEME["popup_text"]}; }}
QLabel#revealPopoverBody {{ color: {LIGHT_THEME["popup_secondary"]}; }}
QLabel#revealPopoverRecommendation {{ color: {LIGHT_THEME["popup_warning"]}; }}
QFrame#revealPopoverDivider {{ color: {LIGHT_THEME["popup_divider"]}; background-color: {LIGHT_THEME["popup_divider"]}; }}
QToolButton#revealInfoButton {{ color: {LIGHT_THEME["popup_accent"]}; }}
QToolButton#revealInfoButton:hover {{ background: {LIGHT_THEME["selection"]}; }}
QToolButton#revealInfoButton:focus {{ border-color: {LIGHT_THEME["popup_accent"]}; }}
QToolButton#reviewHeaderInfo {{ color: {LIGHT_THEME["popup_accent"]}; }}
QToolButton#reviewHeaderInfo:hover {{ background: {LIGHT_THEME["selection"]}; }}
QFrame#reviewHeaderPopover {{ background: {LIGHT_THEME["popup_bg"]}; border-color: {LIGHT_THEME["popup_border"]}; }}
QLabel#reviewPopoverTitle {{ color: {LIGHT_THEME["popup_text"]}; }}
QLabel#reviewPopoverBody {{ color: {LIGHT_THEME["popup_secondary"]}; }}
QLabel#reviewPopoverHeading {{ color: {LIGHT_THEME["popup_text"]}; }}
QLabel#reviewPopoverRecommendation {{ color: {LIGHT_THEME["popup_warning"]}; }}
QLabel#reviewPopoverImportant {{ color: {LIGHT_THEME["popup_text"]}; }}
QLabel#sidebarBrand {{ color: {LIGHT_THEME["text"]}; }}
QLabel#sidebarSubtitle, QLabel#sidebarStatus {{ color: {LIGHT_THEME["text_dim"]}; }}
QLabel#statusPill {{ color: #166534; background-color: #dcfce7; border-color: #86efac; }}
QLabel#statusPill[connected="false"] {{ color: #854d0e; background-color: #fef9c3; border-color: #fde047; }}
"""


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("card")
    return f


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {BORDER}; background-color: {BORDER}; max-height: 1px;")
    return line


class StatCard(QFrame):
    def __init__(self, icon_name: str, number: str, label: str, color: str = TEXT, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(80)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)

        top = QHBoxLayout()
        from core.icons import make_pixmap
        ico = QLabel()
        ico.setPixmap(make_pixmap(icon_name, 20, color))
        top.addWidget(ico)
        top.addStretch()
        lay.addLayout(top)

        self.num_label = QLabel(number)
        self.num_label.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {TEXT};")
        lay.addWidget(self.num_label)

        lbl = QLabel(label)
        self.label = lbl
        lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        lay.addWidget(lbl)

    def set_value(self, v: str):
        self.num_label.setText(v)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self.runner = OsRunner(self)
        self.runner.log_line.connect(self._on_log_line)
        self.runner.finished.connect(self._on_os_finished)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._elapsed_secs = 0
        self._total_outfits = 0
        # UI selection is application state, never derived from the currently
        # visible QTreeWidget rows. The tree is organised by OSP project while
        # BodySlide groups are filters, so selection is global by outfit name.
        # Keep the tuple form for saved in-memory compatibility, with one
        # stable synthetic key independent of the active group filter.
        self._selected_outfits: set[tuple[str, str]] = set()
        self._body_group_filter = ""
        self._scan_generation = 0
        self._scan_workers: list[ScanWorker] = []
        self._groups_loaded = False
        self._temp_files: set[str] = set()
        self._terminal_status: dict[str, str] = {}
        self._review_refresh_pending = False
        saved_theme = str(getattr(self.config, "theme", "dark") or "dark").lower()
        self._theme_mode = saved_theme if saved_theme in {"dark", "light", "system"} else "dark"
        self._target_kind = "outfits"
        self._source_kind = "bodyslide"
        self._source_path = self.config.bodyslide_root
        # BodySlide resolves its project path from the effective game-data
        # directory under MO2, not from the physical directory containing the
        # launcher executable.  Prefer that virtual root when it exposes the
        # SliderGroups/SliderSets entries, then fall back to the configured
        # directory for standalone use.
        self._source_input_root = self._resolve_bodyslide_root()
        self._source_work_root = self._source_input_root
        self._source_path = self._source_input_root
        self._effective_bodyslide_root = ""
        self._effective_tri_root = ""
        self._source_temp_root = None
        self._archive_staging_dir = ""
        self._active_archive_path = ""
        self._selection_context: tuple[str, str, str] | None = None

        self.setWindowTitle("MPPatcher — ModernPhysiquePicker Outfit Patcher")
        self.setMinimumSize(1200, 760)
        self.resize(1340, 840)
        self._build_ui()
        self._load_state()
        self._apply_theme(self._theme_mode)
        self._apply_target_ui_mode()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "execute_review_tree"):
            QTimer.singleShot(0, self._update_execute_review_table_height)
        if hasattr(self, "execute_review_tree"):
            QTimer.singleShot(0, self._position_execute_review_header_help)

    def _system_theme_mode(self) -> str:
        """Return the Windows/Qt system colour scheme, with a dark fallback."""
        try:
            scheme = QApplication.instance().styleHints().colorScheme()
            name = str(scheme).lower()
            if "light" in name:
                return "light"
            if "dark" in name:
                return "dark"
        except Exception:
            pass
        return "dark"

    def _effective_theme(self) -> str:
        return self._system_theme_mode() if self._theme_mode == "system" else self._theme_mode

    def _apply_theme(self, mode: str | None = None):
        requested = (mode or getattr(self, "_theme_mode", "dark")).lower()
        if requested not in {"dark", "light", "system"}:
            requested = "dark"
        self._theme_mode = requested
        effective = self._effective_theme()
        colors = DARK_THEME if effective == "dark" else LIGHT_THEME
        qss = APP_QSS if effective == "dark" else APP_QSS + LIGHT_QSS
        # QApplication styling reaches child dialogs and custom log widgets,
        # while preserving all patch/backend behaviour.
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss + checkbox_qss(
                colors["accent"], colors["input"], colors["border"]))
        if hasattr(self, "config"):
            self.config.theme = requested
        self._refresh_theme_controls()

    def _set_theme_mode(self, mode: str):
        self._theme_mode = mode
        self._apply_theme(mode)

    def _toggle_theme(self):
        self._set_theme_mode("light" if self._effective_theme() == "dark" else "dark")

    def _refresh_theme_controls(self):
        effective = self._effective_theme()
        colors = DARK_THEME if effective == "dark" else LIGHT_THEME
        icon_color = LIGHT_THEME["text_dim"] if effective == "light" else DARK_THEME["text_dim"]
        accent = LIGHT_THEME["accent"] if effective == "light" else DARK_THEME["accent"]
        if hasattr(self, "sidebar_brand"):
            self.sidebar_brand.setStyleSheet(
                f"font-size: 18px; font-weight: 750; color: {LIGHT_THEME['text'] if effective == 'light' else DARK_THEME['text']};")
        if hasattr(self, "sidebar_subtitle"):
            self.sidebar_subtitle.setStyleSheet(
                f"font-size: 11px; color: {LIGHT_THEME['text_dim'] if effective == 'light' else DARK_THEME['text_dim']};")
        if hasattr(self, "sidebar_status"):
            self.sidebar_status.setStyleSheet(
                f"color: {LIGHT_THEME['text_dim'] if effective == 'light' else DARK_THEME['text_dim']}; font-size: 11px;")
        if hasattr(self, "group_tree"):
            self.group_tree.setProperty("revealAccent", colors["warning"])
            # Smart refit keeps one accent across both themes: it is an
            # opt-in cost, not a warning state that should borrow the amber.
            self.group_tree.setProperty("smartRefitAccent", SMART_COLOR)
            self.group_tree.style().unpolish(self.group_tree)
            self.group_tree.style().polish(self.group_tree)
        if hasattr(self, "execute_review_tree"):
            tree = self.execute_review_tree
            tree.setProperty("revealAccent", colors["warning"])
            tree.setProperty("smartRefitAccent", SMART_COLOR)
            tree.setProperty("reviewAttentionColor", colors["warning"])
            for property_name, color_key in (
                    ("comboBg", "input"), ("comboBorder", "border"),
                    ("comboText", "text"), ("comboDim", "text_dim"),
                    ("comboAccent", "accent")):
                tree.setProperty(property_name, colors[color_key])
            tree.viewport().update()
        for _, _, button in getattr(self, "_review_header_help_buttons", []):
            button.setIcon(make_icon("information-outline", 15, colors["popup_accent"]))
        popup = getattr(self, "_review_header_popover", None)
        if popup and popup.isVisible():
            popup.refresh_theme(colors)
        if hasattr(self, "reveal_info_button"):
            self._set_reveal_info_icon(colors)
        if hasattr(self, "reveal_popover"):
            self.reveal_popover.style().unpolish(self.reveal_popover)
            self.reveal_popover.style().polish(self.reveal_popover)
            self._refresh_reveal_popover_theme(colors)
        for name in ("lbl_progress_pct", "lbl_current_outfit", "lbl_current_step"):
            label = getattr(self, name, None)
            if label is not None:
                label.setStyleSheet(f"color: {colors['accent']}; font-weight: bold;")
        if hasattr(self, "_nav_buttons"):
            for button, icon in zip(self._nav_buttons, ("tshirt", "restore", "cog")):
                button.setIcon(make_icon(icon, 16, icon_color))
        if hasattr(self, "sidebar_dark_button"):
            self.sidebar_dark_button.setIcon(make_icon("weather-night", 16, accent if effective == "dark" else icon_color))
            self.sidebar_light_button.setIcon(make_icon("weather-sunny", 16, accent if effective == "light" else icon_color))
            self.sidebar_dark_button.setChecked(effective == "dark")
            self.sidebar_light_button.setChecked(effective == "light")
        if hasattr(self, "theme_mode_buttons"):
            for name, button in self.theme_mode_buttons.items():
                button.setChecked(name == self._theme_mode)
                icon_name = button.property("themeIcon") or "monitor"
                button.setIcon(make_icon(icon_name, 16, accent if name == self._theme_mode else icon_color))
        for button in getattr(self, "_theme_icon_buttons", []):
            button.setIcon(make_icon(button.property("themeIcon"), 16, accent))

    def _set_reveal_info_icon(self, colors=None):
        if not hasattr(self, "reveal_info_button"):
            return
        colors = colors or (DARK_THEME if self._effective_theme() == "dark" else LIGHT_THEME)
        icon = QIcon()
        icon.addPixmap(make_pixmap("information-outline", 16, colors["popup_accent"]),
                       QIcon.Normal, QIcon.Off)
        icon.addPixmap(make_pixmap("information-outline", 16, colors["accent_hover"]),
                       QIcon.Active, QIcon.Off)
        icon.addPixmap(make_pixmap("information-outline", 16, colors["accent_hover"]),
                       QIcon.Selected, QIcon.Off)
        self.reveal_info_button.setIcon(icon)

    def _refresh_reveal_popover_theme(self, colors=None):
        if not hasattr(self, "reveal_popover"):
            return
        colors = colors or (DARK_THEME if self._effective_theme() == "dark" else LIGHT_THEME)
        for attr, icon_name in (
            ("reveal_popup_icon", "information-outline"),
            ("reveal_popup_off_icon", "checkbox-blank-outline"),
            ("reveal_popup_on_icon", "checkbox-marked"),
            ("reveal_popup_recommendation_icon", "lightbulb-on-outline"),
        ):
            label = getattr(self, attr, None)
            if label is not None:
                color = colors["popup_accent"] if attr != "reveal_popup_recommendation_icon" else colors["popup_warning"]
                label.setPixmap(make_pixmap(icon_name, 18, color))

    def _close_reveal_popover(self):
        if hasattr(self, "reveal_popover") and self.reveal_popover.isVisible():
            self.reveal_popover.close()

    def _toggle_reveal_popover(self):
        if self.reveal_popover.isVisible():
            self._close_reveal_popover()
            return
        self.reveal_popover.adjustSize()
        button_top = self.reveal_info_button.mapToGlobal(QPoint(0, 0))
        button_bottom = self.reveal_info_button.mapToGlobal(
            QPoint(0, self.reveal_info_button.height()))
        screen = QApplication.screenAt(button_top) or self.screen()
        available = screen.availableGeometry()
        margin = 8
        x = button_top.x() + self.reveal_info_button.width() // 2 - self.reveal_popover.width() // 2
        x = max(available.left() + margin,
                min(x, available.right() - self.reveal_popover.width() - margin))
        y = button_bottom.y() + margin
        if y + self.reveal_popover.height() > available.bottom() - margin:
            y = button_top.y() - self.reveal_popover.height() - margin
        y = max(available.top() + margin,
                min(y, available.bottom() - self.reveal_popover.height() - margin))
        self.reveal_popover.move(x, y)
        self.reveal_popover.show()
        self.reveal_popover.raise_()
        self.reveal_popover.activateWindow()
        self.reveal_popover.setFocus()

    # ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root_w = QWidget()
        self.setCentralWidget(root_w)
        root = QHBoxLayout(root_w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addLayout(self._build_sidebar(), 0)
        self.page_stack = QStackedWidget()
        # The patch page is a workflow stack.  It deliberately does not use the
        # legacy _build_left/_build_right dashboard composition.
        self.page_stack.addWidget(self._build_patch_page())
        self.page_stack.addWidget(self._build_history_page())
        self.page_stack.addWidget(self._build_settings_page())
        root.addWidget(self.page_stack, 1)

    def _build_patch_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(12)
        # Lightweight compatibility counters for the existing execution code.
        # They are no longer rendered as oversized dashboard cards.
        self.stat_outfits = StatCard("format-list", "0", "Total Outfits", ACCENT, page)
        self.stat_groups = StatCard("account-group", "0", "Groups Selected", SUCCESS, page)
        self.stat_processed = StatCard("checkbox-outline", "0", "Processed", TEXT, page)
        self.stat_elapsed = StatCard("clock-outline", "0:00:00", "Elapsed Time", WARNING, page)
        for stat in (self.stat_outfits, self.stat_groups, self.stat_processed, self.stat_elapsed):
            stat.hide()

        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Patch Meshes")
        title.setObjectName("pageTitle")
        heading.addWidget(title)
        heading.addWidget(_dim("Configure, review and apply morph compatibility patches."))
        header.addLayout(heading)
        header.addStretch()
        self.patch_connection_status = QLabel("Checking BodySlide…")
        self.patch_connection_status.setObjectName("statusPill")
        header.addWidget(self.patch_connection_status)
        lay.addLayout(header)

        self.workflow_stack = QStackedWidget()
        self.workflow_stack.addWidget(self._build_configure_step())
        self.workflow_stack.addWidget(self._build_review_step())
        self.workflow_stack.addWidget(self._build_execute_step())
        lay.addWidget(self.workflow_stack, 1)
        self._set_workflow_step(0)
        return page

    def _choice_card(self, icon: str, title: str, description: str,
                     enabled: bool = True) -> QPushButton:
        button = QPushButton()
        button.setObjectName("choiceCard")
        button.setProperty("choiceCard", True)
        button.setCheckable(True)
        button.setEnabled(enabled)
        button.setMinimumHeight(104)
        button.setIcon(make_icon(icon, 26, ACCENT if enabled else TEXT_DIM))
        button.setIconSize(QSize(26, 26))
        button.setText(f"{title}\n{description}")
        button.setToolTip(description)
        return button

    def _refresh_choice_card(self, button: QPushButton, selected: bool):
        button.setChecked(selected)
        button.setProperty("selected", selected)
        button.style().unpolish(button)
        button.style().polish(button)

    def _build_configure_step(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        target_card = _card()
        target_lay = QVBoxLayout(target_card)
        target_lay.setContentsMargins(18, 16, 18, 16)
        target_lay.addWidget(QLabel("1  What do you want to patch?", objectName="section"))
        target_row = QHBoxLayout()
        self.target_outfits_btn = self._choice_card(
            "tshirt", "Outfits and Armors", "Female / Male body and clothing meshes")
        self.target_headparts_btn = self._choice_card(
            "face-man", "Headparts", "NIFs for hair, brows, beards and accessories")
        self.target_outfits_btn.clicked.connect(lambda: self._select_target_kind("outfits"))
        self.target_headparts_btn.clicked.connect(lambda: self._select_target_kind("headparts"))
        self._refresh_choice_card(self.target_outfits_btn, True)
        target_row.addWidget(self.target_outfits_btn)
        target_row.addWidget(self.target_headparts_btn)
        target_lay.addLayout(target_row)
        lay.addWidget(target_card)

        source_card = _card()
        source_lay = QVBoxLayout(source_card)
        source_lay.setContentsMargins(18, 16, 18, 16)
        source_lay.addWidget(QLabel("2  Where are the files?", objectName="section"))
        source_row = QHBoxLayout()
        self.source_bodyslide_btn = self._choice_card(
            "format-list", "BodySlide Groups", "Installed SliderGroups and OSP projects")
        self.source_zip_btn = self._choice_card(
            "archive", "ZIP Archive", "BodySlide data or headpart assets in a .zip")
        self.source_folder_btn = self._choice_card(
            "folder-open", "Local Folder", "BodySlide data or headpart assets in a folder")
        self.source_bodyslide_btn.clicked.connect(lambda: self._select_source_kind("bodyslide"))
        self.source_zip_btn.clicked.connect(lambda: self._select_source_kind("zip"))
        self.source_folder_btn.clicked.connect(lambda: self._select_source_kind("folder"))
        self._refresh_choice_card(self.source_bodyslide_btn, True)
        source_row.addWidget(self.source_bodyslide_btn)
        source_row.addWidget(self.source_zip_btn)
        source_row.addWidget(self.source_folder_btn)
        source_lay.addLayout(source_row)
        self.configure_source_status = QLabel(
            "BodySlide Groups uses the installation configured in Settings.")
        self.configure_source_status.setObjectName("dim")
        source_lay.addWidget(self.configure_source_status)
        lay.addWidget(source_card)

        lay.addStretch(1)

        action_row = QHBoxLayout()
        action_row.addStretch()
        scan_btn = QPushButton("Continue to Select Outfits")
        scan_btn.setObjectName("primary")
        scan_btn.setIcon(make_icon("arrow-right", 17, "#0d1117"))
        scan_btn.clicked.connect(self._continue_to_review)
        action_row.addWidget(scan_btn)
        lay.addLayout(action_row)
        return page

    def _select_target_kind(self, kind: str):
        if kind == "headparts" and self._source_kind == "bodyslide":
            # BodySlide projects are not a headpart input.  Drop the old
            # source context so selecting Headparts cannot silently rescan the
            # installed SliderGroups as if they were NIF assets.
            self._source_kind = "folder"
            self._source_path = ""
            self._source_input_root = ""
            self._source_work_root = ""
        if kind != self._target_kind:
            self._set_selection_context(kind, self._source_kind, self._source_path)
        self._target_kind = kind
        self._refresh_choice_card(self.target_outfits_btn, kind == "outfits")
        self._refresh_choice_card(self.target_headparts_btn, kind == "headparts")
        if kind == "headparts":
            self.configure_source_status.setText(
                "Headparts mode reads the mod's NIFs and writes a Physiques of "
                "Skyrim morph library, so mods that ship no .tri still work.")
        else:
            self.configure_source_status.setText(
                "Outfits and Armors mode uses BodySlide SliderGroups and OSP projects.")
        self._apply_target_ui_mode()
        self._reload_groups()

    def _apply_target_ui_mode(self):
        """Adapt only the presentation to the selected target.

        Outfits keep the original BodySlide review and patch controls. Headparts
        use the same workflow shell, but must not present body references,
        BodySlide groups, clipping/zap options, or outfit terminology.
        """
        headparts = getattr(self, "_target_kind", "outfits") == "headparts"
        if hasattr(self, "execute_detected_label"):
            self.execute_detected_label.setText("Resolved" if headparts else "Ready")
            self.execute_needs_review_label.setText("Unresolved" if headparts else "Need review")
        self.source_bodyslide_btn.setVisible(not headparts)
        self.source_bodyslide_btn.setEnabled(not headparts)
        self.source_zip_btn.setText("ZIP Archive\nHeadpart NIFs, BSAs and plugins" if headparts
                                    else "ZIP Archive\nBodySlide data or headpart assets in a .zip")
        self.source_folder_btn.setText("Local Folder\nHeadpart NIFs, BSAs and plugins" if headparts
                                      else "Local Folder\nBodySlide data or headpart assets in a folder")
        if hasattr(self, "body_group_filter_combo"):
            self.body_group_filter_combo.setVisible(not headparts)
            if hasattr(self, "body_group_filter_label"):
                self.body_group_filter_label.setVisible(not headparts)
        if hasattr(self, "groups_card_title"):
            self.groups_card_title.setText("Headpart meshes" if headparts else "Outfits")
        if hasattr(self, "group_tree"):
            self.group_tree.setHeaderLabels(
                ["Headpart NIF / archive", "Sex", "Patch Status", "", "", ""]
                if headparts else
                ["Outfit", "Sex", "Part", "Patch Status", "Revealing", "Smart refit"])
        if hasattr(self, "search_edit"):
            self.search_edit.setPlaceholderText(
                "Search NIF paths or shapes…" if headparts else
                "Search projects or outfits…")
        if hasattr(self, "review_page_title"):
            self.review_page_title.setText("Select Headparts" if headparts else "Select Outfits")
            self.review_summary.setText("0 headparts selected" if headparts else "0 outfits selected")
            self.review_continue_btn.setText("Continue to Headpart Setup" if headparts
                                             else "Continue to Patch Setup")
        if hasattr(self, "execute_page_title"):
            self.execute_page_title.setText("Review Headparts" if headparts else "Review & Patch")
            self.execute_intro.setText(
                "Review NIF meshes and their detected sex/part metadata, then generate the Physiques of Skyrim morph library."
                if headparts else
                "Verify the detected metadata for selected outfits, then configure and start the patch.")
            self.execute_review_title.setText("Headpart Geometry" if headparts else "Outfit Configuration")
            self.execute_review_intro.setText(
                "Geometry is read from NIFs. The generated output is a separate MPF TRI morph library; source files are not modified."
                if headparts else
                "Click a field to change its value. A blue dot marks a manual override; amber controls need your attention.")
        if hasattr(self, "_execute_options_card"):
            self._execute_options_card.setVisible(not headparts)
        if hasattr(self, "output_destination_label"):
            self.output_destination_label.setText("Morph library output" if headparts else "Output")
        if hasattr(self, "patch_btn") and not self.runner.is_running():
            self.patch_btn.setText("  Generate Headpart Library" if headparts else "  Start Patching")
        if hasattr(self, "execute_review_tree"):
            self._configure_execute_review_columns(headparts)

    def _configure_execute_review_columns(self, headparts: bool):
        tree = self.execute_review_tree
        if headparts:
            tree.setColumnCount(3)
            tree.setHeaderLabels(["NIF / Shape", "Sex", "Sex source"])
            tree.headerItem().setToolTip(
                2, "How the sex was resolved: plugin, path, filename, paired file, or unresolved.")
            tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
            for column in range(3):
                tree.setItemDelegateForColumn(column, QStyledItemDelegate(tree))
            self._close_review_header_help()
            for _column, _title, button in getattr(self, "_review_header_help_buttons", []):
                button.hide()
            header = tree.header()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            for column, width in ((1, 100), (2, 170)):
                header.setSectionResizeMode(column, QHeaderView.Fixed)
                header.resizeSection(column, width)
            return
        tree.setColumnCount(6)
        tree.setHeaderLabels(["Outfit", "Body Reference", "Mesh Part",
                              "Output Reference", "Revealing", "Smart refit"])
        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in ((1, 150), (2, 126), (3, 158), (4, 104), (5, 104)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            header.resizeSection(column, width)
        tree.setItemDelegateForColumn(
            1, ReviewChoiceDelegate(self, "reference", tree))
        tree.setItemDelegateForColumn(
            2, ReviewChoiceDelegate(self, "mesh", tree))
        tree.setItemDelegateForColumn(0, ReadOnlyReviewDelegate(tree))
        tree.setItemDelegateForColumn(
            3, ReviewChoiceDelegate(self, "output", tree))
        for column in FLAG_COLUMNS:
            tree.setItemDelegateForColumn(column, FlagDelegate(tree, column))
        self._position_execute_review_header_help()

    @staticmethod
    def _source_identity(path: str) -> str:
        """Stable source identity for selection ownership, including Windows paths."""
        if not path:
            return ""
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _set_selection_context(self, target_kind: str, source_kind: str, source_path: str):
        context = (target_kind, source_kind, self._source_identity(source_path))
        if context != self._selection_context:
            self._selected_outfits.clear()
            # Scan metadata carries manual overrides and revealing state. It
            # must not seed a different source merely because an unrelated OSP
            # happens to reuse the same SliderSet name.
            self._scan_data.clear()
            self._all_groups = []
            self._groups_loaded = False
            self._selection_context = context

    def _reload_initial_groups(self):
        """Run the deferred startup scan only while no caller populated a model."""
        if not self._groups_loaded and not getattr(self, "_all_groups", []):
            self._reload_groups()

    def _refresh_source_cards(self):
        self._refresh_choice_card(self.source_bodyslide_btn, self._source_kind == "bodyslide")
        self._refresh_choice_card(self.source_zip_btn, self._source_kind == "zip")
        self._refresh_choice_card(self.source_folder_btn, self._source_kind == "folder")

    def _select_source_kind(self, kind: str):
        if kind == "bodyslide":
            new_path = self._resolve_bodyslide_root()
            self._set_selection_context(self._target_kind, kind, new_path)
            self._source_kind = kind
            self._source_path = new_path
            self._source_input_root = new_path
            self._source_work_root = new_path
        else:
            if kind == "zip":
                path, _ = QFileDialog.getOpenFileName(
                    self, "Select source ZIP", self._default_headpart_folder(), "ZIP archives (*.zip)")
            else:
                path = QFileDialog.getExistingDirectory(
                    self, "Select source folder", self._default_headpart_folder())
            if not path:
                self._refresh_source_cards()
                return
            self._set_selection_context(self._target_kind, kind, path)
            self._source_kind = kind
            self._source_path = path
            self._cleanup_source_temp()
            if kind == "zip":
                import tempfile
                self._source_temp_root = tempfile.mkdtemp(prefix="mppatcher_source_")
                extracted = extract_zip(path, self._source_temp_root)
                self._source_input_root = str(extracted)
            else:
                self._source_input_root = path
            self._source_work_root = self._source_input_root
        self._refresh_source_cards()
        if self._target_kind == "headparts":
            label = "ZIP" if kind == "zip" else "folder" if kind == "folder" else "BodySlide VFS"
            self.configure_source_status.setText(f"Headparts source: {label} · {self._source_path}")
        else:
            root = find_bodyslide_root(self._source_input_root)
            label = "ZIP" if kind == "zip" else "folder" if kind == "folder" else "BodySlide Groups"
            self.configure_source_status.setText(f"Outfit source: {label} · {root}")
        self._reload_groups()

    def _default_headpart_folder(self) -> str:
        """Return MO2's physical mods folder as the convenient dialog start."""
        configured = str(getattr(self.config, "mods_folder", "") or "").strip()
        candidates = [configured] if configured else []
        app_dir = Path(str(getattr(self.config, "app_dir", "") or ""))
        if app_dir:
            candidates.extend(str(parent / "mods") for parent in app_dir.parents)
        for candidate in candidates:
            try:
                if candidate and Path(candidate).is_dir():
                    return candidate
            except OSError:
                continue
        return str(app_dir) if app_dir else ""

    def _headpart_default_sex(self) -> str:
        """Choose the conventional fallback for a source mod's unlabelled NIFs."""
        name = os.path.basename(self._source_path or self._source_input_root or "").lower()
        if "beard" in name or "himbo" in name or re.search(r"(?<!fe)male", name):
            return "male"
        return "female"

    def _build_review_step(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        header = QHBoxLayout()
        self.review_page_title = QLabel("Select Outfits", objectName="pageTitle")
        header.addWidget(self.review_page_title)
        header.addStretch()
        self.review_summary = QLabel("0 outfits selected")
        self.review_summary.setObjectName("dim")
        header.addWidget(self.review_summary)
        lay.addLayout(header)
        # This is intentionally the dominant element of the workflow page.
        self.review_groups_card = self._build_groups_card()
        lay.addWidget(self.review_groups_card, 1)
        nav = QHBoxLayout()
        back = QPushButton("Back")
        back.setIcon(make_icon("arrow-left", 16, TEXT_DIM))
        back.clicked.connect(lambda: self._set_workflow_step(0))
        nav.addWidget(back)
        nav.addStretch()
        self.review_continue_btn = QPushButton("Continue to Patch Setup")
        self.review_continue_btn.setObjectName("primary")
        self.review_continue_btn.setIcon(make_icon("arrow-right", 16, "#0d1117"))
        self.review_continue_btn.clicked.connect(self._continue_to_execute)
        nav.addWidget(self.review_continue_btn)
        lay.addLayout(nav)
        return page

    def _build_execute_step(self) -> QWidget:
        page = QWidget()
        outer_lay = QVBoxLayout(page)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(8)
        self.execute_scroll = QScrollArea()
        self.execute_scroll.setWidgetResizable(True)
        self.execute_scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.execute_page_title = QLabel("Review & Patch", objectName="pageTitle")
        lay.addWidget(self.execute_page_title)
        execute_intro = _dim(
            "Verify the detected metadata for selected outfits, then configure and start the patch.")
        self.execute_intro = execute_intro
        execute_intro.setWordWrap(True)
        lay.addWidget(execute_intro)

        # A compact, data-backed summary keeps the execution step meaningful
        # even before the user touches an option.  It is refreshed whenever the
        # review selection or scan metadata changes.
        summary_card = _card()
        summary_lay = QVBoxLayout(summary_card)
        summary_lay.setContentsMargins(14, 8, 14, 8)
        summary_grid = QGridLayout()
        summary_grid.setHorizontalSpacing(10)
        summary_grid.setVerticalSpacing(0)
        self.execute_selection_summary = QLabel("0")
        self.execute_detected_summary = QLabel("0")
        self.execute_needs_review_summary = QLabel("0")
        for value in (self.execute_selection_summary, self.execute_detected_summary,
                      self.execute_needs_review_summary):
            value.setObjectName("stat_num")
        summary_grid.addWidget(self.execute_selection_summary, 0, 0)
        summary_grid.addWidget(_dim("Selected"), 0, 1)
        summary_grid.addWidget(_dim("|"), 0, 2)
        summary_grid.addWidget(self.execute_detected_summary, 0, 3)
        self.execute_detected_label = _dim("Ready")
        summary_grid.addWidget(self.execute_detected_label, 0, 4)
        summary_grid.addWidget(_dim("|"), 0, 5)
        summary_grid.addWidget(self.execute_needs_review_summary, 0, 6)
        self.execute_needs_review_label = _dim("Need review")
        summary_grid.addWidget(self.execute_needs_review_label, 0, 7)
        summary_grid.setColumnStretch(8, 1)
        summary_lay.addLayout(summary_grid)
        lay.addWidget(summary_card)

        review_card = _card()
        review_lay = QVBoxLayout(review_card)
        review_lay.setContentsMargins(16, 14, 16, 14)
        review_lay.setSpacing(8)
        self.execute_review_title = QLabel("Outfit Configuration", objectName="section")
        review_lay.addWidget(self.execute_review_title)
        review_intro = _dim(
            "Click a field to change its value. A blue dot marks a manual override; amber controls need your attention.")
        self.execute_review_intro = review_intro
        review_intro.setWordWrap(True)
        review_lay.addWidget(review_intro)
        self.execute_review_tree = OutfitReviewTree()
        self.execute_review_tree.setObjectName("reviewTree")
        self.execute_review_tree.setColumnCount(6)
        self.execute_review_tree.setHeaderLabels(
            ["Outfit", "Body Reference", "Mesh Part", "Output Reference",
             "Revealing", "Smart refit"])
        self.execute_review_tree.setRootIsDecorated(False)
        self.execute_review_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # Mouse and keyboard both open an anchored QMenu. No native row editors.
        self.execute_review_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.execute_review_tree.setAlternatingRowColors(True)
        review_header = self.execute_review_tree.header()
        review_header.setStretchLastSection(False)
        review_header.setMinimumHeight(34)
        review_header.setSectionResizeMode(0, QHeaderView.Stretch)
        # Six columns now share the row. Trimmed so the fixed ones total
        # 642px, leaving the stretched Outfit column real room at the
        # window's 1200px minimum instead of pushing the flags off-screen.
        for column, width in ((1, 150), (2, 126), (3, 158), (4, 104), (5, 104)):
            review_header.setSectionResizeMode(column, QHeaderView.Fixed)
            review_header.resizeSection(column, width)
        self.execute_review_tree.itemClicked.connect(self._on_execute_review_clicked)
        self.execute_review_tree.setItemDelegateForColumn(
            1, ReviewChoiceDelegate(self, "reference", self.execute_review_tree))
        self.execute_review_tree.setItemDelegateForColumn(
            2, ReviewChoiceDelegate(self, "mesh", self.execute_review_tree))
        self.execute_review_tree.setItemDelegateForColumn(
            0, ReadOnlyReviewDelegate(self.execute_review_tree))
        self.execute_review_tree.setItemDelegateForColumn(
            3, ReviewChoiceDelegate(self, "output", self.execute_review_tree))
        for column in FLAG_COLUMNS:
            self.execute_review_tree.setItemDelegateForColumn(
                column, FlagDelegate(self.execute_review_tree, column))
        self.execute_review_tree.setProperty("reviewAttentionColor", self._reveal_accent())
        self.execute_review_tree.setProperty("smartRefitAccent", SMART_COLOR)
        self._setup_execute_review_header_help()
        self.execute_review_tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.execute_review_tree.setUniformRowHeights(True)
        self.execute_review_tree.setMinimumHeight(120)
        review_lay.addWidget(self.execute_review_tree, 1)
        review_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(review_card)

        options_card = _card()
        options_lay = QVBoxLayout(options_card)
        options_lay.setContentsMargins(16, 14, 16, 14)
        options_lay.setSpacing(8)
        options_lay.addWidget(QLabel("Patch Options", objectName="section"))
        self.advanced_options_panel = QWidget()
        advanced_lay = QGridLayout(self.advanced_options_panel)
        advanced_lay.setContentsMargins(0, 2, 0, 0)
        advanced_lay.setHorizontalSpacing(30)
        advanced_lay.setVerticalSpacing(8)
        self.auto_zap_check = QCheckBox("Enable Auto-Zap")
        self.auto_zap_check.setChecked(True)
        self.auto_zap_check.setToolTip(
            "Generates a Fix Clipping Zap slider intended to hide body geometry that could "
            "intersect the outfit. Review the result when necessary.")
        # Smart refit is per-outfit, in the review table's Smart refit
        # column. A run-wide switch could not express the only sane usage -
        # paying ~1 min per RM slider on the few garments that need it - and
        # a master switch over a per-row column only invites marking a row
        # and silently getting no Drape.
        self.reduce_clipping_check = QCheckBox("Reduce clipping")
        self.reduce_clipping_check.setToolTip(
            "Runs BodySlide and Outfit Studio's native Fix Clipping operation on generated "
            "RM sliders to help reduce mesh intersections.")
        clip_row = QHBoxLayout()
        clip_row.addWidget(_dim("Fix Clipping strength"))
        self.fix_clipping_strength = QDoubleSpinBox()
        self.fix_clipping_strength.setRange(0.0, 1.0)
        self.fix_clipping_strength.setSingleStep(0.05)
        self.fix_clipping_strength.setDecimals(2)
        self.fix_clipping_strength.setValue(0.5)
        self.fix_clipping_strength.setToolTip(
            "Controls the intensity of the Fix Clipping adjustment, from 0.00 to 1.00. "
            "Higher values may reduce clipping but can introduce visible deformation.")
        clip_row.addWidget(self.fix_clipping_strength)
        clip_row.addStretch()
        self._clip_strength_row = QWidget()
        self._clip_strength_row.setLayout(clip_row)
        advanced_lay.addWidget(self.auto_zap_check, 0, 0)
        advanced_lay.addWidget(self.reduce_clipping_check, 0, 1)
        advanced_lay.addWidget(self._clip_strength_row, 1, 1)
        advanced_lay.setColumnStretch(3, 1)
        self.reduce_clipping_check.toggled.connect(self._update_fix_clipping_visibility)
        self._update_fix_clipping_visibility(False)
        options_lay.addWidget(self.advanced_options_panel)
        # options_card is added to outer_lay below the scroll area so it never
        # disappears beneath a long technical-review table.
        self._execute_options_card = options_card

        output_panel = QWidget()
        output_lay = QHBoxLayout(output_panel)
        output_lay.setContentsMargins(2, 2, 2, 2)
        output_lay.setSpacing(8)
        out_row = QHBoxLayout()
        self.output_destination_label = QLabel("Output", objectName="section")
        out_row.addWidget(self.output_destination_label)
        self._output_mode_value = "folder"
        output_mode_switch = QWidget()
        output_mode_switch.setToolTip(
            "Choose loose files for a normal mod folder, or a ready-to-install ZIP archive.")
        output_mode_lay = QHBoxLayout(output_mode_switch)
        output_mode_lay.setContentsMargins(0, 0, 0, 0)
        output_mode_lay.setSpacing(0)
        self.output_mode_folder_btn = QPushButton("Loose files")
        self.output_mode_zip_btn = QPushButton("ZIP archive")
        for button, side in ((self.output_mode_folder_btn, "left"),
                             (self.output_mode_zip_btn, "right")):
            button.setCheckable(True)
            button.setProperty("outputMode", True)
            button.setProperty("outputModeSide", side)
            output_mode_lay.addWidget(button)
        self.output_mode_folder_btn.clicked.connect(
            lambda: self._set_output_mode("folder"))
        self.output_mode_zip_btn.clicked.connect(
            lambda: self._set_output_mode("zip"))
        out_row.addWidget(output_mode_switch)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Separate output data root")
        self.output_edit.setToolTip(
            "Patched projects are written here; the source BodySlide projects remain unchanged.")
        self.output_edit.editingFinished.connect(self._on_output_destination_changed)
        out_row.addWidget(self.output_edit, 1)
        self.output_browse_btn = QPushButton("Choose…")
        self.output_browse_btn.setIcon(make_icon("folder-open", 14, ACCENT))
        self.output_browse_btn.clicked.connect(self._browse_output_destination)
        out_row.addWidget(self.output_browse_btn)
        output_lay.addLayout(out_row, 1)
        self._execute_output_panel = output_panel
        # output_panel is also anchored outside the scrollable review.

        # Compatibility controls used by the non-destructive backend path.  The
        # public UI exposes only the safe mode, but the existing save/run code
        # continues to use these values without a second code path.
        self.radio_new = QRadioButton("Create New Projects (prefix: MPP_)")
        self.radio_new.setChecked(True)
        self.radio_overwrite = QRadioButton("Overwrite Existing Projects")
        self.radio_overwrite.setChecked(False)
        self.radio_overwrite.setEnabled(False)
        self.radio_overwrite.setVisible(False)
        self.radio_new.setVisible(False)
        self.backup_check = QCheckBox()
        self.backup_check.setChecked(True)
        self.backup_check.setVisible(False)
        self.backup_edit = QLineEdit()
        self.backup_edit.setVisible(False)

        self.progress_card = self._build_progress_card()
        self.progress_card.setVisible(False)
        lay.addWidget(self.progress_card)
        self.execute_completion = QLabel("")
        self.execute_completion.setObjectName("dim")
        self.execute_completion.setWordWrap(True)
        lay.addWidget(self.execute_completion)

        action_row = QHBoxLayout()
        self.back_to_review_btn = QPushButton("Back to Review")
        self.back_to_review_btn.setIcon(make_icon("arrow-left", 16, TEXT_DIM))
        self.back_to_review_btn.clicked.connect(self._back_to_review)
        action_row.addWidget(self.back_to_review_btn)
        action_row.addStretch()
        self.patch_btn = QPushButton("Start Patching")
        self.patch_btn.setObjectName("primary")
        self.patch_btn.setMinimumHeight(42)
        self.patch_btn.setIcon(make_icon("play", 18, "#0d1117"))
        self.patch_btn.clicked.connect(self._on_patch)
        self.stop_btn = QPushButton("Cancel")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setIcon(make_icon("pause", 18, ERROR))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop)
        action_row.addWidget(self.patch_btn)
        action_row.addWidget(self.stop_btn)
        # Added below the scroll area so this action bar remains accessible.

        log_toggle = QPushButton("Activity Log  ·  Show")
        log_toggle.setCheckable(True)
        self.execute_log_toggle = log_toggle
        self.execute_log = self._build_log_card()
        self.execute_log.setVisible(False)
        log_toggle.setVisible(False)
        log_toggle.toggled.connect(
            lambda visible: (self.execute_log.setVisible(visible),
                             log_toggle.setText("Activity Log  ·  Hide" if visible
                                                else "Activity Log  ·  Show")))
        lay.addWidget(log_toggle)
        lay.addWidget(self.execute_log, 1)
        lay.addStretch(1)
        self.execute_scroll.setWidget(content)
        outer_lay.addWidget(self.execute_scroll, 1)
        outer_lay.addWidget(options_card, 0)
        outer_lay.addWidget(output_panel, 0)
        outer_lay.addLayout(action_row)
        return page

    def _set_workflow_step(self, index: int):
        if index != 1:
            self._close_reveal_popover()
        if index != 2:
            self._close_review_header_help()
        if hasattr(self, "workflow_stack"):
            self.workflow_stack.setCurrentIndex(index)
        for widget_name, visible in (("review_summary", index == 1),
                                     ("execute_selection_summary", index == 2)):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setVisible(visible)

    def _continue_to_review(self):
        if not self._groups_loaded:
            self._reload_groups()
        self._set_workflow_step(1)

    def _continue_to_execute(self):
        groups = self._checked_groups()
        if not groups:
            noun = "headpart" if self._target_kind == "headparts" else "outfit"
            QMessageBox.warning(self, f"No {noun}s selected",
                                f"Select at least one {noun} before continuing.")
            return
        self._update_execute_summary()
        self._refresh_execute_review()
        self._set_workflow_step(2)

    def _back_to_review(self):
        if self.runner.is_running():
            return
        self._set_workflow_step(1)

    def _update_execute_summary(self):
        """Refresh the Step 3 summary from the real review model state."""
        if not hasattr(self, "execute_selection_summary"):
            return
        selected = {member for group in self._checked_groups()
                    for member in group.members}
        if self._target_kind == "headparts":
            rows = getattr(self, "_headpart_rows", {})
            unresolved = sum(
                1 for name in selected
                if getattr(rows.get(name), "kind", "mesh") == "mesh"
                and getattr(rows.get(name), "sex", None) is None)
            self.execute_selection_summary.setText(str(len(selected)))
            self.execute_detected_summary.setText(str(len(selected) - unresolved))
            self.execute_needs_review_summary.setText(str(unresolved))
            return
        needs_review = sum(
            1 for name in selected
            if not self._scan_data.get(name) or self._scan_data[name].needs_attention)
        detected = len(selected) - needs_review
        self.execute_selection_summary.setText(str(len(selected)))
        self.execute_detected_summary.setText(str(detected))
        self.execute_needs_review_summary.setText(str(needs_review))

    def _refresh_execute_review(self):
        """Render the selected outfits once for the technical final review."""
        if not hasattr(self, "execute_review_tree"):
            return
        selected = sorted(
            {member for group in self._checked_groups() for member in group.members},
            key=str.casefold,
        )
        tree = self.execute_review_tree
        if self._target_kind == "headparts":
            self._refresh_headpart_execute_review(selected)
            return
        tree.blockSignals(True)
        tree.clear()
        try:
            for name in selected:
                info = self._scan_data.get(name)
                item = QTreeWidgetItem(tree)
                item.setText(0, name)
                item.setData(0, Qt.UserRole, name)
                # Keep the technical table dense; stylesheet padding must not
                # turn each logical 34px row into a 45–50px row.
                item.setSizeHint(0, QSize(0, 34))
                if not info:
                    item.setText(1, "Select reference...")
                    item.setText(2, "Select part...")
                    item.setText(3, "Leave as Original")
                    item.setData(3, Qt.UserRole + 10, "original")
                    item.setData(1, Qt.UserRole + 12, True)
                    item.setData(2, Qt.UserRole + 12, True)
                    item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
                    item.setCheckState(4, Qt.Unchecked)
                    item.setToolTip(0, f"{name}\n\nNo scan metadata was found for this outfit. Choose a Body Reference and Mesh Part before patching.")
                    item.setToolTip(1, "No Body Reference detected. Click to choose the MPP reference used to generate morphs.")
                    item.setToolTip(2, "No Mesh Part detected. Click to choose Body, Hands, or Feet.")
                    item.setToolTip(3, "Keeps the original project's reference structure after patching.")
                    item.setToolTip(4, "Covered morph selected. Click to enable the detailed Revealing morph.")
                    continue
                reference = ("3BA (Female)" if info.eff_reference == "3BA" else
                             "HIMBO (Male)" if info.eff_reference == "HIMBO" else
                             "Select reference...")
                part_known = info.eff_mesh in SUPPORTED_MESH_PARTS
                part = info.eff_mesh if part_known else (
                    "Not applicable" if info.status_label == "Not Applicable"
                    else "Select part...")
                manual_reference = info.has_manual_reference_override
                manual_mesh = bool(info.mesh_override)
                item.setText(1, reference)
                item.setData(1, Qt.UserRole + 10, info.eff_reference)
                item.setData(1, Qt.UserRole + 11, manual_reference)
                item.setData(1, Qt.UserRole + 12, info.eff_reference not in SUPPORTED_REFERENCES)
                item.setForeground(1, QBrush(QColor(
                    GENDER_COLORS.get(info.eff_sex, GENDER_COLORS["Unknown"]))))
                item.setText(2, part)
                item.setData(2, Qt.UserRole + 10, info.eff_mesh)
                item.setData(2, Qt.UserRole + 11, manual_mesh)
                item.setData(2, Qt.UserRole + 12,
                             not part_known and info.status_label != "Not Applicable")
                part_color = (GARMENT_COLORS.get(info.garment_tags[0], MESH_COLORS.get(info.eff_mesh, TEXT))
                              if info.garment_tags else MESH_COLORS.get(info.eff_mesh, TEXT))
                item.setForeground(2, QBrush(QColor(part_color)))
                policy = info.output_reference_policy
                if policy not in {"original", "remove", "force"}:
                    policy = "original"
                    info.output_reference_policy = policy
                policy_labels = {
                    "original": "Leave as Original",
                    "remove": "Remove Reference",
                    "force": "Force Reference",
                }
                item.setText(COL_OUTPUT, policy_labels[policy])
                item.setData(COL_OUTPUT, Qt.UserRole + 10, policy)
                item.setData(COL_OUTPUT, Qt.UserRole + 11, policy != "original")
                # A full-body outfit whose project carries no body is worth
                # flagging: the patch then generates morphs for the garment
                # alone and the body does not follow, which reads downstream
                # as "the RM sliders do nothing". It stays a warning, not a
                # block - a cape or a partial piece is legitimately like this.
                body_without_reference = (
                    part_known and info.eff_mesh == "Body"
                    and not info.has_reference and policy == "original")
                item.setData(COL_OUTPUT, Qt.UserRole + 12, body_without_reference)
                original_state = "contains a reference" if info.has_reference else "is reference-less"
                available_change = ("Remove Reference" if info.has_reference else "Force Reference")
                item.setToolTip(
                    COL_OUTPUT,
                    "Marked as a Body outfit, but the original project has no body "
                    "inside.\n\nFull-body outfits normally carry one. Without it the "
                    "patch generates morphs for the garment only, and the body does "
                    "not follow.\n\nIf this outfit really covers the body, choose "
                    "Force Reference here to embed the MPP body in the output. If it "
                    "is an accessory, a cape or a partial piece, leave it as it is."
                    if body_without_reference else
                    f"The original project {original_state}. Leave as Original preserves that state. "
                    f"You can also choose {available_change} for this output.")
                for column, attr in FLAG_COLUMNS.items():
                    item.setCheckState(
                        column,
                        Qt.Checked if getattr(info, attr) else Qt.Unchecked)
                    item.setText(column, "")
                    item.setData(column, Qt.UserRole + 12, False)
                # The delegate owns the checkbox event and keeps CheckStateRole
                # synchronized with the matching OutfitInfo field.
                item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
                if manual_reference:
                    item.setText(1, reference + "  •")
                if manual_mesh:
                    item.setText(2, part + "  •")
                # Manual override status is rendered as a delegate dot, not
                # appended to the value where it corrupts elision/alignment.
                item.setText(1, reference)
                item.setText(2, part)
                source = info.source_project or "Unknown project"
                issues = list(dict.fromkeys([*info.issues, *info.warnings]))
                issue_text = "\n".join(f"• {issue}" for issue in issues[:3])
                item.setToolTip(
                    0,
                    f"{name}\nSource project: {source}\nStatus: {info.status_label}" +
                    (f"\n\nDetection notes:\n{issue_text}" if issue_text else ""))
                reference_state = "Manually overridden" if manual_reference else "Automatically detected"
                item.setToolTip(
                    1,
                    f"{reference_state}: {reference}.\nClick to change the MPP body reference used to generate morphs.")
                mesh_state = "Manually overridden" if manual_mesh else "Automatically detected"
                mesh_evidence = info.evidence[0] if info.evidence else "No direct mesh evidence recorded."
                item.setToolTip(
                    2,
                    f"{mesh_state}: {part}.\nDetection: {mesh_evidence}" +
                    ("\nThis selection needs review before patching." if not part_known else "") +
                    "\nClick to choose Body, Hands, or Feet.")
                for column, attr in FLAG_COLUMNS.items():
                    on_tip, off_tip = FLAG_TOOLTIPS[column]
                    item.setToolTip(
                        column, on_tip if getattr(info, attr) else off_tip)
        finally:
            tree.blockSignals(False)
        self._update_execute_review_table_height(len(selected))
        QTimer.singleShot(0, lambda count=len(selected):
                          self._update_execute_review_table_height(count))

    def _refresh_headpart_execute_review(self, selected: list[str]):
        """Render headpart metadata without exposing BodySlide controls."""
        tree = self.execute_review_tree
        rows = getattr(self, "_headpart_rows", {})
        tree.blockSignals(True)
        tree.clear()
        try:
            for name in selected:
                row = rows.get(name)
                item = QTreeWidgetItem(tree)
                item.setText(0, name)
                item.setText(1, "Archive" if row and row.kind == "archive"
                             else (row.sex.capitalize() if row and row.sex else "Unknown"))
                source_labels = {
                    "plugin": "Plugin record", "path": "Path", "filename": "Filename",
                    "paired": "F/M pair", "default": "Default", "unresolved": "Unresolved",
                    "manual": "Manual override", "archive": "During run",
                }
                item.setText(2, "During run" if row and row.kind == "archive"
                             else (source_labels.get(row.sex_source, row.sex_source)
                                   if row else "Not scanned"))
                item.setSizeHint(0, QSize(0, 34))
                warning = "\n".join(getattr(row, "warnings", [])[:3]) if row else ""
                if warning:
                    item.setToolTip(0, f"{name}\n\n{warning}")
        finally:
            tree.blockSignals(False)
        self._update_execute_review_table_height(len(selected))
        QTimer.singleShot(0, lambda count=len(selected):
                          self._update_execute_review_table_height(count))

    def _update_execute_review_table_height(self, count: int | None = None):
        """Size the technical table to its data without starving patch controls."""
        if not hasattr(self, "execute_review_tree"):
            return
        tree = self.execute_review_tree
        rows = tree.topLevelItemCount() if count is None else count
        row_height = max(34, tree.sizeHintForRow(0), tree.fontMetrics().height() + 10)
        header_height = max(36, tree.header().height())
        viewport_height = (self.execute_scroll.viewport().height()
                           if hasattr(self, "execute_scroll") else 0)
        if viewport_height <= 0:
            viewport_height = max(380, self.height() - 320)
        # Fixed controls (Patch Options, Output Destination and navigation)
        # now live outside this scroll area. Reserve space here ONLY for the
        # page title, description, summary and review-card chrome.
        # Avoid both scrollbars appearing during normal pre-patch review.
        available_for_table = max(112, min(640, viewport_height - 205))
        desired_height = header_height + max(1, rows) * row_height + 5
        target_height = max(112, min(desired_height, available_for_table))
        if tree.minimumHeight() != target_height or tree.maximumHeight() != target_height:
            tree.setMinimumHeight(target_height)
            tree.setMaximumHeight(target_height)

    def _show_execution_feedback(self):
        """Reveal progress and logs only once there is an active job/result."""
        self.progress_card.setVisible(True)
        self.execute_log_toggle.setVisible(True)
        if not self.execute_log_toggle.isChecked():
            self.execute_log_toggle.setChecked(True)

    def _setup_execute_review_header_help(self):
        header = self.execute_review_tree.header()
        definitions = {
            1: {
                "title": "Body Reference",
                "hover": "Choose which MPP body reference will be used to generate morphs for this outfit.",
                "description": "Determines which MPP reference is used to conform the outfit and generate its morphs.",
                "sections": [
                    ("3BA (Female)", "For compatible female BodySlide outfits."),
                    ("HIMBO (Male)", "For compatible male BodySlide outfits."),
                ],
                "important": "Important: The reference used for conforming is separate from the final outfit's embedded body geometry.",
            },
            2: {
                "title": "Mesh Part",
                "hover": "Choose the type of mesh being conformed: Body, Hands, or Feet.",
                "description": "Selects which body region and corresponding MPP reference geometry will be used during morph generation.",
                "sections": [
                    ("Body", "For torso clothing, armor, capes, and other projects requiring body-based conforming."),
                    ("Hands", "For gloves and meshes requiring hand-based conforming."),
                    ("Feet", "For boots, footwear, and meshes requiring foot-based conforming."),
                ],
                "recommendation": "Recommendation: MPPatcher attempts to detect this automatically. Change it only if the detected part is incorrect or unresolved.",
            },
            3: {
                "title": "Output Reference",
                "hover": "Choose whether the output keeps, removes, or adds an embedded body reference.",
                "description": "Controls the embedded body reference in the generated BodySlide project.",
                "sections": [
                    ("Leave as Original (Default)", "Reference-less projects remain reference-less. Projects with a reference receive the matching MPP reference."),
                    ("Remove Reference", "Available when the original project contains a reference. The output is saved without embedded body geometry."),
                    ("Force Reference", "Available when the original project is reference-less. The matching MPP body reference is embedded in the output."),
                ],
                "important": "This setting changes only the generated output. Source OSP, NIF, and OSD files are not modified.",
            },
            4: {
                "title": "Revealing",
                "hover": "Enable the detailed body morph for outfits that expose significant skin.",
                "description": "Selects how much anatomical detail is retained in the generated body morph.",
                "sections": [
                    ("Off: Covered Outfit", "Uses a smoother morph to minimize conforming artifacts beneath clothing."),
                    ("On: Revealing Outfit", "Uses the detailed morph, preserving features such as skin folds, navel definition, and cellulite."),
                ],
                "recommendation": "Recommendation: Enable for outfits with substantial exposed skin. Leave disabled for most covered clothing and armor.",
            },
            5: {
                "title": "Smart refit",
                "hover": "Run the experimental Drape refit on this outfit only.",
                "description": "Replaces the normal conform with Drape's distance-weighted refit, solving the whole outfit as one system against the morphed body.",
                "sections": [
                    ("Off: Normal conform", "Fast, and correct for most armor and fitted clothing."),
                    ("On: Drape Smart refit (ALPHA)", "Runs once per RM slider and takes roughly a minute each, so a marked outfit costs several minutes. It can also produce a worse result on some meshes."),
                ],
                "recommendation": "Recommendation: Mark only the loose garments the normal conform handles badly - long skirts, robes, capes - and compare the output before keeping it.",
                "important": "This is per outfit on purpose: the cost is paid per marked outfit, not once per run.",
            },
        }
        self._review_header_help_buttons = []
        for column, definition in definitions.items():
            title = definition["title"]
            button = QToolButton(header)
            button.setObjectName("reviewHeaderInfo")
            button.setIcon(make_icon("information-outline", 15, DARK_THEME["popup_accent"]))
            button.setIconSize(QSize(15, 15))
            button.setToolTip(definition["hover"])
            button.setAccessibleName(f"Help: {title}")
            button.setAccessibleDescription(definition["hover"])
            button.setFixedSize(24, 24)
            button.setAutoRaise(True)
            button.clicked.connect(
                lambda checked=False, details=definition, source=button:
                self._toggle_review_header_help(source, details))
            self._review_header_help_buttons.append((column, title, button))
        header.sectionResized.connect(lambda *_: self._position_execute_review_header_help())
        if hasattr(header, "geometriesChanged"):
            header.geometriesChanged.connect(self._position_execute_review_header_help)
        QTimer.singleShot(0, self._position_execute_review_header_help)

    def _position_execute_review_header_help(self):
        if not hasattr(self, "execute_review_tree"):
            return
        header = self.execute_review_tree.header()
        header_font = QFont(header.font())
        header_font.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(header_font)
        header_pad_left = 9
        icon_gap = 4
        for column, title, button in getattr(self, "_review_header_help_buttons", []):
            width = header.sectionSize(column)
            text_width = metrics.horizontalAdvance(title)
            section_x = header.sectionViewportPosition(column)
            inset = (button.width() - button.iconSize().width()) // 2
            desired_x = section_x + header_pad_left + text_width + icon_gap - inset
            right_edge = section_x + width - button.width()
            x = min(desired_x, right_edge)
            y = max(0, (header.height() - button.height()) // 2)
            button.move(x, y)
            button.setVisible(width >= text_width + header_pad_left + 20 and
                              x + button.width() > 0 and x < header.width())
            button.raise_()

    def _toggle_review_header_help(self, button: QToolButton, details: dict):
        if self._target_kind == "headparts":
            self._close_review_header_help()
            return
        existing = getattr(self, "_review_header_popover", None)
        if existing and existing.isVisible():
            existing.close()
            self._review_header_popover = None
            return
        popup = ReviewHeaderPopover(
            details["title"], details["description"], details["sections"],
            details.get("recommendation", ""), details.get("important", ""), self)
        self._review_header_popover = popup
        popup.adjustSize()
        trigger = button.rect()
        global_top_left = button.mapToGlobal(trigger.topLeft())
        global_bottom_right = button.mapToGlobal(trigger.bottomRight())
        screen = QGuiApplication.screenAt(global_top_left) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        x = global_top_left.x()
        y = global_bottom_right.y() + 6
        if x + popup.width() > available.right():
            x = global_bottom_right.x() - popup.width()
        if y + popup.height() > available.bottom():
            y = global_top_left.y() - popup.height() - 6
        x = max(available.left(), min(x, available.right() - popup.width() + 1))
        y = max(available.top(), min(y, available.bottom() - popup.height() + 1))
        popup.move(QPoint(x, y))
        popup.show()
        popup.raise_()
        popup.setFocus()

    def _close_review_header_help(self):
        popup = getattr(self, "_review_header_popover", None)
        if popup:
            popup.close()
            self._review_header_popover = None

    def _on_execute_review_clicked(self, item: QTreeWidgetItem, column: int):
        """Open one anchored, themed menu rather than a native combobox editor."""
        if self._target_kind == "headparts":
            if column == 1:
                self._show_headpart_sex_menu(item)
            return
        if column in (1, 2, 3):
            self._show_review_choice_menu(item, column)

    def _show_headpart_sex_menu(self, item: QTreeWidgetItem):
        if self.runner.is_running() or item.treeWidget() is not self.execute_review_tree:
            return
        name = item.data(0, Qt.UserRole)
        row = getattr(self, "_headpart_rows", {}).get(name)
        if not row:
            return
        menu = QMenu(self.execute_review_tree)
        current = row.sex
        for sex, label in (("female", "Female"), ("male", "Male")):
            action = menu.addAction(label)
            action.setData(sex)
            action.setCheckable(True)
            action.setChecked(sex == current)
        picked = menu.exec(self.execute_review_tree.viewport().mapToGlobal(
            self.execute_review_tree.visualItemRect(item).bottomLeft()))
        if not picked or picked.data() == current:
            return
        row.sex = picked.data()
        row.sex_source = "manual"
        row.confidence = "Manual"
        info = self._scan_data.get(name)
        if info:
            info.gender = row.sex.capitalize()
            info.body_family = row.sex.capitalize()
            info.confidence = "Manual"
            info.warnings = [w for w in info.warnings if "Sex could not be determined" not in w]
        self._refresh_execute_review()
        self._update_execute_summary()

    def _toggle_execute_flag(self, item: QTreeWidgetItem, attr: str):
        """Toggle one per-outfit flag without relying on stock checkbox state."""
        if self.runner.is_running() or item.treeWidget() is not self.execute_review_tree:
            return
        column = FLAG_ATTR_COLUMN[attr]
        name = item.data(0, Qt.UserRole)
        info = self._scan_data.get(name)
        if info is None:
            # A provisional review row may exist before the scanner has found
            # its OSP. Keep the user's choice in the same model, but preserve
            # project_found=False so the patch validation still blocks that
            # outfit until its source project is available.
            info = OutfitInfo(
                name=name,
                project_found=False,
                warnings=["No matching OSP project was found in the scanned SliderSets."],
            )
            self._scan_data[name] = info
        value = not getattr(info, attr)
        setattr(info, attr, value)
        item.setData(column, Qt.CheckStateRole,
                     Qt.Checked if value else Qt.Unchecked)
        on_tip, off_tip = FLAG_TOOLTIPS[column]
        item.setToolTip(column, on_tip if value else off_tip)
        self._update_execute_summary()

    def _toggle_execute_revealing(self, item: QTreeWidgetItem):
        self._toggle_execute_flag(item, "revealing")

    def _toggle_execute_smart_refit(self, item: QTreeWidgetItem):
        self._toggle_execute_flag(item, "smart_refit")

    def _flag_names(self, members: list[str], attr: str) -> list[str]:
        """Return the exact SliderSet names carrying one per-outfit flag."""
        return [name for name in members
                if self._scan_data.get(name)
                and getattr(self._scan_data[name], attr)]

    def _write_flag_file(self, members: list[str], attr: str) -> str:
        """Create a newline-separated name list for one of the CLI's set flags.

        Returns "" when nothing is marked, so the caller omits the argument
        rather than handing the CLI an empty file to open.
        """
        names = self._flag_names(members, attr)
        if not names:
            return ""
        import tempfile
        rf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8")
        rf.write("\n".join(names))
        rf.close()
        self._temp_files.add(rf.name)
        return rf.name

    def _revealing_names(self, members: list[str]) -> list[str]:
        return self._flag_names(members, "revealing")

    def _write_revealing_file(self, members: list[str]) -> str:
        """Create the list consumed by the CLI's ``--revealing-sets`` option."""
        return self._write_flag_file(members, "revealing")

    def _smart_refit_names(self, members: list[str]) -> list[str]:
        return self._flag_names(members, "smart_refit")

    def _write_smart_refit_file(self, members: list[str]) -> str:
        """Create the list consumed by the CLI's ``--smart-refit-sets`` option."""
        return self._write_flag_file(members, "smart_refit")

    def _show_review_choice_menu(self, item: QTreeWidgetItem, column: int):
        """Same polished, keyboard-accessible popup for mouse and keyboard.

        The menu is anchored to the field, not to the cursor. A choice commits
        only after the transient QMenu closes, so the tree is never rebuilt in
        the middle of a delegate editor's setModelData call.
        """
        tree = self.execute_review_tree
        if (self.runner.is_running() or not tree.isEnabled() or
                column not in (1, 2, 3) or item.treeWidget() is not tree):
            return
        name = item.data(0, Qt.UserRole)
        if not isinstance(name, str):
            return
        kind = "reference" if column == 1 else "mesh" if column == 2 else "output"
        if column == 1:
            choices = (("3BA (Female)", "3BA"), ("HIMBO (Male)", "HIMBO"))
        elif column == 2:
            choices = (("Body", "Body"), ("Hands", "Hands"), ("Feet", "Feet"))
        else:
            info = self._scan_data.get(name)
            choices = [("Leave as Original", "original")]
            if info and info.has_reference:
                choices.append(("Remove Reference", "remove"))
            elif info:
                choices.append(("Force Reference", "force"))
        current = item.data(column, Qt.UserRole + 10)
        tree.setCurrentItem(item, column)
        menu = QMenu(tree)
        menu.setObjectName("reviewChoiceMenu")
        colors = LIGHT_THEME if self._effective_theme() == "light" else DARK_THEME
        menu.setStyleSheet(f"""
            QMenu#reviewChoiceMenu {{
                background-color: {colors['card']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 8px;
                padding: 5px;
            }}
            QMenu#reviewChoiceMenu::item {{
                background-color: transparent;
                color: {colors['text']};
                padding: 9px 16px 9px 28px;
                margin: 1px 3px;
                border-radius: 5px;
            }}
            QMenu#reviewChoiceMenu::item:selected {{
                background-color: {colors['selection']};
            }}
        """)
        for label, value in choices:
            action = menu.addAction(label)
            action.setData(value)
            action.setCheckable(True)
            action.setChecked(value == current)
        field_x = tree.header().sectionViewportPosition(column) + ReviewChoiceDelegate.H_INSET
        field_width = tree.header().sectionSize(column) - 2 * ReviewChoiceDelegate.H_INSET
        row = tree.visualItemRect(item)
        menu.setMinimumWidth(max(field_width, 176 if column in (1, 3) else 142))
        menu.ensurePolished()
        size = menu.sizeHint()
        global_top = tree.viewport().mapToGlobal(QPoint(field_x, row.top()))
        global_bottom = tree.viewport().mapToGlobal(QPoint(field_x, row.bottom() + 1))
        screen = QGuiApplication.screenAt(global_top) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        x = max(available.left() + 4,
                min(global_bottom.x(), available.right() - size.width() - 3))
        y = global_bottom.y() + 2
        if y + size.height() > available.bottom() - 4:
            y = global_top.y() - size.height() - 2
        y = max(available.top() + 3,
                min(y, available.bottom() - size.height() - 3))
        picked = menu.exec(QPoint(x, y))
        menu.deleteLater()
        if picked and picked.data() != current:
            self._apply_review_override(name, kind, picked.data())

    def _apply_review_override(self, name: str, kind: str, value: str):
        info = self._scan_data.get(name)
        if not info:
            info = OutfitInfo(name=name)
            self._scan_data[name] = info
        if kind == "reference" and value in SUPPORTED_REFERENCES:
            info.gender_override = value
        elif kind == "mesh" and value in SUPPORTED_MESH_PARTS:
            info.mesh_override = value
        elif kind == "output" and value in {"original", "remove", "force"}:
            if value == "remove" and not info.has_reference:
                return
            if value == "force" and info.has_reference:
                return
            info.output_reference_policy = value
        else:
            return
        # The delegate is still committing its QComboBox. Rebuilding the tree
        # here destroys that editor mid-commit; queue one consolidated refresh.
        if not self._review_refresh_pending:
            self._review_refresh_pending = True
            QTimer.singleShot(0, self._flush_review_override_refresh)

    def _flush_review_override_refresh(self):
        self._review_refresh_pending = False
        if not self.runner.is_running():
            self._refresh_execute_review()
            self._update_execute_summary()

    def _popup_execute_review_choice(self, name: str, kind: str, options: list[str]):
        menu = QMenu(self)
        labels = {"3BA": "Female", "HIMBO": "Male", "Unknown": "Unknown",
                  "Body": "Body", "Hands": "Hands", "Feet": "Feet"}
        for option in options:
            action = menu.addAction(labels.get(option, option))
            action.setData(option)
        chosen = menu.exec(QCursor.pos())
        if not chosen:
            return
        info = self._scan_data.get(name)
        if not info:
            info = OutfitInfo(name=name)
            self._scan_data[name] = info
        if kind == "gender":
            info.gender_override = chosen.data()
        else:
            info.mesh_override = chosen.data()
        self._refresh_execute_review()

    def _build_sidebar(self) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(6)
        brand = QLabel("◈  MPPatcher")
        self.sidebar_brand = brand
        brand.setObjectName("sidebarBrand")
        brand.setStyleSheet(f"font-size: 18px; font-weight: 750; color: {TEXT};")
        lay.addWidget(brand)
        sub = QLabel("Modern Physique Patcher")
        self.sidebar_subtitle = sub
        sub.setObjectName("sidebarSubtitle")
        sub.setStyleSheet(f"font-size: 11px; color: {TEXT_DIM};")
        lay.addWidget(sub)
        lay.addSpacing(14)
        self._nav_buttons = []
        for index, (label, icon) in enumerate((("Patch", "⚒"), ("History / Backups", "◷"),
                                                ("Settings", "⚙"))):
            button = QPushButton(f"{icon}  {label}")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setProperty("navButton", True)
            button.clicked.connect(lambda checked=False, i=index: self._show_page(i))
            lay.addWidget(button)
            self._nav_buttons.append(button)
        # Replace the prototype's decorative glyph labels with proper SVG icons.
        brand.setText("MPPatcher")
        for button, label, icon in zip(
                self._nav_buttons,
                ("Patch", "History / Backups", "Settings"),
                ("tshirt", "restore", "cog")):
            button.setText(label)
            button.setIcon(make_icon(icon, 16, TEXT_DIM))
        lay.addStretch()
        status = QLabel("Female / Male\nRM_Heavy · RM_Muscular · RM_Thin")
        status.setText("Female / Male\nRM_Heavy / RM_Muscular / RM_Thin")
        appearance = QHBoxLayout()
        appearance.setContentsMargins(0, 0, 0, 0)
        appearance.setSpacing(4)
        appearance.addWidget(QLabel("Appearance"))
        appearance.addStretch()
        self.sidebar_dark_button = QPushButton()
        self.sidebar_dark_button.setObjectName("themeButton")
        self.sidebar_dark_button.setProperty("themeIcon", "weather-night")
        self.sidebar_dark_button.setCheckable(True)
        self.sidebar_dark_button.setFixedSize(34, 30)
        self.sidebar_dark_button.setToolTip("Use the dark theme")
        self.sidebar_dark_button.clicked.connect(lambda: self._set_theme_mode("dark"))
        self.sidebar_light_button = QPushButton()
        self.sidebar_light_button.setObjectName("themeButton")
        self.sidebar_light_button.setProperty("themeIcon", "weather-sunny")
        self.sidebar_light_button.setCheckable(True)
        self.sidebar_light_button.setFixedSize(34, 30)
        self.sidebar_light_button.setToolTip("Use the light theme")
        self.sidebar_light_button.clicked.connect(lambda: self._set_theme_mode("light"))
        appearance.addWidget(self.sidebar_light_button)
        appearance.addWidget(self.sidebar_dark_button)
        lay.addLayout(appearance)
        self.sidebar_status = status
        status.setObjectName("sidebarStatus")
        status.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        lay.addWidget(status)
        return lay

    def _show_page(self, index: int):
        self._close_reveal_popover()
        self.page_stack.setCurrentIndex(index)
        for i, button in enumerate(self._nav_buttons):
            button.setChecked(i == index)
        if index == 1:
            self._refresh_history_list()

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 8, 18, 8)
        title = QLabel("History / Backups")
        title.setObjectName("section")
        lay.addWidget(title)
        lay.addWidget(_dim("Restore a saved backup without cluttering the patch workflow."))
        self.history_backup_list = QListWidget()
        self.backup_list = self.history_backup_list
        lay.addWidget(self.history_backup_list, 1)
        restore = QPushButton("Restore Selected Backup")
        restore.clicked.connect(self._on_restore_history)
        lay.addWidget(restore)
        return page

    def _refresh_history_list(self):
        if not hasattr(self, "history_backup_list"):
            return
        self.history_backup_list.clear()
        folder = self.backup_edit.text().strip() or self.config.backup_folder
        if not folder:
            return
        for entry in backup_manager.list_backups(folder):
            item = QListWidgetItem(f"{entry.timestamp}  —  {entry.file_count} files")
            item.setData(Qt.UserRole, entry.zip_path)
            self.history_backup_list.addItem(item)

    def _on_restore_history(self):
        item = self.history_backup_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if QMessageBox.question(self, "Restore Backup", f"Restore this backup?\n\n{path}",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            if backup_manager.restore_backup(path, [self.config.app_dir]):
                self._log_success("Backup restored.")
            else:
                self._log_error("Backup restore failed.")

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(14)
        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        lay.addWidget(title)
        lay.addWidget(_dim("Paths and tool detection used by the public, output-only pipeline."))
        self.settings_bs_edit = QLineEdit()
        self.settings_output_edit = QLineEdit()
        self.settings_backup_edit = QLineEdit()
        for field in (self.settings_bs_edit, self.settings_output_edit, self.settings_backup_edit):
            field.setMaximumWidth(820)
        # These aliases keep the existing scanner/patch code independent from
        # the page on which the settings are presented.
        self.bs_path_edit = self.settings_bs_edit
        self.backup_edit = self.settings_backup_edit
        self.lbl_bs_found = QLabel()
        self.lbl_sg_found = QLabel()
        self.lbl_os_found = QLabel()
        appearance_card = _card()
        appearance_lay = QVBoxLayout(appearance_card)
        appearance_lay.setContentsMargins(14, 12, 14, 12)
        appearance_lay.addWidget(QLabel("Appearance", objectName="section"))
        appearance_lay.addWidget(_dim("Choose Light, Dark, or follow the Windows system colour scheme."))
        appearance_row = QHBoxLayout()
        self.theme_mode_buttons = {}
        for mode, label, icon in (("light", "Light", "weather-sunny"),
                                  ("dark", "Dark", "weather-night"),
                                  ("system", "System", "monitor")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("themeIcon", icon)
            button.setIcon(make_icon(icon, 16, TEXT_DIM))
            button.clicked.connect(lambda checked=False, value=mode: self._set_theme_mode(value))
            appearance_row.addWidget(button)
            self.theme_mode_buttons[mode] = button
        appearance_row.addStretch()
        appearance_lay.addLayout(appearance_row)
        lay.addWidget(appearance_card)
        paths_card = _card()
        paths_lay = QVBoxLayout(paths_card)
        paths_lay.setContentsMargins(14, 12, 14, 12)
        paths_lay.addWidget(QLabel("Tool paths", objectName="section"))
        for label, field, browse_key in (("BodySlide directory", self.settings_bs_edit, "bodyslide_path"),
                                         ("Default output directory", self.settings_output_edit, "output_folder"),
                                         ("Backup directory", self.settings_backup_edit, "backup_folder")):
            row = QHBoxLayout()
            row.addWidget(_dim(label))
            field.setText(getattr(self.config, browse_key))
            row.addWidget(field, 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _, edit=field, key=browse_key:
                                   self._browse_folder(edit, key))
            row.addWidget(browse)
            paths_lay.addLayout(row)
        lay.addWidget(paths_card)
        # Settings is a real alternate entry point, not a read-only duplicate of
        # the fields in the patch page.  Keep both views synchronized as soon as
        # the user leaves a field, including the persisted QSettings value.
        self.settings_bs_edit.editingFinished.connect(
            lambda: self._sync_settings_field(self.settings_bs_edit, self.bs_path_edit,
                                               "bodyslide_path", self._on_bs_path_changed))
        self.settings_output_edit.editingFinished.connect(
            lambda: self._sync_settings_field(self.settings_output_edit, self.output_edit,
                                               "output_folder"))
        self.settings_backup_edit.editingFinished.connect(
            lambda: self._sync_settings_field(self.settings_backup_edit, self.backup_edit,
                                               "backup_folder"))
        status_card = _card()
        status_lay = QVBoxLayout(status_card)
        status_lay.setContentsMargins(14, 12, 14, 12)
        status_lay.addWidget(QLabel("Tool status", objectName="section"))
        status_lay.addWidget(self.lbl_bs_found)
        status_lay.addWidget(self.lbl_sg_found)
        status_lay.addWidget(self.lbl_os_found)
        lay.addWidget(status_card)
        lay.addStretch()
        return page

    def _sync_settings_field(self, source: QLineEdit, target: QLineEdit,
                             config_key: str, callback=None):
        value = source.text().strip()
        # Settings owns the default *folder* only.  Do not replace an active
        # ZIP destination in the patch workflow when that default is edited.
        if not (config_key == "output_folder" and self._output_mode() == "zip"):
            target.setText(value)
        setattr(self.config, config_key, value)
        if callback:
            callback()
        if config_key in {"output_folder", "backup_folder"}:
            self._refresh_backup_list()

    # ── LEFT ──────────────────────────────────────────────────────────
    def _build_left(self) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setSpacing(10)
        lay.addWidget(self._build_workflow_card())
        lay.addWidget(self._build_bs_card())
        lay.addWidget(self._build_groups_card(), 1)
        lay.addWidget(self._build_output_card())
        lay.addWidget(self._build_progress_card())
        return lay

    def _build_workflow_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        title = QLabel("Patch target and input")
        title.setObjectName("section")
        lay.addWidget(title)
        row = QHBoxLayout()
        self.target_outfits_btn = QPushButton("Outfits and Armors\nFemale / Male")
        self.target_outfits_btn.setCheckable(True)
        self.target_outfits_btn.setChecked(True)
        self.target_outfits_btn.setToolTip("Supported: BodySlide SliderGroups and OSP projects.")
        self.target_headparts_btn = QPushButton("Headparts\nHair, brows and beards")
        self.target_headparts_btn.setEnabled(False)
        self.target_headparts_btn.setToolTip(
            "Headpart patching is not implemented by the current backend.")
        row.addWidget(self.target_outfits_btn)
        row.addWidget(self.target_headparts_btn)
        lay.addLayout(row)
        source_row = QHBoxLayout()
        self.source_bodyslide_btn = QPushButton("BodySlide Groups")
        self.source_bodyslide_btn.setCheckable(True)
        self.source_bodyslide_btn.setChecked(True)
        self.source_bodyslide_btn.setToolTip(
            "Supported input: installed BodySlide SliderGroups.")
        self.source_zip_btn = QPushButton("ZIP Archive")
        self.source_folder_btn = QPushButton("Local Folder")
        for button in (self.source_zip_btn, self.source_folder_btn):
            button.setEnabled(False)
            button.setToolTip(
                "This input source is reserved for a future backend; no fake scan "
                "or patch action is exposed.")
        source_row.addWidget(self.source_bodyslide_btn)
        source_row.addWidget(self.source_zip_btn)
        source_row.addWidget(self.source_folder_btn)
        lay.addLayout(source_row)
        return card

    def _build_bs_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title = QLabel("BodySlide Installation")
        title.setObjectName("section")
        lay.addWidget(title)

        # BodySlide path row
        bs_row = QHBoxLayout()
        bs_row.addWidget(_dim("BodySlide path:"))
        self.bs_path_edit = QLineEdit()
        self.bs_path_edit.setPlaceholderText("…\\CalienteTools\\BodySlide")
        self.bs_path_edit.editingFinished.connect(self._on_bs_path_changed)
        bs_row.addWidget(self.bs_path_edit, 1)
        btn = QPushButton("Browse…")
        btn.setIcon(make_icon("folder-open", 14, ACCENT))
        btn.setFixedWidth(95)
        btn.clicked.connect(self._browse_bs_path)
        bs_row.addWidget(btn)
        lay.addLayout(bs_row)

        # Status badges
        self._status_row = QHBoxLayout()
        self._status_row.setSpacing(16)
        self.lbl_bs_found = QLabel()
        self.lbl_sg_found = QLabel()
        self.lbl_os_found = QLabel()
        for lbl in (self.lbl_bs_found, self.lbl_sg_found, self.lbl_os_found):
            self._status_row.addWidget(lbl)
        self._status_row.addStretch()
        lay.addLayout(self._status_row)
        return card

    def _build_groups_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        header = QHBoxLayout()
        self.groups_card_title = QLabel("Outfits")
        title = self.groups_card_title
        title.setObjectName("section")
        header.addWidget(title)
        header.addStretch()
        self.lbl_scan_status = QLabel("Scanning…")
        self.lbl_scan_status.setObjectName("dim")
        header.addWidget(self.lbl_scan_status)
        self.lbl_selected = QLabel("0 selected")
        self.lbl_selected.setObjectName("dim")
        header.addWidget(self.lbl_selected)
        lay.addLayout(header)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search projects or outfits…")
        self.search_edit.textChanged.connect(self._filter_groups)
        lay.addWidget(self.search_edit)

        group_filter_row = QHBoxLayout()
        group_filter_row.setSpacing(8)
        self.body_group_filter_label = _dim("BodySlide group filter")
        group_filter_row.addWidget(self.body_group_filter_label)
        self.body_group_filter_combo = QComboBox()
        self.body_group_filter_combo.setToolTip(
            "Filters outfits by their literal SliderGroups XML membership. "
            "It never uses gender, body family, or mesh detection.")
        self.body_group_filter_combo.currentIndexChanged.connect(
            self._on_body_group_filter_changed)
        group_filter_row.addWidget(self.body_group_filter_combo, 1)
        lay.addLayout(group_filter_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(5)
        self._status_filter = "all"
        self._filter_buttons = {}
        for key, label in (("all", "All"), ("unpatched", "Unpatched"),
                           ("patched", "Patched"), ("warning", "Needs Attention")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.setProperty("filterButton", True)
            button.setProperty("activeFilter", key == "all")
            button.clicked.connect(lambda checked=False, value=key:
                                   self._set_status_filter(value))
            filter_row.addWidget(button)
            self._filter_buttons[key] = button
        filter_row.addStretch()
        lay.addLayout(filter_row)

        self.group_tree = QTreeWidget()
        self.group_tree.setColumnCount(6)
        self.group_tree.setHeaderHidden(False)
        self.group_tree.setHeaderLabels(
            ["Outfit", "Sex", "Part", "Patch Status", "Revealing", "Smart refit"])
        hdr = self.group_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COL_NAME,   QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_GENDER, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_MESH,   QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_REVEAL, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_SMART,  QHeaderView.Fixed)
        hdr.resizeSection(COL_GENDER, 82)
        hdr.resizeSection(COL_MESH,   64)
        # Keep the full "MPP Patched" header visible even when Revealing is shown.
        # The extra padding also prevents the check glyph from feeling cramped.
        hdr.resizeSection(COL_STATUS, 170)
        hdr.resizeSection(COL_REVEAL, 132)
        hdr.resizeSection(COL_SMART,  132)
        hdr.setSectionsClickable(True)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.group_tree.headerItem().setTextAlignment(COL_STATUS, Qt.AlignLeft | Qt.AlignVCenter)
        # Match the centered checkbox/text treatment in the Revealing column.
        self.group_tree.headerItem().setTextAlignment(COL_REVEAL, Qt.AlignCenter)
        self.group_tree.headerItem().setTextAlignment(COL_SMART, Qt.AlignCenter)
        hdr.sectionClicked.connect(self._on_header_clicked)
        self.group_tree.headerItem().setToolTip(
            COL_STATUS,
            "Patched means all RM sliders are present. Partially patched means only "
            "some RM sliders are present. Detection details are reviewed in the next step.")
        self.group_tree.headerItem().setToolTip(
            COL_REVEAL,
            "Revealing selects the detailed morph for outfits that expose skin.")
        # The information button is positioned beside the header text rather
        # than at the far edge of the section, so it remains visually attached
        # when the table is resized or horizontally scrolled.
        self.reveal_info_button = QToolButton(hdr)
        self.reveal_info_button.setObjectName("revealInfoButton")
        self.reveal_info_button.setFixedSize(24, 24)
        self.reveal_info_button.setAutoRaise(True)
        self.reveal_info_button.setFocusPolicy(Qt.StrongFocus)
        self.reveal_info_button.setAccessibleName("About Revealing outfits")
        self.reveal_info_button.setAccessibleDescription(
            "Open contextual help explaining Covered and Revealing morphs.")
        self.reveal_info_button.clicked.connect(self._toggle_reveal_popover)
        self._set_reveal_info_icon()
        hdr.sectionResized.connect(lambda *_: self._position_reveal_help())
        if hasattr(hdr, "geometriesChanged"):
            hdr.geometriesChanged.connect(self._position_reveal_help)
        self.group_tree.horizontalScrollBar().valueChanged.connect(
            lambda *_: self._position_reveal_help())
        self._sort_col = -1
        self._sort_order = Qt.AscendingOrder
        self.group_tree.setSelectionMode(QTreeWidget.NoSelection)
        self.group_tree.setIndentation(16)
        for column in FLAG_COLUMNS:
            self.group_tree.setItemDelegateForColumn(
                column, FlagDelegate(self.group_tree, column))
        self.group_tree.itemChanged.connect(self._on_tree_item_changed)
        self.group_tree.itemClicked.connect(self._on_tree_item_clicked)
        # Step 2 deliberately contains only outfit selection and patch status.
        # Detection and the per-outfit flags belong to Review & Patch.
        for column in (COL_GENDER, COL_MESH, *FLAG_COLUMNS):
            self.group_tree.setColumnHidden(column, True)
        lay.addWidget(self.group_tree, 1)

        self._scan_data: dict[str, OutfitInfo] = {}
        self._scan_worker: ScanWorker | None = None

        btn_row = QHBoxLayout()
        btn_all  = QPushButton("Select All")
        btn_all.clicked.connect(self._select_all_groups)
        btn_none = QPushButton("Select None")
        btn_none.clicked.connect(self._select_no_groups)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self.reveal_popover = self._build_reveal_popover()
        self.reveal_info_button.hide()
        return card

    def _position_reveal_help(self):
        """Anchor the compact help button beside the Revealing header."""
        if not hasattr(self, "reveal_info_button") or not hasattr(self, "group_tree"):
            return
        if self.group_tree.isColumnHidden(COL_REVEAL):
            self.reveal_info_button.hide()
            return
        hdr = self.group_tree.header()
        width = self.group_tree.columnWidth(COL_REVEAL)
        text_width = hdr.fontMetrics().horizontalAdvance("Revealing")
        spacing = 7
        total_width = text_width + spacing + self.reveal_info_button.width()
        x = hdr.sectionViewportPosition(COL_REVEAL) + max(0, (width - total_width) // 2)
        x += text_width + spacing
        y = max(0, (hdr.height() - self.reveal_info_button.height()) // 2)
        self.reveal_info_button.move(x, y)
        self.reveal_info_button.setVisible(
            x + self.reveal_info_button.width() > 0 and x < hdr.width())
        self.reveal_info_button.raise_()

    def _build_reveal_popover(self) -> QFrame:
        popup = RevealPopover(self)
        popup.setFixedWidth(348)
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(9)

        title_row = QHBoxLayout()
        self.reveal_popup_icon = QLabel()
        self.reveal_popup_icon.setFixedSize(20, 20)
        title_row.addWidget(self.reveal_popup_icon)
        title = QLabel("Revealing Outfits")
        title.setObjectName("revealPopoverTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        lay.addLayout(title_row)

        intro = QLabel("Choose which body morph is used for outfits that expose skin.")
        intro.setObjectName("revealPopoverBody")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addWidget(self._reveal_popover_divider())

        off_row = QHBoxLayout()
        self.reveal_popup_off_icon = QLabel()
        self.reveal_popup_off_icon.setFixedSize(20, 20)
        off_row.addWidget(self.reveal_popup_off_icon, 0, Qt.AlignTop)
        off_text = QVBoxLayout()
        off_heading = QLabel("Off  ·  Covered Outfit")
        off_heading.setObjectName("revealPopoverHeading")
        off_heading.setText("Off \u2014 Covered Outfit")
        off_body = QLabel(
            "Uses a smoother body morph to minimize conforming distortions underneath clothing.")
        off_body.setObjectName("revealPopoverBody")
        off_body.setWordWrap(True)
        off_text.addWidget(off_heading)
        off_text.addWidget(off_body)
        off_row.addLayout(off_text, 1)
        lay.addLayout(off_row)

        on_row = QHBoxLayout()
        self.reveal_popup_on_icon = QLabel()
        self.reveal_popup_on_icon.setFixedSize(20, 20)
        on_row.addWidget(self.reveal_popup_on_icon, 0, Qt.AlignTop)
        on_text = QVBoxLayout()
        on_heading = QLabel("On  ·  Revealing Outfit")
        on_heading.setObjectName("revealPopoverHeading")
        on_heading.setText("On \u2014 Revealing Outfit")
        on_body = QLabel(
            "Uses a more detailed morph that preserves skin folds, navel definition, and cellulite.")
        on_body.setObjectName("revealPopoverBody")
        on_body.setWordWrap(True)
        on_text.addWidget(on_heading)
        on_text.addWidget(on_body)
        on_row.addLayout(on_text, 1)
        lay.addLayout(on_row)

        lay.addWidget(self._reveal_popover_divider())
        recommendation_row = QHBoxLayout()
        self.reveal_popup_recommendation_icon = QLabel()
        self.reveal_popup_recommendation_icon.setFixedSize(20, 20)
        recommendation_row.addWidget(self.reveal_popup_recommendation_icon, 0, Qt.AlignTop)
        recommendation = QLabel(
            "Enable for outfits that expose substantial skin. Leave disabled for most covered clothing and armor.")
        recommendation.setObjectName("revealPopoverRecommendation")
        recommendation.setWordWrap(True)
        recommendation_row.addWidget(recommendation, 1)
        lay.addLayout(recommendation_row)
        self._refresh_reveal_popover_theme()
        return popup

    @staticmethod
    def _reveal_popover_divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("revealPopoverDivider")
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Plain)
        divider.setFixedHeight(1)
        return divider

    def _build_output_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        title = QLabel("Output Mode")
        title.setObjectName("section")
        lay.addWidget(title)

        self.radio_new = QRadioButton("Create New Projects  (prefix: MPP_)")
        self.radio_new.setChecked(True)
        self.radio_overwrite = QRadioButton("Overwrite Existing Projects")
        self.radio_overwrite.setEnabled(False)
        self.radio_overwrite.setToolTip(
            "Unavailable in the public pipeline. The source project is always left "
            "untouched and results are written to the output data root."
        )
        new_mode_row = QHBoxLayout()
        new_mode_row.addWidget(self.radio_new)
        recommended = QLabel("Recommended")
        recommended.setStyleSheet(
            f"color: {SUCCESS}; background-color: #12351f; border: 1px solid #245c35; "
            "border-radius: 8px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        new_mode_row.addWidget(recommended)
        new_mode_row.addStretch()
        lay.addLayout(new_mode_row)
        lay.addWidget(self.radio_overwrite)
        self.radio_overwrite.clicked.connect(self._confirm_overwrite_mode)

        patch_options = QLabel("Patch Options")
        patch_options.setObjectName("section")
        lay.addWidget(patch_options)

        self.auto_zap_check = QCheckBox(
            "Enable Auto-Zap  (Generate Zap Slider to fix clipping)"
        )
        self.auto_zap_check.setChecked(True)
        self.auto_zap_check.setToolTip(
            "Analyzes the base outfit, open garment edges, and RM morphs. Disable "
            "this only when you want to author the Fix Clipping zap manually."
        )
        lay.addWidget(self.auto_zap_check)
        self.auto_zap_check.toggled.connect(self._update_auto_zap_visibility)
        self._update_auto_zap_visibility(self.auto_zap_check.isChecked())

        self.reduce_clipping_check = QCheckBox("Reduce clipping")
        self.reduce_clipping_check.setToolTip(
            "Runs BodySlide/Outfit Studio's native Fix Clipping pass over RM_Heavy, "
            "RM_Muscular and RM_Thin. It can move intentional internal geometry."
        )
        lay.addWidget(self.reduce_clipping_check)
        clip_strength_row = QHBoxLayout()
        clip_strength_row.addWidget(_dim("Fix Clipping strength"))
        self.fix_clipping_strength = QDoubleSpinBox()
        self.fix_clipping_strength.setRange(0.0, 1.0)
        self.fix_clipping_strength.setSingleStep(0.05)
        self.fix_clipping_strength.setDecimals(2)
        self.fix_clipping_strength.setValue(0.5)
        self.fix_clipping_strength.setToolTip("Native Fix Clipping inflation strength, from 0 to 1.")
        clip_strength_row.addWidget(self.fix_clipping_strength)
        clip_strength_row.addStretch()
        self._clip_strength_row = QWidget()
        self._clip_strength_row.setLayout(clip_strength_row)
        lay.addWidget(self._clip_strength_row)
        self.reduce_clipping_check.toggled.connect(self._update_fix_clipping_visibility)
        self._update_fix_clipping_visibility(False)

        # Backup options (only relevant for overwrite)
        bk_box = QFrame()
        bk_lay = QVBoxLayout(bk_box)
        bk_lay.setContentsMargins(0, 0, 0, 0)
        bk_lay.setSpacing(6)

        self.backup_check = QCheckBox("Generate backup before overwriting")
        self.backup_check.setChecked(True)
        bk_lay.addWidget(self.backup_check)

        bk_folder_row = QHBoxLayout()
        bk_folder_row.addWidget(_dim("Backup Folder"))
        self.backup_edit = QLineEdit()
        self.backup_edit.setPlaceholderText("Backup folder path…")
        bk_folder_row.addWidget(self.backup_edit, 1)
        bk_browse = QPushButton("Browse…")
        bk_browse.setIcon(make_icon("folder-open", 14, ACCENT))
        bk_browse.clicked.connect(lambda: self._browse_folder(self.backup_edit, "backup_folder"))
        bk_folder_row.addWidget(bk_browse)
        bk_lay.addLayout(bk_folder_row)

        lay.addWidget(bk_box)
        self._backup_box = bk_box

        out_folder_row = QHBoxLayout()
        out_folder_row.addWidget(_dim("Output Folder"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Auto: …/CalienteTools/MPPatcher Output (separate data root)")
        out_folder_row.addWidget(self.output_edit, 1)
        out_browse = QPushButton("Browse…")
        out_browse.setIcon(make_icon("folder-open", 14, ACCENT))
        out_browse.clicked.connect(lambda: self._browse_folder(self.output_edit, "output_folder"))
        out_folder_row.addWidget(out_browse)
        self._out_folder_row_widget = QWidget()
        QHBoxLayout(self._out_folder_row_widget).addLayout(out_folder_row)
        self._out_folder_row_widget.layout().setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._out_folder_row_widget)

        # Connect and run AFTER both _backup_box and _out_folder_row_widget exist
        self.radio_new.toggled.connect(self._update_output_visibility)
        self._update_output_visibility()

        return card

    def _update_auto_zap_visibility(self, enabled: bool):
        """Keep Revealing available independently of the Auto-Zap toggle."""
        if hasattr(self, "group_tree"):
            self.group_tree.setColumnHidden(COL_REVEAL, False)
        if hasattr(self, "reveal_info_button"):
            self.reveal_info_button.setVisible(True)
            self._position_reveal_help()

    def _update_fix_clipping_visibility(self, enabled: bool):
        self._clip_strength_row.setVisible(enabled)
        self.fix_clipping_strength.setEnabled(enabled)

    def _confirm_overwrite_mode(self, checked: bool):
        """Require an explicit user confirmation before enabling destructive output."""
        if not checked:
            return
        answer = QMessageBox.warning(
            self,
            "Overwrite existing projects?",
            "Overwrite mode modifies the existing BodySlide .osp/.osd projects in place.\n\n"
            "Manual slider work can be replaced, and recovery depends on having a valid "
            "backup. Create New Projects is recommended for normal use.\n\n"
            "Continue with overwrite mode?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            self.radio_new.setChecked(True)

    def _build_progress_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel("Progress")
        title.setObjectName("section")
        hdr.addWidget(title)
        hdr.addStretch()
        self.lbl_progress_pct = QLabel("0%")
        self.lbl_progress_pct.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        hdr.addWidget(self.lbl_progress_pct)
        self.lbl_elapsed = QLabel("Elapsed: 00:00:00")
        self.lbl_elapsed.setObjectName("dim")
        hdr.addWidget(self.lbl_elapsed)
        lay.addLayout(hdr)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        lay.addWidget(self.progress_bar)

        detail_row = QHBoxLayout()
        self.lbl_current_outfit = QLabel("")
        self.lbl_current_outfit.setStyleSheet(f"color: {ACCENT};")
        self.lbl_current_step = QLabel("")
        self.lbl_current_step.setStyleSheet(f"color: {ACCENT};")
        detail_row.addWidget(_dim("Current Outfit:"))
        detail_row.addWidget(self.lbl_current_outfit)
        detail_row.addStretch()
        detail_row.addWidget(_dim("Current Step:"))
        detail_row.addWidget(self.lbl_current_step)
        lay.addLayout(detail_row)
        self.lbl_result_summary = QLabel("Processed 0  ·  Skipped 0  ·  Failed 0  ·  Warnings 0")
        self.lbl_result_summary.setObjectName("dim")
        lay.addWidget(self.lbl_result_summary)
        return card

    # ── RIGHT ─────────────────────────────────────────────────────────
    def _build_right(self) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.setSpacing(10)
        lay.addWidget(self._build_overview_card())
        lay.addWidget(self._build_actions_card())
        lay.addWidget(self._build_log_card(), 1)
        return lay

    def _build_overview_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        title = QLabel("Overview")
        title.setObjectName("section")
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.stat_outfits   = StatCard("format-list",   "0",       "Total Outfits",   ACCENT)
        self.stat_groups    = StatCard("account-group", "0",       "Groups Selected", SUCCESS)
        self.stat_processed = StatCard("checkbox-outline", "0",    "Processed",       TEXT)
        self.stat_elapsed   = StatCard("clock-outline", "0:00:00", "Elapsed Time",    WARNING)
        grid.addWidget(self.stat_outfits,   0, 0)
        grid.addWidget(self.stat_groups,    0, 1)
        grid.addWidget(self.stat_processed, 1, 0)
        grid.addWidget(self.stat_elapsed,   1, 1)
        lay.addLayout(grid)
        return card

    def _build_actions_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        self.patch_btn = QPushButton("  Start Patching")
        self.patch_btn.setObjectName("primary")
        self.patch_btn.setMinimumHeight(44)
        self.patch_btn.setIcon(make_icon("play", 18, "#0d1117"))
        self.patch_btn.setIconSize(QSize(18, 18))
        self.patch_btn.clicked.connect(self._on_patch)
        lay.addWidget(self.patch_btn)

        self.stop_btn = QPushButton("  Stop Patching")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.setIcon(make_icon("pause", 18, ERROR))
        self.stop_btn.setIconSize(QSize(18, 18))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        lay.addWidget(self.stop_btn)

        # Restore backup section
        lay.addWidget(_sep())
        lay.addWidget(_dim("Restore Backup"))
        self.backup_list = QListWidget()
        self.backup_list.setMaximumHeight(80)
        lay.addWidget(self.backup_list)
        restore_btn = QPushButton("Restore Selected")
        restore_btn.clicked.connect(self._on_restore)
        lay.addWidget(restore_btn)
        return card

    def _build_log_card(self) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel("Activity Log")
        title.setObjectName("section")
        hdr.addWidget(title)
        hdr.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setIcon(make_icon("trash-can", 14, TEXT_DIM))
        clear_btn.setMinimumWidth(84)
        clear_btn.clicked.connect(self.log_box.clear if hasattr(self, "log_box") else lambda: None)
        hdr.addWidget(clear_btn)
        lay.addLayout(hdr)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        clear_btn.clicked.connect(self.log_box.clear)
        lay.addWidget(self.log_box, 1)
        return card

    # ──────────────────────────────────────────────────────────────────
    # State / load
    # ──────────────────────────────────────────────────────────────────
    def _default_output_folder(self) -> str:
        """Safe sibling output root; the public pipeline never overwrites source data."""
        root = self._source_input_root or self.config.bodyslide_root
        if root:
            # Keep generated data outside the virtual BodySlide source root.  A
            # child such as ``BodySlide/MPPatcher Output`` is still inside the
            # source tree and is easy to mistake for an overwrite target.
            return os.path.join(os.path.dirname(root),
                                "MPPatcher Output")
        return ""

    def _default_output_archive(self) -> str:
        """Default ZIP beside the safe folder destination, never inside its tree."""
        root = self._source_input_root or self.config.bodyslide_root
        if root:
            return os.path.join(os.path.dirname(root), "MPPatcher Output.zip")
        return ""

    def _output_mode(self) -> str:
        if not hasattr(self, "_output_mode_value"):
            return "folder"
        return self._output_mode_value

    def _set_output_mode(self, mode: str) -> None:
        mode = "zip" if mode == "zip" else "folder"
        changed = mode != self._output_mode()
        self._output_mode_value = mode
        if hasattr(self, "output_mode_folder_btn"):
            self.output_mode_folder_btn.setChecked(mode == "folder")
            self.output_mode_zip_btn.setChecked(mode == "zip")
        if changed:
            self._on_output_mode_changed()

    def _refresh_output_destination_ui(self) -> None:
        """Make the single destination field accurately describe its current mode."""
        is_archive = self._output_mode() == "zip"
        self.output_destination_label.setText("Output")
        self.output_edit.setPlaceholderText(
            "Mod-ready ZIP archive (.zip)" if is_archive else "Separate output data root")
        self.output_edit.setToolTip(
            "Generated files are first isolated, then packed into this ZIP. "
            "The source BodySlide projects remain unchanged."
            if is_archive else
            "Patched projects are written here; the source BodySlide projects remain unchanged.")
        self.output_browse_btn.setText("Save as…" if is_archive else "Choose…")
        self.output_browse_btn.setIcon(make_icon(
            "archive" if is_archive else "folder-open", 14, ACCENT))

    def _on_output_mode_changed(self) -> None:
        mode = self._output_mode()
        self.config.output_mode = mode
        value = (self.config.output_archive or self._default_output_archive()) \
            if mode == "zip" else (self.config.output_folder or self._default_output_folder())
        self.output_edit.setText(value)
        self._refresh_output_destination_ui()

    def _browse_output_destination(self) -> None:
        if self._output_mode() != "zip":
            self._browse_folder(self.output_edit, "output_folder")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save patched mod archive", self.output_edit.text().strip(),
            "ZIP archives (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self.output_edit.setText(path)
        self._on_output_destination_changed()

    def _prepare_output_destination(self, source_root: str) -> tuple[str, str]:
        """Return CLI output directory and optional final ZIP path for one run."""
        destination = self.output_edit.text().strip()
        if self._output_mode() != "zip":
            out_dir = destination or self._default_output_folder()
            if not out_dir or self._paths_overlap(source_root, out_dir):
                raise ValueError(
                    "Choose a separate output folder. The public pipeline never writes "
                    "into or below the BodySlide source directory.")
            return out_dir, ""

        archive_path = destination or self._default_output_archive()
        if not archive_path:
            raise ValueError("Choose where to save the ZIP archive.")
        if not archive_path.lower().endswith(".zip"):
            archive_path += ".zip"
        archive_path = os.path.abspath(archive_path)
        source_path = os.path.abspath(self._source_path or source_root)
        if os.path.normcase(archive_path) == os.path.normcase(source_path):
            raise ValueError("The ZIP archive cannot replace the selected source archive.")
        if os.path.isdir(archive_path):
            raise ValueError("The ZIP destination points to a folder, not an archive file.")
        if os.path.exists(archive_path):
            answer = QMessageBox.question(
                self, "Replace existing ZIP?",
                f"The archive already exists:\n{archive_path}\n\nReplace it after patching succeeds?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                raise ValueError("ZIP archive replacement was cancelled.")
        staging = tempfile.mkdtemp(prefix="mppatcher_output_")
        self._archive_staging_dir = staging
        return staging, archive_path

    def _cleanup_archive_staging(self) -> None:
        staging = self._archive_staging_dir
        self._archive_staging_dir = ""
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _paths_overlap(source: str, target: str) -> bool:
        """Return true when either normalized path contains the other.

        ``commonpath`` rejects different Windows drives.  That is precisely
        the safe and supported case for an output folder, not an overlap.
        """
        if not source or not target:
            return True
        # ``find_bodyslide_root`` may return pathlib paths while fields return
        # strings. Normalise the public helper before using regex/path APIs.
        source = os.fspath(source)
        target = os.fspath(target)
        windows_path = (bool(re.match(r"^[A-Za-z]:[\\/]", source)) or
                        bool(re.match(r"^[A-Za-z]:[\\/]", target)) or
                        source.startswith("\\\\") or target.startswith("\\\\"))
        pathmod = ntpath if windows_path else os.path
        try:
            source = pathmod.normcase(pathmod.normpath(pathmod.abspath(source)))
            target = pathmod.normcase(pathmod.normpath(pathmod.abspath(target)))
            return pathmod.commonpath((source, target)) in {source, target}
        except ValueError:
            # Different drives or UNC shares cannot contain one another.
            return False
        except OSError:
            return True

    def _load_state(self):
        self.bs_path_edit.setText(self.config.bodyslide_path)
        self._set_output_mode(self.config.output_mode)
        # Auto-fill output folder if never manually set
        saved_out = (self.config.output_archive if self._output_mode() == "zip"
                     else self.config.output_folder)
        configured_root = self.config.bodyslide_root
        if self._output_mode() != "zip" and saved_out and configured_root and os.path.normcase(os.path.abspath(saved_out)) == \
                os.path.normcase(os.path.abspath(configured_root)):
            saved_out = ""
        # A previous UI build could persist a destination inside BodySlide
        # (for example its SliderSets directory).  Do not present an unsafe
        # default and wait for Start Patching to reject it.
        source_root = find_bodyslide_root(self.config.bodyslide_path) or configured_root
        if self._output_mode() != "zip" and saved_out and source_root and self._paths_overlap(source_root, saved_out):
            self._log_warning("Saved output folder was inside the BodySlide source and was reset to a safe location.")
            saved_out = ""
            self.config.output_folder = self._default_output_folder()
        default_destination = (self._default_output_archive() if self._output_mode() == "zip"
                               else self._default_output_folder())
        self.output_edit.setText(saved_out if saved_out else default_destination)
        self._refresh_output_destination_ui()
        self.backup_edit.setText(self.config.backup_folder)
        self.radio_new.setChecked(True)
        self.radio_overwrite.setChecked(False)
        self.backup_check.setChecked(bool(self.config.backup_on_overwrite))
        self.auto_zap_check.setChecked(bool(self.config.auto_zap))
        self.reduce_clipping_check.setChecked(bool(self.config.reduce_clipping))
        self.fix_clipping_strength.setValue(float(self.config.fix_clipping_strength))
        if hasattr(self, "settings_bs_edit"):
            self.settings_bs_edit.setText(self.config.bodyslide_path)
            self.settings_output_edit.setText(self.config.output_folder)
            self.settings_backup_edit.setText(self.config.backup_folder)
        self._update_status_badges()
        # Let the window paint before opening and enumerating a large MO2 VFS.
        # The scan still begins automatically, but startup remains responsive.
        self.configure_source_status.setText("Loading BodySlide projects...")
        QTimer.singleShot(150, self._reload_initial_groups)
        # Backup discovery opens every matching archive to read its manifest.
        # Keep that work out of the constructor so the first window paint is
        # not delayed by an old, potentially large backup directory.
        QTimer.singleShot(0, self._refresh_backup_list)
        self._log_info("MPPatcher v0.2.0 initialized")
        self._log_info(f"BodySlide root: {self.config.bodyslide_root}")
        self._log_info(f"mppatcher-cli: {self.config.mppatcher_cli_exe or '(not found)'}")


    def _save_state(self):
        self.config.bodyslide_path = self.bs_path_edit.text().strip()
        self.config.output_mode = self._output_mode()
        if self._output_mode() == "zip":
            self.config.output_archive = self.output_edit.text().strip()
        else:
            self.config.output_folder = self.output_edit.text().strip()
        self.config.backup_folder  = self.backup_edit.text().strip()
        self.config.new_project_mode    = True
        self.config.backup_on_overwrite = self.backup_check.isChecked()
        self.config.auto_zap = self.auto_zap_check.isChecked()
        self.config.reduce_clipping = self.reduce_clipping_check.isChecked()
        self.config.fix_clipping_strength = self.fix_clipping_strength.value()

    def _update_status_badges(self):
        os_exe = self.config.outfit_studio_exe
        # Check the effective root rather than the GUI's own directory. The
        # standalone executable intentionally lives outside BodySlide.
        body_root = self._resolve_bodyslide_root()
        sg_ok = vfs_isdir(os.path.join(body_root, "SliderGroups"))

        def badge(lbl, ok, text):
            symbol = "✓" if ok else "✗"
            lbl.setText(f"{symbol}  {text}")
            lbl.setStyleSheet(f"color: {SUCCESS};" if ok else f"color: {ERROR};")

        cli_ok = bool(self.config.mppatcher_cli_exe)
        cached_cli = getattr(self.config, "_cli_internal_cache", None)
        internal = bool(cli_ok and cached_cli
                       and cached_cli[0] == self.config.mppatcher_cli_exe
                       and cached_cli[1])
        body_ok = bool(body_root) and (sg_ok or vfs_isdir(os.path.join(body_root, "SliderSets")))
        badge(self.lbl_bs_found, body_ok, "BodySlide path")
        badge(self.lbl_sg_found, sg_ok, "SliderGroups")
        badge(self.lbl_os_found, cli_ok,
              "mppatcher-cli (internal)" if internal else "mppatcher-cli")
        self.lbl_os_found.setToolTip(
            "Internal build: destructive migration tools are available.\n"
            "This binary is not meant for public release."
            if internal else
            "Public build: patching only, no destructive migration tools.")
        if hasattr(self, "patch_connection_status"):
            connected = body_ok and sg_ok and cli_ok
            self.patch_connection_status.setText(
                "BodySlide connected" if connected else "BodySlide setup incomplete")
            self.patch_connection_status.setProperty("connected", connected)
            self.patch_connection_status.style().unpolish(self.patch_connection_status)
            self.patch_connection_status.style().polish(self.patch_connection_status)

        # Asking the bundled CLI for --help starts another process. It is
        # useful for the Settings diagnostics, but it should not block the
        # first frame of the main window. Run it after the event loop starts.
        if cli_ok and not (cached_cli and cached_cli[0] == self.config.mppatcher_cli_exe):
            if not getattr(self, "_cli_badge_refresh_pending", False):
                self._cli_badge_refresh_pending = True
                QTimer.singleShot(0, self._finish_cli_badge_check)

    def _finish_cli_badge_check(self):
        self._cli_badge_refresh_pending = False
        try:
            _ = self.config.cli_is_internal
        finally:
            self._update_status_badges()

    # ──────────────────────────────────────────────────────────────────
    # Groups
    # ──────────────────────────────────────────────────────────────────
    # ── Scan ──────────────────────────────────────────────────────────
    def _start_scan(self):
        root = self._effective_bodyslide_root or self._source_work_root or self.config.app_dir
        ss_dir = os.path.join(root, "SliderSets")
        if not vfs_isdir(ss_dir):
            self.lbl_scan_status.setText("No SliderSets")
            return
        self.lbl_scan_status.setText("Scanning…")
        self._scan_generation += 1
        generation = self._scan_generation
        for worker in list(self._scan_workers):
            if worker.isRunning():
                worker.requestInterruption()
        worker = ScanWorker(ss_dir, generation, self)
        worker.scanned.connect(self._on_scan_done)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(lambda: self._retire_scan_worker(worker))
        self._scan_workers.append(worker)
        self._scan_worker = worker
        worker.start()

    def _retire_scan_worker(self, worker):
        if worker in self._scan_workers:
            self._scan_workers.remove(worker)
        worker.deleteLater()

    def _on_scan_failed(self, generation: int, message: str):
        if generation == self._scan_generation:
            self.lbl_scan_status.setText("Scan failed")
            self._log_error(f"Scan failed: {message}")

    def _on_scan_done(self, generation: int, data: dict):
        if generation != self._scan_generation:
            return
        old = self._scan_data
        # SliderGroups can reference a set for which no OSP project exists in
        # the scanned source. Keep those rows visible and infer only a
        # provisional classification from their group context. They remain
        # explicitly marked as Missing project and are not patchable until an
        # actual OSP is available.
        merged = dict(data)
        for group in getattr(self, "_all_groups", []):
            for name in dict.fromkeys(group.members):
                if name in merged:
                    continue
                gender, family, confidence, evidence, warnings = classify_metadata(
                    [group.name, name])
                merged[name] = OutfitInfo(
                    name=name,
                    gender=gender,
                    body_family=family,
                    confidence=confidence,
                    evidence=evidence + [f"inferred from SliderGroup: {group.name}"],
                    warnings=warnings + ["No matching OSP project was found in the scanned SliderSets."],
                    project_found=False,
                )
        for name, info in merged.items():
            previous = old.get(name)
            if previous:
                info.gender_override = previous.gender_override
                info.mesh_override = previous.mesh_override
                info.revealing = previous.revealing
                info.smart_refit = previous.smart_refit
                info.output_reference_policy = previous.output_reference_policy
        self._scan_data = merged
        n = len(merged)
        patched = sum(1 for v in merged.values() if v.already_patched)
        self.lbl_scan_status.setText(f"{n} outfits · {patched} patched")
        self._rebuild_tree()
        self._update_stats()
        if (hasattr(self, "workflow_stack") and self.workflow_stack.currentIndex() == 2
                and not self.runner.is_running()):
            self._update_execute_summary()
            self._refresh_execute_review()

    # ── Groups tree ───────────────────────────────────────────────────
    def _reload_groups(self):
        if self._target_kind == "headparts":
            from core.headpart_sources import scan_rows
            from core.slider_group_reader import SliderGroup
            if not self._source_input_root:
                self._effective_tri_root = ""
                self._all_groups = []
                self._headpart_rows = {}
                self._scan_data = {}
                self._rebuild_tree()
                self._update_stats()
                self.lbl_scan_status.setText("Choose a local folder or ZIP containing headpart NIFs")
                self._groups_loaded = True
                return
            root = self._source_input_root
            self._effective_tri_root = str(root)
            # NIFs, not TRIs: a headpart's geometry only exists in its mesh, and
            # plenty of mods ship no .tri at all. The scan stays file-based —
            # reading geometry means dumping every mesh in the mod, which is the
            # run's job, not the scan's.
            rows = scan_rows(root, default_part="unclassified",
                             default_sex=self._headpart_default_sex())
            names = [row.relpath for row in rows]
            groups = [SliderGroup(name="Headparts (NIF)", members=names)] if names else []
            self._all_groups = groups
            self._headpart_rows = {row.relpath: row for row in rows}
            self._scan_data = {
                row.relpath: OutfitInfo(
                    name=row.relpath,
                    # The sex of a headpart, not a body family. The old code put
                    # "3BA"/"HIMBO" here, which are body reference names and
                    # meant nothing for a brow.
                    gender=("Archive" if row.kind == "archive"
                            else row.sex.capitalize() if row.sex else "Unknown"),
                    body_family=("Archive" if row.kind == "archive"
                                 else row.sex.capitalize() if row.sex
                                 else "Unknown"),
                    confidence=row.confidence,
                    mesh_type=("BSA" if row.kind == "archive"
                               else row.part.capitalize()),
                    warnings=list(row.warnings))
                for row in rows
            }
            self._rebuild_tree()
            self._update_stats()
            meshes = [row for row in rows if row.kind == "mesh"]
            archives = [row for row in rows if row.kind == "archive"]
            unresolved = sum(1 for row in meshes if row.sex is None)
            status = f"{len(meshes)} headpart meshes"
            if archives:
                status += f" · {len(archives)} archive(s) to read during the run"
            if unresolved:
                status += f" · {unresolved} with no declared sex"
            self.lbl_scan_status.setText(status)
            self._groups_loaded = True
            return

        root = find_bodyslide_root(self._source_input_root or self.config.app_dir)
        self._effective_bodyslide_root = str(root)
        vfs_sg = os.path.join(self._effective_bodyslide_root, "SliderGroups")
        groups = read_groups(vfs_sg) if vfs_sg else []

        if groups:
            total = sum(len(g.members) for g in groups)
            self._log_info(f"Found {total} outfits across {len(groups)} groups")
        else:
            self._log_warning(f"No groups found. VFS path: {vfs_sg}")

        self._all_groups = groups
        self._member_group_sources = {}
        for group in groups:
            for definition in group.definitions:
                source_name = os.path.basename(definition.source_xml)
                for member in definition.members:
                    self._member_group_sources.setdefault(member, source_name)
        self._refresh_body_group_filter()
        self._rebuild_tree()
        self._update_stats()
        self._groups_loaded = True
        self._start_scan()

    def _refresh_body_group_filter(self):
        """Populate the literal BodySlide-group filter without changing membership."""
        if not hasattr(self, "body_group_filter_combo"):
            return
        combo = self.body_group_filter_combo
        previous = self._body_group_filter
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("All BodySlide groups", "")
            for group in sorted(getattr(self, "_all_groups", []),
                                key=lambda g: g.name.casefold()):
                count = len(dict.fromkeys(group.members))
                combo.addItem(f"{group.name} ({count})", group.name)
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
            self._body_group_filter = combo.currentData() or ""
        finally:
            combo.blockSignals(False)

    def _on_body_group_filter_changed(self, _index: int):
        self._body_group_filter = self.body_group_filter_combo.currentData() or ""
        self._rebuild_tree(self.search_edit.text())

    def _rebuild_tree(self, filter_text: str = ""):
        self.group_tree.blockSignals(True)
        self.group_tree.clear()
        ft = filter_text.lower()
        status_filter = getattr(self, "_status_filter", "all")

        def status_matches(name: str) -> bool:
            info = self._scan_data.get(name)
            if status_filter == "all":
                return True
            if info and info.status_label == "Not Applicable":
                return False
            if status_filter == "patched":
                return bool(info and info.already_patched)
            if status_filter == "unpatched":
                return not (info and info.already_patched)
            # Needs Attention excludes Not Applicable rows and groups
            # unverified/blocked entries by their actionable state.
            return not info or info.status_label in {
                "Missing project", "Needs Confirmation", "Cannot Patch", "Conflict / Error"
            }

        if self._target_kind == "headparts":
            members = [name for name in sorted(
                {member for group in getattr(self, "_all_groups", []) for member in group.members},
                key=str.casefold)
                       if not ft or ft in name.lower()]
            members = [name for name in members if status_matches(name)]
            if members:
                view_group = SliderGroup(name=f"Headparts ({len(members)})", members=members)
                grp_item = TreeItem(self.group_tree)
                grp_item.setFlags(grp_item.flags() | Qt.ItemIsUserCheckable)
                selection_key = "__outfits__"
                selected_members = [m for m in members if (selection_key, m) in self._selected_outfits]
                grp_item.setCheckState(COL_NAME, Qt.Checked if selected_members and
                                       len(selected_members) == len(members) else
                                       Qt.PartiallyChecked if selected_members else Qt.Unchecked)
                grp_item.setData(COL_NAME, Qt.UserRole, view_group)
                grp_item.setData(COL_NAME, Qt.UserRole + 1, "headpart-group")
                grp_item.setData(COL_NAME, Qt.UserRole + 3, members)
                grp_item.setData(COL_NAME, Qt.UserRole + 4, selection_key)
                for name in members:
                    child = TreeItem(grp_item)
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(COL_NAME, Qt.Checked if (selection_key, name)
                                        in self._selected_outfits else Qt.Unchecked)
                    child.setText(COL_NAME, name)
                    info = self._scan_data.get(name)
                    row = getattr(self, "_headpart_rows", {}).get(name)
                    child.setText(1, row.sex.capitalize() if row and row.sex else "Unknown")
                    child.setText(2, "Not patched")
                    child.setData(COL_NAME, Qt.UserRole, name)
                    child.setData(COL_NAME, Qt.UserRole + 1, "headpart")
                    child.setData(COL_NAME, Qt.UserRole + 4, selection_key)
                self._apply_group_badges(grp_item, view_group, members)
            self._visible_member_names = set(members)
            self.group_tree.blockSignals(False)
            return

        # SliderGroups XML decides the allowed membership.  The selected group
        # is a filter only; the visible hierarchy is by OSP project so the
        # same SliderSet is shown once even when it has several BodySlide tags.
        source_groups = getattr(self, "_all_groups", [])
        if self._body_group_filter:
            filtered_groups = [g for g in source_groups
                               if g.name == self._body_group_filter]
        else:
            filtered_groups = source_groups
        allowed_members = {
            member for group in filtered_groups for member in group.members
        }

        projects: dict[str, list[str]] = {}
        for name in sorted(allowed_members, key=str.casefold):
            info = self._scan_data.get(name)
            project = (info.source_project if info and info.source_project else "")
            if not project:
                source = getattr(self, "_member_group_sources", {}).get(name)
                project = (f"Unmatched · {source}" if source
                           else "Unmatched SliderSet project")
            projects.setdefault(project, []).append(name)

        visible_members: set[str] = set()
        for project, project_members in sorted(projects.items(), key=lambda pair: pair[0].casefold()):
            members = [m for m in project_members
                       if status_matches(m) and (not ft or ft in project.lower()
                                                  or ft in m.lower())]
            if not members:
                continue
            visible_members.update(members)

            all_project_members = list(dict.fromkeys(project_members))
            selectable_members = [m for m in all_project_members
                                  if not (self._scan_data.get(m)
                                          and self._scan_data[m].status_label == "Not Applicable")]
            view_group = SliderGroup(name=project, members=all_project_members)
            grp_item = TreeItem(self.group_tree)
            if selectable_members:
                grp_item.setFlags(grp_item.flags() | Qt.ItemIsUserCheckable)
            selection_key = "__outfits__"
            selected_members = [m for m in selectable_members
                                if (selection_key, m) in self._selected_outfits]
            group_state = (Qt.Checked if selected_members and
                           len(selected_members) == len(selectable_members)
                           else Qt.PartiallyChecked if selected_members
                           else Qt.Unchecked)
            grp_item.setCheckState(COL_NAME, group_state)
            grp_item.setData(COL_NAME, Qt.UserRole, view_group)
            grp_item.setData(COL_NAME, Qt.UserRole + 1, "project")
            grp_item.setData(COL_NAME, Qt.UserRole + 3, selectable_members)
            grp_item.setData(COL_NAME, Qt.UserRole + 4, selection_key)

            for m in members:
                child = TreeItem(grp_item)
                info = self._scan_data.get(m)
                not_applicable = bool(info and info.status_label == "Not Applicable")
                if not not_applicable:
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                else:
                    child.setFlags(child.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled)
                child.setCheckState(COL_NAME, Qt.Checked
                                    if (selection_key, m) in self._selected_outfits
                                    else Qt.Unchecked)
                child.setText(COL_NAME, m)
                child.setData(COL_NAME, Qt.UserRole, m)
                child.setData(COL_NAME, Qt.UserRole + 1, "outfit")
                child.setData(COL_NAME, Qt.UserRole + 4, selection_key)
                self._apply_outfit_badges(child, m)

            self._apply_group_badges(grp_item, view_group, all_project_members)

        self._visible_member_names = visible_members
        self.group_tree.blockSignals(False)
        # Reapply active sort, if any (rebuild resets to insertion order)
        if getattr(self, "_sort_col", -1) >= 0:
            self.group_tree.sortItems(self._sort_col, self._sort_order)
        self._update_stats()

    @staticmethod
    def _classification_tooltip(info: OutfitInfo) -> str:
        evidence = "<br>".join(info.evidence[:4]) if info.evidence else "No direct project evidence."
        warnings = "<br>".join(info.warnings[:4]) if info.warnings else "None"
        issues = "<br>".join(info.issues[:4]) if info.issues else "None"
        garment = ", ".join(info.garment_tags) if info.garment_tags else "None"
        garment_note = ""
        if "Hood" in info.garment_tags:
            garment_note = "<br>Hoods have no compatible Body/Hands/Feet reference and are not patched."
        elif info.excludes_thin:
            garment_note = "<br>Capes and cloaks use the Body reference, but RM_Thin is excluded."
        return (
            f"Status: {info.status_label}<br>"
            f"Sex: {info.eff_sex}<br>"
            f"Body family: {info.eff_body_family}<br>"
            f"MPP reference: {info.display_reference}<br>"
            f"Compatibility state: {info.compatibility_status}<br>"
            f"Mesh part: {info.eff_mesh}<br>"
            f"Garment category: {garment}{garment_note}<br>"
            f"Confidence: {info.confidence}<br>"
            f"Evidence:<br>{evidence}<br>"
            f"Issues: {issues}<br>"
            f"Warnings: {warnings}"
        )

    def _apply_outfit_badges(self, item: QTreeWidgetItem, name: str):
        info = self._scan_data.get(name)
        if not info:
            # Not yet scanned — still allow manual set
            item.setText(COL_GENDER, "?")
            item.setForeground(COL_GENDER, QBrush(QColor(GENDER_COLORS["Unknown"])))
            item.setTextAlignment(COL_GENDER, Qt.AlignCenter)
            item.setData(COL_GENDER, SORT_ROLE, GENDER_SORT["?"])
            item.setToolTip(COL_GENDER, "Sex not detected  (click to set)")
            item.setText(COL_MESH, "?")
            item.setForeground(COL_MESH, QBrush(QColor(GENDER_COLORS["Unknown"])))
            item.setTextAlignment(COL_MESH, Qt.AlignCenter)
            item.setData(COL_MESH, SORT_ROLE, MESH_SORT["?"])
            item.setToolTip(COL_MESH, "Body part not detected  (click to set)")
            self._apply_flag_badge(item, COL_REVEAL, False)
            self._apply_flag_badge(item, COL_SMART, False)
            return

        g   = info.eff_sex
        m   = info.eff_mesh
        gc  = GENDER_COLORS.get(g, GENDER_COLORS["Unknown"])
        mc  = (GARMENT_COLORS.get(info.garment_tags[0], MESH_COLORS.get(m, MESH_COLORS["Body"]))
               if info.garment_tags else MESH_COLORS.get(m, MESH_COLORS["Body"]))
        item.setText(COL_GENDER, GENDER_ICONS.get(g, "?"))
        item.setForeground(COL_GENDER, QBrush(QColor(gc)))
        item.setTextAlignment(COL_GENDER, Qt.AlignCenter)
        item.setData(COL_GENDER, SORT_ROLE, GENDER_SORT.get(GENDER_ICONS.get(g, "?"), 3))
        item.setText(COL_MESH, info.display_mesh_label if info.garment_tags else MESH_LABELS.get(m, "?"))
        item.setForeground(COL_MESH, QBrush(QColor(mc)))
        item.setTextAlignment(COL_MESH, Qt.AlignCenter)
        item.setData(COL_MESH, SORT_ROLE, MESH_SORT.get(m, 4))
        item.setToolTip(COL_MESH, self._classification_tooltip(info))
        # Step 2 is intentionally non-technical: it only communicates whether
        # an outfit still needs patching. Detection details live in Step 3.
        patch_text = info.patch_status
        status_palette = {
            "Patched": (PATCHED_COLOR, 0),
            "Partially patched": (self._reveal_accent(), 1),
            "Not patched": (TEXT, 2),
            "Missing project": ("#ef4444", 3),
            "Not applicable": ("#64748b", 4),
        }
        status_color, status_sort = status_palette.get(patch_text, (TEXT_DIM, 5))
        item.setText(COL_STATUS, patch_text)
        item.setForeground(COL_STATUS, QBrush(QColor(status_color)))
        item.setTextAlignment(COL_STATUS, Qt.AlignLeft | Qt.AlignVCenter)
        item.setData(COL_STATUS, SORT_ROLE, status_sort)
        item.setToolTip(COL_STATUS, patch_text)
        item.setToolTip(COL_GENDER,
                        f"Sex: {g}  (click to change)<br>"
                        + self._classification_tooltip(info))
        item.setToolTip(COL_MESH,
                        f"Body part: {m}  (click to change)<br>"
                        + self._classification_tooltip(info))
        self._apply_flag_badge(item, COL_REVEAL, info.revealing)
        self._apply_flag_badge(item, COL_SMART, info.smart_refit)

    def _reveal_accent(self) -> str:
        colors = DARK_THEME if self._effective_theme() == "dark" else LIGHT_THEME
        return colors["warning"]

    def _flag_accent(self, column: int) -> str:
        return self._reveal_accent() if column == COL_REVEAL else SMART_COLOR

    def _apply_flag_badge(self, item: QTreeWidgetItem, column: int, value: bool):
        """Render one per-outfit flag as a checkbox plus a readable state."""
        signals_were_blocked = self.group_tree.signalsBlocked()
        self.group_tree.blockSignals(True)
        item.setCheckState(column, Qt.Checked if value else Qt.Unchecked)
        # FlagDelegate owns the painting for this column.  Keep the item's
        # user-checkable flag intact because the same flag also enables the
        # primary Outfit checkbox in COL_NAME.
        # Individual rows use the checkbox only. The group row is the only
        # place where a count is shown, which keeps the column compact.
        item.setText(column, "")
        item.setForeground(column, QBrush(QColor(
            self._flag_accent(column) if value else GENDER_COLORS["Unknown"])))
        # Center the checkbox indicator in the column when no label is shown;
        # checked rows keep their state label centered with the indicator.
        item.setTextAlignment(column, Qt.AlignCenter)
        item.setData(column, SORT_ROLE, 0 if value else 1)
        on_tip, off_tip = FLAG_TOOLTIPS[column]
        item.setToolTip(column, on_tip if value else off_tip)
        self.group_tree.blockSignals(signals_were_blocked)

    def _apply_reveal_badge(self, item: QTreeWidgetItem, revealing: bool):
        self._apply_flag_badge(item, COL_REVEAL, revealing)

    def _apply_group_badges(self, grp_item: QTreeWidgetItem, group, members: list[str]):
        infos = [self._scan_data[m] for m in members if m in self._scan_data]
        n = len(members)
        patched = sum(1 for i in infos if i.already_patched)

        # Keep the warning tied to the exact member list and separate the
        # actionable categories instead of treating every yellow triangle as
        # "gender unknown".
        missing_names = [m for m in members
                         if m not in self._scan_data
                         or not self._scan_data[m].project_found]
        states = [self._scan_data[m].status_label if m in self._scan_data
                  else "Missing project" for m in members]
        missing_count = states.count("Missing project")
        error_count = states.count("Conflict / Error") + states.count("Cannot Patch")
        confirmation_count = states.count("Needs Confirmation")
        not_applicable_count = states.count("Not Applicable")
        error_names = [m for m in members if m in self._scan_data
                       and self._scan_data[m].status_label in {"Conflict / Error", "Cannot Patch"}]
        # Kept empty for compatibility with the older tooltip branch below;
        # the concise semantic summary is applied immediately afterwards.
        unknown_names = unsupported_names = reference_names = mesh_names = conflict_names = []
        unresolved_names = list(dict.fromkeys(
            missing_names + error_names))

        genders   = {i.eff_sex for i in infos} if infos else set()
        meshes    = {i.eff_mesh   for i in infos} if infos else set()

        # Group name — warn if any member has unknown gender
        if unresolved_names:
            grp_item.setText(COL_NAME, f"⚠ {group.name}  ({n})")
            grp_item.setForeground(COL_NAME, QBrush(QColor(self._reveal_accent())))
            details = ["Needs attention. Expand the group and inspect the tooltips."]
            if missing_names:
                details.append(
                    "Missing project: "
                    + ", ".join(missing_names[:8])
                    + (f" (+{len(missing_names) - 8} more)"
                       if len(missing_names) > 8 else ""))
            if unknown_names:
                details.append(
                    "Review suggested: "
                    + ", ".join(unknown_names[:8])
                    + (f" (+{len(unknown_names) - 8} more)"
                       if len(unknown_names) > 8 else ""))
            if unsupported_names:
                details.append(
                    "Unsupported body family: "
                    + ", ".join(unsupported_names[:8])
                    + (f" (+{len(unsupported_names) - 8} more)"
                       if len(unsupported_names) > 8 else ""))
            if reference_names:
                details.append(
                    "Reference compatibility needs review: "
                    + ", ".join(reference_names[:8])
                    + (f" (+{len(reference_names) - 8} more)"
                       if len(reference_names) > 8 else ""))
            if mesh_names:
                details.append(
                    "Mesh part needs review: "
                    + ", ".join(mesh_names[:8])
                    + (f" (+{len(mesh_names) - 8} more)"
                       if len(mesh_names) > 8 else ""))
            if conflict_names:
                details.append(
                    "Conflicting metadata: "
                    + ", ".join(conflict_names[:8])
                    + (f" (+{len(conflict_names) - 8} more)"
                       if len(conflict_names) > 8 else ""))
            warning_tip = "<br>".join(details)
            grp_item.setToolTip(COL_NAME, warning_tip)
            grp_item.setToolTip(COL_GENDER, warning_tip)
        else:
            grp_item.setText(COL_NAME, f"{group.name}  ({n})")
            grp_item.setForeground(COL_NAME, QBrush(QColor(TEXT)))
            grp_item.setToolTip(COL_NAME, "")

        # Replace the legacy name-list warning with a compact state summary.
        if error_count or missing_count or confirmation_count:
            if error_count:
                prefix, color = "!", "#ef4444"
            elif missing_count:
                prefix, color = "?", self._reveal_accent()
            else:
                prefix, color = "i", "#60a5fa"
            grp_item.setText(COL_NAME, f"{prefix} {group.name}  ({n})")
            grp_item.setForeground(COL_NAME, QBrush(QColor(color)))
            details = ["Group status summary:"]
            if error_count:
                details.append(f"Conflict / Error: {error_count}")
            if missing_count:
                details.append(f"Missing project: {missing_count}")
            if confirmation_count:
                details.append(f"Needs Confirmation / Unverified: {confirmation_count}")
            if not_applicable_count:
                details.append(f"Not Applicable: {not_applicable_count}")
            summary_tip = "<br>".join(details)
            grp_item.setToolTip(COL_NAME, summary_tip)
            grp_item.setToolTip(COL_GENDER, summary_tip)

        # Gender badge
        if len(genders) == 1:
            g  = next(iter(genders))
            gc = GENDER_COLORS.get(g, GENDER_COLORS["Unknown"])
            grp_item.setText(COL_GENDER, GENDER_ICONS.get(g, "?"))
            grp_item.setForeground(COL_GENDER, QBrush(QColor(gc)))
            grp_item.setData(COL_GENDER, SORT_ROLE, GENDER_SORT.get(GENDER_ICONS.get(g, "?"), 3))
        elif genders:
            grp_item.setText(COL_GENDER, "mix")
            grp_item.setForeground(COL_GENDER, QBrush(QColor("#f59e0b")))
            grp_item.setData(COL_GENDER, SORT_ROLE, GENDER_SORT["mix"])
        else:
            grp_item.setText(COL_GENDER, "?")
            grp_item.setForeground(COL_GENDER, QBrush(QColor(GENDER_COLORS["Unknown"])))
            grp_item.setData(COL_GENDER, SORT_ROLE, GENDER_SORT["?"])
        grp_item.setTextAlignment(COL_GENDER, Qt.AlignCenter)

        # Mesh badge
        if len(meshes) == 1:
            m  = next(iter(meshes))
            mc = MESH_COLORS.get(m, MESH_COLORS["Body"])
            grp_item.setText(COL_MESH, MESH_LABELS.get(m, "?"))
            grp_item.setForeground(COL_MESH, QBrush(QColor(mc)))
            grp_item.setData(COL_MESH, SORT_ROLE, MESH_SORT.get(m, 4))
        elif meshes:
            grp_item.setText(COL_MESH, "mix")
            grp_item.setForeground(COL_MESH, QBrush(QColor("#f59e0b")))
            grp_item.setData(COL_MESH, SORT_ROLE, MESH_SORT["mix"])
        grp_item.setTextAlignment(COL_MESH, Qt.AlignCenter)

        # Status
        if patched:
            # The count is useful in the tooltip, but combining it with the glyph
            # makes the checkmark look off-center in a narrow table cell.
            grp_item.setText(COL_STATUS, "✓")
            grp_item.setForeground(COL_STATUS, QBrush(QColor(PATCHED_COLOR)))
            grp_item.setTextAlignment(COL_STATUS, Qt.AlignCenter)
            grp_item.setData(COL_STATUS, SORT_ROLE, n - patched)
            grp_item.setToolTip(COL_STATUS,
                                f"{patched} of {n} outfits have all required MPP sliders")
        else:
            grp_item.setText(COL_STATUS, "")
            grp_item.setTextAlignment(COL_STATUS, Qt.AlignCenter)
            grp_item.setData(COL_STATUS, SORT_ROLE, n)

        # Step 2 reports only patch state. A project can include a mixture of
        # patched and unpatched sets, which is meaningfully "Partially patched"
        # even though its technical metadata may need review later.
        patch_states = [info.patch_status for info in infos]
        if not patch_states:
            aggregate_text, aggregate_color, aggregate_sort = "Missing project", "#ef4444", 4
        elif all(state == "Not applicable" for state in patch_states):
            aggregate_text, aggregate_color, aggregate_sort = "Not applicable", "#64748b", 5
        elif all(state == "Patched" for state in patch_states):
            aggregate_text, aggregate_color, aggregate_sort = "Patched", PATCHED_COLOR, 0
        elif any(state in {"Patched", "Partially patched"} for state in patch_states):
            aggregate_text, aggregate_color, aggregate_sort = "Partially patched", self._reveal_accent(), 1
        elif any(state == "Missing project" for state in patch_states):
            aggregate_text, aggregate_color, aggregate_sort = "Missing project", "#ef4444", 4
        else:
            aggregate_text, aggregate_color, aggregate_sort = "Not patched", TEXT, 2
        grp_item.setText(COL_STATUS, aggregate_text)
        grp_item.setForeground(COL_STATUS, QBrush(QColor(aggregate_color)))
        grp_item.setTextAlignment(COL_STATUS, Qt.AlignLeft | Qt.AlignVCenter)
        grp_item.setData(COL_STATUS, SORT_ROLE, aggregate_sort)
        grp_item.setToolTip(COL_STATUS, aggregate_text)

        for column in FLAG_COLUMNS:
            self._apply_group_flag_badge(grp_item, column, infos, n)
        # Selection is not a diagnostic view. Keep the project name clean and
        # let Patch Status communicate any missing/partial state; all scanner
        # evidence remains available in the final technical review.
        grp_item.setText(COL_NAME, f"{group.name}  ({n})")
        grp_item.setForeground(COL_NAME, QBrush(QColor(TEXT)))

    def _apply_group_flag_badge(self, item: QTreeWidgetItem, column: int,
                                infos: list[OutfitInfo], total: int):
        """Show a group's full flag state, independent of filtering."""
        attr = FLAG_COLUMNS[column]
        accent = self._flag_accent(column)
        marked = sum(1 for info in infos if getattr(info, attr))
        if marked == 0:
            text, color, sort, state = "", GENDER_COLORS["Unknown"], 2, Qt.Unchecked
        elif marked == total and total:
            text, color, sort, state = f"{total}/{total}", accent, 0, Qt.Checked
        else:
            text, color, sort, state = (
                f"{marked}/{total}", accent, 1, Qt.PartiallyChecked)
        signals_were_blocked = self.group_tree.signalsBlocked()
        self.group_tree.blockSignals(True)
        item.setCheckState(column, state)
        # Keep ItemIsUserCheckable: it is shared by the Outfit checkbox and the
        # custom-painted flag indicator.
        item.setText(column, text)
        item.setForeground(column, QBrush(QColor(color)))
        item.setTextAlignment(column, Qt.AlignCenter)
        item.setData(column, SORT_ROLE, sort)
        label = "the Revealing morph" if column == COL_REVEAL else "Smart refit"
        item.setToolTip(
            column,
            f"Some outfits in this group use {label}. Click to enable it for the entire group."
            if state == Qt.PartiallyChecked else
            f"All outfits in this group use {label}. Click to disable it for the entire group."
            if state == Qt.Checked else
            f"No outfit in this group uses {label}. Click to enable it for the entire group."
        )
        self.group_tree.blockSignals(signals_were_blocked)

    def _filter_groups(self, text: str):
        self._rebuild_tree(text)

    def _set_status_filter(self, value: str):
        if value not in {"all", "unpatched", "patched", "warning"}:
            value = "all"
        self._status_filter = value
        for key, button in getattr(self, "_filter_buttons", {}).items():
            active = key == value
            button.setChecked(active)
            button.setProperty("activeFilter", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self._rebuild_tree(self.search_edit.text())

    def _legacy_on_tree_item_changed(self, item: QTreeWidgetItem, col: int):
        if col in FLAG_COLUMNS:
            self._set_flag(item, FLAG_COLUMNS[col],
                           item.checkState(col) == Qt.Checked)
            return
        if col != COL_NAME:
            return
        self.group_tree.blockSignals(True)
        state = item.checkState(COL_NAME)
        # If group → propagate to children
        if item.data(COL_NAME, Qt.UserRole + 1) == "group":
            for i in range(item.childCount()):
                item.child(i).setCheckState(COL_NAME, state)
        # Update parent tristate
        parent = item.parent()
        if parent:
            checked = sum(
                1 for i in range(parent.childCount())
                if parent.child(i).checkState(COL_NAME) == Qt.Checked
            )
            total = parent.childCount()
            if checked == 0:
                parent.setCheckState(COL_NAME, Qt.Unchecked)
            elif checked == total:
                parent.setCheckState(COL_NAME, Qt.Checked)
            else:
                parent.setCheckState(COL_NAME, Qt.PartiallyChecked)
        self.group_tree.blockSignals(False)
        self._update_stats()

    # Persistent selection implementation.  This definition intentionally
    # follows the legacy widget-only handler above so the model-backed version
    # is the one Python binds at class creation time.
    def _on_tree_item_changed(self, item: QTreeWidgetItem, col: int):
        if col != COL_NAME:
            return
        state = item.checkState(COL_NAME)
        group_key = item.data(COL_NAME, Qt.UserRole + 4)
        if item.data(COL_NAME, Qt.UserRole + 1) in {"group", "project"}:
            names = item.data(COL_NAME, Qt.UserRole + 3) or []
        else:
            names = [item.data(COL_NAME, Qt.UserRole)]
        keys = {(group_key, n) for n in names
                if isinstance(group_key, str) and isinstance(n, str)}
        if state == Qt.Checked:
            self._selected_outfits.update(keys)
        else:
            self._selected_outfits.difference_update(keys)
        self._sync_selection_widgets()

    def _sync_selection_widgets(self):
        self.group_tree.blockSignals(True)
        try:
            for i in range(self.group_tree.topLevelItemCount()):
                group = self.group_tree.topLevelItem(i)
                names = group.data(COL_NAME, Qt.UserRole + 3) or []
                group_key = group.data(COL_NAME, Qt.UserRole + 4)
                selected = [n for n in names
                            if (group_key, n) in self._selected_outfits]
                state = (Qt.Checked if selected and len(selected) == len(names)
                         else Qt.PartiallyChecked if selected else Qt.Unchecked)
                group.setCheckState(COL_NAME, state)
                for j in range(group.childCount()):
                    child = group.child(j)
                    name = child.data(COL_NAME, Qt.UserRole)
                    child.setCheckState(COL_NAME, Qt.Checked
                                        if (group_key, name) in self._selected_outfits
                                        else Qt.Unchecked)
        finally:
            self.group_tree.blockSignals(False)
        self._update_stats()

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, col: int):
        """Click on gender/mesh column → popup menu to pick the value directly."""
        if col == COL_GENDER:
            self._popup_choice(item, "gender", GENDER_CYCLE)
        elif col == COL_MESH:
            self._popup_choice(item, "mesh", MESH_CYCLE)
        elif col in FLAG_COLUMNS:
            # These columns use a custom delegate, so Qt does not toggle the
            # check state automatically when the user clicks the cell. Toggle
            # from the model state explicitly. Partial group state counts as
            # off, making the first click enable the flag for the full group.
            self._set_flag(item, FLAG_COLUMNS[col],
                           item.checkState(col) != Qt.Checked)

    def _popup_choice(self, item: QTreeWidgetItem, kind: str, options: list[str]):
        """Show a menu to manually set gender/mesh for an outfit or a whole group."""
        kind_str = item.data(COL_NAME, Qt.UserRole + 1)
        # Collect affected outfit names (single outfit, or all members of a group)
        if kind_str == "group":
            names = [item.child(i).data(COL_NAME, Qt.UserRole)
                     for i in range(item.childCount())]
            header = "Apply to entire group:"
        else:
            names = [item.data(COL_NAME, Qt.UserRole)]
            header = None
        names = [n for n in names if isinstance(n, str)]
        if not names:
            return

        menu = QMenu(self)
        if header:
            act = menu.addAction(header)
            act.setEnabled(False)
            menu.addSeparator()
        col = COL_GENDER if kind == "gender" else COL_MESH
        for opt in options:
            label = {"3BA": "♀ Female", "HIMBO": "♂ Male", "Unknown": "? Unknown",
                     "Body": "Body", "Hands": "Hands", "Feet": "Feet"}.get(opt, opt)
            a = menu.addAction(label)
            a.setData(opt)
        chosen = menu.exec(QCursor.pos())
        if not chosen:
            return
        val = chosen.data()

        for n in names:
            info = self._scan_data.get(n)
            if not info:
                info = OutfitInfo(name=n)
                self._scan_data[n] = info
            if kind == "gender":
                info.gender_override = val
            else:
                info.mesh_override = val

        # Refresh badges for affected items
        if kind_str == "group":
            for i in range(item.childCount()):
                ch = item.child(i)
                nm = ch.data(COL_NAME, Qt.UserRole)
                if isinstance(nm, str):
                    self._apply_outfit_badges(ch, nm)
            g = item.data(COL_NAME, Qt.UserRole)
            self._apply_group_badges(item, g, names)
        else:
            self._apply_outfit_badges(item, names[0])
            parent = item.parent()
            if parent:
                g = parent.data(COL_NAME, Qt.UserRole)
                members = [parent.child(i).data(COL_NAME, Qt.UserRole)
                           for i in range(parent.childCount())]
                self._apply_group_badges(parent, g, members)

    def _set_flag(self, item: QTreeWidgetItem, attr: str, new_val: bool):
        """Write one per-outfit flag for a row, or for a whole group/project."""
        kind = item.data(COL_NAME, Qt.UserRole + 1)
        if kind in {"group", "project"}:
            names = item.data(COL_NAME, Qt.UserRole + 3) or []
        else:
            names = [item.data(COL_NAME, Qt.UserRole)]
        for name in names:
            info = self._scan_data.get(name)
            if info:
                setattr(info, attr, new_val)
        self._rebuild_tree(self.search_edit.text())

    def _set_revealing(self, item: QTreeWidgetItem, new_val: bool):
        self._set_flag(item, "revealing", new_val)

    def _set_smart_refit(self, item: QTreeWidgetItem, new_val: bool):
        self._set_flag(item, "smart_refit", new_val)

    def _legacy_popup_choice(self, item: QTreeWidgetItem, kind: str, options: list[str]):
        kind_str = item.data(COL_NAME, Qt.UserRole + 1)
        names = (item.data(COL_NAME, Qt.UserRole + 3) or []
                 if kind_str in {"group", "project"} else [item.data(COL_NAME, Qt.UserRole)])
        names = [n for n in names if isinstance(n, str)]
        if not names:
            return
        menu = QMenu(self)
        if kind_str in {"group", "project"}:
            header = menu.addAction("Apply to entire project")
            header.setEnabled(False)
            menu.addSeparator()
        labels = {"3BA": "♀ Female", "HIMBO": "♂ Male", "Unknown": "? Unknown",
                  "Body": "Body", "Hands": "Hands", "Feet": "Feet"}
        for option in options:
            action = menu.addAction(labels.get(option, option))
            action.setData(option)
        chosen = menu.exec(QCursor.pos())
        if not chosen:
            return
        value = chosen.data()
        for name in names:
            info = self._scan_data.get(name)
            if not info:
                info = OutfitInfo(name=name)
                self._scan_data[name] = info
            if kind == "gender":
                info.gender_override = value
            else:
                info.mesh_override = value
        self._rebuild_tree(self.search_edit.text())

    def _on_header_clicked(self, col: int):
        """Toggle sort order on header click. No sort until first click."""
        if self._sort_col == col:
            self._sort_order = (Qt.DescendingOrder
                                if self._sort_order == Qt.AscendingOrder
                                else Qt.AscendingOrder)
        else:
            self._sort_col = col
            self._sort_order = Qt.AscendingOrder
        self.group_tree.sortItems(self._sort_col, self._sort_order)
        self.group_tree.header().setSortIndicator(self._sort_col, self._sort_order)
        self.group_tree.header().setSortIndicatorShown(True)

    def _legacy_select_all_groups(self):
        self.group_tree.blockSignals(True)
        for i in range(self.group_tree.topLevelItemCount()):
            grp = self.group_tree.topLevelItem(i)
            grp.setCheckState(COL_NAME, Qt.Checked)
            for j in range(grp.childCount()):
                grp.child(j).setCheckState(COL_NAME, Qt.Checked)
        self.group_tree.blockSignals(False)
        self._update_stats()

    def _select_all_groups(self):
        visible = {
            ("__outfits__", member)
            for member in getattr(self, "_visible_member_names", set())
            if isinstance(member, str)
            and not (self._scan_data.get(member)
                     and self._scan_data[member].status_label == "Not Applicable")
        }
        self._selected_outfits.update(visible)
        self._rebuild_tree(self.search_edit.text())
        self._sync_selection_widgets()

    def _legacy_select_no_groups(self):
        self.group_tree.blockSignals(True)
        for i in range(self.group_tree.topLevelItemCount()):
            grp = self.group_tree.topLevelItem(i)
            grp.setCheckState(COL_NAME, Qt.Unchecked)
            for j in range(grp.childCount()):
                grp.child(j).setCheckState(COL_NAME, Qt.Unchecked)
        self.group_tree.blockSignals(False)
        self._update_stats()

    def _select_no_groups(self):
        visible = {( "__outfits__", member)
                   for member in getattr(self, "_visible_member_names", set())
                   if isinstance(member, str)}
        self._selected_outfits.difference_update(visible)
        self._rebuild_tree(self.search_edit.text())
        self._sync_selection_widgets()

    def _legacy_checked_groups(self):
        """Returns groups that have at least one checked child (for compat)."""
        from core.slider_group_reader import SliderGroup
        result = []
        for i in range(self.group_tree.topLevelItemCount()):
            grp = self.group_tree.topLevelItem(i)
            members = [
                grp.child(j).data(COL_NAME, Qt.UserRole)
                for j in range(grp.childCount())
                if grp.child(j).checkState(COL_NAME) == Qt.Checked
            ]
            if members:
                g = grp.data(COL_NAME, Qt.UserRole)
                result.append(SliderGroup(name=g.name, members=members))
        return result

    def _checked_groups(self):
        """Return selected members from the model, independent of filtering."""
        from core.slider_group_reader import SliderGroup
        members = sorted({name for key, name in self._selected_outfits
                          if key == "__outfits__" and isinstance(name, str)},
                         key=str.casefold)
        return [SliderGroup(name="Selected Outfits", members=members)] if members else []

    def _legacy_update_stats(self):
        checked = self._checked_groups()
        total = sum(len(g.members) for g in checked)
        self._total_outfits = total
        n_groups = len(checked)
        self.stat_outfits.set_value(str(total))
        self.stat_groups.set_value(str(n_groups))
        n_top = self.group_tree.topLevelItemCount()
        self.lbl_selected.setText(f"{n_groups}/{n_top} groups · {total} outfits")

    # ──────────────────────────────────────────────────────────────────
    def _update_stats(self):
        checked = self._checked_groups()
        unique = {member for group in checked for member in group.members}
        total = len(unique)
        self._total_outfits = total
        self.stat_outfits.set_value(str(total))
        self.stat_groups.set_value(str(len(checked)))
        self._update_execute_summary()
        noun = "headpart(s)" if self._target_kind == "headparts" else "outfit(s)"
        if hasattr(self, "review_summary"):
            self.review_summary.setText(f"{total} {noun} selected")
        n_top = self.group_tree.topLevelItemCount()
        shown = "headpart meshes" if self._target_kind == "headparts" else "unique outfits"
        prefix = f"{n_top} groups shown" if self._target_kind != "headparts" else f"{n_top} source shown"
        self.lbl_selected.setText(f"{prefix} · {total} {shown} selected")

    # Patch
    # ──────────────────────────────────────────────────────────────────
    def _on_patch(self):
        try:
            self._on_patch_impl()
        except Exception as e:
            self._cleanup_archive_staging()
            import traceback
            self._log_error(f"Patch error: {e}\n{traceback.format_exc()}")

    def _confirm_smart_refit_warning(self) -> bool:
        """Ask once before starting a potentially long Smart Refit job.

        Smart refit is per-outfit now, so the cost is proportional to how
        many are marked. Say the number: that is the part the user can act on.
        """
        members = list(dict.fromkeys(
            m for group in self._checked_groups() for m in group.members))
        marked = len(self._smart_refit_names(members))
        if not marked:
            return True
        if self.config.smart_refit_warning_suppressed:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Smart refit enabled")
        box.setText(f"Smart refit is enabled for {marked} "
                    f"outfit{'' if marked == 1 else 's'}.")
        box.setInformativeText(
            "The experimental Drape pass runs once per RM slider and takes "
            "roughly a minute each, so expect several minutes per marked outfit. "
            "Continue?"
        )
        dont_show = QCheckBox("Don't show again")
        box.setCheckBox(dont_show)
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Ok)
        continue_button = box.button(QMessageBox.Ok)
        if continue_button is not None:
            continue_button.setText("Continue")

        accepted = box.exec() == QMessageBox.Ok
        if accepted and dont_show.isChecked():
            self.config.smart_refit_warning_suppressed = True
        return accepted

    def _on_patch_impl(self):
        if not self._confirm_smart_refit_warning():
            return
        self._save_state()
        if self._target_kind == "headparts":
            return self._on_headparts_patch()
        groups = self._checked_groups()
        self._log_info(f"Groups selected: {len(groups)}")
        if not groups:
            QMessageBox.warning(self, "No groups selected",
                                "Select at least one Slider Group.")
            return

        missing_project = [m for g in groups for m in g.members
                           if not self._scan_data.get(m)
                           or not self._scan_data[m].project_found]
        unsupported = [m for g in groups for m in g.members
                       if self._scan_data.get(m)
                       and self._scan_data[m].eff_body_family not in SUPPORTED_BODY_FAMILIES
                       and not self._scan_data[m].has_manual_reference_override]
        reference_review = [m for g in groups for m in g.members
                            if self._scan_data.get(m)
                            and self._scan_data[m].eff_reference not in SUPPORTED_REFERENCES
                            and not self._scan_data[m].has_manual_reference_override]
        mesh_review = [m for g in groups for m in g.members
                       if self._scan_data.get(m)
                       and self._scan_data[m].eff_mesh not in SUPPORTED_MESH_PARTS]
        unknown_gender = [m for g in groups for m in g.members
                          if (self._scan_data.get(m) or OutfitInfo(name=m)).eff_gender == "Unknown"]
        if missing_project or unsupported or reference_review or mesh_review or unknown_gender:
            lines = []
            if missing_project:
                lines.append(f"Missing project ({len(missing_project)}): "
                             + ", ".join(missing_project[:6]))
            if unsupported:
                lines.append(f"Unsupported body family ({len(unsupported)}): "
                             + ", ".join(unsupported[:6]))
            if reference_review:
                lines.append(f"Reference compatibility needs review ({len(reference_review)}): "
                             + ", ".join(reference_review[:6]))
            if mesh_review:
                lines.append(f"Mesh part needs review ({len(mesh_review)}): "
                             + ", ".join(mesh_review[:6]))
            if unknown_gender:
                lines.append(f"Sex needs review ({len(unknown_gender)}): "
                             + ", ".join(unknown_gender[:6]))
            QMessageBox.warning(
                self, "Review required before patching",
                "Some selected outfits cannot be safely mapped to an MPP reference.\n\n"
                + "\n".join(lines)
                + "\n\nAdd the missing OSP, choose a supported outfit, or set a manual gender override."
            )
            return

        cli_exe = self.config.mppatcher_cli_exe
        self._log_info(f"CLI exe: {cli_exe!r}")
        if not cli_exe:
            QMessageBox.critical(self, "mppatcher-cli not found",
                "Could not find mppatcher-cli.exe.\nCheck the BodySlide path.")
            return

        bs_data    = self._source_work_root or self.config.app_dir
        slider_dir = os.path.join(bs_data, "SliderSets")
        try:
            out_dir, archive_path = self._prepare_output_destination(bs_data)
        except ValueError as exc:
            QMessageBox.warning(
                self, "Invalid output destination", str(exc))
            return
        self._active_output_dir = out_dir
        self._active_archive_path = archive_path

        # Write filter file — one set name per line
        import tempfile
        all_members = list(dict.fromkeys(m for g in groups for m in g.members))
        filter_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                                   delete=False, encoding="utf-8")
        filter_file.write("\n".join(all_members))
        filter_file.close()
        self._filter_file_path = filter_file.name  # keep ref so we can delete after
        self._temp_files.add(filter_file.name)

        # Revealing list: set names the user ticked in the Revealing column. The CLI
        # conforms these from the detailed RM_Heavy instead of the flattened RM_HeavyOutfit.
        auto_zap_enabled = self.auto_zap_check.isChecked()
        reduce_clipping_enabled = self.reduce_clipping_check.isChecked()
        clipping_strength = self.fix_clipping_strength.value()
        # Revealing selects the detailed body morph and is independent of
        # Auto-Zap.  Auto-Zap only controls generation of the clipping slider.
        revealing = self._revealing_names(all_members)
        self._revealing_file_path = self._write_revealing_file(all_members)
        # Smart refit list: the outfits ticked in the Smart refit column. An
        # unmarked run sends no argument, so the CLI keeps the plain conform.
        smart_refit = self._smart_refit_names(all_members)
        self._smart_refit_file_path = self._write_smart_refit_file(all_members)

        def write_policy_file(names: list[str]) -> str:
            if not names:
                return ""
            policy_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8")
            policy_file.write("\n".join(names))
            policy_file.close()
            self._temp_files.add(policy_file.name)
            return policy_file.name

        remove_reference_file = write_policy_file([
            name for name in all_members
            if self._scan_data.get(name)
            and self._scan_data[name].output_reference_policy == "remove"
        ])
        force_reference_file = write_policy_file([
            name for name in all_members
            if self._scan_data.get(name)
            and self._scan_data[name].output_reference_policy == "force"
        ])

        # Split by gender using scan overrides when available
        buckets = {"cbbe": [], "himbo": [], "": []}
        for m in all_members:
            info = self._scan_data.get(m)
            g = (info.eff_gender if info else
                 detect_gender(next((grp.name for grp in groups if m in grp.members), "")))
            key = "cbbe" if g in ("3BA", "CBBE") else "himbo" if g == "HIMBO" else ""
            buckets[key].append(m)

        jobs = []
        if self._source_kind == "bodyslide":
            try:
                for gflag, members in buckets.items():
                    by_project: dict[str, list[str]] = {}
                    for name in members:
                        info = self._scan_data.get(name)
                        project = info.source_project if info else ""
                        if not project:
                            raise LookupError(f"selected outfit has no OSP project: {name}")
                        by_project.setdefault(project, []).append(name)
                    for project, project_members in by_project.items():
                        provider = resolve_physical_bodyslide_project(
                            project, set(project_members),
                            virtual_bodyslide_root=bs_data,
                            output_root=out_dir,
                            configured_mods_root=self.config.mods_folder,
                            app_dir=self.config.app_dir,
                        )
                        self._log_info(
                            f"Clean source: {project} <- {provider.mod_root.name}")
                        jobs.append((
                            gflag, project_members, str(provider.body_root),
                            str(provider.project_path),
                            mo2_texture_environment(provider.mod_root.parent),
                        ))
            except LookupError as exc:
                self._cleanup_archive_staging()
                QMessageBox.critical(
                    self, "Could not isolate the original project",
                    "MPPatcher refused to read this outfit from MO2's merged "
                    "ShapeData because an enabled output can replace its input.\n\n"
                    f"{exc}\n\nSet the physical MO2 mods folder in Settings "
                    "or use Local Folder as the source."
                )
                return
        else:
            texture_environment = mo2_texture_environment(self.config.mods_folder)
            jobs = [(gflag, members, bs_data, "", texture_environment)
                    for gflag, members in buckets.items() if members]

        self._pending_runs = []
        for gflag, members, job_bs_data, project_path, process_environment in jobs:
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                             delete=False, encoding="utf-8")
            tf.write("\n".join(members))
            tf.close()
            self._temp_files.add(tf.name)
            # The source can be an extracted archive or a standalone folder;
            # MPP reference projects remain in the installed BodySlide VFS.
            run_args = ["--bs-data", job_bs_data,
                        "--ref-data", self._resolve_bodyslide_root()]
            if project_path:
                run_args += ["--project", project_path]
            else:
                run_args += ["--input-dir", slider_dir]
            run_args += ["--filter-sets", tf.name]
            # The shipped CLI is the isolated public pipeline: it always writes a
            # new data tree after replacing the embedded body with the MPP body.
            run_args += ["--output-dir", out_dir or self._default_output_folder()]
            if auto_zap_enabled:
                run_args += ["--auto-zap"]
            if reduce_clipping_enabled:
                run_args += ["--fix-clipping", "RM_Heavy,RM_Muscular,RM_Thin",
                             "--fix-clipping-strength", f"{clipping_strength:.2f}"]
            if gflag:
                run_args += ["--gender", gflag]
            if self._revealing_file_path:
                run_args += ["--revealing-sets", self._revealing_file_path]
            if self._smart_refit_file_path:
                run_args += ["--smart-refit-sets", self._smart_refit_file_path]
                # Without this pair the CLI looks for the Drape checkout of the
                # machine it was built on. Resolving can copy the bundled
                # runtime out of the VFS, which is why it is announced first.
                self._log_info("Resolving the Drape runtime for Smart refit…")
                drape = drape_environment(self.config.app_dir)
                if drape:
                    process_environment = {**process_environment, **drape}
                    self._log_info(f"Drape runtime: {drape['MPP_DRAPE_ROOT']}")
                elif not os.environ.get("MPP_DRAPE_ROOT"):
                    self._log_warning(
                        f"No bundled Drape runtime ('{DRAPE_RUNTIME_DIRNAME}') next to "
                        "MPPatcher.exe. Smart refit falls back to the Drape location "
                        "compiled into mppatcher-cli, which only exists on a "
                        "development machine.")
            if remove_reference_file:
                run_args += ["--remove-reference-sets", remove_reference_file]
            if force_reference_file:
                run_args += ["--force-reference-sets", force_reference_file]
            self._pending_runs.append((tf.name, run_args, process_environment))

        if not self._pending_runs:
            self._cleanup_archive_staging()
            return

        tf_name, args, process_environment = self._pending_runs.pop(0)
        self._filter_file_path = tf_name

        if self.radio_overwrite.isChecked() and self.backup_check.isChecked():
            bk = self.backup_edit.text().strip()
            if bk:
                self._do_backup(bk)

        self._log_info(f"mppatcher-cli: {cli_exe}")
        # The exact argv, not just the options it was derived from. A run whose
        # result looks like a flag never arrived is otherwise unfalsifiable: the
        # per-option lines below say what the GUI intended, and only this says
        # what the CLI was actually told.
        self._log_info(f"Command:       {subprocess.list2cmdline([cli_exe, *args])}")
        self._log_info(f"bs-data:       {bs_data}")
        self._log_info(f"Sets selected: {len(all_members)}")
        self._log_info(f"Revealing:     {len(revealing)}")
        self._log_info(f"Auto-Zap:      {'enabled' if auto_zap_enabled else 'disabled'}")
        self._log_info(f"Smart refit:   {len(smart_refit)} outfit(s) (ALPHA)"
                       if smart_refit else "Smart refit:   none")
        self._log_info(f"Reduce clip:   {'enabled (' + format(clipping_strength, '.2f') + ')' if reduce_clipping_enabled else 'disabled'}")
        self._log_info(
            f"Buckets:  Female={len(buckets['cbbe'])}  "
            f"Male={len(buckets['himbo'])}  unknown={len(buckets[''])}"
        )

        self._patch_total = len(all_members)
        self._patch_done  = 0
        self._patch_members = set(all_members)
        self._terminal_status = {}
        self._warning_count = 0
        self._user_cancelled = False
        self._show_execution_feedback()
        self._set_patch_controls_enabled(False)
        self.patch_btn.setText("  Patching…")
        self.patch_btn.setIcon(make_icon("pause", 18, "#0d1117"))
        self.stop_btn.setEnabled(True)
        self.progress_bar.setRange(0, max(1, self._patch_total))
        self.progress_bar.setValue(0)
        self.lbl_progress_pct.setText("0%")
        self._elapsed_secs = 0
        self._elapsed_timer.start(1000)
        self.stat_processed.set_value("0")
        self.execute_completion.setText("")
        self.lbl_result_summary.setText("Processed 0  ·  Skipped 0  ·  Failed 0  ·  Warnings 0")

        self.runner.run(cli_exe, args, process_environment)

    def _on_headparts_patch(self):
        """Run the headpart backend through this same packaged executable."""
        members = [m for group in self._checked_groups() for m in group.members]
        source = self._source_path
        if not source or not members:
            QMessageBox.warning(self, "No headparts selected",
                                "Select a headpart source and at least one mesh.")
            return
        try:
            out_dir, archive_path = self._prepare_output_destination(
                self._effective_tri_root or self._source_input_root)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid output destination", str(exc))
            return
        self._active_output_dir = out_dir
        self._active_archive_path = archive_path
        # The executable lives in a separate MO2 tools mod. The active
        # Physiques files are exposed through the game's virtual Data root, not
        # beside that executable, so search Data first and keep physical mods
        # as the standalone fallback.
        mods_root = self.config.mods_folder
        if not mods_root:
            # The normal installation keeps the executable in
            # <instance>\tools\MPPatcher and the physical providers in
            # <instance>\mods. Discover that sibling without using stat calls
            # on the MO2 virtual Data path.
            for parent in Path(self.config.app_dir).parents:
                candidate = parent / "mods"
                try:
                    if any(candidate.iterdir()):
                        mods_root = str(candidate)
                        break
                except OSError:
                    continue
        ref_roots = [self._detect_game_data_path(), self.config.app_dir]
        refs = None
        for ref_root in dict.fromkeys(root for root in ref_roots if root):
            refs = discover_references(ref_root, mods_root)
            if refs:
                break
        if not refs:
            self._cleanup_archive_staging()
            QMessageBox.critical(self, "Physiques references not found",
                                 "Could not find FACE_FemaleHead.tri and FACE_MaleHead.tri "
                                 "from Physiques of Skyrim in the active Data/VFS tree.")
            return
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, []
        else:
            exe, args = sys.executable, [os.path.abspath(sys.argv[0])]
        from core.headparts_cli import default_tag
        rows = getattr(self, "_headpart_rows", {})
        args += ["--headparts", "--mode", "library",
                 "--source", source, "--output-dir", out_dir,
                 "--tag", default_tag(source),
                 "--part", "unclassified",
                 "--default-sex", self._headpart_default_sex(),
                 "--female-ref", refs[0], "--male-ref", refs[1]]
        manual_rows = [row for row in rows.values()
                       if getattr(row, "sex_source", "") == "manual"]
        if manual_rows:
            import tempfile
            override_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8")
            override_file.write("\n".join(
                f"{row.relpath}\t{row.sex}" for row in manual_rows))
            override_file.close()
            self._temp_files.add(override_file.name)
            args += ["--sex-overrides", override_file.name]
        # Meshes inside a BSA are only known once the run opens it, so they can
        # never appear in the filter. Including them is conditional on the user
        # having checked at least one archive row — otherwise a mod with a few
        # loose NIFs beside a large archive reported success over the few.
        archive_rows = [name for name in members
                        if getattr(rows.get(name), "kind", "mesh") == "archive"]
        args += ["--archives", "all" if archive_rows else "none"]

        # No filter at all when every row is checked: it would list every mesh
        # in the mod in order to say "all of them", and the run walks the same
        # tree anyway. Passing one unconditionally is also half of what hid a
        # mod's archive content, since a filter can only ever name loose paths.
        mesh_rows = {name for name, row in rows.items()
                     if getattr(row, "kind", "mesh") == "mesh"}
        if sorted(members) != sorted(rows):
            import tempfile
            filter_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8")
            filter_file.write("\n".join(name for name in members
                                        if name in mesh_rows))
            filter_file.close()
            self._temp_files.add(filter_file.name)
            args += ["--filter-file", filter_file.name]
        self._headpart_mode_active = True
        self._pending_runs = []
        self._patch_total = len(members)
        self._patch_done = 0
        self._patch_members = set(members)
        self._terminal_status = {}
        self._warning_count = 0
        self._user_cancelled = False
        self._elapsed_secs = 0
        self._show_execution_feedback()
        self._elapsed_timer.start(1000)
        self.progress_bar.setRange(0, max(1, self._patch_total))
        self.progress_bar.setValue(0)
        self.lbl_progress_pct.setText("0%")
        self._set_patch_controls_enabled(False)
        self.patch_btn.setText("  Patching…")
        self.stop_btn.setEnabled(True)
        self.execute_completion.setText("")
        self.lbl_result_summary.setText("Processed 0  ·  Skipped 0  ·  Failed 0  ·  Warnings 0")
        self._log_info(f"Headparts source: {source}")
        self._log_info(f"Headparts references: {refs[0]} / {refs[1]}")
        self.runner.run(exe, args)

    def _set_patch_controls_enabled(self, enabled: bool):
        widgets = [
            self.bs_path_edit, self.search_edit, self.group_tree,
            self.output_edit, self.output_mode_folder_btn, self.output_mode_zip_btn,
            self.auto_zap_check,
            self.reduce_clipping_check, self.fix_clipping_strength,
            self.output_browse_btn, self.back_to_review_btn, self.patch_btn,
            self.execute_review_tree, self.target_outfits_btn,
            self.target_headparts_btn, self.source_bodyslide_btn,
            self.source_zip_btn, self.source_folder_btn,
        ]
        for name in ("settings_bs_edit", "settings_output_edit", "settings_backup_edit"):
            widget = getattr(self, name, None)
            if widget is not None:
                widgets.append(widget)
        for widget in widgets:
            widget.setEnabled(enabled)
        for button in getattr(self, "_nav_buttons", []):
            button.setEnabled(enabled)
        self.stop_btn.setEnabled(not enabled)
        self.stop_btn.setVisible(not enabled)
        if enabled:
            self._update_fix_clipping_visibility(self.reduce_clipping_check.isChecked())

    def _on_stop(self):
        self._user_cancelled = True
        for _, pending_args, _environment in getattr(self, "_pending_runs", []):
            for arg in pending_args:
                if isinstance(arg, str) and arg in self._temp_files:
                    self._remove_temp_file(arg)
        self._pending_runs = []
        self.stop_btn.setEnabled(False)
        self.runner.cancel()

    def _remove_temp_file(self, path: str):
        try:
            os.remove(path)
        except OSError:
            pass
        self._temp_files.discard(path)

    def _cleanup_temp_files(self):
        for path in list(self._temp_files):
            self._remove_temp_file(path)

    def _cleanup_source_temp(self):
        """Remove only the temporary directory owned by ZIP staging."""
        if not self._source_temp_root:
            return
        import shutil
        shutil.rmtree(self._source_temp_root, ignore_errors=True)
        self._source_temp_root = None

    def closeEvent(self, event):
        self._close_reveal_popover()
        if self.runner.is_running():
            self.runner.cancel()
        active_scans = [worker for worker in self._scan_workers if worker.isRunning()]
        for worker in active_scans:
            worker.requestInterruption()
        # Do not delete a ZIP staging directory or destroy a QThread while it
        # may still be reading OSP files.  Bound the wait and keep the window
        # alive if a worker needs more time rather than freezing indefinitely.
        for worker in active_scans:
            worker.wait(1200)
        if any(worker.isRunning() for worker in active_scans):
            self._log_warning("Waiting for the active scan to stop before closing.")
            event.ignore()
            return
        self._cleanup_temp_files()
        self._cleanup_source_temp()
        if not self.runner.is_running():
            self._cleanup_archive_staging()
        event.accept()

    def _on_os_finished(self, exit_code: int):
        # Clean up current temp filter file
        fp = getattr(self, "_filter_file_path", None)
        if fp:
            self._remove_temp_file(fp)
            self._filter_file_path = None

        # Run next pending gender bucket if any
        pending = getattr(self, "_pending_runs", [])
        if exit_code == 0 and pending:
            tf_name, args, process_environment = pending.pop(0)
            self._filter_file_path = tf_name
            self._log_info(f"Next run: {args}")
            self.runner.run(
                self.config.mppatcher_cli_exe, args, process_environment)
            return

        # All done
        if pending and exit_code != 0:
            # Abandoning the queue is deliberate - a failing run usually means a
            # bad path or reference that the rest would hit too - but saying so
            # is not optional: the symptom is otherwise just missing outfits.
            projects = []
            for _, pending_args, _environment in pending:
                if "--project" in pending_args:
                    projects.append(ntpath.basename(
                        pending_args[pending_args.index("--project") + 1]))
            self._log_error(
                f"Stopped after a failed run: {len(pending)} queued "
                f"project(s) were not patched"
                + (f" ({', '.join(projects[:6])}"
                   + (", …" if len(projects) > 6 else "") + ")" if projects else "")
                + ". Fix the error above and patch those again."
            )
        self._pending_runs = []
        self._cleanup_temp_files()
        output_root = getattr(self, "_active_output_dir", "")
        self._active_output_dir = ""
        archive_path = getattr(self, "_active_archive_path", "")
        self._active_archive_path = ""
        cancelled = getattr(self, "_user_cancelled", False)
        archive_created = ""
        if output_root and not archive_path:
            changed, permission_errors = normalize_output_ownership(output_root)
            if permission_errors:
                self._log_warning(
                    "Could not normalize ownership for every output file: "
                    + "; ".join(permission_errors[:3]))
            elif changed:
                self._log_info(
                    f"Output permissions normalized for {changed} item(s).")
        if archive_path and exit_code == 0 and not cancelled:
            try:
                self._log_info(f"Creating ZIP archive: {archive_path}")
                archive = create_zip_archive(output_root, archive_path)
                archive_created = str(archive.path)
                self._log_success(
                    f"Created ZIP archive with {archive.file_count} file(s): {archive.path}")
            except Exception as exc:
                exit_code = 1
                self._log_error(f"Could not create ZIP archive: {exc}")
            finally:
                self._cleanup_archive_staging()
        elif archive_path:
            # A cancelled or failed archive run must not leave a visible partial
            # data tree behind; only a complete ZIP is an output in this mode.
            self._cleanup_archive_staging()
        self._elapsed_timer.stop()
        self.patch_btn.setEnabled(True)
        self.patch_btn.setText("  Start Patching")
        self.patch_btn.setIcon(make_icon("play", 18, "#0d1117"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self._user_cancelled = False
        done = getattr(self, "_patch_done", 0)
        total = getattr(self, "_patch_total", 0)
        elapsed = self._elapsed_secs
        t = f"{elapsed//3600}:{(elapsed%3600)//60:02d}:{elapsed%60:02d}"
        if cancelled:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.lbl_progress_pct.setText("0%")
            self.execute_completion.setText(
                f"Patching cancelled after {done}/{total} item(s). No source files were modified.")
            self._log_warning(f"Patching cancelled by user after {done}/{total} outfit(s).")
        elif exit_code == 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.lbl_progress_pct.setText("100%")
            kind = "headpart mesh(es)" if getattr(self, "_headpart_mode_active", False) else "outfit(s)"
            self._log_success(
                f"✓ Patching complete — {done} {kind} processed in {t}.")
            self.stat_processed.set_value(str(done))
            self.execute_completion.setText(
                f"Patching complete: {done} {kind} processed in {t}. "
                + (f"Created: {archive_created}" if archive_created
                   else "The generated files are in the selected output folder."))
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.lbl_progress_pct.setText("0%")
            self.execute_completion.setText(
                f"Patching failed (exit code {exit_code}). Review the Activity Log for details.")
            self._log_error(f"mppatcher-cli exited with code {exit_code}.")
        self._set_patch_controls_enabled(True)
        was_headparts = getattr(self, "_headpart_mode_active", False)
        self._headpart_mode_active = False
        self._refresh_backup_list()
        if not was_headparts:
            self._start_scan()
        if exit_code == 0 and not cancelled:
            output_target = archive_created or output_root
            if output_target:
                self._show_output_completion_dialog(output_target, bool(archive_created))
        elif exit_code != 0 and not cancelled:
            QMessageBox.critical(
                self, "MPPatcher — Processing failed",
                f"The patch process stopped with exit code {exit_code}.\n\n"
                "Check the Activity Log for the exact error.")

    def _show_output_completion_dialog(self, output_target: str, is_archive: bool):
        """Give every successful run an explicit, actionable completion state."""
        label = "ZIP archive" if is_archive else "output folder"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("MPPatcher — Processing complete")
        box.setText(f"Processing finished successfully.\n\n{label}:\n{output_target}")
        show_button = box.addButton("Show in Explorer", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is show_button:
            try:
                if is_archive:
                    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(output_target)])
                else:
                    os.startfile(output_target)
            except OSError as exc:
                self._log_warning(f"Could not open output in Explorer: {exc}")

    def _tick_elapsed(self):
        self._elapsed_secs += 1
        h = self._elapsed_secs // 3600
        m = (self._elapsed_secs % 3600) // 60
        s = self._elapsed_secs % 60
        t = f"{h}:{m:02d}:{s:02d}"
        self.stat_elapsed.set_value(t)
        self.lbl_elapsed.setText(f"Elapsed: {t}")

    def _legacy_on_log_line(self, line: str):
        self._log_info(line)
        if _PROGRESS_RE.match(line):
            self._patch_done = getattr(self, "_patch_done", 0) + 1
            total = getattr(self, "_patch_total", 0)
            if total > 0:
                self.progress_bar.setValue(self._patch_done)
                pct = int(100 * self._patch_done / total)
                self.lbl_progress_pct.setText(f"{pct}%")
                m = re.match(r'^\s+\[\w[\w ]*\]\s+(.+?)(?:\s+[—→].*)?$', line)
                if m:
                    self.lbl_current_outfit.setText(m.group(1).strip())

    # ──────────────────────────────────────────────────────────────────
    def _on_log_line(self, line: str):
        self._log_info(line)
        if line.lstrip().startswith("[WARN"):
            self._warning_count = getattr(self, "_warning_count", 0) + 1
            self._update_result_summary()
        match = _PROGRESS_RE.match(line)
        if not match:
            return
        status, text = match.groups()
        selected = getattr(self, "_patch_members", set())
        # CLI status lines may use an arrow, an ASCII separator, or an error
        # suffix. Prefer an exact selected-set prefix instead of depending on
        # the console encoding of that separator.
        name = text.strip()
        # Each set is reported twice: once under its output name, as
        # "MPP_<set> -> N new slider entries -> <osd>", and once under the
        # selected name. Resolving both to the same key lets the guard below
        # dedupe them; without it every outfit counted twice and the bar hit
        # 100% halfway through the run.
        texts = [text]
        if text.startswith(OUTPUT_SET_PREFIX):
            texts.append(text[len(OUTPUT_SET_PREFIX):])
        for candidate in sorted(selected, key=len, reverse=True):
            if any(t == candidate or t.startswith(candidate + " ")
                   for t in texts):
                name = candidate
                break
        # PATCH is an in-progress message. PATCHED and DONE are terminal
        # successes; do not advance progress until one of those arrives.
        if status == "PATCH":
            self.lbl_current_outfit.setText(name)
            return
        if (status in {"DONE", "PATCHED", "SKIP", "SKIPPED", "ERROR", "FAILED"}
                and name not in self._terminal_status):
            self._terminal_status[name] = status
            self._patch_done = len(self._terminal_status)
            total = max(1, getattr(self, "_patch_total", 0))
            self.progress_bar.setValue(min(self._patch_done, total))
            self.lbl_progress_pct.setText(
                f"{min(100, int(100 * self._patch_done / total))}%")
            self.lbl_current_outfit.setText(name)
            self.stat_processed.set_value(str(self._patch_done))
            self._update_result_summary()

    def _update_result_summary(self):
        statuses = getattr(self, "_terminal_status", {})
        done = sum(1 for status in statuses.values() if status in {"DONE", "PATCHED"})
        skipped = sum(1 for status in statuses.values()
                      if status in {"SKIP", "SKIPPED"})
        failed = sum(1 for status in statuses.values()
                     if status in {"ERROR", "FAILED"})
        warnings = getattr(self, "_warning_count", 0)
        if hasattr(self, "lbl_result_summary"):
            self.lbl_result_summary.setText(
                f"Processed {done}  ·  Skipped {skipped}  ·  Failed {failed}  ·  Warnings {warnings}")

    # Backup / restore
    # ──────────────────────────────────────────────────────────────────
    def _do_backup(self, backup_folder: str):
        sf = os.path.join(self.config.app_dir, "SliderSets")
        names = [f for f in vfs_listdir(sf) if f.endswith((".osp", ".osd"))]
        if not names:
            return
        files = [os.path.join(sf, f) for f in names]
        result = backup_manager.create_backup(files, backup_folder)
        if result:
            self._log_info(f"Backup: {os.path.basename(result)}")

    def _refresh_backup_list(self):
        self.backup_list.clear()
        bk = self.backup_edit.text().strip() or self.config.backup_folder
        if not bk:
            return
        for e in backup_manager.list_backups(bk):
            item = QListWidgetItem(f"{e.timestamp}  —  {e.file_count} files")
            item.setData(Qt.UserRole, e.zip_path)
            self.backup_list.addItem(item)

    def _on_restore(self):
        item = self.backup_list.currentItem()
        if not item:
            return
        zip_path = item.data(Qt.UserRole)
        if QMessageBox.question(self, "Restore Backup",
                f"Restore this backup?\n\n{zip_path}",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        if backup_manager.restore_backup(zip_path, [self.config.app_dir]):
            self._log_success("Backup restored.")
        else:
            self._log_error("Restore failed.")

    # ──────────────────────────────────────────────────────────────────
    # Logging
    # ──────────────────────────────────────────────────────────────────
    def _log_file_path(self) -> str:
        """Run log next to the exe, so a finished run can still be diagnosed.

        The panel dies with the window, which left questions like "did Smart
        refit actually run" unanswerable after the fact.
        """
        base = self.config.app_dir or os.path.dirname(os.path.abspath(sys.argv[0]))
        return os.path.join(base, "MPPatcher.log")

    def _log_to_file(self, level: str, ts: str, msg: str):
        # Logging must never take the UI down with it: a read-only install
        # directory is a configuration problem, not a reason to stop patching.
        try:
            with open(self._log_file_path(), "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {level:<7} {msg}\n")
        except OSError:
            pass

    def _log(self, level: str, color: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_to_file(level, ts, str(msg))
        safe_msg = html.escape(str(msg))
        colors = DARK_THEME if self._effective_theme() == "dark" else LIGHT_THEME
        html_output = (f'<span style="color:{colors["text_dim"]}">[{ts}]</span> '
                       f'<span style="color:{color};font-weight:bold">{level}</span> '
                       f'<span style="color:{colors["text"]}">{safe_msg}</span>')
        self.log_box.append(html_output)

    def _log_info(self, msg: str):    self._log("Info",    ACCENT,   msg)
    def _log_success(self, msg: str): self._log("Success", SUCCESS,  msg)
    def _log_error(self, msg: str):   self._log("Error",   ERROR,    msg)
    def _log_warning(self, msg: str): self._log("Warning", WARNING,  msg)

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _detect_game_data_path(self) -> str:
        # Open directly (reads work through usvfs; isfile does not).  The
        # configured BodySlide directory is also checked because MPPatcher is
        # normally installed as a separate MO2 mod and therefore its own
        # executable directory has no Config.xml.
        config_paths = [
            os.path.join(self.config.bodyslide_path, "Config.xml"),
            os.path.join(self.config.app_dir, "Config.xml"),
        ]
        for config_xml in dict.fromkeys(p for p in config_paths if p):
            try:
                import xml.etree.ElementTree as ET
                root = ET.parse(config_xml).getroot()
                p = root.findtext("GameDataPath", "").strip()
                if p:
                    return p.replace("\\", "/").rstrip("/")
            except Exception:
                continue
        return "C:/program files (x86)/steam/steamapps/common/Skyrim Special Edition/Data"

    def _resolve_bodyslide_root(self) -> str:
        """Find the same effective project root that BodySlide uses.

        Under MO2 the first candidate is the game Data path, whose
        ``CalienteTools/BodySlide`` directory is supplied by USVFS.  We never
        union that directory with physical mod folders in the normal source
        mode.  Configured and executable directories remain standalone
        fallbacks for users running outside MO2.
        """
        data_root = self._detect_game_data_path()
        configured = (self.config.bodyslide_path or "").strip()
        candidates = [
            os.path.join(data_root, "CalienteTools", "BodySlide"),
            configured,
            self.config.app_dir,
        ]
        for root in dict.fromkeys(path for path in candidates if path):
            if (vfs_isdir(os.path.join(root, "SliderGroups"))
                    or vfs_isdir(os.path.join(root, "SliderSets"))):
                return root
        # Keep the configured value visible even before its VFS is mounted;
        # this allows the next refresh to pick it up without rewriting settings.
        return configured or self.config.app_dir

    def _on_bs_path_changed(self):
        self.config.bodyslide_path = self.bs_path_edit.text().strip()
        if hasattr(self, "settings_bs_edit"):
            self.settings_bs_edit.setText(self.config.bodyslide_path)
        if self._source_kind == "bodyslide":
            root = self._resolve_bodyslide_root()
            self._set_selection_context(self._target_kind, self._source_kind, root)
            self._source_input_root = root
            self._source_work_root = root
        self._update_status_badges()
        self._reload_groups()
        self._refresh_backup_list()

    def _on_output_destination_changed(self):
        value = self.output_edit.text().strip()
        if self._output_mode() == "zip":
            self.config.output_archive = value
        else:
            self.config.output_folder = value
        if self._output_mode() != "zip" and hasattr(self, "settings_output_edit"):
            self.settings_output_edit.setText(value)

    def _browse_bs_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select BodySlide folder", self.bs_path_edit.text())
        if path:
            self.bs_path_edit.setText(path)
            self._on_bs_path_changed()

    def _browse_folder(self, edit: QLineEdit, key: str):
        path = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if path:
            edit.setText(path)
            setattr(self.config, key, path)
            if key == "bodyslide_path":
                self.bs_path_edit.setText(path)
                if hasattr(self, "settings_bs_edit"):
                    self.settings_bs_edit.setText(path)
                self._on_bs_path_changed()
            elif key == "output_folder":
                if self._output_mode() != "zip":
                    self.output_edit.setText(path)
                if hasattr(self, "settings_output_edit"):
                    self.settings_output_edit.setText(path)
            elif key == "backup_folder" and hasattr(self, "settings_backup_edit"):
                self.settings_backup_edit.setText(path)
                self._refresh_backup_list()

    def _update_output_visibility(self):
        # The public pipeline is output-only. Keep the old controls visible enough
        # to explain the change, but never route a run into an in-place overwrite.
        self._backup_box.setVisible(False)
        self._out_folder_row_widget.setVisible(True)


# ── Tiny helpers ──────────────────────────────────────────────────────
def _bold(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {TEXT}; font-weight: bold;")
    return lbl

def _dim(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("dim")
    return lbl
