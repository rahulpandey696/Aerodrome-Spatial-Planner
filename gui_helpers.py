#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui_helpers.py
==============
Reusable GUI helper widgets for the Airport Runway Planner Suite.
Compatible with QGIS 3.x (Qt5) and QGIS 4.x (Qt6) on Windows/Linux/macOS.

Classes
-------
EnhancedWindow       – QMainWindow base with dark-mode toggle and status bar.
ImageViewerDialog    – Mouse-wheel zoom + drag-to-pan image viewer.
AnimatedProgressBar  – Progress bar with animated airplane emoji.
"""

# ---------------------------------------------------------------------------
# QGIS / Qt imports — use string-safe attribute access for Qt5/Qt6 compat
# ---------------------------------------------------------------------------
from qgis.PyQt.QtCore  import Qt, QTimer, QSize, QPoint
from qgis.PyQt.QtGui   import (QIcon, QColor, QFont, QPixmap, QImage,
                                QPainter, QMovie, QCursor)
from qgis.PyQt.QtWidgets import (
    QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QAction, QMenu,
    QFileDialog, QToolBar, QScrollBar,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QStatusBar, QApplication,
)

# ---------------------------------------------------------------------------
# Qt enum compat helpers (Qt5 uses plain ints, Qt6 uses scoped enums)
# ---------------------------------------------------------------------------

def _qt(obj, *attrs):
    """Safely traverse a chain of Qt enum attributes, return first that works."""
    for attr in attrs:
        parts = attr.split('.')
        v = obj
        try:
            for p in parts:
                v = getattr(v, p)
            return v
        except AttributeError:
            continue
    raise AttributeError(f"None of {attrs} found on {obj}")


# Window flags — Qt6 scoped vs Qt5 unscoped
try:
    _WIN_FLAGS = (Qt.WindowType.Window |
                  Qt.WindowType.WindowMinimizeButtonHint |
                  Qt.WindowType.WindowMaximizeButtonHint |
                  Qt.WindowType.WindowCloseButtonHint)
except AttributeError:
    _WIN_FLAGS = (Qt.Window |                      # type: ignore
                  Qt.WindowMinimizeButtonHint |    # type: ignore
                  Qt.WindowMaximizeButtonHint |    # type: ignore
                  Qt.WindowCloseButtonHint)        # type: ignore

try:
    _KEEP_ASPECT = Qt.AspectRatioMode.KeepAspectRatio
except AttributeError:
    _KEEP_ASPECT = Qt.KeepAspectRatio              # type: ignore

try:
    _ANCHOR_MOUSE = QGraphicsView.ViewportAnchor.AnchorUnderMouse
except AttributeError:
    _ANCHOR_MOUSE = QGraphicsView.AnchorUnderMouse  # type: ignore

try:
    _SCROLL_OFF = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
except AttributeError:
    _SCROLL_OFF = Qt.ScrollBarAlwaysOff            # type: ignore

try:
    _CURSOR_OPEN   = Qt.CursorShape.OpenHandCursor
    _CURSOR_CLOSED = Qt.CursorShape.ClosedHandCursor
    _CURSOR_ARROW  = Qt.CursorShape.ArrowCursor
except AttributeError:
    _CURSOR_OPEN   = Qt.OpenHandCursor             # type: ignore
    _CURSOR_CLOSED = Qt.ClosedHandCursor           # type: ignore
    _CURSOR_ARROW  = Qt.ArrowCursor                # type: ignore

try:
    _BTN_LEFT = Qt.MouseButton.LeftButton
except AttributeError:
    _BTN_LEFT = Qt.LeftButton                      # type: ignore

try:
    _CTRL = Qt.KeyboardModifier.ControlModifier
except AttributeError:
    _CTRL = Qt.ControlModifier                     # type: ignore

try:
    _KEY_PLUS  = Qt.Key.Key_Plus
    _KEY_EQUAL = Qt.Key.Key_Equal
    _KEY_MINUS = Qt.Key.Key_Minus
    _KEY_0     = Qt.Key.Key_0
except AttributeError:
    _KEY_PLUS  = Qt.Key_Plus                       # type: ignore
    _KEY_EQUAL = Qt.Key_Equal                      # type: ignore
    _KEY_MINUS = Qt.Key_Minus                      # type: ignore
    _KEY_0     = Qt.Key_0                          # type: ignore

try:
    _RENDER_AA    = QPainter.RenderHint.Antialiasing
    _RENDER_SMOOTH= QPainter.RenderHint.SmoothPixmapTransform
except AttributeError:
    _RENDER_AA    = QPainter.Antialiasing          # type: ignore
    _RENDER_SMOOTH= QPainter.SmoothPixmapTransform # type: ignore

try:
    _DRAG_NONE   = QGraphicsView.DragMode.NoDrag
    _DRAG_SCROLL = QGraphicsView.DragMode.ScrollHandDrag
except AttributeError:
    _DRAG_NONE   = QGraphicsView.NoDrag            # type: ignore
    _DRAG_SCROLL = QGraphicsView.ScrollHandDrag    # type: ignore

try:
    _ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    _ALIGN_CENTER = Qt.AlignCenter                 # type: ignore


# ============================================================================
# ENHANCED WINDOW BASE CLASS
# ============================================================================

class EnhancedWindow(QMainWindow):
    """
    QMainWindow subclass with:
    • Dark / light mode toggle
    • Status bar
    Compatible with QGIS 3.x (Qt5) and QGIS 4.x (Qt6).

    NOTE: Font/UI scaling via Ctrl+/- is intentionally removed because
    QGIS 4 on Windows uses system DPI scaling and manual font scaling
    causes layout breakage.  Use QGIS's own View > Zoom instead.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dark_mode   = False
        self.ui_scale    = 1.0   # kept for API compatibility

        self.setWindowFlags(_WIN_FLAGS)
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self._create_menu_bar()
        self.status_bar = self.statusBar()
        self._apply_styling()

    # ── Menu ─────────────────────────────────────────────────────────────────

    def _create_menu_bar(self):
        menubar  = self.menuBar()
        view_menu = menubar.addMenu('View')

        # Dark mode toggle
        self.dark_mode_action = QAction('Dark Mode', self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(False)
        self.dark_mode_action.triggered.connect(self._toggle_dark_mode)
        view_menu.addAction(self.dark_mode_action)

    # ── Styling ───────────────────────────────────────────────────────────────

    def _apply_styling(self):
        if self.dark_mode:
            self.setStyleSheet("""
                QMainWindow,QDialog,QWidget{background:#2d2d2d;color:#e0e0e0;}
                QStatusBar{background:#1a1a1a;color:#e0e0e0;}
                QMenuBar{background:#3c3c3c;color:#e0e0e0;}
                QMenuBar::item:selected{background:#5a5a5a;}
                QMenu{background:#3c3c3c;color:#e0e0e0;border:1px solid #5a5a5a;}
                QMenu::item:selected{background:#5a5a5a;}
                QToolBar{background:#3c3c3c;border:none;}
                QTabWidget::pane{background:#2d2d2d;border:1px solid #5a5a5a;}
                QTabBar::tab{background:#3c3c3c;color:#e0e0e0;padding:6px 12px;margin-right:2px;}
                QTabBar::tab:selected{background:#2d2d2d;}
                QGroupBox{color:#e0e0e0;border:1px solid #5a5a5a;margin-top:8px;padding-top:6px;}
                QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px;}
                QLabel{color:#e0e0e0;}
                QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit,QDateEdit,QTableWidget{
                    background:#3c3c3c;color:#e0e0e0;border:1px solid #5a5a5a;
                    border-radius:3px;padding:4px;}
                QComboBox QAbstractItemView{background:#3c3c3c;color:#e0e0e0;
                    selection-background-color:#5a5a5a;}
                QTableWidget::item:selected{background:#5a5a5a;}
                QHeaderView::section{background:#3c3c3c;color:#e0e0e0;border:1px solid #5a5a5a;}
                QPushButton{background:#3c3c3c;color:#e0e0e0;border:1px solid #5a5a5a;
                    border-radius:3px;padding:5px 10px;}
                QPushButton:hover{background:#5a5a5a;}
                QPushButton:pressed{background:#7a7a7a;}
                QCheckBox,QRadioButton{color:#e0e0e0;spacing:6px;}
                QCheckBox::indicator,QRadioButton::indicator{
                    width:14px;height:14px;border:1px solid #95a5a6;border-radius:2px;background:white;}
                QCheckBox::indicator:checked{background:#2c5282;border-color:#2c5282;}
                QProgressBar{border:2px solid #5a5a5a;border-radius:4px;text-align:center;color:#e0e0e0;}
                QProgressBar::chunk{background:#2c5282;}
                QScrollBar:vertical{background:#2d2d2d;width:10px;border-radius:5px;}
                QScrollBar::handle:vertical{background:#5a5a5a;border-radius:5px;min-height:20px;}
                QScrollBar::handle:vertical:hover{background:#7a7a7a;}
                QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
            """)
        else:
            self.setStyleSheet("""
                QMainWindow,QDialog,QWidget{
                    background-color:#ece9d8;
                    color:#000000;
                    font-family:'Tahoma','Arial',sans-serif;
                    font-size:11px;}
                QStatusBar{background-color:#ece9d8;color:#000000;
                    border-top:1px solid #aca899;padding:2px;}
                QMenuBar{background-color:#ece9d8;color:#000000;
                    border-bottom:1px solid #aca899;}
                QMenuBar::item:selected{background-color:#316ac5;color:#ffffff;}
                QMenu{background-color:#ffffff;border:1px solid #aca899;padding:2px 0;}
                QMenu::item:selected{background-color:#316ac5;color:#ffffff;}
                QTabWidget::pane{background-color:#ece9d8;
                    border:1px solid #aca899;border-top:2px solid #316ac5;}
                QTabBar::tab{background-color:#d4d0c8;color:#000000;
                    border:1px solid #aca899;border-bottom:none;
                    padding:5px 12px;margin-right:1px;
                    border-top-left-radius:2px;border-top-right-radius:2px;
                    font-weight:bold;font-size:10px;}
                QTabBar::tab:selected{background-color:#ece9d8;color:#000000;
                    border-bottom:2px solid #ece9d8;margin-bottom:-1px;}
                QTabBar::tab:hover:!selected{background-color:#e8e4d8;}
                QGroupBox{background-color:#ece9d8;
                    border:2px groove #aca899;border-radius:0px;
                    margin-top:18px;padding-top:6px;
                    font-weight:bold;color:#003c74;}
                QGroupBox::title{subcontrol-origin:margin;
                    subcontrol-position:top left;
                    left:8px;top:-1px;padding:2px 5px;
                    background-color:#ece9d8;color:#003c74;font-size:11px;}
                QLabel{color:#000000;background:transparent;}
                QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit,QDateEdit{
                    background-color:#ffffff;color:#000000;
                    border-top:2px solid #7f9db9;border-left:2px solid #7f9db9;
                    border-right:1px solid #ffffff;border-bottom:1px solid #ffffff;
                    padding:2px 4px;min-height:20px;
                    selection-background-color:#316ac5;selection-color:#ffffff;}
                QComboBox QAbstractItemView{background:#ffffff;
                    border:1px solid #7f9db9;
                    selection-background-color:#316ac5;selection-color:#ffffff;}
                QTableWidget{background-color:#ffffff;
                    gridline-color:#d4d0c8;
                    selection-background-color:#316ac5;
                    selection-color:#ffffff;
                    border:2px solid #7f9db9;
                    alternate-background-color:#f0eee8;}
                QTableWidget::item:selected{background-color:#316ac5;}
                QHeaderView::section{
                    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                        stop:0 #f4f2ec,stop:0.5 #ece9d8,stop:1 #d4d0c8);
                    color:#000000;
                    border-top:1px solid #ffffff;border-left:1px solid #ffffff;
                    border-right:1px solid #808080;border-bottom:1px solid #808080;
                    padding:4px;font-weight:bold;}
                QPushButton{
                    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                        stop:0 #f4f2ec,stop:0.45 #ece9d8,
                        stop:0.55 #dedad0,stop:1 #cdc9be);
                    color:#000000;
                    border-top:2px solid #ffffff;border-left:2px solid #ffffff;
                    border-right:2px solid #808080;border-bottom:2px solid #808080;
                    border-radius:2px;padding:4px 12px;min-height:22px;
                    font-family:'Tahoma',Arial;font-size:11px;}
                QPushButton:hover{
                    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                        stop:0 #f0f4fe,stop:0.45 #dce8fc,
                        stop:0.55 #ccdaf8,stop:1 #b8ccf0);
                    border-right:2px solid #316ac5;border-bottom:2px solid #316ac5;}
                QPushButton:pressed{
                    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                        stop:0 #b8b4ac,stop:1 #ccc8c0);
                    border-top:2px solid #808080;border-left:2px solid #808080;
                    border-right:2px solid #ffffff;border-bottom:2px solid #ffffff;}
                QPushButton:disabled{
                    background:#d4d0c8;color:#aca899;
                    border-top:2px solid #e8e4dc;border-left:2px solid #e8e4dc;
                    border-right:2px solid #b0aca4;border-bottom:2px solid #b0aca4;}
                QCheckBox,QRadioButton{color:#000000;spacing:6px;background:transparent;}
                QCheckBox::indicator,QRadioButton::indicator{
                    width:13px;height:13px;
                    border-top:2px solid #7f9db9;border-left:2px solid #7f9db9;
                    border-right:1px solid #d4d0c8;border-bottom:1px solid #d4d0c8;
                    background-color:#ffffff;}
                QCheckBox::indicator:checked{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 #ffffff,stop:0.3 #dce8fc,stop:1 #b0c8f4);
                    border-top:2px solid #316ac5;border-left:2px solid #316ac5;}
                QRadioButton::indicator{border-radius:7px;}
                QRadioButton::indicator:checked{
                    background:qlineargradient(x1:0.2,y1:0.2,x2:0.8,y2:0.8,
                        stop:0 #316ac5,stop:1 #1040a0);
                    border:2px solid #7f9db9;}
                QProgressBar{background-color:#ffffff;
                    border-top:2px solid #7f9db9;border-left:2px solid #7f9db9;
                    border-right:1px solid #d4d0c8;border-bottom:1px solid #d4d0c8;
                    text-align:center;color:#000000;height:16px;}
                QProgressBar::chunk{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #0da030,stop:0.5 #11BA38,stop:1 #0da030);
                    margin:1px;width:10px;}
                QTextEdit{font-family:'Courier New','Consolas',monospace;font-size:10px;
                    background-color:#ffffff;
                    border-top:2px solid #7f9db9;border-left:2px solid #7f9db9;
                    border-right:1px solid #d4d0c8;border-bottom:1px solid #d4d0c8;}
                QScrollBar:vertical{background-color:#d4d0c8;width:17px;
                    border:1px solid #aca899;}
                QScrollBar::handle:vertical{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #f4f2ec,stop:0.5 #ece9d8,stop:1 #d4d0c8);
                    border-top:1px solid #ffffff;border-left:1px solid #ffffff;
                    border-right:1px solid #808080;border-bottom:1px solid #808080;
                    min-height:20px;margin:1px;}
                QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
                QScrollArea{border:none;background:transparent;}
                QToolBar{background-color:#ece9d8;border:none;spacing:3px;padding:2px;}
                QToolBar QToolButton{color:#000000;background:transparent;border:none;
                    border-radius:2px;padding:3px 6px;}
                QToolBar QToolButton:hover{
                    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                        stop:0 #dce8fc,stop:1 #b8d0f8);
                    border:1px solid #316ac5;}
            """)

    def _toggle_dark_mode(self, checked):
        self.dark_mode = checked
        self._apply_styling()

    # ── Kept for API compatibility with callers ───────────────────────────────
    def apply_styling(self):
        self._apply_styling()

    def toggle_dark_mode(self, checked):
        self._toggle_dark_mode(checked)

    def zoom_in(self):
        pass   # No-op: let QGIS handle UI scaling via system DPI

    def zoom_out(self):
        pass

    def reset_zoom(self):
        pass

    def set_font_scale(self, scale):
        pass

    def apply_ui_scaling(self):
        pass

    def create_menu_bar(self):
        self._create_menu_bar()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)


# ============================================================================
# ZOOMABLE GRAPHICS VIEW (mouse-wheel zoom + drag-to-pan)
# ============================================================================

class _ZoomableGraphicsView(QGraphicsView):
    """
    QGraphicsView with mouse-wheel zoom and left-drag pan.
    Works on Qt5 and Qt6.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom        = 1.0
        self._zoom_min    = 0.02
        self._zoom_max    = 50.0
        self._zoom_factor = 1.18
        self._panning     = False
        self._pan_start   = None

        self.setDragMode(_DRAG_NONE)
        try:
            self.setTransformationAnchor(_ANCHOR_MOUSE)
            self.setResizeAnchor(_ANCHOR_MOUSE)
        except Exception:
            pass
        self.setVerticalScrollBarPolicy(_SCROLL_OFF)
        self.setHorizontalScrollBarPolicy(_SCROLL_OFF)
        self.setCursor(_CURSOR_OPEN)
        self.setRenderHints(_RENDER_AA | _RENDER_SMOOTH)

    # ── Mouse wheel → zoom ────────────────────────────────────────────────────
    def wheelEvent(self, event):
        try:
            delta = event.angleDelta().y()
        except AttributeError:
            delta = event.delta()
        if delta == 0:
            return
        factor = self._zoom_factor if delta > 0 else 1.0 / self._zoom_factor
        new_z  = self._zoom * factor
        if self._zoom_min <= new_z <= self._zoom_max:
            self._zoom = new_z
            self.scale(factor, factor)
        event.accept()

    # ── Left-drag → pan ───────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == _BTN_LEFT:
            self._panning   = True
            self._pan_start = event.pos()
            self.setCursor(_CURSOR_CLOSED)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            d = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - d.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == _BTN_LEFT:
            self._panning = False
            self.setCursor(_CURSOR_OPEN)
        super().mouseReleaseEvent(event)

    # ── Double-click → fit ────────────────────────────────────────────────────
    def mouseDoubleClickEvent(self, event):
        self.fit_to_window()
        super().mouseDoubleClickEvent(event)

    def fit_to_window(self):
        if self.scene() and self.scene().items():
            self.fitInView(self.scene().itemsBoundingRect(), _KEEP_ASPECT)
            try:
                self._zoom = self.transform().m11()
            except Exception:
                self._zoom = 1.0


# ============================================================================
# IMAGE VIEWER DIALOG
# ============================================================================

class ImageViewerDialog(QDialog):
    """
    Full-featured image viewer:
    • Mouse-wheel zoom (centred on cursor)
    • Left-drag to pan
    • Double-click to fit
    • Toolbar: Zoom In / Zoom Out / Fit / 100% / 200% / Save As
    • Live zoom % label
    """

    def __init__(self, title, image_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(900, 650)
        self.image_path = image_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = QToolBar()

        def _btn(label, slot, tip=''):
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            b.setStyleSheet(
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                "stop:0 #f4f2ec,stop:0.5 #ece9d8,stop:1 #d4d0c8);"
                "color:#000000;"
                "border-top:2px solid #ffffff;border-left:2px solid #ffffff;"
                "border-right:2px solid #808080;border-bottom:2px solid #808080;"
                "border-radius:2px;padding:2px 10px;font-family:'Tahoma',Arial;font-size:10px;}"
                "QPushButton:hover{"
                "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                "stop:0 #e8f0fc,stop:1 #b8d0f8);"
                "border-right:2px solid #316ac5;border-bottom:2px solid #316ac5;}"
                "QPushButton:pressed{"
                "background:#c8c4bc;"
                "border-top:2px solid #808080;border-left:2px solid #808080;"
                "border-right:2px solid #ffffff;border-bottom:2px solid #ffffff;}"
            )
            toolbar.addWidget(b)
            return b

        _btn("🔍 Zoom In",  self._zoom_in,  "Zoom In  (or scroll wheel)")
        _btn("🔎 Zoom Out", self._zoom_out, "Zoom Out (or scroll wheel)")
        _btn("⬜ Fit",       self._fit,      "Fit image to window (or double-click)")
        toolbar.addSeparator()
        _btn("100%", self._zoom_100, "Actual pixel size")
        _btn("200%", self._zoom_200, "200% zoom")
        toolbar.addSeparator()
        _btn("💾 Save As…", self._save, "Save image to file")

        self._zoom_label = QLabel("  100%  ")
        self._zoom_label.setStyleSheet("padding:0 8px;color:#000000;font-family:'Tahoma',Arial;font-size:10px;")
        toolbar.addWidget(self._zoom_label)

        layout.addWidget(toolbar)

        # ── Hint ──────────────────────────────────────────────────────────────
        hint = QLabel("🖱 Scroll to zoom  |  Drag to pan  |  Double-click to fit")
        hint.setStyleSheet("color:#444433;font-size:10px;padding:2px 4px;"
                           "background:#ece9d8;border-bottom:1px solid #aca899;"
                           "font-family:'Tahoma',Arial;")
        layout.addWidget(hint)

        # ── View ──────────────────────────────────────────────────────────────
        self.view  = _ZoomableGraphicsView(self)
        self.scene = QGraphicsScene(self)
        self.view.setScene(self.scene)
        self._pxitem = QGraphicsPixmapItem()
        self.scene.addItem(self._pxitem)

        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self._pxitem.setPixmap(pixmap)
            self.scene.setSceneRect(self._pxitem.boundingRect())

        layout.addWidget(self.view, 1)
        self.setLayout(layout)

        # Update zoom label periodically
        self._t = QTimer(self)
        self._t.timeout.connect(self._upd_label)
        self._t.start(250)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(60, self._fit)

    def _zoom_in(self):
        f = self.view._zoom_factor
        if self.view._zoom * f <= self.view._zoom_max:
            self.view._zoom *= f
            self.view.scale(f, f)

    def _zoom_out(self):
        f = 1.0 / self.view._zoom_factor
        if self.view._zoom * f >= self.view._zoom_min:
            self.view._zoom *= f
            self.view.scale(f, f)

    def _fit(self):
        self.view.fit_to_window()

    def _zoom_100(self):
        self.view.resetTransform()
        self.view._zoom = 1.0

    def _zoom_200(self):
        self.view.resetTransform()
        self.view._zoom = 2.0
        self.view.scale(2.0, 2.0)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Image", "",
            "PNG Files (*.png);;JPEG Files (*.jpg);;All Files (*)")
        if path:
            self._pxitem.pixmap().save(path)

    def _upd_label(self):
        pct = int(self.view._zoom * 100)
        self._zoom_label.setText(f"  {pct}%  ")


# ============================================================================
# ANIMATED PROGRESS BAR
# ============================================================================

class AnimatedProgressBar(QWidget):
    """
    QProgressBar with an animated ✈ emoji tracking progress position.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(36)
        self.progress_value     = 0
        self.animation_offset   = 0
        self.animation_direction= 1
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar{border:2px solid #c8cdd2;border-radius:4px;
                         text-align:center;color:#2c3e50;background:#f2f3f5;}
            QProgressBar::chunk{background:#11BA38;border-radius:3px;}
        """)
        layout.addWidget(self.progress_bar, 1)

        self.airplane_label = QLabel("✈")
        self.airplane_label.setStyleSheet(
            "font-size:20px;color:magenta;background:transparent;border:none;")
        self.airplane_label.setFixedSize(28, 28)
        self.airplane_label.setAlignment(_ALIGN_CENTER)
        layout.addWidget(self.airplane_label, 0)

        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self._animate)

    def setValue(self, value):
        self.progress_value = value
        self.progress_bar.setValue(value)
        self._update_position()

    def _update_position(self):
        bar_w = self.progress_bar.width()
        pos_x = int(bar_w * self.progress_value / 100) - self.airplane_label.width() // 2
        pos_x = max(0, min(pos_x, bar_w - self.airplane_label.width()))
        self.airplane_label.move(pos_x, self.airplane_label.y())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_position()

    def start_animation(self):
        self.animation_timer.start(120)

    def stop_animation(self):
        self.animation_timer.stop()

    def _animate(self):
        self.animation_offset += self.animation_direction * 2
        if abs(self.animation_offset) > 4:
            self.animation_direction *= -1
        y = self.airplane_label.y() + self.animation_direction
        y = max(0, min(y, self.height() - self.airplane_label.height()))
        self.airplane_label.move(self.airplane_label.x(), y)

    def showEvent(self, event):
        self.start_animation()
        super().showEvent(event)

    def hideEvent(self, event):
        self.stop_animation()
        super().hideEvent(event)
