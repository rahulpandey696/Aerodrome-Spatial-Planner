#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
airport_planner.py
==================
Main plugin module – QGIS toolbox window and plugin entry point.

Contains
--------
ProfessionalAirportPlannerToolbox
    Full Qt main-window hosting all analysis tabs, controls and result displays.
AirportPlannerSuite
    Thin QGIS plugin wrapper (initGui / unload / run).
classFactory()
    Module-level entry point required by QGIS plugin loader.
"""

import os, sys, math, json, time, shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import requests

from qgis.PyQt.QtCore import (
    QSettings, QTranslator, QCoreApplication, Qt,
    QDateTime, QThread, pyqtSignal, QUrl, QTimer, QSize,
    QPropertyAnimation, QEasingCurve, QDate,
)
from qgis.PyQt.QtGui import (
    QIcon, QColor, QFont, QPixmap, QImage, QPainter,
    QFontMetrics, QMovie, QDesktopServices,
)
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QComboBox, QLineEdit, QPushButton,
    QCheckBox, QSpinBox, QDoubleSpinBox, QDateEdit,
    QProgressBar, QTabWidget, QTextEdit, QFileDialog,
    QMessageBox, QListWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QToolBox,
    QFrame, QApplication, QToolButton, QSizePolicy, QWidget,
    QRadioButton, QButtonGroup, QDialogButtonBox, QScrollArea,
    QScrollBar, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QTreeWidget, QTreeWidgetItem,
    QStyledItemDelegate, QStyle, QMainWindow, QMenuBar, QMenu,
    QSlider, QStyleFactory, QSizeGrip, QToolBar, QInputDialog,
)
from qgis.gui import (
    QgsMapToolEmitPoint, QgsDoubleSpinBox, QgsFileWidget,
    QgsFieldComboBox, QgsColorButton, QgsMessageBar, QgsMapCanvas,
    QgsProjectionSelectionWidget, QgsRasterBandComboBox,
    QgsMapLayerComboBox, QgsVertexMarker, QgsRubberBand,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsFields, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsRasterFileWriter,
    QgsProcessing, QgsProcessingContext, QgsProcessingFeedback,
    QgsSymbol, QgsSingleSymbolRenderer, QgsFillSymbol,
    QgsLineSymbol, QgsMarkerSymbol,
    QgsRendererCategory, QgsCategorizedSymbolRenderer,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
    QgsMapLayerProxyModel, QgsUnitTypes, QgsDistanceArea,
    QgsPoint, QgsRectangle, QgsMessageLog, QgsExpression,
    QgsMapLayer, QgsFeatureRequest, QgsCoordinateTransformContext,
    QgsApplication, QgsVectorDataProvider,
    QgsGraduatedSymbolRenderer, QgsColorRampShader, QgsRasterShader,
    QgsSingleBandPseudoColorRenderer, QgsStyle,
    QgsSimpleFillSymbolLayer, QgsLayerTree,
    QgsMapSettings, QgsPrintLayout, QgsLayoutExporter,
    QgsLayoutItemMap, QgsLayoutItemLegend, QgsLayoutItemLabel,
    QgsLayoutItemScaleBar, QgsLayoutItemPicture,
    QgsLayoutPoint, QgsLayoutSize, QgsLayoutMeasurement,
    QgsLayoutAligner, QgsReadWriteContext, QgsRasterBandStats,
    QgsTextBufferSettings, QgsRendererRange,
)
import processing
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# CRITICAL: inject plugin directory into sys.path BEFORE any plugin imports
# Plain absolute imports — guaranteed to work on Windows QGIS 4 / Python 3.12
# ---------------------------------------------------------------------------
import sys as _sys, os as _os
_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _THIS_DIR not in _sys.path:
    _sys.path.insert(0, _THIS_DIR)

from icao_standards  import ICAOStandards, ICAOComplianceChecker          # noqa: E402
from engine          import ICAOCompliantAirportRunwayPlanner              # noqa: E402
from analysis_thread import ProfessionalAnalysisThread                     # noqa: E402
from gui_helpers     import EnhancedWindow, ImageViewerDialog, AnimatedProgressBar  # noqa: E402

PLUGIN_DIR        = os.path.dirname(os.path.abspath(__file__))
BANNER_PATH       = os.path.join(PLUGIN_DIR, "banner.png")
AIRPLANE_GIF_PATH = os.path.join(PLUGIN_DIR, "airplane.gif")

class ProfessionalAirportPlannerToolbox(EnhancedWindow):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.message_bar = iface.messageBar()
        self.analysis_thread = None
        self.results = None
        self.planner = None
        self.setupUi()
        self.connect_signals()
        self.set_default_values()
        self.apply_professional_styling()
        self.setWindowTitle("Aerodrome Spatial Planner v1.0 — ICAO Compliant | GeoPackage Output | Open-Meteo API")
        self.setMinimumSize(900, 700)
        self.center_window()

    def insert_file_menu_before_view(self):
        pass  # Menu bar removed; New/Close buttons now in the title bar

    def _toggle_dark_mode_ui(self, checked):
        """Toggle dark mode on both the parent EnhancedWindow and professional styling."""
        self.dark_mode = checked
        if checked:
            self._apply_dark_aero_styling()
        else:
            self.apply_professional_styling()

    def _open_output_folder_from_menu(self):
        out = self.output_dir.text().strip() if hasattr(self, 'output_dir') else ''
        if not out and self.results:
            out = self.results.get('planner', {}).output_dir if hasattr(self.results.get('planner',None), 'output_dir') else ''
        if out and os.path.exists(out):
            import subprocess, platform
            if platform.system() == 'Windows':
                os.startfile(out)
            elif platform.system() == 'Darwin':
                subprocess.Popen(['open', out])
            else:
                subprocess.Popen(['xdg-open', out])
        else:
            QMessageBox.warning(self, 'Output Folder', 'Output folder not set or does not exist.')

    def _export_results_from_menu(self):
        if hasattr(self, 'export_results'):
            self.export_results()
        else:
            QMessageBox.information(self, 'Export', 'Run analysis first to export results.')

    def center_window(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move((screen.width() - self.width())//2, (screen.height() - self.height())//2)

    def setupUi(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(2,2,2,2)
        main_layout.setSpacing(2)
        # Toolbar removed — all actions in File/View menus
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        scroll.setWidget(container)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(10,10,10,10)
        container_layout.setSpacing(8)
        # Slim Aero title bar instead of banner
        title_bar = self._create_aero_titlebar()
        container_layout.addWidget(title_bar)
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.setup_project_tab()
        self.setup_aircraft_tab()
        self.setup_runway_tab()
        self.setup_environment_tab()
        self.setup_analysis_tab()
        self.setup_results_tab()
        self.setup_safety_surfaces_tab()
        self.setup_terminal_utilities_tab()
        self.setup_lighting_tab()
        self.setup_commercial_tab()
        self.setup_flightplan_tab()
        container_layout.addWidget(self.tab_widget, 1)
        self.progress_bar = AnimatedProgressBar()
        self.progress_bar.setVisible(False)
        container_layout.addWidget(self.progress_bar)
        main_layout.addWidget(scroll)
        self.central_widget.setLayout(main_layout)

    def create_toolbar(self):
        pass  # Toolbar removed; actions available in File/View menus

    def new_project(self):
        self.set_default_values()
        self.results = None
        self.results_table.setRowCount(0)
        self.details_display.clear()
        self.enable_results_controls(False)
        self.log_message("New project started.", "INFO")

    def create_header(self):
        return self._create_aero_titlebar()

    def _create_aero_titlebar(self):
        """Windows XP Luna Blue title bar with New Project and Close buttons."""
        # Hide the inherited menu bar completely
        self.menuBar().setVisible(False)

        bar = QFrame()
        bar.setFixedHeight(42)
        # XP Luna Blue horizontal caption gradient
        bar.setStyleSheet(
            "QFrame{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #0831d9,stop:0.06 #1958d8,"
            "stop:0.08 #2168e0,stop:0.22 #2a86e8,"
            "stop:0.50 #1a6cd8,stop:0.76 #1556c8,"
            "stop:0.90 #1040b0,stop:0.92 #0f3ea8,"
            "stop:0.96 #1245b8,stop:1 #2060d0);"
            "border-bottom:2px solid #1040a0;border-radius:0px;}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(8)

        # ✈ Icon + title (XP white caption)
        title_lbl = QLabel("✈  Aerodrome Spatial Planner  v1.0")
        title_lbl.setStyleSheet(
            "color:#ffffff;"
            "font-family:'Tahoma',Arial;"
            "font-weight:bold;font-size:13px;"
            "background:transparent;border:none;")
        row.addWidget(title_lbl)
        row.addStretch()

        # Status pill
        self._aero_status = QLabel("Ready")
        self._aero_status.setStyleSheet(
            "color:#b8d8ff;font-size:10px;"
            "font-family:'Tahoma',Arial;"
            "background:transparent;border:none;")
        row.addWidget(self._aero_status)
        row.addSpacing(10)

        # ── New Project button — XP caption button style ──────────────────
        new_btn = QPushButton("⊕ New")
        new_btn.setToolTip("Start a new project — clears all current inputs and results (Ctrl+N)")
        new_btn.setShortcut("Ctrl+N")
        new_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        new_btn.setStyleSheet(
            "QPushButton{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #4a8fe8,stop:0.5 #2060c8,stop:1 #1040a0);"
            "color:white;font-family:'Tahoma',Arial;font-size:10px;font-weight:bold;"
            "border-top:1px solid #a0c8f8;border-left:1px solid #a0c8f8;"
            "border-right:1px solid #0030a0;border-bottom:1px solid #0030a0;"
            "border-radius:3px;padding:3px 10px;}"
            "QPushButton:hover{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #60a8f8,stop:0.5 #3878d8,stop:1 #1858b8);}"
            "QPushButton:pressed{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #1040a0,stop:1 #2060c8);"
            "border-top:1px solid #0030a0;border-left:1px solid #0030a0;"
            "border-right:1px solid #a0c8f8;border-bottom:1px solid #a0c8f8;}")
        new_btn.clicked.connect(self.new_project)
        row.addWidget(new_btn)

        # ── Close button — XP red ✕ button ───────────────────────────────
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close the Airport Runway Planner plugin window (Ctrl+Q)")
        close_btn.setShortcut("Ctrl+Q")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            "QPushButton{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #e06060,stop:0.5 #c02020,stop:1 #a01010);"
            "color:white;font-family:'Tahoma',Arial;font-size:11px;font-weight:bold;"
            "border-top:1px solid #f09090;border-left:1px solid #f09090;"
            "border-right:1px solid #600000;border-bottom:1px solid #600000;"
            "border-radius:3px;padding:0;}"
            "QPushButton:hover{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #f08080,stop:0.5 #e03030,stop:1 #c01010);}"
            "QPushButton:pressed{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #a01010,stop:1 #c02020);}")
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)

        return bar

    def apply_ui_scaling(self):
        pass  # UI scaling handled by QGIS system DPI

    def setup_project_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)
        g1 = QGroupBox("Project Information")
        g1l = QGridLayout()
        g1l.addWidget(QLabel("Project Name:"),0,0)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("Enter project name")
        self.project_name.setToolTip("Unique name for this airport planning project.")
        g1l.addWidget(self.project_name,0,1)
        g1l.addWidget(QLabel("Client Name:"),1,0)
        self.client_name = QLineEdit()
        self.client_name.setToolTip("Name of the client or commissioning organisation.")
        g1l.addWidget(self.client_name,1,1)
        g1l.addWidget(QLabel("Project ID:"),2,0)
        self.project_id = QLineEdit()
        self.project_id.setToolTip("Unique project identifier code (auto-generated from date).")
        g1l.addWidget(self.project_id,2,1)
        g1.setLayout(g1l)
        cl.addWidget(g1)
        g2 = QGroupBox("Data Inputs")
        g2l = QGridLayout()
        g2l.addWidget(QLabel("Area of Interest (AOI):"),0,0)
        self.aoi_combo = QgsMapLayerComboBox()
        self.aoi_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.aoi_combo.setToolTip("Polygon layer defining the site boundary; centroid used as wind observation point when AOI centroid mode is selected.")
        g2l.addWidget(self.aoi_combo,0,1)
        g2l.addWidget(QLabel("DSM (Surface Model):"),1,0)
        self.dsm_combo = QgsMapLayerComboBox()
        self.dsm_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dsm_combo.setToolTip("Digital Surface Model raster (includes vegetation and structures) — used for obstacle height extraction.")
        g2l.addWidget(self.dsm_combo,1,1)
        g2l.addWidget(QLabel("DTM (Terrain Model):"),2,0)
        self.dtm_combo = QgsMapLayerComboBox()
        self.dtm_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dtm_combo.setToolTip("Digital Terrain Model raster (bare earth) — DSM minus DTM gives true obstacle height above ground.")
        g2l.addWidget(self.dtm_combo,2,1)
        g2l.addWidget(QLabel("Output Directory:"),3,0)
        h = QHBoxLayout()
        self.output_dir = QLineEdit()
        self.output_dir.setToolTip("Folder where all GeoPackages, maps and reports will be saved.")
        self.browse_output_btn = QPushButton("Browse...")
        self.browse_output_btn.setToolTip("Select the output directory using the file browser.")
        self.browse_output_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        h.addWidget(self.output_dir)
        h.addWidget(self.browse_output_btn)
        g2l.addLayout(h,3,1)
        g2l.addWidget(QLabel("Output CRS:"),4,0)
        self.crs_selector = QgsProjectionSelectionWidget()
        self.crs_selector.setToolTip("Coordinate Reference System for all output GeoPackage layers (default: project CRS).")
        g2l.addWidget(self.crs_selector,4,1)
        g2.setLayout(g2l)
        cl.addWidget(g2)
        g3 = QGroupBox("Open-Meteo API Configuration")
        g3l = QGridLayout()
        g3l.addWidget(QLabel("API Status:"),0,0)
        self.api_status_label = QLabel("[OK] Ready")
        self.api_status_label.setStyleSheet("color:#1a6620; font-weight:bold; font-family:Tahoma,Arial;")
        g3l.addWidget(self.api_status_label,0,1)
        g3l.addWidget(QLabel("Wind Data Location:"),1,0)
        self.wind_method = QComboBox()
        self.wind_method.addItems(["Use AOI Centroid", "Manual Coordinates", "Pick from Map"])
        self.wind_method.setToolTip("How to determine the lat/lon for downloading wind data from Open-Meteo API.")
        g3l.addWidget(self.wind_method,1,1)
        self.wind_coord_frame = QFrame()
        wcl = QGridLayout(self.wind_coord_frame)
        wcl.addWidget(QLabel("Latitude:"),0,0)
        self.wind_lat = QDoubleSpinBox()
        self.wind_lat.setRange(-90,90); self.wind_lat.setDecimals(6)
        wcl.addWidget(self.wind_lat,0,1)
        wcl.addWidget(QLabel("Longitude:"),1,0)
        self.wind_lon = QDoubleSpinBox()
        self.wind_lon.setRange(-180,180); self.wind_lon.setDecimals(6)
        wcl.addWidget(self.wind_lon,1,1)
        self.pick_wind_btn = QPushButton("Pick from Map")
        wcl.addWidget(self.pick_wind_btn,2,0,1,2)
        g3l.addWidget(self.wind_coord_frame,2,0,1,2)
        g3l.addWidget(QLabel("Historical Data Period:"),3,0)
        date_layout = QHBoxLayout()
        today = QDate.currentDate()
        ten_years_ago = today.addYears(-10)
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setDate(ten_years_ago)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setToolTip("Start date for historical wind data; longer periods give more statistically reliable orientation scoring.")
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setDate(today)
        self.end_date_edit.setMaximumDate(today)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setToolTip("End date for historical wind data (cannot exceed today's date).")
        date_layout.addWidget(self.start_date_edit)
        date_layout.addWidget(QLabel("to"))
        date_layout.addWidget(self.end_date_edit)
        g3l.addLayout(date_layout,3,1)
        self.test_api_btn = QPushButton("Test Open-Meteo API Connection")
        self.test_api_btn.setToolTip("Send a test request to the Open-Meteo archive API to verify internet connectivity and API availability.")
        self.test_api_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        g3l.addWidget(self.test_api_btn,4,0,1,2)
        g3.setLayout(g3l)
        cl.addWidget(g3)
        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[P] Project")

    def setup_aircraft_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)
        g1 = QGroupBox("Aircraft Selection")
        g1l = QGridLayout()
        g1l.addWidget(QLabel("Design Aircraft:"),0,0)
        self.design_aircraft = QComboBox()
        self.design_aircraft.addItems(list(ICAOStandards.AIRCRAFT_PERFORMANCE.keys()))
        self.design_aircraft.setToolTip("Select the critical design aircraft; determines ICAO ARC code, runway width and pavement strength requirements.")
        g1l.addWidget(self.design_aircraft,0,1)
        g1.setLayout(g1l)
        cl.addWidget(g1)
        g2 = QGroupBox("Aircraft Parameters")
        g2l = QGridLayout()
        g2l.addWidget(QLabel("Wingspan:"),0,0); self.wingspan_label = QLabel("35.8 m"); g2l.addWidget(self.wingspan_label,0,1)
        g2l.addWidget(QLabel("Wheel Span:"),1,0); self.wheel_span_label = QLabel("5.2 m"); g2l.addWidget(self.wheel_span_label,1,1)
        g2l.addWidget(QLabel("MTOW:"),2,0); self.max_takeoff_weight_label = QLabel("79,000 kg"); g2l.addWidget(self.max_takeoff_weight_label,2,1)
        g2l.addWidget(QLabel("Ref Field Length:"),3,0); self.reference_field_length_label = QLabel("2,400 m"); g2l.addWidget(self.reference_field_length_label,3,1)
        g2l.addWidget(QLabel("Approach Speed:"),4,0); self.approach_speed_label = QLabel("140 knots"); g2l.addWidget(self.approach_speed_label,4,1)
        g2.setLayout(g2l)
        cl.addWidget(g2)
        g3 = QGroupBox("ICAO Classification")
        g3l = QGridLayout()
        g3l.addWidget(QLabel("Code Number:"),0,0); self.code_number = QLabel("4"); g3l.addWidget(self.code_number,0,1)
        g3l.addWidget(QLabel("Code Letter:"),1,0); self.code_letter = QLabel("C"); g3l.addWidget(self.code_letter,1,1)
        g3l.addWidget(QLabel("ARC:"),2,0); self.arc_display = QLabel("4C"); self.arc_display.setStyleSheet("font-weight:bold; color:#003c74; font-family:Tahoma,Arial;"); g3l.addWidget(self.arc_display,2,1)
        g3l.addWidget(QLabel("Category:"),3,0); self.aircraft_category_label = QLabel("Medium"); g3l.addWidget(self.aircraft_category_label,3,1)
        g3.setLayout(g3l)
        cl.addWidget(g3)
        g4 = QGroupBox("Crosswind Limits (knots)")
        g4l = QGridLayout()
        g4l.addWidget(QLabel("Dry:"),0,0); self.crosswind_dry_label = QLabel("20"); g4l.addWidget(self.crosswind_dry_label,0,1)
        g4l.addWidget(QLabel("Wet:"),1,0); self.crosswind_wet_label = QLabel("15"); g4l.addWidget(self.crosswind_wet_label,1,1)
        g4l.addWidget(QLabel("Icy:"),2,0); self.crosswind_icy_label = QLabel("7.5"); g4l.addWidget(self.crosswind_icy_label,2,1)
        g4.setLayout(g4l)
        cl.addWidget(g4)
        g5 = QGroupBox("Aircraft Information")
        g5l = QVBoxLayout()
        self.aircraft_info = QTextEdit()
        self.aircraft_info.setReadOnly(True)
        self.aircraft_info.setMinimumHeight(90)
        self.aircraft_info.setMaximumHeight(180)
        g5l.addWidget(self.aircraft_info)
        g5.setLayout(g5l)
        cl.addWidget(g5)
        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[A] Aircraft")

    def setup_runway_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)
        g1 = QGroupBox("Runway Configuration")
        g1l = QGridLayout()
        g1l.addWidget(QLabel("Configuration:"),0,0)
        self.runway_config = QComboBox()
        self.runway_config.addItems(["Single", "Parallel", "Crosswind", "Open-V", "Intersecting"])
        self.runway_config.setToolTip(
            "Single: one runway\n"
            "Parallel: two runways same heading, offset laterally (ICAO Sec.2.3)\n"
            "Crosswind: primary + secondary at 90° (ICAO Sec.3.1.2)\n"
            "Open-V: two runways diverging from common apex at ±15° (ICAO Sec.2.2)\n"
            "Intersecting: two runways crossing at a point — set angle via separation spinbox (30°–150°)")
        g1l.addWidget(self.runway_config,0,1)
        g1l.addWidget(QLabel("Number of Runways:"),1,0)
        self.num_runways = QSpinBox()
        self.num_runways.setRange(1,4); self.num_runways.setValue(1)
        self.num_runways.setToolTip("Total number of runways in the configuration (1–4).")
        g1l.addWidget(self.num_runways,1,1)
        self._sep_label = QLabel("Runway Separation / Intersection Angle:")
        g1l.addWidget(self._sep_label, 2, 0)
        self.runway_separation = QDoubleSpinBox()
        self.runway_separation.setRange(30, 1000)
        self.runway_separation.setValue(300)
        self.runway_separation.setSuffix(" m / °")
        self.runway_separation.setToolTip(
            "Parallel/Crosswind: separation distance in metres\n"
            "Open-V: divergence half-angle (default 30° = ±15°)\n"
            "Intersecting: crossing angle in degrees (30°–150°, ICAO Doc 9157 Sec.2.2)")
        g1l.addWidget(self.runway_separation, 2, 1)
        # Update label dynamically when config changes
        self.runway_config.currentTextChanged.connect(self._update_sep_label)
        g1.setLayout(g1l)
        cl.addWidget(g1)
        g2 = QGroupBox("Approach Configuration")
        g2l = QGridLayout()
        g2l.addWidget(QLabel("Approach Type:"),0,0)
        self.approach_type = QComboBox()
        self.approach_type.addItems(["Non-precision", "Precision Cat I", "Precision Cat II/III"])
        self.approach_type.setToolTip("Instrument approach category — affects OLS surface dimensions and centreline light spacing.")
        g2l.addWidget(self.approach_type,0,1)
        g2l.addWidget(QLabel("Runway Lighting:"),1,0)
        self.runway_lighting = QComboBox()
        self.runway_lighting.addItems(["HIRL", "MIRL", "LIRL", "None"])
        self.runway_lighting.setToolTip("Runway edge light intensity: HIRL = High, MIRL = Medium, LIRL = Low intensity runway lights.")
        g2l.addWidget(self.runway_lighting,1,1)
        g2l.addWidget(QLabel("Approach Lighting:"),2,0)
        self.approach_lighting = QComboBox()
        self.approach_lighting.addItems(["MALSR", "ALSF", "SSALR", "None"])
        self.approach_lighting.setToolTip("Approach lighting system type — ALSF-2 for Cat II/III, MALSR for Cat I non-precision.")
        g2l.addWidget(self.approach_lighting,2,1)
        g2l.addWidget(QLabel("Visual Glide Slope:"),3,0)
        self.visual_glide_slope = QComboBox()
        self.visual_glide_slope.addItems(["PAPI", "VASI", "APAPI", "None"])
        self.visual_glide_slope.setToolTip("Visual approach slope indicator type: PAPI (4-light), VASI (2-bar), APAPI (2-light simplified).")
        g2l.addWidget(self.visual_glide_slope,3,1)
        g2.setLayout(g2l)
        cl.addWidget(g2)
        g3 = QGroupBox("Safety Areas")
        g3l = QGridLayout()
        g3l.addWidget(QLabel("RESA Length:"),0,0)
        self.resa_length = QDoubleSpinBox()
        self.resa_length.setRange(30,150); self.resa_length.setValue(90); self.resa_length.setSuffix(" m")
        self.resa_length.setToolTip("Runway End Safety Area length; minimum 90 m (ICAO Annex 14 Sec.3.5). Extends beyond runway strip end.")
        g3l.addWidget(self.resa_length,0,1)
        g3l.addWidget(QLabel("RESA Width:"),1,0)
        self.resa_width = QDoubleSpinBox()
        self.resa_width.setRange(30,60); self.resa_width.setValue(30); self.resa_width.setSuffix(" m")
        self.resa_width.setToolTip("RESA width; minimum twice the runway width or 30 m (ICAO Annex 14 Sec.3.5).")
        g3l.addWidget(self.resa_width,1,1)
        g3l.addWidget(QLabel("Runway Strip Width:"),2,0)
        self.strip_width = QDoubleSpinBox()
        self.strip_width.setRange(30,100); self.strip_width.setValue(75); self.strip_width.setSuffix(" m")
        self.strip_width.setToolTip("Graded half-width of the runway strip each side of centreline (ICAO Annex 14 Sec.3.4).")
        g3l.addWidget(self.strip_width,2,1)
        g3l.addWidget(QLabel("Stopway Length:"),3,0)
        self.stopway_length = QDoubleSpinBox()
        self.stopway_length.setRange(0,300); self.stopway_length.setValue(0); self.stopway_length.setSuffix(" m")
        self.stopway_length.setToolTip("Paved stopway beyond runway end; adds to ASDA declared distance (0 = none).")
        g3l.addWidget(self.stopway_length,3,1)
        g3l.addWidget(QLabel("Clearway Length:"),4,0)
        self.clearway_length = QDoubleSpinBox()
        self.clearway_length.setRange(0,300); self.clearway_length.setValue(0); self.clearway_length.setSuffix(" m")
        self.clearway_length.setToolTip("Clearway beyond runway end; limited to 50% of TORA (Annex 14 Sec.3.5.1). Adds to TODA.")
        g3l.addWidget(self.clearway_length,4,1)
        g3l.addWidget(QLabel("Displaced Threshold:"),5,0)
        self.displaced_threshold = QDoubleSpinBox()
        self.displaced_threshold.setRange(0,300); self.displaced_threshold.setValue(0); self.displaced_threshold.setSuffix(" m")
        self.displaced_threshold.setToolTip("Distance threshold is moved inboard from runway end; reduces LDA (landing distance available).")
        g3l.addWidget(self.displaced_threshold,5,1)
        g3.setLayout(g3l)
        cl.addWidget(g3)
        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[R] Runway")

    def setup_environment_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)
        g0 = QGroupBox("Units")
        g0l = QHBoxLayout()
        self.metric_radio = QRadioButton("Metric (meters, m/s)")
        self.metric_radio.setChecked(True)
        self.imperial_radio = QRadioButton("Imperial (feet, mph)")
        g0l.addWidget(self.metric_radio)
        g0l.addWidget(self.imperial_radio)
        g0l.addStretch()
        g0.setLayout(g0l)
        cl.addWidget(g0)
        g1 = QGroupBox("Climate Parameters")
        g1l = QGridLayout()
        g1l.addWidget(QLabel("Mean Temperature:"),0,0)
        self.mean_temperature = QDoubleSpinBox()
        self.mean_temperature.setRange(-30,50); self.mean_temperature.setValue(15); self.mean_temperature.setSuffix(" °C")
        self.mean_temperature.setToolTip("Mean daily maximum temperature of the hottest month; used for runway length temperature correction.")
        g1l.addWidget(self.mean_temperature,0,1)
        g1l.addWidget(QLabel("Annual Precipitation:"),1,0)
        self.precipitation = QDoubleSpinBox()
        self.precipitation.setRange(0,5000); self.precipitation.setValue(1000); self.precipitation.setSuffix(" mm")
        self.precipitation.setToolTip("Total annual rainfall — influences pavement drainage design and wet-runway crosswind limits.")
        g1l.addWidget(self.precipitation,1,1)
        g1l.addWidget(QLabel("Relative Humidity:"),2,0)
        self.humidity = QDoubleSpinBox()
        self.humidity.setRange(0,100); self.humidity.setValue(70); self.humidity.setSuffix(" %")
        self.humidity.setToolTip("Mean annual relative humidity; values >80% trigger a 3% runway length correction (Doc 9157 Sec.3.5.3).")
        g1l.addWidget(self.humidity,2,1)
        g1l.addWidget(QLabel("PCN:"),3,0)
        self.pcn_input = QLineEdit("50/R/B/W/T")
        self.pcn_input.setToolTip("Pavement Classification Number (format: PCN/type/sub/strength/tyre). Determines pavement load capacity.")
        g1l.addWidget(self.pcn_input,3,1)
        self.fetch_climate_btn = QPushButton("Fetch From Openmeteo")
        self.fetch_climate_btn.setToolTip("Automatically retrieve historical temperature, precipitation and humidity from the Open-Meteo API.")
        self.fetch_climate_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        g1l.addWidget(self.fetch_climate_btn,4,0,1,2)
        g1.setLayout(g1l)
        cl.addWidget(g1)
        g2 = QGroupBox("Magnetic Variation")
        g2l = QGridLayout()
        g2l.addWidget(QLabel("Magnetic Variation:"),0,0)
        self.magnetic_variation = QDoubleSpinBox()
        self.magnetic_variation.setRange(-30,30); self.magnetic_variation.setValue(0); self.magnetic_variation.setSuffix(" °")
        g2l.addWidget(self.magnetic_variation,0,1)
        g2l.addWidget(QLabel("Annual Change:"),1,0)
        self.magnetic_annual_change = QDoubleSpinBox()
        self.magnetic_annual_change.setRange(-2,2); self.magnetic_annual_change.setValue(0); self.magnetic_annual_change.setSuffix(" °/year")
        g2l.addWidget(self.magnetic_annual_change,1,1)
        self.declination_web_btn = QPushButton("Manual: Open NOAA Declination Website")
        self.declination_web_btn.clicked.connect(self.open_magnetic_declination_website)
        g2l.addWidget(self.declination_web_btn,2,0,1,2)
        self.declination_auto_btn = QPushButton("⚡ Auto-Fetch Declination from NOAA API (Wind Point)")
        self.declination_auto_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #d4ecd4,stop:0.45 #a8d4a8,stop:0.55 #90c490,stop:1 #78b478);color:#000000;font-weight:bold;border-top:2px solid #ffffff;border-left:2px solid #ffffff;border-right:2px solid #2d7a2d;border-bottom:2px solid #2d7a2d;border-radius:2px;padding:7px 14px;min-height:22px;}""QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #e0f4e0,stop:1 #b8e0b8);}""QPushButton:disabled{background:#d4d0c8;color:#aca899;border-top:2px solid #e8e4dc;border-left:2px solid #e8e4dc;border-right:2px solid #b0aca4;border-bottom:2px solid #b0aca4;}")
        self.declination_auto_btn.setToolTip(
            "Automatically fetches magnetic declination from the NOAA WMM API\n"
            "using the Wind Observation Point coordinates (EPSG:4326).\n"
            "Positive = East declination, Negative = West declination.")
        self.declination_auto_btn.clicked.connect(self.fetch_magnetic_declination_auto)
        g2l.addWidget(self.declination_auto_btn,3,0,1,2)
        self.declination_status_label = QLabel("Declination: not fetched yet")
        self.declination_status_label.setStyleSheet("color:#666655;font-style:italic;font-size:10px;padding:2px;")
        g2l.addWidget(self.declination_status_label,4,0,1,2)
        g2.setLayout(g2l)
        cl.addWidget(g2)
        g3 = QGroupBox("Wind Analysis Settings")
        g3l = QGridLayout()
        g3l.addWidget(QLabel("Analysis Step:"),0,0)
        self.wind_step = QComboBox()
        self.wind_step.addItems(["1° (High Precision)", "5° (Standard)", "10° (Fast)"])
        self.wind_step.setToolTip("Angular resolution for scanning candidate runway orientations; 1° is most accurate but slowest.")
        g3l.addWidget(self.wind_step,0,1)
        g3l.addWidget(QLabel("Number of Candidates:"),1,0)
        self.num_candidates_spin = QSpinBox()
        self.num_candidates_spin.setRange(1,20); self.num_candidates_spin.setValue(5)
        self.num_candidates_spin.setToolTip("How many top-ranked runway orientation candidates to report and map (1–20).")
        g3l.addWidget(self.num_candidates_spin,1,1)
        g3l.addWidget(QLabel("Obstacle Threshold:"),2,0)
        self.obstacle_threshold = QDoubleSpinBox()
        self.obstacle_threshold.setRange(0.1,100.0); self.obstacle_threshold.setValue(1.0); self.obstacle_threshold.setSuffix(" m")
        self.obstacle_threshold.setToolTip("Minimum height above terrain to classify a point as an obstacle requiring assessment.")
        g3l.addWidget(self.obstacle_threshold,2,1)
        self.obstacle_free_zone = QCheckBox("Maintain Obstacle Free Zone")
        self.obstacle_free_zone.setChecked(True)
        self.obstacle_free_zone.setToolTip("Enforce the ICAO Obstacle Free Zone (OFZ) check — objects inside are automatically flagged as violations.")
        g3l.addWidget(self.obstacle_free_zone,3,0,1,2)
        self.generate_surfaces = QCheckBox("Generate Obstacle Limitation Surfaces")
        self.generate_surfaces.setChecked(True)
        self.generate_surfaces.setToolTip("Create vector GeoPackage layers for all ICAO Annex 14 Obstacle Limitation Surfaces (OLS).")
        g3l.addWidget(self.generate_surfaces,4,0,1,2)
        g3l.addWidget(QLabel("Wind Rose Colormap:"),5,0)
        self.wind_rose_cmap = QComboBox()
        self.wind_rose_cmap.addItems(['viridis','plasma','inferno','magma','coolwarm','RdYlBu','Spectral'])
        self.wind_rose_cmap.setCurrentText('viridis')
        self.wind_rose_cmap.setToolTip("Matplotlib colour map used for the wind rose speed gradient. Choose for print or screen clarity.")
        g3l.addWidget(self.wind_rose_cmap,5,1)
        g3.setLayout(g3l)
        cl.addWidget(g3)
        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[E] Environment")

    def setup_analysis_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        g1 = QGroupBox("Analysis Steps")
        g1l = QVBoxLayout()
        self.analysis_tree = QTreeWidget()
        self.analysis_tree.setHeaderLabel("Analysis Pipeline")
        steps = ["1. Validation", "2. Elevation Processing", "3. Open-Meteo API", "4. Runway Length",
                 "5. Wind Analysis", "6. Obstacle Analysis", "7. Candidate Generation",
                 "8. Compliance Check", "9. Commercial Analysis", "10. Reporting"]
        for s in steps:
            self.analysis_tree.addTopLevelItem(QTreeWidgetItem([s]))
        self.analysis_tree.expandAll()
        g1l.addWidget(self.analysis_tree)
        step_btns = QHBoxLayout()
        self.run_selected_btn = QPushButton("Run Selected Step")
        self.run_selected_btn.setToolTip("Execute only the currently selected analysis step in the pipeline.")
        self.run_selected_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.run_all_btn = QPushButton("Run All Steps")
        self.run_all_btn.setToolTip("Run the full analysis pipeline — fetches wind data, computes runway candidates and compliance.")
        self.run_all_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        step_btns.addWidget(self.run_selected_btn)
        step_btns.addWidget(self.run_all_btn)
        step_btns.addStretch()
        g1l.addLayout(step_btns)
        g1.setLayout(g1l)
        l.addWidget(g1)
        self.validate_btn = QPushButton("Validate All Inputs")
        self.validate_btn.setToolTip("Check all required inputs (project, AOI, output directory) before running analysis.")
        self.validate_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        l.addWidget(self.validate_btn)
        g2 = QGroupBox("Analysis Progress")
        g2l = QVBoxLayout()
        self.status_text = QLabel("Ready")
        self.status_text.setStyleSheet("font-weight:bold; color:#003c74; font-family:Tahoma,Arial;")
        g2l.addWidget(self.status_text)
        g2.setLayout(g2l)
        l.addWidget(g2)
        g3 = QGroupBox("Analysis Log")
        g3l = QVBoxLayout()
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMinimumHeight(90)
        self.log_display.setMaximumHeight(300)
        g3l.addWidget(self.log_display)
        log_btns = QHBoxLayout()
        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.setToolTip("Clear all messages from the analysis log.")
        self.clear_log_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.save_log_btn = QPushButton("Save Log")
        self.save_log_btn.setToolTip("Save the current analysis log as an HTML file.")
        self.save_log_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        log_btns.addWidget(self.clear_log_btn)
        log_btns.addWidget(self.save_log_btn)
        log_btns.addStretch()
        g3l.addLayout(log_btns)
        g3.setLayout(g3l)
        l.addWidget(g3)
        l.addStretch()
        self.tab_widget.addTab(w, "[X] Analysis")

    def setup_results_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        g1 = QGroupBox("Runway Candidates")
        g1l = QVBoxLayout()
        self.results_table = QTableWidget(0, 17)
        self.results_table.setHorizontalHeaderLabels([
            "Rank", "True°", "Mag°", "Recip Mag°", "Designation",
            "Dry %", "Wet %", "Icy %", "Calm %", "Headwind (kt)",
            "ICAO Compliant", "XWind RWY?",
            "Length (m)", "TORA", "TODA", "ASDA", "LDA"
        ])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        g1l.addWidget(self.results_table)
        g1.setLayout(g1l)
        l.addWidget(g1)
        g2 = QGroupBox("Candidate Details")
        g2l = QVBoxLayout()
        self.details_display = QTextEdit()
        self.details_display.setReadOnly(True)
        self.details_display.setMinimumHeight(90)
        self.details_display.setMaximumHeight(280)
        g2l.addWidget(self.details_display)
        g2.setLayout(g2l)
        l.addWidget(g2)
        actions = QHBoxLayout()
        self.load_candidate_btn = QPushButton("Load Selected to QGIS")
        self.load_candidate_btn.setToolTip("Load the selected runway candidate layers into the QGIS map canvas.")
        self.load_candidate_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.load_candidate_btn.setEnabled(False)
        self.view_report_btn = QPushButton("View Compliance Report")
        self.view_report_btn.setToolTip("Open the ICAO Annex 14 compliance report for the selected candidate.")
        self.view_report_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.view_report_btn.setEnabled(False)
        self.export_results_btn = QPushButton("Export All Results")
        self.export_results_btn.setToolTip("Export all results and GeoPackages to a chosen folder.")
        self.export_results_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.export_results_btn.setEnabled(False)
        self.open_output_folder_btn = QPushButton("Open Output Folder")
        self.open_output_folder_btn.setToolTip("Open the analysis output folder in the file explorer.")
        self.open_output_folder_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.open_output_folder_btn.setEnabled(False)
        actions.addWidget(self.load_candidate_btn)
        actions.addWidget(self.view_report_btn)
        actions.addWidget(self.export_results_btn)
        actions.addWidget(self.open_output_folder_btn)
        actions.addStretch()
        l.addLayout(actions)
        viz = QHBoxLayout()
        self.wind_rose_btn = QPushButton("Wind Rose")
        self.wind_rose_btn.setToolTip("Display the wind rose chart generated from historical Open-Meteo data.")
        self.wind_rose_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.wind_rose_btn.setEnabled(False)
        self.usability_chart_btn = QPushButton("Usability Chart")
        self.usability_chart_btn.setToolTip("Show the ICAO runway usability chart — percentage of operable wind hours per orientation.")
        self.usability_chart_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.usability_chart_btn.setEnabled(False)
        self.obstacle_map_btn = QPushButton("Obstacle Map")
        self.obstacle_map_btn.setToolTip("Generate and view the ICAO OLS obstruction map with colour-coded obstacle heights.")
        self.obstacle_map_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.obstacle_map_btn.setEnabled(False)
        self.wind_arrows_btn = QPushButton("Wind Direction Arrows")
        self.wind_arrows_btn.setToolTip("Show monthly prevailing wind direction arrows and recommended runway headings.")
        self.wind_arrows_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.wind_arrows_btn.setEnabled(False)
        viz.addWidget(self.wind_rose_btn)
        viz.addWidget(self.usability_chart_btn)
        viz.addWidget(self.obstacle_map_btn)
        viz.addWidget(self.wind_arrows_btn)
        viz.addStretch()
        l.addLayout(viz)

        obs_grp = QGroupBox("Obstacle Layers (Aerial Obstacles GeoPackage)")
        obs_l   = QHBoxLayout(obs_grp)
        self.load_obs_raster_btn = QPushButton("Load Obstacle Raster → QGIS")
        self.load_obs_raster_btn.setToolTip("Load the Aerial_Obstacles raster layer into QGIS with colour-ramp symbology.")
        self.load_obs_raster_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.load_obs_raster_btn.setEnabled(False)
        self.load_obs_points_btn = QPushButton("Load Obstacle Points → QGIS")
        self.load_obs_points_btn.setToolTip("Load Aerial_Obstacles point layer into QGIS with graduated symbology by height.")
        self.load_obs_points_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.load_obs_points_btn.setEnabled(False)
        obs_l.addWidget(self.load_obs_raster_btn)
        obs_l.addWidget(self.load_obs_points_btn)
        obs_l.addStretch()
        l.addWidget(obs_grp)

        # ── Engineering Drawings ──────────────────────────────────────────────
        drw_grp = QGroupBox("Engineering Drawings (Runway & Taxiway)")
        drw_l   = QHBoxLayout(drw_grp)
        self.view_drawings_btn = QPushButton("🗂  View Engineering Drawings")
        self.view_drawings_btn.setToolTip(
            "Open the engineering drafting-level PNG drawings for each runway candidate.\n"
            "Drawings are saved in the 'drawings' sub-folder of the output directory.")
        self.view_drawings_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.view_drawings_btn.setEnabled(False)
        self.view_drawings_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #d4ecd4,stop:0.45 #a8d4a8,stop:0.55 #90c490,stop:1 #78b478);"
            "color:#000000;font-weight:bold;"
            "border-top:2px solid #ffffff;border-left:2px solid #ffffff;"
            "border-right:2px solid #2d7a2d;border-bottom:2px solid #2d7a2d;"
            "border-radius:2px;padding:6px 14px;min-height:22px;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #e0f4e0,stop:1 #b8e0b8);}"
            "QPushButton:disabled{background:#d4d0c8;color:#aca899;}")
        drw_l.addWidget(self.view_drawings_btn)
        self.drawings_status = QLabel("Drawings not yet generated — run analysis first.")
        self.drawings_status.setStyleSheet("color:#666655;font-style:italic;padding:4px;")
        drw_l.addWidget(self.drawings_status)
        drw_l.addStretch()
        l.addWidget(drw_grp)

        l.addStretch()
        self.tab_widget.addTab(w, "[~] Results")

    # ========================================================================
    # LIGHTING TAB
    # ========================================================================
    def setup_lighting_tab(self):
        """
        ICAO Aerodrome Lighting reference tab.
        Displays a colour-coded table of all ICAO Annex 14 light types,
        their standard colours and references, and allows regeneration
        of the lighting reference diagram.
        """
        w  = QWidget()
        lv = QVBoxLayout(w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        c  = QWidget(); cl = QVBoxLayout(c)

        # ── Header info ───────────────────────────────────────────────────────
        info_box = QGroupBox("ICAO Annex 14  —  Aerodrome Lighting Standard")
        info_layout = QVBoxLayout()
        info_lbl = QLabel(
            "All light types, colours and positions are defined per ICAO Annex 14 Vol.I, "
            "Chapter 5 (Visual Aids) and Chapter 6 (Obstacle Lighting).\n"
            "Select a light type below to see details. Click 'Generate Diagram' to export "
            "a printable reference chart."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color:#000000; padding:4px;")
        info_layout.addWidget(info_lbl)
        info_box.setLayout(info_layout)
        cl.addWidget(info_box)

        # ── Light type table ──────────────────────────────────────────────────
        g_table = QGroupBox("ICAO Light Types and Standard Colours")
        g_table_l = QVBoxLayout()
        self.lighting_table = QTableWidget()
        self.lighting_table.setColumnCount(5)
        self.lighting_table.setHorizontalHeaderLabels(
            ['Light Type', 'ICAO Colour', 'Hex', 'Standard Reference', 'Notes'])
        self.lighting_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.lighting_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.lighting_table.setAlternatingRowColors(True)
        self.lighting_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lighting_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lighting_table.setMinimumHeight(380)
        self._populate_lighting_table()
        g_table_l.addWidget(self.lighting_table)
        g_table.setLayout(g_table_l)
        cl.addWidget(g_table)

        # ── Detail box ────────────────────────────────────────────────────────
        g_det = QGroupBox("Selected Light Detail")
        g_det_l = QVBoxLayout()
        self.light_detail = QTextEdit()
        self.light_detail.setReadOnly(True)
        self.light_detail.setMinimumHeight(90)
        self.light_detail.setMaximumHeight(180)
        self.light_detail.setPlainText("Select a row above to see full detail.")
        g_det_l.addWidget(self.light_detail)
        g_det.setLayout(g_det_l)
        cl.addWidget(g_det)
        self.lighting_table.itemSelectionChanged.connect(self._show_light_detail)

        # ── Generate diagram button ───────────────────────────────────────────
        g_gen = QGroupBox("Export Lighting Reference Diagram")
        g_gen_l = QVBoxLayout()
        gen_btn = QPushButton("Generate ICAO Lighting Reference Chart (PNG)")
        gen_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #f4f2ec,stop:0.45 #ece9d8,stop:0.55 #dedad0,stop:1 #cdc9be);color:#000000;font-weight:bold;border-top:2px solid #ffffff;border-left:2px solid #ffffff;border-right:2px solid #808080;border-bottom:2px solid #808080;border-radius:2px;padding:10px 14px;min-height:22px;}""QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #e8f0fc,stop:1 #b8d0f8);border-right:2px solid #316ac5;border-bottom:2px solid #316ac5;}")
        gen_btn.clicked.connect(self._generate_lighting_diagram_ui)
        g_gen_l.addWidget(gen_btn)
        self.lighting_status = QLabel("Diagram not yet generated.")
        self.lighting_status.setStyleSheet("color:#666655; font-style:italic; padding:4px;")
        g_gen_l.addWidget(self.lighting_status)
        g_gen.setLayout(g_gen_l)
        cl.addWidget(g_gen)

        # --- Candidate Selector ---
        g_cand_lt = QGroupBox("Candidate Selection")
        g_cand_lt_inner = QWidget()
        g_cand_lt_row = QHBoxLayout(g_cand_lt_inner)
        g_cand_lt_row.setContentsMargins(6, 4, 6, 4)
        g_cand_lt_row.addWidget(QLabel("Generate Lights for Candidate #:"))
        self.lt_candidate_spin = QSpinBox()
        self.lt_candidate_spin.setRange(1, 20)
        self.lt_candidate_spin.setValue(1)
        self.lt_candidate_spin.setToolTip(
            "ICAO lights will be generated for this ranked candidate.\n"
            "Run full analysis first to see available candidates.")
        self.lt_candidate_spin.setMinimumWidth(60)
        g_cand_lt_row.addWidget(self.lt_candidate_spin)
        g_cand_lt_row.addStretch()
        g_cand_lt_vl = QVBoxLayout(g_cand_lt)
        g_cand_lt_vl.setContentsMargins(4, 4, 4, 4)
        g_cand_lt_vl.addWidget(g_cand_lt_inner)
        cl.addWidget(g_cand_lt)

        # ── GeoPackage light points ───────────────────────────────────────────
        g_gpkg = QGroupBox("Generate ICAO Lights GeoPackage (Point Layer)")
        g_gpkg.setStyleSheet(
            "QGroupBox{border:2px groove #aca899;border-radius:0px;margin-top:18px;"
            "padding-top:6px;font-weight:bold;color:#2c5282;background:#ffffff;}"
            "QGroupBox::title{subcontrol-origin:margin;subcontrol-position:top left;"
            "padding:0 6px;color:#2c5282;font-size:11px;}")
        g_gpkg_l = QVBoxLayout()
        gpkg_info = QLabel(
            "Creates a GeoPackage with georeferenced point features for every ICAO "
            "light type (runway edge, threshold, centreline, PAPI, taxiway, approach, "
            "obstacle lights, etc.) placed at their correct positions around the runway. "
            "Each point carries ICAO color, hex, standard reference and intensity attributes.")
        gpkg_info.setWordWrap(True)
        gpkg_info.setStyleSheet("color:#000000; font-size:10px; padding:2px;")
        g_gpkg_l.addWidget(gpkg_info)

        gpkg_row = QHBoxLayout()
        gpkg_row.addWidget(QLabel("Output Dir:"))
        self.lights_gpkg_dir = QLineEdit()
        self.lights_gpkg_dir.setPlaceholderText("Leave blank to use project output directory")
        gpkg_row.addWidget(self.lights_gpkg_dir, 1)
        browse_lights_btn = QPushButton("Browse…")
        browse_lights_btn.setFixedWidth(70)
        browse_lights_btn.clicked.connect(self._browse_lights_output)
        gpkg_row.addWidget(browse_lights_btn)
        g_gpkg_l.addLayout(gpkg_row)

        orient_row = QHBoxLayout()
        orient_row.addWidget(QLabel("Runway Orientation (° True):"))
        self.lights_orientation = QDoubleSpinBox()
        self.lights_orientation.setRange(0, 359)
        self.lights_orientation.setValue(0)
        self.lights_orientation.setSuffix("°")
        self.lights_orientation.setToolTip("0 = auto-detect from top-ranked candidate after analysis")
        orient_row.addWidget(self.lights_orientation)
        orient_row.addStretch()
        g_gpkg_l.addLayout(orient_row)

        btn_row = QHBoxLayout()
        self.gen_lights_gpkg_btn = QPushButton("⚡  Generate ICAO Lights GeoPackage")
        self.gen_lights_gpkg_btn.setStyleSheet(
            "QPushButton{background-color:#1a5276;color:white;font-weight:bold;"
            "padding:9px 14px;border-radius:5px;}"
            "QPushButton:hover{background-color:#154360;}"
            "QPushButton:disabled{background-color:#95a5a6;}")
        btn_row.addWidget(self.gen_lights_gpkg_btn)
        self.load_lights_qgis_btn = QPushButton("🗺  Load into QGIS")
        self.load_lights_qgis_btn.setEnabled(False)
        self.load_lights_qgis_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #d4ecd4,stop:0.45 #a8d4a8,stop:0.55 #90c490,stop:1 #78b478);color:#000000;font-weight:bold;border-top:2px solid #ffffff;border-left:2px solid #ffffff;border-right:2px solid #2d7a2d;border-bottom:2px solid #2d7a2d;border-radius:2px;padding:9px 14px;min-height:22px;}""QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #e0f4e0,stop:1 #b8e0b8);}""QPushButton:disabled{background:#d4d0c8;color:#aca899;border-top:2px solid #e8e4dc;border-left:2px solid #e8e4dc;border-right:2px solid #b0aca4;border-bottom:2px solid #b0aca4;}")
        btn_row.addWidget(self.load_lights_qgis_btn)
        g_gpkg_l.addLayout(btn_row)

        self.lights_gpkg_status = QLabel("GeoPackage not yet generated.")
        self.lights_gpkg_status.setStyleSheet("color:#666655;font-style:italic;padding:4px;")
        g_gpkg_l.addWidget(self.lights_gpkg_status)
        g_gpkg.setLayout(g_gpkg_l)
        cl.addWidget(g_gpkg)

        # ── Improved Obstruction Map ──────────────────────────────────────────
        g_obs = QGroupBox("Obstruction / OLS Map (Improved)")
        g_obs.setStyleSheet(
            "QGroupBox{border:1px solid #7d3c98;border-radius:4px;margin-top:8px;"
            "padding-top:6px;font-weight:bold;color:#7d3c98;background:#ffffff;}"
            "QGroupBox::title{subcontrol-origin:margin;subcontrol-position:top left;"
            "padding:0 6px;color:#7d3c98;font-size:11px;}")
        g_obs_l = QVBoxLayout()
        obs_info = QLabel(
            "Generates a full-color ICAO OLS obstruction map showing Inner Horizontal, "
            "Conical, Outer Horizontal, Approach and Take-off Climb surfaces with obstacle "
            "height color-coding (green = safe, orange = warning, red = penetration).")
        obs_info.setWordWrap(True)
        obs_info.setStyleSheet("color:#000000;font-size:10px;padding:2px;")
        g_obs_l.addWidget(obs_info)

        obs_btn_row = QHBoxLayout()
        self.gen_obs_map_btn = QPushButton("🛑  Generate Obstruction Map (PNG)")
        self.gen_obs_map_btn.setStyleSheet(
            "QPushButton{background-color:#7d3c98;color:white;font-weight:bold;"
            "padding:9px 14px;border-radius:5px;}"
            "QPushButton:hover{background-color:#6c3483;}"
            "QPushButton:disabled{background-color:#95a5a6;}")
        obs_btn_row.addWidget(self.gen_obs_map_btn)
        g_obs_l.addLayout(obs_btn_row)
        self.obs_map_status = QLabel("Obstruction map not yet generated.")
        self.obs_map_status.setStyleSheet("color:#666655;font-style:italic;padding:4px;")
        g_obs_l.addWidget(self.obs_map_status)
        g_obs.setLayout(g_obs_l)
        cl.addWidget(g_obs)

        cl.addStretch()
        scroll.setWidget(c)
        lv.addWidget(scroll)
        self.tab_widget.addTab(w, "[L] Lighting")

    def _populate_lighting_table(self):
        """Fill the lighting table from ICAOCompliantAirportRunwayPlanner.ICAO_LIGHTING."""
        from qgis.PyQt.QtGui import QColor, QBrush
        lighting = ICAOCompliantAirportRunwayPlanner.ICAO_LIGHTING
        self.lighting_table.setRowCount(len(lighting))

        # Group separator colours
        GROUP_BG = {
            'Runway': QColor('#e8f4fd'),
            'Taxiway': QColor('#eafaf1'),
            'ALSF': QColor('#fef9e7'),
            'MALSR': QColor('#fef9e7'),
            'PAPI': QColor('#fef9e7'),
            'Apron': QColor('#f5eef8'),
            'Obstacle': QColor('#fdf2f8'),
            'Wind': QColor('#f0f0f0'),
        }

        for row, (name, info) in enumerate(lighting.items()):
            items = [
                QTableWidgetItem(name),
                QTableWidgetItem(info.get('color', '')),
                QTableWidgetItem(info.get('hex', '')),
                QTableWidgetItem(info.get('standard', '')),
                QTableWidgetItem(info.get('note', '')),
            ]
            hex_color = info.get('hex', '#888888')
            swatch_item = items[1]
            try:
                qc = QColor(hex_color)
                swatch_item.setBackground(QBrush(qc))
                # Use contrasting text
                luminance = 0.299*qc.red() + 0.587*qc.green() + 0.114*qc.blue()
                text_col = QColor('#000000') if luminance > 160 else QColor('#ffffff')
                swatch_item.setForeground(QBrush(text_col))
            except Exception:
                pass
            # Row background by group
            for kw, bg in GROUP_BG.items():
                if name.startswith(kw):
                    for it in items:
                        it.setBackground(QBrush(bg))
                    break
            for col_idx, it in enumerate(items):
                self.lighting_table.setItem(row, col_idx, it)

        self.lighting_table.resizeRowsToContents()

    def _show_light_detail(self):
        rows = self.lighting_table.selectedItems()
        if not rows:
            return
        name = self.lighting_table.item(self.lighting_table.currentRow(), 0).text()
        info = ICAOCompliantAirportRunwayPlanner.ICAO_LIGHTING.get(name, {})
        txt = (f"LIGHT TYPE : {name}\n"
               f"ICAO Colour: {info.get('color','')}  ({info.get('hex','')})\n"
               f"Standard   : {info.get('standard','')}\n"
               f"Notes      : {info.get('note','')}")
        self.light_detail.setPlainText(txt)

    def _generate_lighting_diagram_ui(self):
        out_dir = self.output_dir.text().strip() if hasattr(self,'output_dir') else ""
        if not out_dir:
            out_dir = QFileDialog.getExistingDirectory(self,"Select Output Folder")
        if not out_dir:
            return
        light_dir = os.path.join(out_dir, 'Lighting')
        planner = ICAOCompliantAirportRunwayPlanner()
        planner.airport_reference_code = self.arc_display.text() if hasattr(self,'arc_display') else "---"
        planner.approach_type = self.approach_type.currentText() if hasattr(self,'approach_type') else "---"
        planner.runway_lighting = self.runway_lighting.currentText() if hasattr(self,'runway_lighting') else "---"
        result = planner.generate_icao_lighting_diagram(light_dir)
        if result and os.path.exists(result):
            self.lighting_status.setText(f"Diagram saved: {result}")
            self.lighting_status.setStyleSheet("color:#1a6620; font-weight:bold; padding:4px;")
            QMessageBox.information(self, "Lighting Diagram",
                                    f"ICAO Lighting Reference Chart saved to:\n{result}")
        else:
            self.lighting_status.setText("Diagram generation failed.")
            self.lighting_status.setStyleSheet("color:#aa1111; font-weight:bold; padding:4px;")

    def _browse_lights_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder for Lights GeoPackage",
                                                   QgsProject.instance().homePath())
        if folder:
            self.lights_gpkg_dir.setText(folder)

    def _generate_lights_gpkg_ui(self):
        """Generate ICAO lights point GeoPackage."""
        # Always prefer the fully-populated planner from analysis results
        if self.results and 'planner' in self.results:
            self.planner = self.results['planner']
        elif not hasattr(self, 'planner') or self.planner is None:
            self.planner = ICAOCompliantAirportRunwayPlanner()

        out_dir = self.lights_gpkg_dir.text().strip() or self.output_dir.text().strip()
        if not out_dir:
            out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not out_dir:
            return

        light_dir = os.path.join(out_dir, 'Lighting')

        # Use orientation from selected candidate (lt_candidate_spin)
        cand_spin = getattr(self, 'lt_candidate_spin', None)
        orientation = self._get_candidate_orientation(cand_spin) if hasattr(self, '_get_candidate_orientation') else self.lights_orientation.value()
        if self.results:
            cand_num = cand_spin.value() if cand_spin else 1
            cands = self.results.get('candidates', [])
            if 0 < cand_num <= len(cands):
                orientation = cands[cand_num - 1]['orientation']
        self.lights_orientation.setValue(orientation)

        # Sync planner state
        self.planner.runway_lighting   = self.runway_lighting.currentText()  if hasattr(self,'runway_lighting')   else 'HIRL'
        self.planner.approach_lighting = self.approach_lighting.currentText() if hasattr(self,'approach_lighting') else 'MALSR'
        self.planner.approach_type     = self.approach_type.currentText()     if hasattr(self,'approach_type')     else 'Non-precision'
        self.planner.airport_reference_code = self.arc_display.text()         if hasattr(self,'arc_display')       else '4C'
        if hasattr(self,'code_number'):
            self.planner.runway_code_number = self.code_number.text().strip() or '4'

        # Sync AOI centroid if available and not already set
        if self.planner.wgs84_centroid is None:
            aoi_layer = self.aoi_combo.currentLayer() if hasattr(self, 'aoi_combo') else None
            if aoi_layer and isinstance(aoi_layer, QgsVectorLayer):
                try:
                    feat = next(aoi_layer.getFeatures())
                    geom = feat.geometry()
                    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
                    xform = QgsCoordinateTransform(aoi_layer.crs(), wgs84, QgsProject.instance())
                    geom.transform(xform)
                    centroid = geom.centroid().asPoint()
                    self.planner.wgs84_centroid = (centroid.y(), centroid.x())
                except Exception:
                    pass

        self.gen_lights_gpkg_btn.setEnabled(False)
        self.lights_gpkg_status.setText("Generating…")
        self.lights_gpkg_status.setStyleSheet("color:#316ac5;font-style:italic;padding:4px;")
        QApplication.processEvents()

        gpkg_path, gdf = self.planner.generate_lights_geopackage(light_dir, orientation)

        self.gen_lights_gpkg_btn.setEnabled(True)
        if gpkg_path and os.path.exists(gpkg_path):
            self._last_lights_gpkg = gpkg_path
            n = len(gdf) if gdf is not None else '?'
            self.lights_gpkg_status.setText(f"✅  Saved: {gpkg_path}  ({n} points)")
            self.lights_gpkg_status.setStyleSheet("color:#1a6620;font-weight:bold;padding:4px;")
            self.load_lights_qgis_btn.setEnabled(True)
            QMessageBox.information(self, "ICAO Lights GeoPackage",
                                    f"GeoPackage saved to:\n{gpkg_path}\n\n{n} light points generated.")
        else:
            self.lights_gpkg_status.setText("❌  Generation failed.")
            self.lights_gpkg_status.setStyleSheet("color:#aa1111;font-weight:bold;padding:4px;")
            QMessageBox.warning(self, "Generation Failed",
                                "Could not generate lights GeoPackage.\nCheck output directory and runway parameters.")

    def _load_lights_into_qgis(self):
        """Load the ICAO lights GeoPackage into QGIS in a 'Lighting' group."""
        if not hasattr(self, '_last_lights_gpkg') or not self._last_lights_gpkg:
            QMessageBox.warning(self, "No GeoPackage",
                                "Generate the lights GeoPackage first.")
            return
        gpkg_path = self._last_lights_gpkg
        layer = QgsVectorLayer(
            f"{gpkg_path}|layername=icao_lights", "ICAO Aerodrome Lights", "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Load Failed",
                                f"Could not load layer from:\n{gpkg_path}")
            return

        # Categorized renderer by hex_color
        from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory
        categories, seen = [], set()
        icao_color_map = {
            '#FFFFFF':('#FFFFFF','White'),         '#F0F0F0':('#F0F0F0','White Hi-Int'),
            '#E0E0E0':('#E0E0E0','White Med'),     '#FFF8E0':('#FFF8E0','Warm White'),
            '#FFFFCC':('#FFFFCC','White Windsock'), '#00C000':('#00C000','Green'),
            '#00AA00':('#00AA00','Green Twy CL'),  '#CC0000':('#CC0000','Red'),
            '#FF0000':('#FF0000','Red Stop Bar'),  '#FF2200':('#FF2200','Red Obstacle Low'),
            '#FFC200':('#FFC200','Amber'),         '#FFD700':('#FFD700','Yellow RGL'),
            '#FFC000':('#FFC000','Yellow IHP'),    '#0055FF':('#0055FF','Blue Twy Edge'),
            '#FF6600':('#FF6600','PAPI'),          '#FF8800':('#FF8800','Obstacle Hi'),
        }
        for feat in layer.getFeatures():
            hx = feat['hex_color'] or '#888888'
            if hx in seen: continue
            seen.add(hx)
            col, lbl = icao_color_map.get(hx, (hx, hx))
            sym = QgsMarkerSymbol.createSimple({
                'name':'circle','color':col,'color_border':'#333333',
                'outline_width':'0.1','size':'1.0'})
            categories.append(QgsRendererCategory(hx, sym, lbl))
        if categories:
            layer.setRenderer(QgsCategorizedSymbolRenderer('hex_color', categories))

        # Add into Lighting group
        root = QgsProject.instance().layerTreeRoot()
        grp  = root.insertGroup(0, "Lighting")
        QgsProject.instance().addMapLayer(layer, False)
        grp.addLayer(layer)

        self.log_message(
            f"ICAO lights layer loaded: {layer.featureCount()} points in 'Lighting' group.",
            "SUCCESS")
        QMessageBox.information(
            self, "Loaded",
            f"ICAO Aerodrome Lights layer added to 'Lighting' group.\n"
            f"{layer.featureCount()} light points loaded with ICAO colour coding.")
    def _generate_obs_map_ui(self):
        """Generate the improved obstruction / OLS map."""
        # Always prefer the fully-populated planner from analysis results
        if self.results and 'planner' in self.results:
            self.planner = self.results['planner']
        elif not hasattr(self, 'planner') or self.planner is None:
            self.planner = ICAOCompliantAirportRunwayPlanner()

        out_dir = self.output_dir.text().strip()
        if not out_dir:
            out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not out_dir:
            return

        maps_dir = os.path.join(out_dir, 'Maps')

        orientation = self.lights_orientation.value() if hasattr(self, 'lights_orientation') else 0.0
        if self.results:
            cands = self.results.get('candidates', [])
            if cands:
                orientation = cands[0]['orientation']

        # Sync planner
        if self.planner.wgs84_centroid is None:
            aoi_layer = self.aoi_combo.currentLayer() if hasattr(self, 'aoi_combo') else None
            if aoi_layer and isinstance(aoi_layer, QgsVectorLayer):
                try:
                    feat = next(aoi_layer.getFeatures())
                    geom = feat.geometry()
                    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
                    xform = QgsCoordinateTransform(aoi_layer.crs(), wgs84, QgsProject.instance())
                    geom.transform(xform)
                    centroid = geom.centroid().asPoint()
                    self.planner.wgs84_centroid = (centroid.y(), centroid.x())
                except Exception:
                    pass
        if hasattr(self, 'code_number'):
            self.planner.runway_code_number     = self.code_number.text().strip() or '4'
            self.planner.airport_reference_code = self.arc_display.text()         if hasattr(self,'arc_display') else '4C'
        self.planner.approach_type = self.approach_type.currentText() if hasattr(self,'approach_type') else 'Non-precision'
        if self.planner.corrected_runway_length == 0:
            self.planner.corrected_runway_length = 2500

        self.gen_obs_map_btn.setEnabled(False)
        self.obs_map_status.setText("Generating…")
        self.obs_map_status.setStyleSheet("color:#7d3c98;font-style:italic;padding:4px;")
        QApplication.processEvents()

        result = self.planner.generate_obstruction_map(maps_dir, orientation)

        self.gen_obs_map_btn.setEnabled(True)
        if result and os.path.exists(result):
            self.obs_map_status.setText(f"✅  Saved: {result}")
            self.obs_map_status.setStyleSheet("color:#1a6620;font-weight:bold;padding:4px;")
            dlg = ImageViewerDialog("Obstruction / OLS Map", result, self)
            dlg.exec()
        else:
            self.obs_map_status.setText("❌  Generation failed.")
            self.obs_map_status.setStyleSheet("color:#aa1111;font-weight:bold;padding:4px;")
            QMessageBox.warning(self, "Failed", "Could not generate obstruction map.")

    def setup_commercial_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)
        g1 = QGroupBox("Cost Parameters")
        g1l = QGridLayout()
        g1l.addWidget(QLabel("Runway Unit Cost:"),0,0)
        self.unit_cost_runway = QDoubleSpinBox()
        self.unit_cost_runway.setRange(100,2000); self.unit_cost_runway.setValue(500); self.unit_cost_runway.setPrefix("$ "); self.unit_cost_runway.setSuffix(" / m²")
        g1l.addWidget(self.unit_cost_runway,0,1)
        g1l.addWidget(QLabel("Lighting Cost:"),1,0)
        self.cost_lighting = QDoubleSpinBox()
        self.cost_lighting.setRange(50000,5000000); self.cost_lighting.setValue(250000); self.cost_lighting.setPrefix("$ ")
        g1l.addWidget(self.cost_lighting,1,1)
        g1l.addWidget(QLabel("Engineering %:"),2,0)
        self.engineering_percent = QDoubleSpinBox()
        self.engineering_percent.setRange(5,30); self.engineering_percent.setValue(20); self.engineering_percent.setSuffix(" %")
        g1l.addWidget(self.engineering_percent,2,1)
        g1l.addWidget(QLabel("Contingency %:"),3,0)
        self.contingency_percent = QDoubleSpinBox()
        self.contingency_percent.setRange(5,25); self.contingency_percent.setValue(15); self.contingency_percent.setSuffix(" %")
        g1l.addWidget(self.contingency_percent,3,1)
        g1.setLayout(g1l)
        cl.addWidget(g1)
        g2 = QGroupBox("Commercial Proposal")
        g2l = QVBoxLayout()
        self.proposal_display = QTextEdit()
        self.proposal_display.setReadOnly(True)
        self.proposal_display.setMinimumHeight(90)
        self.proposal_display.setMaximumHeight(350)
        g2l.addWidget(self.proposal_display)
        g2.setLayout(g2l)
        cl.addWidget(g2)
        actions = QHBoxLayout()
        self.calculate_costs_btn = QPushButton("Calculate Costs")
        self.calculate_costs_btn.setToolTip("Compute total project cost from unit rates and runway parameters.")
        self.calculate_costs_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.generate_proposal_btn = QPushButton("Generate Proposal")
        self.generate_proposal_btn.setToolTip("Create a formatted commercial proposal document for the client.")
        self.generate_proposal_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.generate_proposal_btn.setEnabled(False)
        self.view_financials_btn = QPushButton("View Financial Analysis")
        self.view_financials_btn.setToolTip("Open a detailed financial breakdown with NPV and ROI estimates.")
        self.view_financials_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        actions.addWidget(self.calculate_costs_btn)
        actions.addWidget(self.generate_proposal_btn)
        actions.addWidget(self.view_financials_btn)
        actions.addStretch()
        cl.addLayout(actions)
        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[C] Commercial")

    # ========================================================================
    # SAFETY SURFACES TAB
    # ========================================================================
    def setup_safety_surfaces_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)

        # --- Candidate Selector ---
        g_cand_ss = QGroupBox("Candidate Selection")
        g_cand_ss_inner = QWidget()
        g_cand_ss_row = QHBoxLayout(g_cand_ss_inner)
        g_cand_ss_row.setContentsMargins(6, 4, 6, 4)
        g_cand_ss_row.addWidget(QLabel("Generate for Candidate #:"))
        self.ss_candidate_spin = QSpinBox()
        self.ss_candidate_spin.setRange(1, 20)
        self.ss_candidate_spin.setValue(1)
        self.ss_candidate_spin.setMinimumWidth(60)
        self.ss_candidate_spin.setToolTip(
            "Safety surfaces will be generated for this ranked candidate.\n"
            "Run full analysis first to see available candidates.")
        g_cand_ss_row.addWidget(self.ss_candidate_spin)
        g_cand_ss_row.addStretch()
        g_cand_ss_vl = QVBoxLayout(g_cand_ss)
        g_cand_ss_vl.setContentsMargins(4, 4, 4, 4)
        g_cand_ss_vl.addWidget(g_cand_ss_inner)
        cl.addWidget(g_cand_ss)

        # --- ILS / Glide Slope Configuration ---
        g_gs = QGroupBox("ILS Glide Slope Configuration")
        g_gs_l = QGridLayout()
        g_gs_l.addWidget(QLabel("Glide Slope Angle (\u00b0):"), 0, 0)
        self.glide_angle_spin = QDoubleSpinBox()
        self.glide_angle_spin.setRange(2.5, 4.5)
        self.glide_angle_spin.setSingleStep(0.1)
        self.glide_angle_spin.setValue(3.0)
        self.glide_angle_spin.setDecimals(1)
        self.glide_angle_spin.setSuffix("\u00b0")
        self.glide_angle_spin.setToolTip("ICAO standard: 3.0\u00b0. Range 2.5\u00b0\u20133.5\u00b0")
        g_gs_l.addWidget(self.glide_angle_spin, 0, 1)
        g_gs_l.addWidget(QLabel("Threshold Crossing Height (m):"), 1, 0)
        self.tch_spin = QDoubleSpinBox()
        self.tch_spin.setRange(10, 30)
        self.tch_spin.setValue(15)
        self.tch_spin.setSuffix(" m")
        self.tch_spin.setToolTip("Typical TCH: 15 m for precision approaches")
        g_gs_l.addWidget(self.tch_spin, 1, 1)
        g_gs_l.addWidget(QLabel("Approach Category:"), 2, 0)
        self.surface_approach_type = QComboBox()
        self.surface_approach_type.addItems(["Non-precision", "Precision Cat I", "Precision Cat II/III"])
        g_gs_l.addWidget(self.surface_approach_type, 2, 1)
        g_gs.setLayout(g_gs_l)
        cl.addWidget(g_gs)

        # --- OLS Surfaces Selection ---
        g_ols = QGroupBox("Obstacle Limitation Surfaces (OLS) \u2014 ICAO Annex 14")
        g_ols_l = QVBoxLayout()
        self.cb_glide_slope = QCheckBox("Glide Slope Surface (ILS)")
        self.cb_glide_slope.setChecked(True)
        self.cb_takeoff_climb = QCheckBox("Take-off Climb Surface")
        self.cb_takeoff_climb.setChecked(True)
        self.cb_approach_surf = QCheckBox("Approach Surface")
        self.cb_approach_surf.setChecked(True)
        self.cb_transitional = QCheckBox("Transitional Surface")
        self.cb_transitional.setChecked(True)
        self.cb_inner_horiz = QCheckBox("Inner Horizontal Surface (45 m AGL)")
        self.cb_inner_horiz.setChecked(True)
        self.cb_conical = QCheckBox("Conical Surface (5% slope)")
        self.cb_conical.setChecked(True)
        self.cb_outer_horiz = QCheckBox("Outer Horizontal Surface (150 m AGL)")
        self.cb_outer_horiz.setChecked(True)
        for cb in [self.cb_glide_slope, self.cb_takeoff_climb, self.cb_approach_surf,
                   self.cb_transitional, self.cb_inner_horiz, self.cb_conical, self.cb_outer_horiz]:
            g_ols_l.addWidget(cb)
        g_ols.setLayout(g_ols_l)
        cl.addWidget(g_ols)

        # --- Runway Orientation Override ---
        g_rwy_sel = QGroupBox("Runway / Candidate Selection")
        g_rwy_sel_l = QGridLayout()
        g_rwy_sel_l.addWidget(QLabel("Use Orientation (\u00b0 True):"), 0, 0)
        self.surfaces_orientation = QDoubleSpinBox()
        self.surfaces_orientation.setRange(0, 359)
        self.surfaces_orientation.setValue(0)
        self.surfaces_orientation.setSuffix("\u00b0")
        self.surfaces_orientation.setToolTip("Leave 0 to auto-use top ranked candidate from analysis")
        g_rwy_sel_l.addWidget(self.surfaces_orientation, 0, 1)
        self.surfaces_use_top_candidate = QCheckBox("Auto-use top ranked candidate")
        self.surfaces_use_top_candidate.setChecked(True)
        g_rwy_sel_l.addWidget(self.surfaces_use_top_candidate, 1, 0, 1, 2)
        g_rwy_sel.setLayout(g_rwy_sel_l)
        cl.addWidget(g_rwy_sel)

        # --- Generate Button & Status ---
        g_gen = QGroupBox("Generate")
        g_gen_l = QVBoxLayout()
        self.generate_safety_surfaces_btn = QPushButton("Generate All Safety Surfaces (GeoPackage)")
        self.generate_safety_surfaces_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #dce8fc,stop:0.45 #b0c8f4,stop:0.55 #9ab8ee,stop:1 #88aaec);"
            "color:#000000;font-weight:bold;"
            "border-top:2px solid #ffffff;border-left:2px solid #ffffff;"
            "border-right:2px solid #316ac5;border-bottom:2px solid #316ac5;"
            "border-radius:2px;padding:10px 14px;min-height:22px;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #e8f0fc,stop:1 #b8d0f8);}"
            "QPushButton:disabled{background:#d4d0c8;color:#aca899;"
            "border-top:2px solid #e8e4dc;border-left:2px solid #e8e4dc;"
            "border-right:2px solid #b0aca4;border-bottom:2px solid #b0aca4;}"
        )
        g_gen_l.addWidget(self.generate_safety_surfaces_btn)
        self.load_safety_surfaces_qgis_btn = QPushButton("Load Safety Surfaces into QGIS")
        self.load_safety_surfaces_qgis_btn.setEnabled(False)
        g_gen_l.addWidget(self.load_safety_surfaces_qgis_btn)
        g_gen.setLayout(g_gen_l)
        cl.addWidget(g_gen)

        # --- OLS Parameters Reference ---
        g_ref = QGroupBox("ICAO Annex 14 Surface Parameters Reference")
        g_ref_l = QVBoxLayout()
        self.surfaces_ref_display = QTextEdit()
        self.surfaces_ref_display.setReadOnly(True)
        self.surfaces_ref_display.setMinimumHeight(90)
        self.surfaces_ref_display.setMaximumHeight(200)
        self.surfaces_ref_display.setPlainText(
            "Inner Horizontal Surface: Height 45 m | Radius 2000\u20134000 m per code\n"
            "Conical Surface: Slope 5% | Rises from IHS edge | Height 35\u2013100 m per code\n"
            "Outer Horizontal Surface: Height 150 m above aerodrome elevation\n"
            "Approach Surface: Divergence 10\u201312.5% | Length 1000\u20133600 m | Slope 2.5\u20135%\n"
            "Transitional Surface: Slope 20% (1:5) from strip edges to IHS level\n"
            "Take-off Climb: Slope 1.2\u20132% | Length 1600\u201315000 m | Divergence 10\u201312.5%\n"
            "Glide Slope: Standard 3\u00b0 | TCH 15 m | Inner width 150 m"
        )
        g_ref_l.addWidget(self.surfaces_ref_display)
        g_ref.setLayout(g_ref_l)
        cl.addWidget(g_ref)

        # --- Generation Log ---
        g_log = QGroupBox("Generation Log")
        g_log_l = QVBoxLayout()
        self.surfaces_log = QTextEdit()
        self.surfaces_log.setReadOnly(True)
        self.surfaces_log.setMinimumHeight(90)
        self.surfaces_log.setMaximumHeight(200)
        g_log_l.addWidget(self.surfaces_log)
        g_log.setLayout(g_log_l)
        cl.addWidget(g_log)

        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[S] Safety Surfaces")

    # ========================================================================
    # FLIGHT PLAN TAB  — Takeoff / Landing Route with Airways & Elevation
    # Data sources (all free / open-source):
    #   • OurAirports CSV  – airport database (ourairports.com/data/)
    #   • OpenAIP REST API – airways, waypoints, navaids (openaip.net)
    #   • Open-Meteo API   – wind aloft + pressure altitude data
    # ========================================================================
    def setup_flightplan_tab(self):
        w  = QWidget()
        lv = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c  = QWidget()
        cl = QVBoxLayout(c)
        cl.setSpacing(8)

        # ── Header info ───────────────────────────────────────────────────────
        hdr = QGroupBox("Takeoff & Landing Flight Plan — Nearest Airways")
        hdr_l = QVBoxLayout()
        hdr_lbl = QLabel(
            "Generates a VFR/IFR route from the planned runway to the nearest airways "
            "using free open-source data (OurAirports, OpenAIP, Open-Meteo).  "
            "Includes wind-corrected headings, pressure-altitude profiles and a "
            "GeoPackage route layer for QGIS."
        )
        hdr_lbl.setWordWrap(True)
        hdr_lbl.setStyleSheet("color:#000000;padding:3px;")
        hdr_l.addWidget(hdr_lbl)
        hdr.setLayout(hdr_l)
        cl.addWidget(hdr)

        # ── Departure / Arrival ───────────────────────────────────────────────
        g_dep = QGroupBox("Departure / Arrival Airport")
        g_dep_l = QGridLayout()

        g_dep_l.addWidget(QLabel("Departure ICAO:"), 0, 0)
        self.fp_dep_icao = QLineEdit()
        self.fp_dep_icao.setPlaceholderText("e.g. VIDP  (leave blank = use planned runway)")
        self.fp_dep_icao.setToolTip(
            "ICAO code of departure airport. Leave blank to use the planned runway centroid.")
        g_dep_l.addWidget(self.fp_dep_icao, 0, 1)

        g_dep_l.addWidget(QLabel("Destination ICAO:"), 1, 0)
        self.fp_dest_icao = QLineEdit()
        self.fp_dest_icao.setPlaceholderText("e.g. VABB")
        self.fp_dest_icao.setToolTip(
            "ICAO code of the destination airport.")
        g_dep_l.addWidget(self.fp_dest_icao, 1, 1)

        g_dep_l.addWidget(QLabel("Alternate ICAO:"), 2, 0)
        self.fp_alt_icao = QLineEdit()
        self.fp_alt_icao.setPlaceholderText("Optional — e.g. VOBL")
        self.fp_alt_icao.setToolTip(
            "Alternate airport ICAO code (optional, used for fuel planning).")
        g_dep_l.addWidget(self.fp_alt_icao, 2, 1)

        lookup_btn = QPushButton("Lookup Airports from OurAirports Database")
        lookup_btn.setToolTip(
            "Fetches airport coordinates from the free OurAirports CSV dataset.")
        lookup_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lookup_btn.clicked.connect(self._fp_lookup_airports)
        g_dep_l.addWidget(lookup_btn, 3, 0, 1, 2)

        self.fp_airport_status = QLabel("Airports not yet resolved.")
        self.fp_airport_status.setStyleSheet("color:#666655;font-style:italic;padding:2px;")
        g_dep_l.addWidget(self.fp_airport_status, 4, 0, 1, 2)
        g_dep.setLayout(g_dep_l)
        cl.addWidget(g_dep)

        # ── Flight Parameters ─────────────────────────────────────────────────
        g_fp = QGroupBox("Flight Parameters")
        g_fp_l = QGridLayout()

        g_fp_l.addWidget(QLabel("Flight Rules:"), 0, 0)
        self.fp_rules = QComboBox()
        self.fp_rules.addItems(["VFR", "IFR", "VFR/IFR Mixed"])
        self.fp_rules.setToolTip(
            "VFR: Visual Flight Rules (below transition altitude). "
            "IFR: Instrument Flight Rules using airways.")
        g_fp_l.addWidget(self.fp_rules, 0, 1)

        g_fp_l.addWidget(QLabel("Cruise Altitude (ft):"), 1, 0)
        self.fp_cruise_alt = QSpinBox()
        self.fp_cruise_alt.setRange(500, 45000)
        self.fp_cruise_alt.setValue(8000)
        self.fp_cruise_alt.setSingleStep(500)
        self.fp_cruise_alt.setToolTip(
            "Planned cruise altitude in feet MSL. ICAO hemispherical rules: "
            "odd thousands (e.g. 7000 ft) eastbound, even (e.g. 8000 ft) westbound.")
        g_fp_l.addWidget(self.fp_cruise_alt, 1, 1)

        g_fp_l.addWidget(QLabel("Cruise Speed (knots TAS):"), 2, 0)
        self.fp_cruise_spd = QSpinBox()
        self.fp_cruise_spd.setRange(60, 600)
        self.fp_cruise_spd.setValue(250)
        self.fp_cruise_spd.setToolTip(
            "True airspeed at cruise altitude in knots. Used to compute wind-corrected "
            "ground speed, headings, and ETE for each segment.")
        g_fp_l.addWidget(self.fp_cruise_spd, 2, 1)

        g_fp_l.addWidget(QLabel("Aircraft Type:"), 3, 0)
        self.fp_ac_type = QLineEdit("B738")
        self.fp_ac_type.setToolTip(
            "ICAO aircraft type designator (e.g. B738, A320, C172). Used for "
            "performance lookup and filed flight plan format.")
        g_fp_l.addWidget(self.fp_ac_type, 3, 1)

        g_fp_l.addWidget(QLabel("SID / Departure Procedure:"), 4, 0)
        self.fp_sid = QLineEdit()
        self.fp_sid.setPlaceholderText("e.g. ANIRO1D  (leave blank for direct)")
        self.fp_sid.setToolTip(
            "Standard Instrument Departure procedure name. Leave blank for a direct "
            "climb to first en-route waypoint.")
        g_fp_l.addWidget(self.fp_sid, 4, 1)

        g_fp_l.addWidget(QLabel("STAR / Arrival Procedure:"), 5, 0)
        self.fp_star = QLineEdit()
        self.fp_star.setPlaceholderText("e.g. DOLOP1A  (leave blank for direct)")
        self.fp_star.setToolTip(
            "Standard Terminal Arrival Route procedure name. Leave blank for direct "
            "descent to destination.")
        g_fp_l.addWidget(self.fp_star, 5, 1)

        g_fp.setLayout(g_fp_l)
        cl.addWidget(g_fp)

        # ── Airways / Waypoints ───────────────────────────────────────────────
        g_awy = QGroupBox("Airways & Waypoints (OpenAIP)")
        g_awy_l = QGridLayout()

        g_awy_l.addWidget(QLabel("Max Waypoints:"), 0, 0)
        self.fp_max_wpts = QSpinBox()
        self.fp_max_wpts.setRange(2, 30)
        self.fp_max_wpts.setValue(8)
        self.fp_max_wpts.setToolTip(
            "Maximum number of en-route waypoints to include in the flight plan.")
        g_awy_l.addWidget(self.fp_max_wpts, 0, 1)

        g_awy_l.addWidget(QLabel("Airway Search Radius (nm):"), 1, 0)
        self.fp_awy_radius = QSpinBox()
        self.fp_awy_radius.setRange(10, 300)
        self.fp_awy_radius.setValue(50)
        self.fp_awy_radius.setToolTip(
            "Search radius in nautical miles around the direct track to find "
            "airways and fixes to route via.")
        g_awy_l.addWidget(self.fp_awy_radius, 1, 1)

        self.fp_use_airways = QCheckBox("Route via nearest airways (IFR)")
        self.fp_use_airways.setChecked(True)
        self.fp_use_airways.setToolTip(
            "When checked, the planner routes via published airways fetched from "
            "the OpenAIP free API. Uncheck for direct great-circle routing.")
        g_awy_l.addWidget(self.fp_use_airways, 2, 0, 1, 2)

        self.fp_use_navaids = QCheckBox("Include VOR/NDB navaids as waypoints")
        self.fp_use_navaids.setChecked(True)
        self.fp_use_navaids.setToolTip(
            "Add VOR and NDB stations near the route as waypoints for conventional "
            "navigation backup.")
        g_awy_l.addWidget(self.fp_use_navaids, 3, 0, 1, 2)

        fetch_awy_btn = QPushButton("Fetch Airways from OpenAIP")
        fetch_awy_btn.setToolTip(
            "Downloads airways and navaids from the free OpenAIP REST API "
            "for the area around the planned route.")
        fetch_awy_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        fetch_awy_btn.clicked.connect(self._fp_fetch_airways)
        g_awy_l.addWidget(fetch_awy_btn, 4, 0, 1, 2)

        self.fp_awy_status = QLabel("Airways not yet fetched.")
        self.fp_awy_status.setStyleSheet("color:#666655;font-style:italic;padding:2px;")
        g_awy_l.addWidget(self.fp_awy_status, 5, 0, 1, 2)
        g_awy.setLayout(g_awy_l)
        cl.addWidget(g_awy)

        # ── Wind / Elevation Data ─────────────────────────────────────────────
        g_wind = QGroupBox("Wind Aloft & Pressure Altitude (Open-Meteo)")
        g_wind_l = QGridLayout()

        g_wind_l.addWidget(QLabel("Wind Altitude Levels (ft):"), 0, 0)
        self.fp_wind_levels = QLineEdit("3000, 6000, 9000, 12000, 18000")
        self.fp_wind_levels.setToolTip(
            "Comma-separated altitude levels (feet MSL) for which wind data "
            "is fetched from Open-Meteo pressure levels.")
        g_wind_l.addWidget(self.fp_wind_levels, 0, 1)

        fetch_wind_btn = QPushButton("Fetch Wind Aloft from Open-Meteo")
        fetch_wind_btn.setToolTip(
            "Downloads upper-air wind data at multiple altitudes from Open-Meteo "
            "for the departure, destination, and route midpoint.")
        fetch_wind_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        fetch_wind_btn.clicked.connect(self._fp_fetch_wind_aloft)
        g_wind_l.addWidget(fetch_wind_btn, 1, 0, 1, 2)

        self.fp_wind_status = QLabel("Wind aloft not yet fetched.")
        self.fp_wind_status.setStyleSheet("color:#666655;font-style:italic;padding:2px;")
        g_wind_l.addWidget(self.fp_wind_status, 2, 0, 1, 2)
        g_wind.setLayout(g_wind_l)
        cl.addWidget(g_wind)

        # ── Generate ──────────────────────────────────────────────────────────
        g_gen = QGroupBox("Generate Flight Plan")
        g_gen_l = QVBoxLayout()

        gen_fp_btn = QPushButton("⚡  Generate Flight Plan + GeoPackage Route")
        gen_fp_btn.setToolTip(
            "Computes the full flight plan: SID→airways→STAR, wind-corrected headings, "
            "segment ETEs, fuel estimates and an elevation profile. Saves a GeoPackage "
            "route layer and a plain-text flight plan log.")
        gen_fp_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #dce8fc,stop:0.45 #b0c8f4,stop:0.55 #9ab8ee,stop:1 #88aaec);"
            "color:#000000;font-weight:bold;"
            "border-top:2px solid #ffffff;border-left:2px solid #ffffff;"
            "border-right:2px solid #316ac5;border-bottom:2px solid #316ac5;"
            "border-radius:2px;padding:8px 14px;min-height:22px;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #e8f0fc,stop:1 #b8d0f8);}"
            "QPushButton:disabled{background:#d4d0c8;color:#aca899;"
            "border-top:2px solid #e8e4dc;border-left:2px solid #e8e4dc;"
            "border-right:2px solid #b0aca4;border-bottom:2px solid #b0aca4;}")
        gen_fp_btn.clicked.connect(self._fp_generate)
        g_gen_l.addWidget(gen_fp_btn)

        self.fp_gen_status = QLabel("Flight plan not yet generated.")
        self.fp_gen_status.setStyleSheet("color:#666655;font-style:italic;padding:3px;")
        g_gen_l.addWidget(self.fp_gen_status)
        g_gen.setLayout(g_gen_l)
        cl.addWidget(g_gen)

        # ── Route Summary Table ───────────────────────────────────────────────
        g_tbl = QGroupBox("Route Waypoints & Segment Data")
        g_tbl_l = QVBoxLayout()
        self.fp_route_table = QTableWidget(0, 9)
        self.fp_route_table.setHorizontalHeaderLabels([
            "WPT", "Lat", "Lon", "Airway", "Alt (ft)",
            "TAS (kt)", "GS (kt)", "Hdg (°M)", "ETE (min)"
        ])
        self.fp_route_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.fp_route_table.setAlternatingRowColors(True)
        self.fp_route_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fp_route_table.setMinimumHeight(160)
        g_tbl_l.addWidget(self.fp_route_table)
        g_tbl.setLayout(g_tbl_l)
        cl.addWidget(g_tbl)

        # ── Flight Plan Text Output ───────────────────────────────────────────
        g_log = QGroupBox("Flight Plan Log / ICAO Format")
        g_log_l = QVBoxLayout()
        self.fp_log = QTextEdit()
        self.fp_log.setReadOnly(True)
        self.fp_log.setMinimumHeight(120)
        self.fp_log.setMaximumHeight(280)
        self.fp_log.setPlaceholderText(
            "Flight plan output will appear here after generation…")
        g_log_l.addWidget(self.fp_log)

        fp_btns = QHBoxLayout()
        load_fp_qgis_btn = QPushButton("Load Route → QGIS")
        load_fp_qgis_btn.setToolTip(
            "Load the generated flight plan route as a vector layer into QGIS.")
        load_fp_qgis_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        load_fp_qgis_btn.clicked.connect(self._fp_load_to_qgis)
        save_fp_btn = QPushButton("Save Flight Plan (TXT)")
        save_fp_btn.setToolTip(
            "Save the flight plan log to a plain-text file.")
        save_fp_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        save_fp_btn.clicked.connect(self._fp_save_txt)
        fp_btns.addWidget(load_fp_qgis_btn)
        fp_btns.addWidget(save_fp_btn)
        fp_btns.addStretch()
        g_log_l.addLayout(fp_btns)
        g_log.setLayout(g_log_l)
        cl.addWidget(g_log)

        cl.addStretch()
        scroll.setWidget(c)
        lv.addWidget(scroll)
        self.tab_widget.addTab(w, "[F] Flight Plan")

        # Internal state
        self._fp_dep_coords  = None   # (lat, lon) departure
        self._fp_dest_coords = None   # (lat, lon) destination
        self._fp_alt_coords  = None   # (lat, lon) alternate
        self._fp_airways     = []     # list of airway dicts
        self._fp_wind_data   = {}     # altitude → {u, v, speed, dir}
        self._fp_route_gpkg  = None   # path to saved GeoPackage

    # ── Flight Plan helpers ───────────────────────────────────────────────────

    def _fp_get_dep_coords(self):
        """Return (lat, lon) for departure — from ICAO lookup or planned runway."""
        if self._fp_dep_coords:
            return self._fp_dep_coords
        # Fall back to planned runway centroid
        if self.planner and self.planner.wgs84_centroid:
            return self.planner.wgs84_centroid
        if self.results and 'planner' in self.results:
            p = self.results['planner']
            if p.wgs84_centroid:
                return p.wgs84_centroid
        return None

    def _fp_lookup_airports(self):
        """Fetch airport lat/lon from OurAirports open CSV."""
        dep  = self.fp_dep_icao.text().strip().upper()
        dest = self.fp_dest_icao.text().strip().upper()
        alt  = self.fp_alt_icao.text().strip().upper()
        if not dest:
            QMessageBox.warning(self, "Flight Plan",
                                "Please enter at least a Destination ICAO code.")
            return

        self.fp_airport_status.setText("Fetching OurAirports database…")
        self.fp_airport_status.setStyleSheet("color:#316ac5;font-style:italic;padding:2px;")
        QApplication.processEvents()

        try:
            url = "https://davidmegginson.github.io/ourairports-data/airports.csv"
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            import io, csv
            reader = csv.DictReader(io.StringIO(r.text))
            airports = {}
            for row in reader:
                icao = row.get('ident','').strip().upper()
                try:
                    lat = float(row['latitude_deg'])
                    lon = float(row['longitude_deg'])
                    elev = float(row.get('elevation_ft', 0) or 0)
                    airports[icao] = (lat, lon, elev, row.get('name',''))
                except (ValueError, KeyError):
                    pass

            msgs = []
            if dep and dep in airports:
                self._fp_dep_coords = airports[dep][:2]
                msgs.append(f"DEP {dep}: {airports[dep][0]:.4f}°, {airports[dep][1]:.4f}° (elev {airports[dep][2]:.0f} ft)")
            elif dep:
                msgs.append(f"DEP {dep}: not found — will use planned runway centroid")

            if dest in airports:
                self._fp_dest_coords = airports[dest][:2]
                msgs.append(f"DEST {dest}: {airports[dest][0]:.4f}°, {airports[dest][1]:.4f}° (elev {airports[dest][2]:.0f} ft)")
            else:
                msgs.append(f"DEST {dest}: NOT FOUND in OurAirports database")
                self.fp_airport_status.setStyleSheet("color:#aa1111;font-weight:bold;padding:2px;")

            if alt and alt in airports:
                self._fp_alt_coords = airports[alt][:2]
                msgs.append(f"ALT {alt}: {airports[alt][0]:.4f}°, {airports[alt][1]:.4f}°")
            elif alt:
                msgs.append(f"ALT {alt}: not found")

            self.fp_airport_status.setText("  |  ".join(msgs))
            self.fp_airport_status.setStyleSheet("color:#1a6620;font-weight:bold;padding:2px;")
            self.log_message("Flight plan airport lookup complete.", "SUCCESS")
        except Exception as e:
            self.fp_airport_status.setText(f"Lookup failed: {e}")
            self.fp_airport_status.setStyleSheet("color:#aa1111;font-weight:bold;padding:2px;")
            self.log_message(f"Airport lookup error: {e}", "ERROR")

    def _fp_fetch_airways(self):
        """
        Fetch airways, VORs, NDBs and fixes from the Overpass API (OpenStreetMap).
        Builds a graph of airway segments so the planner can find the nearest
        published route and intercept it rather than routing in a straight line.
        No API key required.
        """
        dep_coords  = self._fp_get_dep_coords()
        dest_coords = self._fp_dest_coords

        if not dep_coords or not dest_coords:
            QMessageBox.warning(self, "Flight Plan",
                                "Resolve departure and destination airports first.")
            return

        self.fp_awy_status.setText("Querying Overpass API for airways and navaids…")
        self.fp_awy_status.setStyleSheet("color:#316ac5;font-style:italic;padding:2px;")
        QApplication.processEvents()

        dep_lat, dep_lon   = dep_coords
        dest_lat, dest_lon = dest_coords
        # Bounding box that covers both airports + buffer
        radius_nm  = self.fp_awy_radius.value()
        radius_deg = radius_nm / 60.0
        min_lat = min(dep_lat, dest_lat) - radius_deg
        max_lat = max(dep_lat, dest_lat) + radius_deg
        min_lon = min(dep_lon, dest_lon) - radius_deg
        max_lon = max(dep_lon, dest_lon) + radius_deg
        bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"

        self._fp_airways     = []   # list of waypoint dicts
        self._fp_airway_segs = []   # list of (wpt_id_a, wpt_id_b, airway_name) edges

        # ── Overpass QL query: airways + VOR/NDB navaids ─────────────────────
        overpass_query = f"""
[out:json][timeout:30];
(
  relation["route"="air"]({bbox});
  node["aeroway"="navigationaid"]({bbox});
  node["aeroway"="waypoint"]({bbox});
  node["man_made"="beacon"]["beacon:type"~"VOR|NDB|DVOR|TACAN",i]({bbox});
);
out body;
>;
out skel qt;
"""
        overpass_url = "https://overpass-api.de/api/interpreter"
        found_wpts = 0
        found_segs = 0

        try:
            try:
                resp = requests.post(overpass_url,
                                     data={'data': overpass_query},
                                     timeout=35, verify=True)
            except requests.exceptions.SSLError:
                import urllib3; urllib3.disable_warnings()
                resp = requests.post(overpass_url,
                                     data={'data': overpass_query},
                                     timeout=35, verify=False)

            if resp.status_code == 200:
                data  = resp.json()
                nodes = {}   # osm_id → {id, lat, lon, name}
                rels  = []   # airway relations

                for el in data.get('elements', []):
                    etype = el.get('type')
                    if etype == 'node':
                        tags = el.get('tags', {})
                        name = (tags.get('icao') or tags.get('name', '') or
                                tags.get('ref', '') or str(el['id']))
                        nodes[el['id']] = {
                            'id':   name[:8].upper(),
                            'name': tags.get('name', name),
                            'lat':  el['lat'],
                            'lon':  el['lon'],
                            'type': tags.get('aeroway', tags.get('man_made', 'FIX')),
                            'freq': tags.get('frequency', ''),
                        }
                    elif etype == 'relation':
                        tags = el.get('tags', {})
                        if tags.get('route') == 'air':
                            rels.append({
                                'name':    tags.get('name', tags.get('ref', 'UNK')),
                                'members': [m for m in el.get('members', [])
                                            if m.get('type') == 'node']
                            })

                # Build waypoint list and airway graph
                wpt_by_id = {}
                for node_id, wpt in nodes.items():
                    wpt_by_id[node_id] = wpt
                    if wpt['id'] not in [w['id'] for w in self._fp_airways]:
                        self._fp_airways.append(wpt)
                        found_wpts += 1

                # Build airway segment graph from relations
                for rel in rels:
                    member_ids = [m['ref'] for m in rel['members']
                                  if m['ref'] in wpt_by_id]
                    for i in range(len(member_ids) - 1):
                        a = wpt_by_id[member_ids[i]]
                        b = wpt_by_id[member_ids[i+1]]
                        self._fp_airway_segs.append({
                            'from': a['id'], 'to': b['id'],
                            'airway': rel['name'],
                            'lat_a': a['lat'], 'lon_a': a['lon'],
                            'lat_b': b['lat'], 'lon_b': b['lon'],
                        })
                        found_segs += 1

            self.log_message(
                f"Overpass: {found_wpts} nav-fixes, {found_segs} airway segments.", "INFO")

        except Exception as e:
            self.log_message(f"Overpass API error: {e}", "WARNING")

        # ── Fallback: OurAirports navaids CSV if Overpass returned nothing ────
        if not self._fp_airways:
            self.log_message("Overpass empty — fetching navaids from OurAirports CSV.", "WARNING")
            self.fp_awy_status.setText("Fetching navaids from OurAirports CSV…")
            QApplication.processEvents()
            try:
                nav_url = "https://davidmegginson.github.io/ourairports-data/navaids.csv"
                r2 = requests.get(nav_url, timeout=20)
                r2.raise_for_status()
                import io, csv
                reader = csv.DictReader(io.StringIO(r2.text))
                for row in reader:
                    try:
                        nlat = float(row['latitude_deg'])
                        nlon = float(row['longitude_deg'])
                        if min_lat <= nlat <= max_lat and min_lon <= nlon <= max_lon:
                            self._fp_airways.append({
                                'id':   (row.get('ident') or row.get('name','UNK'))[:8].upper(),
                                'name': row.get('name',''),
                                'lat':  nlat,
                                'lon':  nlon,
                                'type': row.get('type','NAVAID'),
                                'freq': row.get('frequency_khz',''),
                            })
                            found_wpts += 1
                    except (ValueError, KeyError):
                        pass
                self.log_message(f"OurAirports navaids: {found_wpts} loaded.", "INFO")
            except Exception as e2:
                self.log_message(f"OurAirports navaids fallback error: {e2}", "WARNING")

        # ── Last resort: geometric fixes along direct track ───────────────────
        if not self._fp_airways:
            self.log_message("No nav data available — using direct-track fixes.", "WARNING")
            n_pts = min(self.fp_max_wpts.value(), 6)
            for i in range(1, n_pts + 1):
                frac = i / (n_pts + 1)
                self._fp_airways.append({
                    'id':   f'D{i:03d}',
                    'name': f'Direct Fix {i}',
                    'lat':  dep_lat + frac * (dest_lat - dep_lat),
                    'lon':  dep_lon + frac * (dest_lon - dep_lon),
                    'type': 'FIX',
                    'freq': '',
                })
            found_wpts = len(self._fp_airways)

        seg_txt = f"  {found_segs} airway segments" if found_segs else "  (no airway graph — direct routing)"
        self.fp_awy_status.setText(
            f"✅  {found_wpts} nav-fixes loaded.{seg_txt}")
        self.fp_awy_status.setStyleSheet("color:#1a6620;font-weight:bold;padding:2px;")
        self.log_message(
            f"Airways fetch complete: {found_wpts} fixes, {found_segs} segments.", "SUCCESS")

    def _fp_fetch_wind_aloft(self):
        """
        Fetch upper-air wind data from Open-Meteo pressure-level forecast API.
        Maps ft altitudes to nearest hPa pressure levels.
        """
        dep_coords = self._fp_get_dep_coords()
        if not dep_coords:
            QMessageBox.warning(self, "Flight Plan",
                                "No departure coordinates — set project AOI or lookup airports first.")
            return

        self.fp_wind_status.setText("Fetching wind aloft from Open-Meteo…")
        self.fp_wind_status.setStyleSheet("color:#316ac5;font-style:italic;padding:2px;")
        QApplication.processEvents()

        try:
            # Parse altitude levels
            level_strs = self.fp_wind_levels.text().replace(' ','').split(',')
            alt_ft_list = [int(x) for x in level_strs if x.isdigit()]
            if not alt_ft_list:
                alt_ft_list = [3000, 6000, 9000, 12000, 18000]

            # ft → approximate pressure levels (standard ISA)
            # 1013.25 hPa at MSL; roughly -34 hPa per 1000 ft below FL180
            ft_to_hpa = {
                1000:950, 2000:930, 3000:912, 4000:875, 5000:843,
                6000:812, 7000:781, 8000:753, 9000:724, 10000:696,
                12000:644, 14000:595, 16000:549, 18000:506, 20000:466,
                24000:393, 30000:301, 35000:238, 40000:188, 45000:147,
            }
            # Map each ft level to nearest standard pressure level
            std_levels = sorted(ft_to_hpa.keys())
            avail_hpa  = [1000, 975, 950, 925, 900, 875, 850, 825, 800,
                          775, 750, 700, 650, 600, 550, 500, 450, 400,
                          350, 300, 250, 225, 200, 175, 150]
            hpa_set = set()
            ft_to_chosen_hpa = {}
            for ft in alt_ft_list:
                nearest_std = min(std_levels, key=lambda x: abs(x - ft))
                raw_hpa = ft_to_hpa[nearest_std]
                chosen  = min(avail_hpa, key=lambda x: abs(x - raw_hpa))
                hpa_set.add(chosen)
                ft_to_chosen_hpa[ft] = chosen

            hpa_vars = []
            for hpa in sorted(hpa_set, reverse=True):
                hpa_vars.extend([f'wind_speed_{hpa}hPa', f'wind_direction_{hpa}hPa',
                                  f'temperature_{hpa}hPa'])

            lat, lon = dep_coords
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                'latitude':   lat,
                'longitude':  lon,
                'hourly':     ','.join(hpa_vars),
                'wind_speed_unit': 'kn',
                'timezone':   'auto',
                'forecast_days': 1,
            }
            try:
                r = requests.get(url, params=params, timeout=20, verify=True)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings()
                r = requests.get(url, params=params, timeout=20, verify=False)

            if r.status_code != 200:
                raise ValueError(f"Open-Meteo HTTP {r.status_code}")

            data = r.json()
            hourly = data.get('hourly', {})
            # Use index 6 (approx 06:00 local — a mid-morning flight time)
            idx = min(6, len(hourly.get(hpa_vars[0], [0])) - 1)

            self._fp_wind_data = {}
            lines = []
            for ft in alt_ft_list:
                hpa = ft_to_chosen_hpa[ft]
                spd_key = f'wind_speed_{hpa}hPa'
                dir_key = f'wind_direction_{hpa}hPa'
                tmp_key = f'temperature_{hpa}hPa'
                spd = hourly.get(spd_key, [0])[idx]  if spd_key in hourly else 0
                wdir= hourly.get(dir_key, [0])[idx]  if dir_key in hourly else 0
                tmp = hourly.get(tmp_key, [0])[idx]  if tmp_key in hourly else None
                u   = -float(spd) * np.sin(np.radians(float(wdir)))  # east component
                v   = -float(spd) * np.cos(np.radians(float(wdir)))  # north component
                self._fp_wind_data[ft] = {
                    'speed_kt': float(spd),
                    'dir_deg':  float(wdir),
                    'u': u, 'v': v,
                    'temp_c':   float(tmp) if tmp is not None else None,
                    'hpa':      hpa,
                }
                temp_str = f"{tmp:.1f}°C" if tmp is not None else "N/A"
                lines.append(
                    f"  FL{ft//100:03d} ({hpa} hPa):  "
                    f"{spd:.0f} kt from {wdir:.0f}°  |  Temp {temp_str}")

            self.fp_wind_status.setText(
                "✅  Wind aloft fetched:\n" + "\n".join(lines))
            self.fp_wind_status.setStyleSheet(
                "color:#1a6620;font-weight:bold;padding:2px;")
            self.log_message(
                f"Wind aloft fetched for {len(alt_ft_list)} levels.", "SUCCESS")

        except Exception as e:
            self.fp_wind_status.setText(f"❌ Fetch failed: {e}")
            self.fp_wind_status.setStyleSheet("color:#aa1111;font-weight:bold;padding:2px;")
            self.log_message(f"Wind aloft fetch error: {e}", "ERROR")

    def _fp_generate(self):
        """
        Build a flight plan that INTERCEPTS the nearest published airway:
        1. Dep → climb to nearest airway entry fix (shortest lateral deviation)
        2. Fly along airway fixes toward destination
        3. Exit airway at fix closest to destination → Dest
        Wind correction + heading applied per segment.
        """
        dep_coords  = self._fp_get_dep_coords()
        dest_coords = self._fp_dest_coords

        if not dep_coords:
            QMessageBox.warning(self, "Flight Plan",
                                "No departure point. Set AOI or lookup airports first.")
            return
        if not dest_coords:
            QMessageBox.warning(self, "Flight Plan",
                                "No destination. Lookup airports first.")
            return

        self.fp_gen_status.setText("Generating flight plan…")
        self.fp_gen_status.setStyleSheet("color:#316ac5;font-style:italic;padding:3px;")
        QApplication.processEvents()

        try:
            from geographiclib.geodesic import Geodesic
            geod = Geodesic.WGS84

            dep_lat, dep_lon   = dep_coords
            dest_lat, dest_lon = dest_coords
            tas_kt   = self.fp_cruise_spd.value()
            alt_ft   = self.fp_cruise_alt.value()
            dep_icao = self.fp_dep_icao.text().strip().upper() or "ZZZZ"
            dest_icao= self.fp_dest_icao.text().strip().upper() or "ZZZZ"
            ac_type  = self.fp_ac_type.text().strip().upper() or "ZZZZ"
            rules    = self.fp_rules.currentText()
            sid      = self.fp_sid.text().strip().upper()
            star     = self.fp_star.text().strip().upper()
            max_wpts = self.fp_max_wpts.value()
            mag_var  = (self.planner.magnetic_variation
                        if self.planner and hasattr(self.planner,'magnetic_variation') else 0.0)

            # ── Wind at cruise altitude ───────────────────────────────────────
            wind_spd, wind_dir = 0.0, 0.0
            if self._fp_wind_data:
                nearest_alt = min(self._fp_wind_data, key=lambda x: abs(x - alt_ft))
                w = self._fp_wind_data[nearest_alt]
                wind_spd = w['speed_kt']
                wind_dir = w['dir_deg']

            direct_inv     = geod.Inverse(dep_lat, dep_lon, dest_lat, dest_lon)
            total_dist_nm  = direct_inv['s12'] / 1852.0

            # ── Helper: wind-corrected heading & ground speed ─────────────────
            def wind_correct(from_lat, from_lon, to_lat, to_lon):
                inv = geod.Inverse(from_lat, from_lon, to_lat, to_lon)
                dist_nm = inv['s12'] / 1852.0
                track   = inv['azi1'] % 360
                if wind_spd > 0 and tas_kt > 0:
                    swca = max(-1.0, min(1.0,
                                (wind_spd / tas_kt) *
                                math.sin(math.radians(wind_dir - track))))
                    wca   = math.degrees(math.asin(swca))
                    hdg_t = (track + wca) % 360
                    gs    = max(10.0,
                                tas_kt * math.cos(math.radians(wca)) -
                                wind_spd * math.cos(math.radians(wind_dir - track)))
                else:
                    hdg_t = track
                    gs    = float(tas_kt)
                hdg_m = (hdg_t - mag_var) % 360
                ete   = (dist_nm / gs) * 60 if gs > 0 else 0
                return dist_nm, hdg_m, gs, ete

            # ═══════════════════════════════════════════════════════════════
            # ROUTE BUILDING — intercept nearest airway
            # ═══════════════════════════════════════════════════════════════
            route_wpts = []
            airway_segs = getattr(self, '_fp_airway_segs', [])
            nav_fixes   = getattr(self, '_fp_airways',     [])

            if airway_segs and self.fp_use_airways.isChecked():
                # ── Build airway fix index ────────────────────────────────────
                fix_by_id = {}
                for fix in nav_fixes:
                    fix_by_id[fix['id']] = fix

                # ── Score each airway segment: pick the one that minimises the
                #    total path length  dep→A + A→B(along airway)→ … →dest
                # For simplicity use: dep_to_A + A_to_dest as proxy for
                # "how close does this segment bring us to dest" ───────────────
                best_entry  = None   # nav fix closest to departure on an airway
                best_exit   = None   # nav fix closest to destination on an airway
                best_airway = None
                best_cost   = float('inf')

                # Index unique airways
                airway_names = {}
                for seg in airway_segs:
                    nm = seg['airway']
                    if nm not in airway_names:
                        airway_names[nm] = []
                    airway_names[nm].append(seg)

                for awy_name, segs in airway_names.items():
                    # Collect ordered fixes on this airway
                    awy_ids_set = set()
                    for s in segs:
                        awy_ids_set.add(s['from'])
                        awy_ids_set.add(s['to'])
                    awy_fixes = [fix_by_id[fid] for fid in awy_ids_set
                                 if fid in fix_by_id]
                    if len(awy_fixes) < 2:
                        continue

                    for entry_fix in awy_fixes:
                        d_dep_entry = geod.Inverse(
                            dep_lat, dep_lon,
                            entry_fix['lat'], entry_fix['lon'])['s12'] / 1852.0
                        for exit_fix in awy_fixes:
                            if exit_fix['id'] == entry_fix['id']:
                                continue
                            d_exit_dest = geod.Inverse(
                                exit_fix['lat'], exit_fix['lon'],
                                dest_lat, dest_lon)['s12'] / 1852.0
                            cost = d_dep_entry + d_exit_dest
                            if cost < best_cost:
                                best_cost   = cost
                                best_entry  = entry_fix
                                best_exit   = exit_fix
                                best_airway = awy_name

                if best_entry and best_exit and best_airway:
                    # Build ordered airway fixes from entry to exit
                    # using BFS on the airway segment graph
                    from collections import deque
                    adj = {}
                    for seg in airway_names.get(best_airway, []):
                        adj.setdefault(seg['from'], []).append(seg['to'])
                        adj.setdefault(seg['to'],   []).append(seg['from'])

                    queue = deque([[best_entry['id']]])
                    visited = {best_entry['id']}
                    best_path = [best_entry['id']]
                    while queue:
                        path = queue.popleft()
                        last = path[-1]
                        if last == best_exit['id']:
                            best_path = path
                            break
                        for nxt in adj.get(last, []):
                            if nxt not in visited:
                                visited.add(nxt)
                                queue.append(path + [nxt])

                    # Thin to max_wpts intermediate fixes
                    mid_ids = best_path[1:-1]   # exclude entry and exit
                    if len(mid_ids) > max_wpts - 2:
                        step = max(1, len(mid_ids) // (max_wpts - 2))
                        mid_ids = mid_ids[::step][:max_wpts - 2]

                    # Assemble full route: DEP → entry → middles → exit → DEST
                    route_wpts.append({
                        'id': dep_icao, 'lat': dep_lat, 'lon': dep_lon,
                        'type': 'ADEP', 'airway': sid or 'SID'
                    })
                    route_wpts.append({
                        'id': best_entry['id'],
                        'lat': best_entry['lat'], 'lon': best_entry['lon'],
                        'type': best_entry.get('type', 'FIX'),
                        'airway': 'DCT'
                    })
                    for fid in mid_ids:
                        fix = fix_by_id.get(fid)
                        if fix:
                            route_wpts.append({
                                'id': fix['id'],
                                'lat': fix['lat'], 'lon': fix['lon'],
                                'type': fix.get('type', 'FIX'),
                                'airway': best_airway
                            })
                    route_wpts.append({
                        'id': best_exit['id'],
                        'lat': best_exit['lat'], 'lon': best_exit['lon'],
                        'type': best_exit.get('type', 'FIX'),
                        'airway': best_airway
                    })
                    route_wpts.append({
                        'id': dest_icao, 'lat': dest_lat, 'lon': dest_lon,
                        'type': 'ADES', 'airway': star or 'STAR'
                    })
                    self.log_message(
                        f"Airway route: DEP→{best_entry['id']}→{best_airway}→"
                        f"{best_exit['id']}→DEST  (cost {best_cost:.0f} nm)", "INFO")
                else:
                    # No viable airway interception — fall through to fix-based routing
                    airway_segs = []

            if not route_wpts:
                # ── Fallback: pick fixes nearest to direct track ──────────────
                candidates = []
                for nav in nav_fixes:
                    d_dep  = geod.Inverse(dep_lat, dep_lon,
                                          nav['lat'], nav['lon'])['s12'] / 1852.0
                    d_dest = geod.Inverse(nav['lat'], nav['lon'],
                                          dest_lat, dest_lon)['s12'] / 1852.0
                    cross  = abs(d_dep + d_dest - total_dist_nm)
                    if d_dep + d_dest <= total_dist_nm * 1.30 and cross <= 60:
                        candidates.append((d_dep, nav))
                candidates.sort(key=lambda x: x[0])
                if len(candidates) > max_wpts:
                    step = max(1, len(candidates) // max_wpts)
                    candidates = candidates[::step][:max_wpts]

                route_wpts = (
                    [{'id': dep_icao,  'lat': dep_lat,  'lon': dep_lon,
                      'type': 'ADEP',  'airway': sid or 'DCT'}] +
                    [{'id': c[1]['id'], 'lat': c[1]['lat'], 'lon': c[1]['lon'],
                      'type': c[1]['type'], 'airway': 'DCT'}
                     for c in candidates] +
                    [{'id': dest_icao, 'lat': dest_lat, 'lon': dest_lon,
                      'type': 'ADES',  'airway': star or 'DCT'}]
                )

            # ── Compute segment data ──────────────────────────────────────────
            segments = []
            total_ete = 0.0
            for i in range(len(route_wpts) - 1):
                a, b = route_wpts[i], route_wpts[i+1]
                dist_nm, hdg_m, gs, ete = wind_correct(
                    a['lat'], a['lon'], b['lat'], b['lon'])
                total_ete += ete
                segments.append({
                    'from': a['id'], 'to': b['id'],
                    'lat': b['lat'], 'lon': b['lon'],
                    'airway': b.get('airway','DCT'),
                    'alt_ft': alt_ft, 'tas_kt': tas_kt,
                    'gs_kt':  round(gs, 1),
                    'hdg_mag':round(hdg_m, 1),
                    'ete_min':round(ete, 1),
                    'dist_nm':round(dist_nm, 1),
                    'type':   b['type'],
                })

            # ── Populate route table ──────────────────────────────────────────
            self.fp_route_table.setRowCount(0)
            for seg in segments:
                r = self.fp_route_table.rowCount()
                self.fp_route_table.insertRow(r)
                vals = [seg['to'], f"{seg['lat']:.4f}", f"{seg['lon']:.4f}",
                        seg['airway'], str(seg['alt_ft']), str(seg['tas_kt']),
                        str(seg['gs_kt']), f"{seg['hdg_mag']:.1f}",
                        f"{seg['ete_min']:.1f}"]
                for c, v in enumerate(vals):
                    self.fp_route_table.setItem(r, c, QTableWidgetItem(v))

            # ── ICAO flight plan text ─────────────────────────────────────────
            total_h = int(total_ete // 60)
            total_m = int(total_ete % 60)
            route_str = ' '.join(
                ([dep_icao] + ([sid] if sid else []) +
                 [s['to'] for s in segments[:-1]] +
                 ([star] if star else []) + [dest_icao]))
            wind_str = (f"{int(wind_dir):03d}/{wind_spd:04.1f}KT"
                        if wind_spd else "00000KT")

            fp_text = (
                f"{'='*64}\n  ICAO FLIGHT PLAN\n{'='*64}\n"
                f"  Aircraft      : {ac_type}\n"
                f"  Flight Rules  : {rules}\n"
                f"  Departure     : {dep_icao}  ({dep_lat:.4f}N  {dep_lon:.4f}E)\n"
                f"  Destination   : {dest_icao}  ({dest_lat:.4f}N  {dest_lon:.4f}E)\n"
                f"  Cruise Alt    : FL{alt_ft//100:03d}  ({alt_ft} ft)\n"
                f"  TAS           : N{tas_kt:04d}\n"
                f"  Wind at level : {wind_str}\n"
                f"  Total ETE     : {total_h:02d}h {total_m:02d}m\n"
                f"  Route         : {route_str}\n"
                f"{'─'*64}\n  SEGMENT DETAIL\n{'─'*64}\n"
            )
            for seg in segments:
                fp_text += (
                    f"  {seg['from']:>6} → {seg['to']:<6}  "
                    f"AWY:{seg['airway']:<10}  Hdg:{seg['hdg_mag']:05.1f}M  "
                    f"GS:{seg['gs_kt']:5.1f}kt  Dist:{seg['dist_nm']:6.1f}nm  "
                    f"ETE:{seg['ete_min']:5.1f}min\n"
                )
            if self._fp_wind_data:
                fp_text += f"{'─'*64}\n  WIND ALOFT\n"
                for ft_l in sorted(self._fp_wind_data):
                    wd = self._fp_wind_data[ft_l]
                    tc = f"{wd['temp_c']:.1f}°C" if wd.get('temp_c') else "N/A"
                    fp_text += (f"  FL{ft_l//100:03d}: "
                                f"{wd['speed_kt']:.0f}kt from {wd['dir_deg']:.0f}°  |  {tc}\n")
            fp_text += "="*64 + "\n"
            self.fp_log.setPlainText(fp_text)

            # ── Save GeoPackage ───────────────────────────────────────────────
            out_dir = (self.planner.output_dir
                       if self.planner and self.planner.output_dir
                       else self.output_dir.text().strip())
            if out_dir:
                fp_dir = os.path.join(out_dir, 'Flight_Plan')
                os.makedirs(fp_dir, exist_ok=True)
                gpkg_path = os.path.join(fp_dir, 'flight_plan_route.gpkg')

                from shapely.geometry import LineString as SLine
                coords = [(w['lon'], w['lat']) for w in route_wpts]
                gpd.GeoDataFrame([{
                    'dep': dep_icao, 'dest': dest_icao, 'rules': rules,
                    'alt_ft': alt_ft, 'tas_kt': tas_kt,
                    'ete_min': round(total_ete, 1), 'n_wpts': len(route_wpts),
                    'wind_kt': round(wind_spd,1), 'wind_dir': round(wind_dir,1),
                    'geometry': SLine(coords)
                }], crs='EPSG:4326').to_file(gpkg_path, layer='route', driver='GPKG')

                cum = 0.0
                wpt_rows = []
                for j, wpt in enumerate(route_wpts):
                    if j:
                        prev = route_wpts[j-1]
                        cum += geod.Inverse(prev['lat'],prev['lon'],
                                            wpt['lat'],wpt['lon'])['s12']/1852.0
                    wpt_rows.append({
                        'seq': j+1, 'id': wpt['id'], 'type': wpt['type'],
                        'alt_ft': alt_ft, 'cum_dist_nm': round(cum,1),
                        'airway': wpt.get('airway','DCT'),
                        'geometry': Point(wpt['lon'], wpt['lat'])
                    })
                gpd.GeoDataFrame(wpt_rows, crs='EPSG:4326').to_file(
                    gpkg_path, layer='waypoints', driver='GPKG')

                self._fp_route_gpkg = gpkg_path
                self.fp_gen_status.setText(
                    f"✅  {dep_icao}→{dest_icao}  {len(route_wpts)} wpts  "
                    f"ETE {total_h:02d}h{total_m:02d}m  "
                    f"Route: {route_str[:60]}")
            else:
                self._fp_route_gpkg = None
                self.fp_gen_status.setText(
                    f"✅  {dep_icao}→{dest_icao}  {len(route_wpts)} wpts  "
                    f"ETE {total_h:02d}h{total_m:02d}m  (no output dir — GPKG not saved)")
            self.fp_gen_status.setStyleSheet("color:#1a6620;font-weight:bold;padding:3px;")
            self.log_message(
                f"Flight plan: {dep_icao}→{dest_icao} via {route_str}", "SUCCESS")

        except Exception as e:
            self.fp_gen_status.setText(f"❌  Generation failed: {e}")
            self.fp_gen_status.setStyleSheet("color:#aa1111;font-weight:bold;padding:3px;")
            self.log_message(f"Flight plan error: {e}", "ERROR")
            import traceback
            self.log_message(traceback.format_exc(), "ERROR")
    def _fp_load_to_qgis(self):
        """Load the generated flight plan GeoPackage route + waypoints into QGIS."""
        if not self._fp_route_gpkg or not os.path.exists(self._fp_route_gpkg):
            QMessageBox.warning(self, "Flight Plan",
                                "Generate the flight plan first.")
            return
        root = QgsProject.instance().layerTreeRoot()
        grp  = root.insertGroup(0, "Flight Plan Route")
        loaded = 0

        # Route line
        route_lyr = QgsVectorLayer(
            f"{self._fp_route_gpkg}|layername=route", "Route Track", "ogr")
        if route_lyr.isValid():
            sym = QgsLineSymbol.createSimple(
                {'color': '#1040a0', 'width': '1.8', 'penstyle': 'dash'})
            route_lyr.setRenderer(QgsSingleSymbolRenderer(sym))
            QgsProject.instance().addMapLayer(route_lyr, False)
            grp.addLayer(route_lyr)
            loaded += 1

        # Waypoints
        wpt_lyr = QgsVectorLayer(
            f"{self._fp_route_gpkg}|layername=waypoints", "Waypoints", "ogr")
        if wpt_lyr.isValid():
            sym = QgsMarkerSymbol.createSimple(
                {'name': 'triangle', 'color': '#316ac5',
                 'color_border': '#1040a0', 'outline_width': '0.4', 'size': '3.0'})
            wpt_lyr.setRenderer(QgsSingleSymbolRenderer(sym))
            # Label waypoints by id
            pal = QgsPalLayerSettings()
            pal.fieldName = 'id'
            pal.enabled   = True
            tf = QgsTextFormat()
            tf.setSize(8)
            from qgis.PyQt.QtGui import QFont as _QF
            tf.setFont(_QF("Tahoma", 8))
            tf.setColor(QColor('#1040a0'))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(1.0)
            buf.setColor(QColor('#ffffff'))
            tf.setBuffer(buf)
            pal.setFormat(tf)
            wpt_lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
            wpt_lyr.setLabelsEnabled(True)
            QgsProject.instance().addMapLayer(wpt_lyr, False)
            grp.addLayer(wpt_lyr)
            loaded += 1

        if loaded:
            self.log_message(
                f"Flight plan route loaded into QGIS ({loaded} layers).", "SUCCESS")
            QMessageBox.information(self, "Loaded",
                f"Flight plan route loaded.\n{loaded} layers in 'Flight Plan Route' group.")
        else:
            QMessageBox.warning(self, "Load Failed",
                "Could not load flight plan layers from GeoPackage.")

    def _fp_save_txt(self):
        """Save the flight plan log text to a plain-text file."""
        text = self.fp_log.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Flight Plan", "Generate the flight plan first.")
            return
        default = ""
        if self.planner and self.planner.output_dir:
            default = os.path.join(self.planner.output_dir,
                                   'Flight_Plan', 'flight_plan.txt')
        fn, _ = QFileDialog.getSaveFileName(
            self, "Save Flight Plan", default,
            "Text Files (*.txt);;All Files (*)")
        if fn:
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(text)
            self.log_message(f"Flight plan saved to {fn}", "SUCCESS")
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)

        # --- Candidate Selector ---
        g_cand_ss = QGroupBox("Candidate Selection")
        g_cand_ss_inner = QWidget()
        g_cand_ss_row = QHBoxLayout(g_cand_ss_inner)
        g_cand_ss_row.setContentsMargins(6, 4, 6, 4)
        g_cand_ss_row.addWidget(QLabel("Generate for Candidate #:"))
        self.ss_candidate_spin = QSpinBox()
        self.ss_candidate_spin.setRange(1, 20)
        self.ss_candidate_spin.setValue(1)
        self.ss_candidate_spin.setToolTip(
            "Safety surfaces will be generated for this ranked candidate.\n"
            "Run full analysis first to see available candidates.")
        self.ss_candidate_spin.setMinimumWidth(60)
        g_cand_ss_row.addWidget(self.ss_candidate_spin)
        g_cand_ss_row.addStretch()
        g_cand_ss_vl = QVBoxLayout(g_cand_ss)
        g_cand_ss_vl.setContentsMargins(4, 4, 4, 4)
        g_cand_ss_vl.addWidget(g_cand_ss_inner)
        cl.addWidget(g_cand_ss)

        # --- ILS / Glide Slope Configuration ---
        g_gs = QGroupBox("ILS Glide Slope Configuration")
        g_gs_l = QGridLayout()
        g_gs_l.addWidget(QLabel("Glide Slope Angle (°):"), 0, 0)
        self.glide_angle_spin = QDoubleSpinBox()
        self.glide_angle_spin.setRange(2.5, 4.5)
        self.glide_angle_spin.setSingleStep(0.1)
        self.glide_angle_spin.setValue(3.0)
        self.glide_angle_spin.setDecimals(1)
        self.glide_angle_spin.setSuffix("°")
        self.glide_angle_spin.setToolTip("ICAO standard: 3.0°. Range 2.5°–3.5°")
        g_gs_l.addWidget(self.glide_angle_spin, 0, 1)
        g_gs_l.addWidget(QLabel("Threshold Crossing Height (m):"), 1, 0)
        self.tch_spin = QDoubleSpinBox()
        self.tch_spin.setRange(10, 30)
        self.tch_spin.setValue(15)
        self.tch_spin.setSuffix(" m")
        self.tch_spin.setToolTip("Typical TCH: 15 m for precision approaches")
        g_gs_l.addWidget(self.tch_spin, 1, 1)
        g_gs_l.addWidget(QLabel("Approach Category:"), 2, 0)
        self.surface_approach_type = QComboBox()
        self.surface_approach_type.addItems(["Non-precision", "Precision Cat I", "Precision Cat II/III"])
        g_gs_l.addWidget(self.surface_approach_type, 2, 1)
        g_gs.setLayout(g_gs_l)
        cl.addWidget(g_gs)

        # --- OLS Surfaces Selection ---
        g_ols = QGroupBox("Obstacle Limitation Surfaces (OLS) — ICAO Annex 14")
        g_ols_l = QVBoxLayout()
        self.cb_glide_slope = QCheckBox("Glide Slope Surface (ILS)")
        self.cb_glide_slope.setChecked(True)
        self.cb_takeoff_climb = QCheckBox("Take-off Climb Surface")
        self.cb_takeoff_climb.setChecked(True)
        self.cb_approach_surf = QCheckBox("Approach Surface")
        self.cb_approach_surf.setChecked(True)
        self.cb_transitional = QCheckBox("Transitional Surface")
        self.cb_transitional.setChecked(True)
        self.cb_inner_horiz = QCheckBox("Inner Horizontal Surface (45 m AGL)")
        self.cb_inner_horiz.setChecked(True)
        self.cb_conical = QCheckBox("Conical Surface (5% slope)")
        self.cb_conical.setChecked(True)
        self.cb_outer_horiz = QCheckBox("Outer Horizontal Surface (150 m AGL)")
        self.cb_outer_horiz.setChecked(True)
        for cb in [self.cb_glide_slope, self.cb_takeoff_climb, self.cb_approach_surf,
                   self.cb_transitional, self.cb_inner_horiz, self.cb_conical, self.cb_outer_horiz]:
            g_ols_l.addWidget(cb)
        g_ols.setLayout(g_ols_l)
        cl.addWidget(g_ols)

        # --- Runway Orientation Override ---
        g_rwy_sel = QGroupBox("Runway / Candidate Selection")
        g_rwy_sel_l = QGridLayout()
        g_rwy_sel_l.addWidget(QLabel("Use Orientation (° True):"), 0, 0)
        self.surfaces_orientation = QDoubleSpinBox()
        self.surfaces_orientation.setRange(0, 359)
        self.surfaces_orientation.setValue(0)
        self.surfaces_orientation.setSuffix("°")
        self.surfaces_orientation.setToolTip("Leave 0 to auto-use top ranked candidate from analysis")
        g_rwy_sel_l.addWidget(self.surfaces_orientation, 0, 1)
        self.surfaces_use_top_candidate = QCheckBox("Auto-use top ranked candidate")
        self.surfaces_use_top_candidate.setChecked(True)
        g_rwy_sel_l.addWidget(self.surfaces_use_top_candidate, 1, 0, 1, 2)
        g_rwy_sel.setLayout(g_rwy_sel_l)
        cl.addWidget(g_rwy_sel)

        # --- Generate Button & Status ---
        g_gen = QGroupBox("Generate")
        g_gen_l = QVBoxLayout()
        self.generate_safety_surfaces_btn = QPushButton("Generate All Safety Surfaces (GeoPackage)")
        self.generate_safety_surfaces_btn.setStyleSheet(
            "QPushButton { background-color:#2c5282; color:white; font-weight:bold; padding:10px; border-radius:5px; }"
            "QPushButton:hover { background-color:#2a4a7f; }"
            "QPushButton:disabled { background-color:#95a5a6; }"
        )
        g_gen_l.addWidget(self.generate_safety_surfaces_btn)
        self.load_safety_surfaces_qgis_btn = QPushButton("Load Safety Surfaces into QGIS")
        self.load_safety_surfaces_qgis_btn.setEnabled(False)
        g_gen_l.addWidget(self.load_safety_surfaces_qgis_btn)
        g_gen.setLayout(g_gen_l)
        cl.addWidget(g_gen)

        # --- OLS Parameters Reference ---
        g_ref = QGroupBox("ICAO Annex 14 Surface Parameters Reference")
        g_ref_l = QVBoxLayout()
        self.surfaces_ref_display = QTextEdit()
        self.surfaces_ref_display.setReadOnly(True)
        self.surfaces_ref_display.setMinimumHeight(90)
        self.surfaces_ref_display.setMaximumHeight(200)
        self.surfaces_ref_display.setPlainText(
            "Inner Horizontal Surface: Height 45 m | Radius 2000–4000 m per code\n"
            "Conical Surface: Slope 5% | Rises from IHS edge | Height 35–100 m per code\n"
            "Outer Horizontal Surface: Height 150 m above aerodrome elevation\n"
            "Approach Surface: Divergence 10–12.5% | Length 1000–3600 m | Slope 2.5–5%\n"
            "Transitional Surface: Slope 20% (1:5) from strip edges to IHS level\n"
            "Take-off Climb: Slope 1.2–2% | Length 1600–15000 m | Divergence 10–12.5%\n"
            "Glide Slope: Standard 3° | TCH 15 m | Inner width 150 m"
        )
        g_ref_l.addWidget(self.surfaces_ref_display)
        g_ref.setLayout(g_ref_l)
        cl.addWidget(g_ref)

        # --- Generation Log ---
        g_log = QGroupBox("Generation Log")
        g_log_l = QVBoxLayout()
        self.surfaces_log = QTextEdit()
        self.surfaces_log.setReadOnly(True)
        self.surfaces_log.setMinimumHeight(90)
        self.surfaces_log.setMaximumHeight(200)
        g_log_l.addWidget(self.surfaces_log)
        g_log.setLayout(g_log_l)
        cl.addWidget(g_log)

        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[S] Safety Surfaces")

    # ========================================================================
    # TERMINAL & UTILITIES TAB
    # ========================================================================
    def setup_terminal_utilities_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        c = QWidget()
        cl = QVBoxLayout(c)

        # --- Candidate Selector ---
        g_cand_tu = QGroupBox("Candidate Selection")
        g_cand_tu_inner = QWidget()
        g_cand_tu_row = QHBoxLayout(g_cand_tu_inner)
        g_cand_tu_row.setContentsMargins(6, 4, 6, 4)
        g_cand_tu_row.addWidget(QLabel("Generate for Candidate #:"))
        self.tu_candidate_spin = QSpinBox()
        self.tu_candidate_spin.setRange(1, 20)
        self.tu_candidate_spin.setValue(1)
        self.tu_candidate_spin.setToolTip(
            "Terminal & Utilities will be generated for this ranked candidate.\n"
            "Run full analysis first to see available candidates.")
        self.tu_candidate_spin.setMinimumWidth(60)
        g_cand_tu_row.addWidget(self.tu_candidate_spin)
        g_cand_tu_row.addStretch()
        g_cand_tu_vl = QVBoxLayout(g_cand_tu)
        g_cand_tu_vl.setContentsMargins(4, 4, 4, 4)
        g_cand_tu_vl.addWidget(g_cand_tu_inner)
        cl.addWidget(g_cand_tu)

        # --- Terminal Building ---
        g_term = QGroupBox("Terminal Building Parameters")
        g_term_l = QGridLayout()
        g_term_l.addWidget(QLabel("Terminal Length (m):"), 0, 0)
        self.term_length = QDoubleSpinBox()
        self.term_length.setRange(50, 800)
        self.term_length.setValue(200)
        self.term_length.setSuffix(" m")
        g_term_l.addWidget(self.term_length, 0, 1)
        g_term_l.addWidget(QLabel("Terminal Width (m):"), 1, 0)
        self.term_width = QDoubleSpinBox()
        self.term_width.setRange(30, 300)
        self.term_width.setValue(80)
        self.term_width.setSuffix(" m")
        g_term_l.addWidget(self.term_width, 1, 1)
        g_term_l.addWidget(QLabel("Terminal Side:"), 2, 0)
        self.term_side = QComboBox()
        self.term_side.addItems(["Right of Runway (heading)", "Left of Runway (heading)"])
        g_term_l.addWidget(self.term_side, 2, 1)
        g_term_l.addWidget(QLabel("Offset from Runway CL (m):"), 3, 0)
        self.term_offset = QDoubleSpinBox()
        self.term_offset.setRange(150, 1000)
        self.term_offset.setValue(350)
        self.term_offset.setSuffix(" m")
        self.term_offset.setToolTip("Minimum safe offset from runway centerline per ARC code")
        g_term_l.addWidget(self.term_offset, 3, 1)
        g_term.setLayout(g_term_l)
        cl.addWidget(g_term)

        # --- Facilities to Generate ---
        g_fac = QGroupBox("Facilities to Generate")
        g_fac_l = QVBoxLayout()
        self.cb_terminal = QCheckBox("Terminal Building")
        self.cb_terminal.setChecked(True)
        self.cb_atc_tower = QCheckBox("ATC Tower (Point)")
        self.cb_atc_tower.setChecked(True)
        self.cb_apron = QCheckBox("Aircraft Apron / Stand Area")
        self.cb_apron.setChecked(True)
        self.cb_hangars = QCheckBox("Hangars")
        self.cb_hangars.setChecked(True)
        self.cb_fuel_depot = QCheckBox("Fuel Depot")
        self.cb_fuel_depot.setChecked(True)
        self.cb_cargo = QCheckBox("Cargo Terminal")
        self.cb_cargo.setChecked(True)
        self.cb_parking = QCheckBox("Vehicle Parking")
        self.cb_parking.setChecked(True)
        self.cb_fire_station = QCheckBox("ARFF / Fire Station")
        self.cb_fire_station.setChecked(True)
        self.cb_access_roads = QCheckBox("Access Roads")
        self.cb_access_roads.setChecked(True)
        self.cb_taxiways = QCheckBox("Taxiways (Parallel + RETs)")
        self.cb_taxiways.setChecked(True)
        for cb in [self.cb_terminal, self.cb_atc_tower, self.cb_apron, self.cb_hangars,
                   self.cb_fuel_depot, self.cb_cargo, self.cb_parking, self.cb_fire_station,
                   self.cb_access_roads, self.cb_taxiways]:
            g_fac_l.addWidget(cb)
        g_fac.setLayout(g_fac_l)
        cl.addWidget(g_fac)

        # --- Hangar Configuration ---
        g_hang = QGroupBox("Hangar Configuration")
        g_hang_l = QGridLayout()
        g_hang_l.addWidget(QLabel("Number of Hangars:"), 0, 0)
        self.n_hangars_spin = QSpinBox()
        self.n_hangars_spin.setRange(1, 8)
        self.n_hangars_spin.setValue(3)
        g_hang_l.addWidget(self.n_hangars_spin, 0, 1)
        g_hang_l.addWidget(QLabel("Hangar Length (m):"), 1, 0)
        self.hangar_length = QDoubleSpinBox()
        self.hangar_length.setRange(30, 300)
        self.hangar_length.setValue(80)
        self.hangar_length.setSuffix(" m")
        g_hang_l.addWidget(self.hangar_length, 1, 1)
        g_hang_l.addWidget(QLabel("Hangar Width (m):"), 2, 0)
        self.hangar_width = QDoubleSpinBox()
        self.hangar_width.setRange(20, 150)
        self.hangar_width.setValue(60)
        self.hangar_width.setSuffix(" m")
        g_hang_l.addWidget(self.hangar_width, 2, 1)
        g_hang.setLayout(g_hang_l)
        cl.addWidget(g_hang)

        # --- Generate Button ---
        g_gen2 = QGroupBox("Generate")
        g_gen2_l = QVBoxLayout()
        self.generate_terminal_btn = QPushButton("Generate Terminal & Utilities (GeoPackage)")
        self.generate_terminal_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #d4ecd4,stop:0.45 #a8d4a8,stop:0.55 #90c490,stop:1 #78b478);color:#000000;font-weight:bold;border-top:2px solid #ffffff;border-left:2px solid #ffffff;border-right:2px solid #2d7a2d;border-bottom:2px solid #2d7a2d;border-radius:2px;padding:10px 14px;min-height:22px;}""QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #e0f4e0,stop:1 #b8e0b8);}""QPushButton:disabled{background:#d4d0c8;color:#aca899;border-top:2px solid #e8e4dc;border-left:2px solid #e8e4dc;border-right:2px solid #b0aca4;border-bottom:2px solid #b0aca4;}"
        )
        g_gen2_l.addWidget(self.generate_terminal_btn)
        self.load_terminal_qgis_btn = QPushButton("Load Terminal Layers into QGIS")
        self.load_terminal_qgis_btn.setEnabled(False)
        g_gen2_l.addWidget(self.load_terminal_qgis_btn)
        g_gen2.setLayout(g_gen2_l)
        cl.addWidget(g_gen2)

        # --- Generation Log ---
        g_tlog = QGroupBox("Terminal Generation Log")
        g_tlog_l = QVBoxLayout()
        self.terminal_log = QTextEdit()
        self.terminal_log.setReadOnly(True)
        self.terminal_log.setMinimumHeight(90)
        self.terminal_log.setMaximumHeight(200)
        g_tlog_l.addWidget(self.terminal_log)
        g_tlog.setLayout(g_tlog_l)
        cl.addWidget(g_tlog)

        cl.addStretch()
        scroll.setWidget(c)
        l.addWidget(scroll)
        self.tab_widget.addTab(w, "[T] Terminal & Utilities")

    def create_status_area(self):
        # Status area removed — replaced by Aero title bar status label
        self.validation_status = QLabel()
        self.analysis_status = QLabel()
        self.timestamp_label = QLabel()
        return QFrame()  # empty placeholder

    def apply_professional_styling(self):
        """Windows XP Luna Blue theme — the classic 2001 look."""
        self.setStyleSheet("""
/* ═══════════════════════════════════════════════════════════════
   AIRPORT RUNWAY PLANNER — Windows XP Luna Blue Theme
   ═══════════════════════════════════════════════════════════════ */

/* ── Base window / dialog ─────────────────────────────────────── */
QMainWindow, QWidget, QDialog {
    background-color: #ece9d8;
    color: #000000;
    font-family: "Tahoma", "Arial", sans-serif;
    font-size: 11px;
}

/* ── Menu bar ─────────────────────────────────────────────────── */
QMenuBar {
    background-color: #ece9d8;
    color: #000000;
    border-bottom: 1px solid #aca899;
    padding: 1px 0;
}
QMenuBar::item { padding: 3px 8px; background: transparent; }
QMenuBar::item:selected, QMenuBar::item:pressed {
    background-color: #316ac5;
    color: #ffffff;
}
QMenu {
    background-color: #ffffff;
    border: 1px solid #aca899;
    padding: 2px 0;
}
QMenu::item { padding: 4px 22px 4px 10px; color: #000000; }
QMenu::item:selected { background-color: #316ac5; color: #ffffff; }
QMenu::separator { height: 1px; background: #aca899; margin: 2px 4px; }

/* ── Status bar ──────────────────────────────────────────────── */
QStatusBar {
    background-color: #ece9d8;
    color: #000000;
    border-top: 1px solid #aca899;
    font-size: 10px;
}

/* ── Tab widget ──────────────────────────────────────────────── */
QTabWidget::pane {
    background-color: #ece9d8;
    border: 1px solid #aca899;
    border-top: 2px solid #316ac5;
}
QTabBar::tab {
    background-color: #d4d0c8;
    color: #000000;
    border: 1px solid #aca899;
    border-bottom: none;
    padding: 5px 13px;
    margin-right: 1px;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
    font-size: 10px;
    font-weight: bold;
    min-width: 60px;
}
QTabBar::tab:selected {
    background-color: #ece9d8;
    color: #000000;
    border: 1px solid #aca899;
    border-bottom: 2px solid #ece9d8;
    margin-bottom: -1px;
    font-weight: bold;
}
QTabBar::tab:hover:!selected {
    background-color: #e8e4d8;
    color: #000000;
}

/* ── Group boxes ─────────────────────────────────────────────── *
 *  FIX: margin-top must be >= title font line-height (≈16 px).   *
 *  padding-top adds internal space so content clears the border. *
 * ──────────────────────────────────────────────────────────────*/
QGroupBox {
    background-color: #ece9d8;
    border: 2px groove #aca899;
    border-radius: 0px;
    margin-top: 18px;
    padding-top: 6px;
    padding-left: 4px;
    padding-right: 4px;
    padding-bottom: 4px;
    font-weight: bold;
    font-size: 11px;
    color: #003c74;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    top: -1px;
    padding: 2px 5px;
    background-color: #ece9d8;
    color: #003c74;
    font-weight: bold;
    font-size: 11px;
}

/* ── Labels ──────────────────────────────────────────────────── */
QLabel { color: #000000; background: transparent; }

/* ── Input fields — sunken 3-D look ─────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QDateEdit {
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #ffffff;
    border-bottom: 1px solid #ffffff;
    padding: 2px 4px;
    min-height: 20px;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
}
QLineEdit:focus, QTextEdit:focus, QDateEdit:focus {
    border-top: 2px solid #316ac5;
    border-left: 2px solid #316ac5;
}
QLineEdit:disabled, QTextEdit:disabled {
    background-color: #d4d0c8;
    color: #808080;
}

/* ── ComboBox ────────────────────────────────────────────────── */
QComboBox {
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #ffffff;
    border-bottom: 1px solid #ffffff;
    padding: 2px 4px;
    min-height: 20px;
    selection-background-color: #316ac5;
}
QComboBox:focus { border-top: 2px solid #316ac5; border-left: 2px solid #316ac5; }
QComboBox:disabled { background-color: #d4d0c8; color: #808080; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 17px;
    border-left: 1px solid #aca899;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f0ece0, stop:0.5 #ece9d8, stop:1 #d4d0c8);
}
QComboBox::drop-down:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #e8f0fc, stop:1 #c4d8f0);
    border-left: 1px solid #7f9db9;
}
QComboBox::down-arrow {
    width: 0; height: 0;
    border-style: solid;
    border-width: 5px 4px 0 4px;
    border-color: #404040 transparent transparent transparent;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #7f9db9;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
    outline: none;
}

/* ── SpinBox ─────────────────────────────────────────────────── */
QSpinBox, QDoubleSpinBox {
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #ffffff;
    border-bottom: 1px solid #ffffff;
    padding: 2px 4px;
    padding-right: 18px;
    min-height: 20px;
    selection-background-color: #316ac5;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border-top: 2px solid #316ac5;
    border-left: 2px solid #316ac5;
}
QSpinBox:disabled, QDoubleSpinBox:disabled { background-color: #d4d0c8; color: #808080; }

QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 17px;
    border-left: 1px solid #aca899;
    border-bottom: 1px solid #aca899;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f4f2ec, stop:1 #d4d0c8);
    border-top-right-radius: 0px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #e8f0fc, stop:1 #c4d8f0);
    border-left: 1px solid #7f9db9;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed {
    background: #b8c8d8;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 17px;
    border-left: 1px solid #aca899;
    border-top: 1px solid #aca899;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #e8e4d8, stop:1 #d4d0c8);
    border-bottom-right-radius: 0px;
}
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #e8f0fc, stop:1 #c4d8f0);
    border-left: 1px solid #7f9db9;
}
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background: #b8c8d8;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    width: 0; height: 0;
    border-style: solid;
    border-width: 0 4px 5px 4px;
    border-color: transparent transparent #404040 transparent;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
    border-color: transparent transparent #a0a0a0 transparent;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    width: 0; height: 0;
    border-style: solid;
    border-width: 5px 4px 0 4px;
    border-color: #404040 transparent transparent transparent;
}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
    border-color: #a0a0a0 transparent transparent transparent;
}

/* ── Push buttons — XP raised 3-D look ──────────────────────── */
QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f4f2ec, stop:0.45 #ece9d8,
        stop:0.55 #dedad0, stop:1 #cdc9be);
    color: #000000;
    border-top: 2px solid #ffffff;
    border-left: 2px solid #ffffff;
    border-right: 2px solid #808080;
    border-bottom: 2px solid #808080;
    border-radius: 2px;
    padding: 4px 12px;
    min-height: 22px;
    font-family: "Tahoma", Arial;
    font-size: 11px;
}
QPushButton:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f0f4fe, stop:0.45 #dce8fc,
        stop:0.55 #ccdaf8, stop:1 #b8ccf0);
    border-top: 2px solid #ffffff;
    border-left: 2px solid #ffffff;
    border-right: 2px solid #316ac5;
    border-bottom: 2px solid #316ac5;
    color: #000000;
}
QPushButton:pressed {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #b8b4ac, stop:1 #ccc8c0);
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
    padding-top: 5px;
    padding-left: 13px;
}
QPushButton:focus {
    border-top: 2px solid #316ac5;
    border-left: 2px solid #316ac5;
    border-right: 2px solid #316ac5;
    border-bottom: 2px solid #316ac5;
}
QPushButton:default {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #dce8fc, stop:1 #b0c8f4);
    border-top: 2px solid #ffffff;
    border-left: 2px solid #ffffff;
    border-right: 2px solid #316ac5;
    border-bottom: 2px solid #316ac5;
}
QPushButton:disabled {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #e0dcd4, stop:1 #d4d0c8);
    color: #aca899;
    border-top: 2px solid #e8e4dc;
    border-left: 2px solid #e8e4dc;
    border-right: 2px solid #b0aca4;
    border-bottom: 2px solid #b0aca4;
}

/* ── Tables ──────────────────────────────────────────────────── */
QTableWidget {
    background-color: #ffffff;
    color: #000000;
    gridline-color: #d4d0c8;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
    border: 2px solid #7f9db9;
    alternate-background-color: #f0eee8;
}
QHeaderView::section {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f4f2ec, stop:0.5 #ece9d8, stop:1 #d4d0c8);
    color: #000000;
    border-top: 1px solid #ffffff;
    border-left: 1px solid #ffffff;
    border-right: 1px solid #808080;
    border-bottom: 1px solid #808080;
    padding: 4px 6px;
    font-weight: bold;
    font-size: 10px;
}
QHeaderView::section:first { border-left: 1px solid #ffffff; }

/* ── CheckBox — XP square checkbox ──────────────────────────── */
QCheckBox, QRadioButton {
    color: #000000;
    spacing: 6px;
    background: transparent;
    font-family: "Tahoma", Arial;
    font-size: 11px;
}
QCheckBox::indicator {
    width: 13px; height: 13px;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #d4d0c8;
    border-bottom: 1px solid #d4d0c8;
    background-color: #ffffff;
}
QCheckBox::indicator:hover {
    border-top: 2px solid #316ac5;
    border-left: 2px solid #316ac5;
}
QCheckBox::indicator:checked {
    background-color: #ffffff;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    /* XP checkmark in blue */
    image: none;
}
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 #ffffff, stop:0.3 #dce8fc, stop:1 #b0c8f4);
    border-top: 2px solid #316ac5;
    border-left: 2px solid #316ac5;
}
QRadioButton::indicator {
    width: 13px; height: 13px;
    border-radius: 7px;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #d4d0c8;
    border-bottom: 1px solid #d4d0c8;
    background-color: #ffffff;
}
QRadioButton::indicator:checked {
    background: qlineargradient(x1:0.2,y1:0.2,x2:0.8,y2:0.8,
        stop:0 #316ac5, stop:1 #1040a0);
    border: 2px solid #7f9db9;
}
QRadioButton::indicator:hover {
    border-top: 2px solid #316ac5;
    border-left: 2px solid #316ac5;
}

/* ── Progress bar — XP green/blue chunked ───────────────────── */
QProgressBar {
    background-color: #ffffff;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #d4d0c8;
    border-bottom: 1px solid #d4d0c8;
    text-align: center;
    color: #000000;
    height: 18px;
    font-size: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #0da030, stop:0.4 #11BA38,
        stop:0.5 #18d042, stop:0.6 #11BA38, stop:1 #0da030);
    margin: 1px;
    border-radius: 1px;
    width: 10px;
}

/* ── Scroll bars — XP style ──────────────────────────────────── */
QScrollBar:vertical {
    background-color: #d4d0c8;
    width: 17px;
    border: 1px solid #aca899;
}
QScrollBar::handle:vertical {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #f4f2ec, stop:0.5 #ece9d8, stop:1 #d4d0c8);
    border-top: 1px solid #ffffff;
    border-left: 1px solid #ffffff;
    border-right: 1px solid #808080;
    border-bottom: 1px solid #808080;
    min-height: 20px;
    margin: 1px;
}
QScrollBar::handle:vertical:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #dce8fc, stop:1 #b0c8f4);
    border-right: 1px solid #316ac5;
    border-bottom: 1px solid #316ac5;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: #d4d0c8;
}
QScrollBar:horizontal {
    background-color: #d4d0c8;
    height: 17px;
    border: 1px solid #aca899;
}
QScrollBar::handle:horizontal {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #f4f2ec, stop:0.5 #ece9d8, stop:1 #d4d0c8);
    border-top: 1px solid #ffffff;
    border-left: 1px solid #ffffff;
    border-right: 1px solid #808080;
    border-bottom: 1px solid #808080;
    min-width: 20px;
    margin: 1px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── Text edit / log windows ─────────────────────────────────── */
QTextEdit, QPlainTextEdit {
    font-family: "Courier New", "Consolas", monospace;
    font-size: 10px;
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #d4d0c8;
    border-bottom: 1px solid #d4d0c8;
}

/* ── Frames ──────────────────────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #aca899; }

/* ── Scroll area ─────────────────────────────────────────────── */
QScrollArea { border: none; background: transparent; }

/* ── Tree widget ─────────────────────────────────────────────── */
QTreeWidget {
    background-color: #ffffff;
    border-top: 2px solid #7f9db9;
    border-left: 2px solid #7f9db9;
    border-right: 1px solid #d4d0c8;
    border-bottom: 1px solid #d4d0c8;
    selection-background-color: #316ac5;
    selection-color: #ffffff;
}
QTreeWidget::item:hover { background-color: #e8f0fc; }
""")

    def _apply_dark_aero_styling(self):
        """Dark Aero variant (slate/midnight blue)."""
        self.setStyleSheet("""
QMainWindow,QWidget{background:#1c2433;color:#d0dce8;font-family:'Segoe UI',Arial,sans-serif;font-size:11px;}
QMenuBar{background:#243040;color:#d0dce8;border-bottom:1px solid #1a2838;}
QMenuBar::item:selected{background:#2a5a8a;color:white;}
QMenu{background:#1e2d3e;border:1px solid #2a4060;border-radius:4px;padding:3px 0;}
QMenu::item:selected{background:#2a5a8a;color:white;}
QMenu::separator{height:1px;background:#2a4060;margin:3px 6px;}
QTabWidget::pane{background:#1c2433;border:1px solid #2a4060;}
QTabBar::tab{background:#243040;color:#a0b8d0;border:1px solid #2a4060;padding:5px 12px;border-bottom:none;border-top-left-radius:3px;border-top-right-radius:3px;font-weight:bold;}
QTabBar::tab:selected{background:#1c2433;color:#60b0f0;border-color:#3a7ec8;}
QGroupBox{background:#1e2d3e;border:1px solid #2a4060;border-radius:0px;margin-top:18px;padding-top:6px;color:#80b0d8;font-weight:bold;}
QGroupBox::title{color:#60a8e8;left:8px;top:-1px;padding:2px 5px;background:#1e2d3e;subcontrol-origin:margin;subcontrol-position:top left;}
QLabel{color:#b0c8e0;background:transparent;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTextEdit,QDateEdit{background:#141c28;border:1px solid #2a4060;border-radius:3px;padding:4px 6px;color:#c8dce8;min-height:22px;}
QSpinBox,QDoubleSpinBox{padding-right:18px;}
QSpinBox::up-button,QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:top right;width:18px;border-left:1px solid #2a4060;border-bottom:1px solid #2a4060;background:#1e2d3e;border-top-right-radius:3px;}
QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover{background:#2a4060;}
QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;width:18px;border-left:1px solid #2a4060;border-top:1px solid #2a4060;background:#1a2538;border-bottom-right-radius:3px;}
QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:#2a4060;}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{width:6px;height:5px;border-style:solid;border-width:0 3px 5px 3px;border-color:transparent transparent #80b0d8 transparent;}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{width:6px;height:5px;border-style:solid;border-width:5px 3px 0 3px;border-color:#80b0d8 transparent transparent transparent;}
QPushButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3a5880,stop:1 #1a3060);color:#d0e8ff;border:1px solid #2a4880;border-radius:4px;padding:5px 12px;font-weight:bold;}
QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4a70a0,stop:1 #2a4880);}
QPushButton:disabled{background:#283040;color:#506070;border-color:#304050;}
QTableWidget{background:#141c28;gridline-color:#2a3848;alternate-background-color:#1a2438;selection-background-color:#2a5a8a;border:1px solid #2a4060;}
QHeaderView::section{background:#1e3050;color:#80b0d8;padding:4px;border:none;border-right:1px solid #2a4060;}
QCheckBox,QRadioButton{color:#b0c8e0;spacing:6px;background:transparent;}
QCheckBox::indicator,QRadioButton::indicator{width:14px;height:14px;border:1px solid #3a5878;border-radius:2px;background:#141c28;}
QCheckBox::indicator:checked{background:#2a68b8;border-color:#3a80d0;}
QProgressBar{background:#141c28;border:1px solid #2a4060;border-radius:4px;text-align:center;color:#80b0d8;}
QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3a90e0,stop:1 #1a5aaa);}
QScrollBar:vertical{background:#1a2538;width:8px;border-radius:4px;}
QScrollBar::handle:vertical{background:#3a5878;border-radius:4px;min-height:20px;}
QTextEdit,QPlainTextEdit{font-family:'Consolas','Courier New',monospace;font-size:10px;background:#0e1620;color:#a0c0d8;}
QScrollArea{border:none;background:transparent;}
""")

    def connect_signals(self):
        self.browse_output_btn.clicked.connect(self.browse_output_directory)
        self.wind_method.currentTextChanged.connect(self.toggle_wind_coordinates)
        self.pick_wind_btn.clicked.connect(self.pick_wind_point)
        self.test_api_btn.clicked.connect(self.test_openmeteo_api)
        self.design_aircraft.currentTextChanged.connect(self.update_aircraft_parameters)
        self.num_candidates_spin.valueChanged.connect(lambda v: setattr(self.planner, 'num_candidates', v) if self.planner else None)
        self.wind_rose_cmap.currentTextChanged.connect(lambda cmap: setattr(self.planner, 'wind_rose_cmap', cmap) if self.planner else None)
        self.metric_radio.toggled.connect(lambda: setattr(self.planner, 'use_imperial', False) if self.planner else None)
        self.imperial_radio.toggled.connect(lambda: setattr(self.planner, 'use_imperial', True) if self.planner else None)
        self.validate_btn.clicked.connect(self.validate_inputs)
        self.run_selected_btn.clicked.connect(self.run_selected_step)
        self.run_all_btn.clicked.connect(self.run_full_analysis)
        self.clear_log_btn.clicked.connect(self.clear_log)
        self.save_log_btn.clicked.connect(self.save_log)
        self.results_table.itemSelectionChanged.connect(self.show_candidate_details)
        self.load_candidate_btn.clicked.connect(self.load_selected_candidate)
        self.view_report_btn.clicked.connect(self.view_compliance_report)
        self.export_results_btn.clicked.connect(self.export_results)
        self.open_output_folder_btn.clicked.connect(self.open_output_folder)
        self.wind_rose_btn.clicked.connect(self.show_wind_rose)
        self.usability_chart_btn.clicked.connect(self.show_usability_chart)
        self.obstacle_map_btn.clicked.connect(self.show_obstacle_map)
        self.wind_arrows_btn.clicked.connect(self.show_wind_arrows)
        self.load_obs_raster_btn.clicked.connect(self._load_obstacle_raster_ui)
        self.load_obs_points_btn.clicked.connect(self._load_obstacle_points_ui)
        self.calculate_costs_btn.clicked.connect(self.calculate_costs)
        self.generate_proposal_btn.clicked.connect(self.generate_proposal)
        self.view_financials_btn.clicked.connect(self.view_financial_analysis)
        self.fetch_climate_btn.clicked.connect(self.fetch_climate_from_openmeteo)
        self.generate_safety_surfaces_btn.clicked.connect(self.generate_safety_surfaces)
        self.load_safety_surfaces_qgis_btn.clicked.connect(self.load_safety_surfaces_into_qgis)
        self.generate_terminal_btn.clicked.connect(self.generate_terminal_utilities)
        self.load_terminal_qgis_btn.clicked.connect(self.load_terminal_into_qgis)
        self.gen_lights_gpkg_btn.clicked.connect(self._generate_lights_gpkg_ui)
        self.load_lights_qgis_btn.clicked.connect(self._load_lights_into_qgis)
        self.gen_obs_map_btn.clicked.connect(self._generate_obs_map_ui)
        self.view_drawings_btn.clicked.connect(self.view_engineering_drawings)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_timestamp)
        self.timer.start(60000)

    def update_timestamp(self):
        pass  # No timestamp display

    def _update_sep_label(self, config_text):
        """Update the separation/angle spinbox label based on selected config."""
        if not hasattr(self, '_sep_label'):
            return
        if config_text == 'Parallel':
            self._sep_label.setText("Runway Separation (m):")
            self.runway_separation.setSuffix(" m")
            self.runway_separation.setRange(200, 2000)
            self.runway_separation.setValue(400)
        elif config_text == 'Crosswind':
            self._sep_label.setText("Crosswind Separation (m):")
            self.runway_separation.setSuffix(" m")
            self.runway_separation.setRange(100, 1000)
            self.runway_separation.setValue(300)
        elif config_text == 'Open-V':
            self._sep_label.setText("Open-V Total Divergence (°):")
            self.runway_separation.setSuffix("°")
            self.runway_separation.setRange(20, 40)
            self.runway_separation.setValue(30)
        elif config_text == 'Intersecting':
            self._sep_label.setText("Intersection Angle (30°–150°):")
            self.runway_separation.setSuffix("°")
            self.runway_separation.setRange(30, 150)
            self.runway_separation.setValue(60)
        else:
            self._sep_label.setText("Runway Separation / Intersection Angle:")
            self.runway_separation.setSuffix(" m / °")
            self.runway_separation.setRange(30, 1000)
            self.runway_separation.setValue(300)

    def update_aircraft_parameters(self, aircraft):
        data = ICAOStandards.AIRCRAFT_PERFORMANCE.get(aircraft)
        if data:
            self.wingspan_label.setText(f"{data['Wingspan']:.1f} m")
            self.wheel_span_label.setText(f"{data['Wheel span']:.1f} m")
            self.max_takeoff_weight_label.setText(f"{data['Max takeoff weight']:,} kg")
            self.reference_field_length_label.setText(f"{data['Reference field length']:,} m")
            code_num = ICAOStandards.get_runway_code(data['Reference field length'])
            code_let = data['Category']
            self.code_number.setText(code_num)
            self.code_letter.setText(code_let)
            self.arc_display.setText(f"{code_num}{code_let}")
            weight = data['Max takeoff weight']
            if weight < 5700:
                cat = 'Light Aircraft (< 5,700 kg)'
            elif weight < 27000:
                cat = 'Medium Aircraft (5,700 - 27,000 kg)'
            else:
                if 'fighter' in aircraft.lower() or 'f-' in aircraft:
                    cat = 'Military Fighter'
                elif 'transport' in aircraft.lower() or 'c-' in aircraft:
                    cat = 'Military Transport'
                else:
                    cat = 'Heavy Aircraft (> 27,000 kg)'
            self.aircraft_category_label.setText(cat)
            thr = ICAOStandards.CROSSWIND_COMPONENTS.get(cat, ICAOStandards.CROSSWIND_COMPONENTS['Medium Aircraft (5,700 - 27,000 kg)'])
            self.crosswind_dry_label.setText(str(thr['Dry']))
            self.crosswind_wet_label.setText(str(thr['Wet']))
            self.crosswind_icy_label.setText(str(thr['Icy']))
            info = f"""AIRCRAFT: {aircraft}
────────────────────────────
• Wingspan:       {data['Wingspan']:.1f} m
• Wheel Span:     {data['Wheel span']:.1f} m
• MTOW:           {data['Max takeoff weight']:,} kg
• Ref Field Length: {data['Reference field length']:,} m
• ICAO Category:  {data['Category']}
• ARC:            {code_num}{code_let}
• Crosswind Limits (knots):
  - Dry: {thr['Dry']}
  - Wet: {thr['Wet']}
  - Icy: {thr['Icy']}"""
            self.aircraft_info.setPlainText(info)

    def test_openmeteo_api(self):
        try:
            self.log_message("Testing Open-Meteo API connection...", "INFO")
            lat, lon = self.get_wind_coordinates()
            test_url = "https://archive-api.open-meteo.com/v1/archive"
            params = {"latitude":lat, "longitude":lon, "start_date":"2023-01-01", "end_date":"2023-01-02", "hourly":"temperature_2m", "timezone":"auto"}
            try:
                r = requests.get(test_url, params=params, timeout=10, verify=True)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(test_url, params=params, timeout=10, verify=False)
            if r.status_code == 200:
                self.log_message("[OK] Open-Meteo API connection successful!", "SUCCESS")
                self.api_status_label.setText("[OK] API Connected")
                QMessageBox.information(self, "API Test", "Connection successful!")
            else:
                self.log_message(f"[!!] API connection failed: {r.status_code}", "ERROR")
                self.api_status_label.setText("[!!] API Failed")
                QMessageBox.warning(self, "API Test", f"Failed with status {r.status_code}")
        except Exception as e:
            self.log_message(f"[!!] API test error: {str(e)}", "ERROR")
            self.api_status_label.setText("[!!] API Error")
            QMessageBox.critical(self, "API Test Error", str(e))

    def open_magnetic_declination_website(self):
        url = QUrl("https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml#declination")
        QDesktopServices.openUrl(url)
        self.log_message("Opened NOAA magnetic declination website.", "INFO")

    def fetch_magnetic_declination_auto(self):
        """
        Automatically fetch magnetic declination from the NOAA WMM REST API
        using the wind observation point lat/lon (EPSG:4326).

        API endpoint:
          https://www.ngdc.noaa.gov/geomag-web/calculators/calculateDeclination
        Parameters:
          lat1  – decimal degrees, positive = North, negative = South
          lon1  – decimal degrees, positive = East,  negative = West
          key   – public demo key
          resultFormat – json
        """
        try:
            lat, lon = self.get_wind_coordinates()
            if lat is None or lon is None:
                QMessageBox.warning(self, "No Coordinates",
                                    "Please set the Wind Observation Point coordinates first.")
                return

            self.declination_auto_btn.setEnabled(False)
            self.declination_status_label.setText("Fetching from NOAA WMM API…")
            self.declination_status_label.setStyleSheet("color:#316ac5;font-style:italic;font-size:10px;padding:2px;")
            QApplication.processEvents()

            today = datetime.now()
            api_url = "https://www.ngdc.noaa.gov/geomag-web/calculators/calculateDeclination"
            params = {
                "lat1":         round(lat, 6),
                "lon1":         round(lon, 6),
                "key":          "zNEw7",
                "resultFormat": "json",
                "startYear":    today.year,
                "startMonth":   today.month,
                "startDay":     today.day,
            }

            try:
                resp = requests.get(api_url, params=params, timeout=15, verify=True)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                resp = requests.get(api_url, params=params, timeout=15, verify=False)

            if resp.status_code != 200:
                raise ValueError(f"NOAA API returned HTTP {resp.status_code}")

            data = resp.json()
            # Response structure: {"result": [{"declination": <float>, ...}]}
            result_list = data.get("result", [])
            if not result_list:
                raise ValueError("NOAA API returned empty result list.")

            declination = float(result_list[0]["declination"])
            annual_change = float(result_list[0].get("declination_sv", 0.0))

            # Determine direction label matching NOAA convention:
            # positive = East (magnetic east of true north)
            # negative = West
            lat_dir = "N" if lat >= 0 else "S"
            lon_dir = "E" if lon >= 0 else "W"
            decl_dir = "E" if declination >= 0 else "W"

            # Set the UI spin boxes
            self.magnetic_variation.setValue(round(declination, 4))
            self.magnetic_annual_change.setValue(round(annual_change, 4))

            status_msg = (
                f"Declination: {abs(declination):.4f}° {decl_dir}  "
                f"({lat:.4f}°{lat_dir}, {lon:.4f}°{lon_dir})  "
                f"Annual change: {annual_change:+.4f}°/yr"
            )
            self.declination_status_label.setText(status_msg)
            self.declination_status_label.setStyleSheet(
                "color:#27ae60;font-weight:bold;font-size:10px;padding:2px;")

            self.log_message(
                f"NOAA Magnetic Declination: {declination:+.4f}° "
                f"at ({lat:.4f}°{lat_dir}, {lon:.4f}°{lon_dir}) "
                f"on {today.strftime('%Y-%m-%d')}. "
                f"Annual secular change: {annual_change:+.4f}°/yr",
                "SUCCESS"
            )

        except Exception as e:
            self.declination_status_label.setText(f"❌ Fetch failed: {e}")
            self.declination_status_label.setStyleSheet(
                "color:#c0392b;font-weight:bold;font-size:10px;padding:2px;")
            self.log_message(f"Auto declination fetch failed: {e}", "ERROR")
            QMessageBox.critical(
                self, "Declination Fetch Failed",
                f"Could not fetch magnetic declination from NOAA API.\n\n"
                f"Error: {e}\n\n"
                f"Please use the manual NOAA website button instead."
            )
        finally:
            self.declination_auto_btn.setEnabled(True)

    def fetch_climate_from_openmeteo(self):
        try:
            lat, lon = self.get_wind_coordinates()
            if lat is None or lon is None:
                QMessageBox.warning(self, "Error", "Please set wind coordinates first.")
                return
            start = self.start_date_edit.date().toString("yyyy-MM-dd")
            end = self.end_date_edit.date().toString("yyyy-MM-dd")
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": start,
                "end_date": end,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "hourly": "relative_humidity_2m,snowfall,snow_depth",
                "timezone": "auto"
            }
            try:
                r = requests.get(url, params=params, timeout=30, verify=True)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, params=params, timeout=30, verify=False)
            if r.status_code != 200:
                self.log_message(f"Climate data fetch failed: {r.status_code}", "ERROR")
                QMessageBox.warning(self, "Error", f"Failed to fetch climate data: {r.status_code}")
                return
            data = r.json()
            daily = data.get("daily", {})
            temp_max = daily.get("temperature_2m_max", [])
            temp_min = daily.get("temperature_2m_min", [])
            precip = daily.get("precipitation_sum", [])
            if temp_max:
                mean_temp = (np.mean(temp_max) + np.mean(temp_min)) / 2.0
                self.mean_temperature.setValue(round(mean_temp, 1))
            if precip:
                total_precip = np.sum(precip)
                start_dt = datetime.strptime(start, "%Y-%m-%d")
                end_dt = datetime.strptime(end, "%Y-%m-%d")
                num_years = (end_dt - start_dt).days / 365.25
                annual_precip = total_precip / num_years if num_years > 0 else total_precip
                self.precipitation.setValue(round(annual_precip, 0))
            hourly = data.get("hourly", {})
            humidity = hourly.get("relative_humidity_2m", [])
            if humidity:
                mean_humidity = np.mean(humidity)
                self.humidity.setValue(round(mean_humidity, 0))
            self.log_message("Climate data fetched and updated successfully.", "SUCCESS")
            QMessageBox.information(self, "Success", "Climate parameters updated from Open-Meteo.")
        except Exception as e:
            self.log_message(f"Error fetching climate data: {str(e)}", "ERROR")
            QMessageBox.critical(self, "Error", f"Failed to fetch climate data: {str(e)}")

    def get_wind_coordinates(self):
        if self.wind_method.currentText() == "Use AOI Centroid":
            layer = self.aoi_combo.currentLayer()
            if layer:
                f = next(layer.getFeatures())
                pt = f.geometry().centroid().asPoint()
                crs = layer.crs()
                if crs.authid() != 'EPSG:4326':
                    xform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
                    pt = xform.transform(pt)
                return pt.y(), pt.x()
        return self.wind_lat.value(), self.wind_lon.value()

    def browse_output_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Directory", QgsProject.instance().homePath())
        if folder:
            self.output_dir.setText(folder)

    def toggle_wind_coordinates(self, method):
        self.wind_coord_frame.setVisible(method != "Use AOI Centroid")

    def pick_wind_point(self):
        self.pick_tool = QgsMapToolEmitPoint(self.canvas)
        self.pick_tool.canvasClicked.connect(self.on_wind_point_picked)
        self.canvas.setMapTool(self.pick_tool)
        self.message_bar.pushInfo("Aerodrome Spatial Planner", "Click on map to select wind observation point")

    def on_wind_point_picked(self, point, button):
        crs = self.canvas.mapSettings().destinationCrs()
        if crs.authid() != 'EPSG:4326':
            xform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
            point = xform.transform(point)
        self.wind_lat.setValue(point.y())
        self.wind_lon.setValue(point.x())
        self.canvas.unsetMapTool(self.pick_tool)
        self.message_bar.pushSuccess("Aerodrome Spatial Planner", f"Wind point set to: {point.y():.6f}, {point.x():.6f}")

    def validate_inputs(self):
        errors = []
        if not self.project_name.text().strip():
            errors.append("Project name is required")
        if not self.output_dir.text().strip():
            errors.append("Output directory is required")
        else:
            try:
                test = os.path.join(self.output_dir.text(), "test.tmp")
                with open(test, 'w', encoding='utf-8') as f: f.write("test")
                os.remove(test)
            except:
                errors.append("Output directory is not writable")
        if not self.aoi_combo.currentLayer():
            errors.append("AOI layer is required")
        if errors:
            self.log_message("VALIDATION FAILED:\n• " + "\n• ".join(errors), "ERROR")
            if hasattr(self, '_aero_status'):
                self._aero_status.setText("✘ Validation Failed")
            QMessageBox.critical(self, "Validation Failed", "\n".join(errors))
            return False
        else:
            self.log_message("All parameters validated successfully", "SUCCESS")
            if hasattr(self, '_aero_status'):
                self._aero_status.setText("✔ Validated")
            return True

    def log_message(self, msg, level="INFO"):
        markers = {"INFO": "[i]", "SUCCESS": "[+]", "WARNING": "[!]", "ERROR": "[X]"}
        colors  = {"INFO": "#2b6cb0", "SUCCESS": "#276749", "WARNING": "#9a3412", "ERROR": "#991b1b"}
        bg_cols = {"INFO": "#eff6ff", "SUCCESS": "#f0fdf4", "WARNING": "#fff7ed", "ERROR": "#fff1f2"}
        ts   = datetime.now().strftime("%H:%M:%S")
        mark = markers.get(level, "[i]")
        col  = colors.get(level, "#2c3e50")
        bg   = bg_cols.get(level, "#ffffff")
        html = (f'<span style="background:{bg};color:{col};font-family:monospace;font-size:10px;"'
                f'><b>{ts} {mark}</b> {msg}</span>')
        self.log_display.append(html)
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum())

    def clear_log(self):
        self.log_display.clear()

    def save_log(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save Log", os.path.join(self.output_dir.text(), "log.html"), "HTML Files (*.html)")
        if fn:
            with open(fn, 'w', encoding='utf-8') as f:
                f.write("<html><body>")
                f.write(self.log_display.toHtml())
                f.write("</body></html>")

    def run_selected_step(self):
        QMessageBox.information(self, "Step", "Run selected step would execute here.")

    def run_full_analysis(self):
        if not self.validate_inputs():
            return
        self.planner = ICAOCompliantAirportRunwayPlanner()
        self.planner.num_candidates = self.num_candidates_spin.value()
        self.planner.wind_rose_cmap = self.wind_rose_cmap.currentText()
        self.planner.use_imperial = self.imperial_radio.isChecked()
        self.planner.magnetic_variation = self.magnetic_variation.value()
        self.planner.stopway_length = self.stopway_length.value()
        self.planner.clearway_length = self.clearway_length.value()
        self.planner.displaced_threshold = self.displaced_threshold.value()
        self.planner.mean_temperature = self.mean_temperature.value()
        self.planner.mean_annual_precipitation = self.precipitation.value()
        self.planner.mean_relative_humidity = self.humidity.value()
        self.planner.obstacle_threshold = self.obstacle_threshold.value()
        params = self.get_analysis_parameters()
        self.analysis_thread = ProfessionalAnalysisThread(self.planner, params)
        self.analysis_thread.progress_signal.connect(self.update_progress)
        self.analysis_thread.step_started_signal.connect(lambda n,d: self.log_message(f"Starting: {d}", "INFO"))
        self.analysis_thread.step_completed_signal.connect(lambda n,s,m: self.log_message(f"Completed: {m}", "SUCCESS" if s else "ERROR"))
        self.analysis_thread.finished_signal.connect(self.analysis_finished)
        self.analysis_thread.log_signal.connect(self.log_message)
        self.analysis_thread.validation_signal.connect(lambda n,p,m: self.log_message(f"{'✓' if p else '✗'} {n}: {m}", "SUCCESS" if p else "ERROR"))
        self.analysis_thread.output_generated_signal.connect(self.on_output_generated)
        self.set_analysis_controls_enabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_text.setText("Analysis in progress...")
        self.tab_widget.setCurrentIndex(4)
        self.analysis_thread.start()
        self.log_message("Professional analysis started...", "INFO")

    def get_analysis_parameters(self):
        lat, lon = self.get_wind_coordinates()
        return {
            'project_name': self.project_name.text(),
            'client_name': self.client_name.text(),
            'project_id': self.project_id.text(),
            'output_dir': self.output_dir.text(),
            'aoi_layer': self.aoi_combo.currentLayer(),
            'dsm_layer': self.dsm_combo.currentLayer(),
            'dtm_layer': self.dtm_combo.currentLayer(),
            'output_crs': self.crs_selector.crs().authid(),
            'design_aircraft': self.design_aircraft.currentText(),
            'approach_type': self.approach_type.currentText(),
            'runway_lighting': self.runway_lighting.currentText(),
            'approach_lighting': self.approach_lighting.currentText(),
            'visual_glide_slope': self.visual_glide_slope.currentText(),
            'runway_config': self.runway_config.currentText(),
            'num_runways': self.num_runways.value(),
            'runway_separation': self.runway_separation.value(),
            'resa_length': self.resa_length.value(),
            'resa_width': self.resa_width.value(),
            'strip_width': self.strip_width.value(),
            'stopway_length': self.stopway_length.value(),
            'clearway_length': self.clearway_length.value(),
            'displaced_threshold': self.displaced_threshold.value(),
            'mean_temperature': self.mean_temperature.value(),
            'precipitation': self.precipitation.value(),
            'humidity': self.humidity.value(),
            'wind_step': self.wind_step.currentText(),
            'magnetic_variation': self.magnetic_variation.value(),
            'magnetic_annual_change': self.magnetic_annual_change.value(),
            'obstacle_threshold': self.obstacle_threshold.value(),
            'obstacle_free_zone': self.obstacle_free_zone.isChecked(),
            'generate_surfaces': self.generate_surfaces.isChecked(),
            'num_candidates': self.num_candidates_spin.value(),
            'use_imperial': self.imperial_radio.isChecked(),
            'wind_point_lat': lat,
            'wind_point_lon': lon,
            'start_date': self.start_date_edit.date().toString("yyyy-MM-dd"),
            'end_date': self.end_date_edit.date().toString("yyyy-MM-dd")
        }

    def update_progress(self, val, msg):
        self.progress_bar.setValue(val)
        self.status_text.setText(msg)

    def analysis_finished(self, success, msg, results):
        self.set_analysis_controls_enabled(True)
        self.progress_bar.setVisible(False)
        if success:
            self.results = results
            # ── Critical: keep self.planner in sync with the fully-populated
            #    planner from the analysis thread so that safety surfaces,
            #    lighting and terminal generation work without re-init. ──────
            self.planner = results['planner']
            self.log_message("Analysis completed successfully!", "SUCCESS")
            self.status_text.setText("Analysis completed!")
            if hasattr(self, '_aero_status'):
                self._aero_status.setText("✔ Analysis Complete")
            self.enable_results_controls(True)
            self.update_results_table()
            # Generate engineering drawings for all candidates
            self._generate_all_engineering_drawings()
            self.tab_widget.setCurrentIndex(5)
            QMessageBox.information(self, "Analysis Complete", "Analysis completed successfully.\nCheck Results tab.")
        else:
            self.log_message(f"Analysis failed: {msg}", "ERROR")
            self.status_text.setText("Analysis failed")
            QMessageBox.critical(self, "Analysis Failed", msg)

    def on_output_generated(self, out_dir):
        self.log_message(f"Output files generated in: {out_dir}", "SUCCESS")
        self.open_output_folder_btn.setEnabled(True)

    def set_analysis_controls_enabled(self, en):
        self.validate_btn.setEnabled(en)
        self.run_selected_btn.setEnabled(en)
        self.run_all_btn.setEnabled(en)

    def enable_results_controls(self, en):
        self.load_candidate_btn.setEnabled(en)
        self.view_report_btn.setEnabled(en)
        self.export_results_btn.setEnabled(en)
        self.wind_rose_btn.setEnabled(en)
        self.usability_chart_btn.setEnabled(en)
        self.obstacle_map_btn.setEnabled(en)
        self.wind_arrows_btn.setEnabled(en)
        if hasattr(self, 'load_obs_raster_btn'):
            self.load_obs_raster_btn.setEnabled(en)
            self.load_obs_points_btn.setEnabled(en)

    def update_results_table(self):
        if not self.results or 'candidates' not in self.results:
            return
        candidates = self.results['candidates']
        self.results_table.setRowCount(len(candidates))
        for i, cand in enumerate(candidates):
            # Cols 0-4: identification
            self.results_table.setItem(i, 0,  QTableWidgetItem(str(cand['rank'])))
            self.results_table.setItem(i, 1,  QTableWidgetItem(f"{int(cand['orientation']):03d}°"))
            self.results_table.setItem(i, 2,  QTableWidgetItem(f"{int(cand['magnetic_orientation']):03d}°"))
            self.results_table.setItem(i, 3,  QTableWidgetItem(f"{int(cand['reciprocal_magnetic']):03d}°"))
            self.results_table.setItem(i, 4,  QTableWidgetItem(cand['designation']))
            # Cols 5-7: usability factors
            self.results_table.setItem(i, 5,  QTableWidgetItem(f"{cand['dry_coverage']:.1f}%"))
            self.results_table.setItem(i, 6,  QTableWidgetItem(f"{cand['wet_coverage']:.1f}%"))
            self.results_table.setItem(i, 7,  QTableWidgetItem(f"{cand['icy_coverage']:.1f}%"))
            # Cols 8-9: new ICAO fields
            self.results_table.setItem(i, 8,  QTableWidgetItem(f"{cand.get('calm_pct', 0.0):.1f}%"))
            hw = cand.get('mean_headwind_kn', 0.0)
            hw_item = QTableWidgetItem(f"{hw:+.1f} kt")
            hw_item.setForeground(QColor("green" if hw >= 0 else "red"))
            self.results_table.setItem(i, 9,  hw_item)
            # Col 10: ICAO compliant
            comp = QTableWidgetItem("✓ Yes" if cand['icao_compliant'] else "✗ No")
            comp.setForeground(QColor("green" if cand['icao_compliant'] else "red"))
            self.results_table.setItem(i, 10, comp)
            # Col 11: crosswind runway needed
            xw_needed = cand.get('needs_crosswind_rwy', False)
            xw_item = QTableWidgetItem("Yes" if xw_needed else "No")
            xw_item.setForeground(QColor("orange" if xw_needed else "green"))
            self.results_table.setItem(i, 11, xw_item)
            # Cols 12-16: declared distances
            self.results_table.setItem(i, 12, QTableWidgetItem(f"{cand['length']:.0f}"))
            self.results_table.setItem(i, 13, QTableWidgetItem(f"{cand['tora']:.0f}"))
            self.results_table.setItem(i, 14, QTableWidgetItem(f"{cand['toda']:.0f}"))
            self.results_table.setItem(i, 15, QTableWidgetItem(f"{cand['asda']:.0f}"))
            self.results_table.setItem(i, 16, QTableWidgetItem(f"{cand['lda']:.0f}"))

    def show_candidate_details(self):
        sel = self.results_table.selectedItems()
        if not sel:
            return
        row = sel[0].row()
        if row >= len(self.results['candidates']):
            return
        cand = self.results['candidates'][row]
        xw_flag = "⚠ Yes — crosswind runway required (Annex 14 Sec.3.1.2)" \
                  if cand.get('needs_crosswind_rwy', False) else "No"
        hw = cand.get('mean_headwind_kn', 0.0)
        hw_str = f"{hw:+.1f} kt ({'headwind ✓' if hw >= 0 else 'tailwind ⚠'})"
        details = f"""RUNWAY CANDIDATE DETAILS
{'─' * 44}
Rank          : {cand['rank']}
Orientation   : {int(cand['orientation']):03d}° (True) / {int(cand['magnetic_orientation']):03d}° (Magnetic)
Reciprocal    : {int(cand['reciprocal_magnetic']):03d}° (Magnetic)
Designation   : {cand['designation']}

WIND USABILITY  (ICAO Annex 14 Sec.3.1.1):
  Dry         : {cand['dry_coverage']:.2f}%   [limit: {cand.get('icao_xwind_thresh_kn', 20):.1f} kt crosswind]
  Wet         : {cand['wet_coverage']:.2f}%
  Icy         : {cand['icy_coverage']:.2f}%
  Calm (< 3 kt): {cand.get('calm_pct', 0.0):.1f}%
  Mean Headwind : {hw_str}
  ICAO Compliant: {'✓ Yes' if cand['icao_compliant'] else '✗ No'}
  Needs X-Wind RWY: {xw_flag}

SEASONAL USABILITY:
  Winter      : {cand.get('winter_coverage', 0.0):.1f}%
  Spring      : {cand.get('spring_coverage', 0.0):.1f}%
  Summer      : {cand.get('summer_coverage', 0.0):.1f}%
  Fall        : {cand.get('fall_coverage', 0.0):.1f}%

DECLARED DISTANCES  (ICAO Annex 14 Sec.3.6):
  TORA        : {cand['tora']:.0f} m
  TODA        : {cand['toda']:.0f} m
  ASDA        : {cand['asda']:.0f} m
  LDA         : {cand['lda']:.0f} m
  Runway Width: {self.results['planner'].runway_width} m

RECOMMENDED RUNWAY LENGTH : {cand['length']:.0f} m

DATA SOURCE   : Open-Meteo API (Historical Archive)
PERIOD        : {self.results['planner'].start_date} to {self.results['planner'].end_date}"""
        self.details_display.setPlainText(details)

    # ------------------------------------------------------------------
    # QGIS Layer Group Loader
    # ------------------------------------------------------------------
    def _add_layer_to_group(self, gpkg_path, layer_name, display_name,
                             group, fill, border, opacity, gtype):
        """Load one layer from *gpkg_path* into *group* with styled symbology.
        Returns the loaded QgsVectorLayer or None."""
        uri = f"{gpkg_path}|layername={layer_name}"
        lyr = QgsVectorLayer(uri, display_name, "ogr")
        if not lyr.isValid():
            return None
        if gtype == 'polygon':
            sym = QgsFillSymbol.createSimple({
                'color': fill, 'color_border': border or fill,
                'width_border': '0.6', 'style': 'solid'})
            sym.setOpacity(opacity)
        elif gtype == 'line':
            sym = QgsLineSymbol.createSimple(
                {'color': fill, 'width': '1.2', 'penstyle': 'dash'})
        else:
            sym = QgsMarkerSymbol.createSimple(
                {'color': fill, 'size': '3.5', 'name': 'circle'})
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        # Add layer to project WITHOUT showing in root, then move to group
        QgsProject.instance().addMapLayer(lyr, False)
        group.addLayer(lyr)
        return lyr

    def _add_vector_label_layer(self, gpkg_path, layer_name, display_name,
                                 group, fill, size='3.5', label_field=None):
        """Add a point/label layer into group."""
        uri = f"{gpkg_path}|layername={layer_name}"
        lyr = QgsVectorLayer(uri, display_name, "ogr")
        if not lyr.isValid():
            return None
        sym = QgsMarkerSymbol.createSimple(
            {'color': fill, 'size': size, 'name': 'circle'})
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        if label_field:
            pal = QgsPalLayerSettings()
            pal.fieldName = label_field
            pal.enabled   = True
            tf = QgsTextFormat(); tf.setSize(8)
            tf.setColor(QColor('#1a1a1a'))
            from qgis.PyQt.QtGui import QFont as _QF
            tf.setFont(_QF("Arial", 8))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True); buf.setSize(1.0)
            buf.setColor(QColor('#ffffff'))
            tf.setBuffer(buf)
            pal.setFormat(tf)
            lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
            lyr.setLabelsEnabled(True)
        QgsProject.instance().addMapLayer(lyr, False)
        group.addLayer(lyr)
        return lyr

    def load_selected_candidate(self):
        """
        Load all layers for the selected runway candidate into organised
        QGIS layer groups:

        ▼ Candidate 01 — 09/27
            ▼ Runway GeoPackage
                - Runway Pavement / Pavement 2 (parallel/xwind/open-v)
                - Runway Centreline …
                - Runway Strip …
                - RESA …
                - Stopway / Clearway (if present)
                - Obstacle Surfaces
                - Runway Designations
                - Obstacles
            ▼ Safety Surfaces
                - Glide Slope / Approach / TOCS / Transitional / IHS / Conical / OHS
            ▼ Terminal & Utilities
                - Terminal Building, ATC Tower, Apron, Hangars, …
            ▼ Lighting
                - ICAO Aerodrome Lights
        """
        sel = self.results_table.selectedItems()
        if not sel:
            QMessageBox.warning(self, "Selection",
                                "Please select a candidate from the table.")
            return
        row   = sel[0].row()
        cand  = self.results['candidates'][row]
        desig = cand.get('designation', 'UNKNOWN')
        rank  = cand.get('rank', row + 1)
        planner = self.results['planner']

        # ── Locate runway GeoPackage ──────────────────────────────────────────
        gpkg_base = os.path.join(
            planner.output_dir, 'GeoPackages_All',
            f"Candidate_{rank:02d}_{desig}")
        gpkg_file = None
        if os.path.isdir(gpkg_base):
            for f in os.listdir(gpkg_base):
                if f.endswith('.gpkg'):
                    gpkg_file = os.path.join(gpkg_base, f)
                    break
        if not gpkg_file:
            gpkg_top = os.path.join(planner.output_dir, 'GeoPackages')
            if os.path.isdir(gpkg_top):
                for f in os.listdir(gpkg_top):
                    if f.endswith('.gpkg') and desig.replace('/','_') in f:
                        gpkg_file = os.path.join(gpkg_top, f)
                        break
        if not gpkg_file or not os.path.exists(gpkg_file):
            QMessageBox.warning(self, "Not Found",
                                f"GeoPackage for candidate {desig} not found.\n"
                                f"Run full analysis first.")
            return

        # ── Locate optional GeoPackages (safety / terminal / lighting) ────────
        cand_dir = gpkg_base if os.path.isdir(gpkg_base) else                    os.path.join(planner.output_dir, 'GeoPackages')

        safety_gpkg   = os.path.join(planner.output_dir,
                                     'Safety_Surfaces', 'safety_surfaces.gpkg')
        terminal_gpkg = os.path.join(planner.output_dir,
                                     'Terminal_Utilities', 'terminal_utilities.gpkg')
        lights_gpkg   = os.path.join(planner.output_dir,
                                     'Lighting', 'icao_aerodrome_lights.gpkg')
        # Also check inside candidate dir
        for sub, attr in [('Safety_Surfaces/safety_surfaces.gpkg',   'safety'),
                           ('Terminal_Utilities/terminal_utilities.gpkg', 'term'),
                           ('Lighting/icao_aerodrome_lights.gpkg',    'lights')]:
            cp = os.path.join(cand_dir, sub)
            if os.path.exists(cp):
                if 'Safety'    in sub: safety_gpkg   = cp
                if 'Terminal'  in sub: terminal_gpkg = cp
                if 'Lighting'  in sub: lights_gpkg   = cp

        # ── Create top-level candidate group ──────────────────────────────────
        root = QgsProject.instance().layerTreeRoot()
        top_group = root.insertGroup(0, f"Candidate {rank:02d} — {desig}")
        loaded_total = 0

        # ══════════════════════════════════════════════════════════════════════
        # ▼ Runway GeoPackage sub-group
        # ══════════════════════════════════════════════════════════════════════
        rwy_group = top_group.addGroup("Runway GeoPackage")

        # Determine which runways exist (single = 1, multi = 1+2)
        config = getattr(planner, 'runway_configuration', 'Single')
        rwy_suffixes = ['']  # single runway: no suffix
        if config in ('Parallel', 'Crosswind', 'Open-V', 'Intersecting'):
            rwy_suffixes = ['', '_2']

        for sfx in rwy_suffixes:
            lbl = '' if sfx == '' else ' (Runway 2)'
            RUNWAY_LAYERS = [
                (f'runway_pavement{sfx}',     f'Runway Pavement{lbl}',      '#808080','#000000',0.85,'polygon'),
                (f'runway_centerline{sfx}',   f'Runway Centreline{lbl}',    '#2980b9', None,    1.0,'line'),
                (f'runway_strip{sfx}',        f'Runway Strip{lbl}',         '#f39c12', None,    0.35,'polygon'),
                (f'resa{sfx}',                f'RESA{lbl}',                 '#e74c3c','#c0392b',0.40,'polygon'),
                (f'stopway{sfx}',             f'Stopway{lbl}',              '#8e44ad','#6c3483',0.50,'polygon'),
                (f'clearway{sfx}',            f'Clearway{lbl}',             '#27ae60','#1e8449',0.40,'polygon'),
                (f'obstacle_surfaces{sfx}',   f'Obstacle Surfaces{lbl}',   '#e67e22','#d35400',0.30,'polygon'),
                (f'runway_designations{sfx}', f'Runway Designations{lbl}', '#2c3e50', None,    1.0,'point'),
            ]
            for lname, dname, fill, border, opac, gtype in RUNWAY_LAYERS:
                lyr = self._add_layer_to_group(
                    gpkg_file, lname, dname, rwy_group, fill, border, opac, gtype)
                if lyr:
                    loaded_total += 1

        # Config-specific extra layers
        if config == 'Parallel':
            self._add_layer_to_group(
                gpkg_file, 'parallel_separation', 'Parallel Separation',
                rwy_group, '#e74c3c', None, 1.0, 'line')
        elif config == 'Open-V':
            self._add_vector_label_layer(
                gpkg_file, 'openv_apex', 'Open-V Apex (Common Threshold)',
                rwy_group, '#e74c3c', size='5')
        elif config == 'Intersecting':
            self._add_vector_label_layer(
                gpkg_file, 'intersection_point',
                'Runway Intersection Point',
                rwy_group, '#e74c3c', size='8',
                label_field='type')

        # Shared obstacles
        lyr = self._add_layer_to_group(
            gpkg_file, 'obstacles', 'Obstacles',
            rwy_group, '#e74c3c', None, 1.0, 'point')
        if lyr: loaded_total += 1

        # ══════════════════════════════════════════════════════════════════════
        # ▼ Safety Surfaces sub-group
        # ══════════════════════════════════════════════════════════════════════
        if os.path.exists(safety_gpkg):
            ss_group = top_group.addGroup("Safety Surfaces")
            SAFETY_LAYERS = [
                ('glide_slope',             'Glide Slope Surface',          '#FF6B35', 0.40),
                ('takeoff_climb_surface',   'Take-off Climb Surface',       '#FFA500', 0.40),
                ('approach_surface',        'Approach Surface',             '#4CAF50', 0.40),
                ('transitional_surface',    'Transitional Surface',         '#9C27B0', 0.35),
                ('inner_horizontal_surface','Inner Horizontal Surface (45m)','#2196F3', 0.30),
                ('conical_surface',         'Conical Surface',              '#FF9800', 0.30),
                ('outer_horizontal_surface','Outer Horizontal Surface (150m)','#607D8B', 0.20),
            ]
            for lname, dname, color, opac in SAFETY_LAYERS:
                lyr = self._add_layer_to_group(
                    safety_gpkg, lname, dname, ss_group,
                    color, color, opac, 'polygon')
                if lyr: loaded_total += 1

        # ══════════════════════════════════════════════════════════════════════
        # ▼ Terminal & Utilities sub-group
        # ══════════════════════════════════════════════════════════════════════
        if os.path.exists(terminal_gpkg):
            tu_group = top_group.addGroup("Terminal & Utilities")
            TU_POLYGON_LAYERS = [
                ('terminal_building','Terminal Building',     '#3498DB','#2980B9',0.70),
                ('apron',            'Aircraft Apron',         '#95A5A6','#7F8C8D',0.60),
                ('hangars',          'Hangars',                '#E67E22','#D35400',0.70),
                ('fuel_depot',       'Fuel Depot',             '#F39C12','#E67E22',0.70),
                ('cargo_area',       'Cargo Terminal',         '#8E44AD','#6C3483',0.60),
                ('vehicle_parking',  'Vehicle Parking',        '#1ABC9C','#17A589',0.50),
                ('fire_station',     'ARFF / Fire Station',    '#E74C3C','#C0392B',0.80),
            ]
            TU_LINE_LAYERS = [
                ('access_roads', 'Access Roads',  '#2ECC71', 1.0),
            ]
            TU_TAXIWAY_LAYERS = [
                ('taxiways', 'Taxiways (Parallel + RETs)', '#F1C40F', '#D4A800', 0.75),
            ]
            for lname, dname, fill, border, opac in TU_POLYGON_LAYERS:
                lyr = self._add_layer_to_group(
                    terminal_gpkg, lname, dname, tu_group, fill, border, opac, 'polygon')
                if lyr: loaded_total += 1
            for lname, dname, fill, opac in TU_LINE_LAYERS:
                lyr = self._add_layer_to_group(
                    terminal_gpkg, lname, dname, tu_group, fill, fill, opac, 'line')
                if lyr: loaded_total += 1
            # Taxiways are now buffered polygons
            for lname, dname, fill, border, opac in TU_TAXIWAY_LAYERS:
                lyr = self._add_layer_to_group(
                    terminal_gpkg, lname, dname, tu_group, fill, border, opac, 'polygon')
                if lyr: loaded_total += 1
            # ATC tower (point)
            lyr = self._add_vector_label_layer(
                terminal_gpkg, 'atc_tower', 'ATC Tower',
                tu_group, '#E74C3C', size='5')
            if lyr: loaded_total += 1
            # Facility labels
            self._add_vector_label_layer(
                terminal_gpkg, 'facility_labels', 'Facility Labels',
                tu_group, '#00000000', label_field='label')

        # ══════════════════════════════════════════════════════════════════════
        # ▼ Lighting sub-group
        # ══════════════════════════════════════════════════════════════════════
        if os.path.exists(lights_gpkg):
            lt_group = top_group.addGroup("Lighting")
            uri = f"{lights_gpkg}|layername=icao_lights"
            lyr = QgsVectorLayer(uri, "ICAO Aerodrome Lights", "ogr")
            if lyr.isValid():
                # Categorized renderer by hex_color (ICAO colour per light type)
                from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory
                categories = []
                seen = set()
                icao_map = {
                    '#FFFFFF':('#FFFFFF','White'),       '#F0F0F0':('#F0F0F0','White Hi-Int'),
                    '#FFC200':('#FFC200','Amber'),       '#00C000':('#00C000','Green'),
                    '#CC0000':('#CC0000','Red'),         '#FF0000':('#FF0000','Red Stop Bar'),
                    '#FF2200':('#FF2200','Red Obstacle'), '#FFD700':('#FFD700','Yellow RGL'),
                    '#0055FF':('#0055FF','Blue Twy'),    '#FF6600':('#FF6600','PAPI'),
                    '#FF8800':('#FF8800','Obstacle Hi'), '#FFF8E0':('#FFF8E0','Warm White'),
                }
                for feat in lyr.getFeatures():
                    hx = feat['hex_color'] or '#888888'
                    if hx in seen: continue
                    seen.add(hx)
                    col, lbl = icao_map.get(hx, (hx, hx))
                    sym = QgsMarkerSymbol.createSimple(
                        {'name':'circle','color':col,'color_border':'#333333',
                         'outline_width':'0.1','size':'1.0'})
                    categories.append(QgsRendererCategory(hx, sym, lbl))
                if categories:
                    lyr.setRenderer(QgsCategorizedSymbolRenderer('hex_color', categories))
                QgsProject.instance().addMapLayer(lyr, False)
                lt_group.addLayer(lyr)
                loaded_total += 1

        # ── Summary ───────────────────────────────────────────────────────────
        self.log_message(
            f"Candidate {desig} loaded — {loaded_total} layers in "
            f"group 'Candidate {rank:02d} — {desig}'", "SUCCESS")
        QMessageBox.information(
            self, "Layers Loaded",
            f"Candidate {desig} — {loaded_total} layers loaded.\n"
            f"Organised in group: 'Candidate {rank:02d} — {desig}'\n\n"
            f"Sub-groups:\n"
            f"  • Runway GeoPackage\n"
            f"  • Safety Surfaces (if generated)\n"
            f"  • Terminal & Utilities (if generated)\n"
            f"  • Lighting (if generated)")

    def view_compliance_report(self):
        if not self.results:
            return
        path = os.path.join(self.results['planner'].output_dir, 'ICAO_Compliance_Reports', 'icao_compliance_report.txt')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                text = f.read()
            dlg = QDialog(self)
            dlg.setWindowTitle("ICAO Compliance Report")
            dlg.setMinimumSize(800,600)
            l = QVBoxLayout()
            te = QTextEdit()
            te.setPlainText(text)
            te.setReadOnly(True)
            l.addWidget(te)
            dlg.setLayout(l)
            dlg.exec()
        else:
            QMessageBox.warning(self, "Not Found", "Compliance report not found.")

    def export_results(self):
        if not self.results:
            return
        export_dir = QFileDialog.getExistingDirectory(self, "Export Directory", self.results['planner'].output_dir)
        if export_dir:
            import shutil
            dest = os.path.join(export_dir, f"Airport_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copytree(self.results['planner'].output_dir, dest)
            self.log_message(f"Exported to {dest}", "SUCCESS")
            QMessageBox.information(self, "Export Complete", f"Exported to:\n{dest}")

    def open_output_folder(self):
        if not self.results:
            return
        import subprocess, platform
        d = self.results['planner'].output_dir
        if os.path.exists(d):
            if platform.system() == "Windows":
                os.startfile(d)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        else:
            QMessageBox.warning(self, "Not Found", "Output folder not found.")

    def show_wind_rose(self):
        if not self.results:
            QMessageBox.warning(self, "No Results", "Run analysis first.")
            return
        rose_dir = os.path.join(self.results['planner'].output_dir, 'Maps', 'Wind_Roses')
        rose_files = {
            "Traditional (stacked bars)":   "wind_rose_traditional.png",
            "Scatter (speed vs direction)":  "wind_rose_scatter.png",
            "Frequency Polygon (ICAO)":      "wind_rose_polygon_frequency.png",
            "Seasonal (4 panels)":           "wind_rose_seasonal.png",
            "Verification (with runway)":    os.path.join("..", "..", "verification_wind_rose_runway.png"),
        }
        # pick whichever exists
        available = {k: os.path.normpath(os.path.join(rose_dir, v))
                     for k, v in rose_files.items()
                     if os.path.exists(os.path.normpath(os.path.join(rose_dir, v)))}
        if not available:
            QMessageBox.warning(self, "Not Found",
                                "No wind rose images found. Run full analysis first.")
            return
        if len(available) == 1:
            dlg = ImageViewerDialog("Wind Rose", list(available.values())[0], self)
            dlg.exec()
            return
        from qgis.PyQt.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Select Wind Rose", "Choose rose type:",
            list(available.keys()), 0, False)
        if ok and choice in available:
            dlg = ImageViewerDialog(choice, available[choice], self)
            dlg.exec()

    def show_usability_chart(self):
        if not self.results:
            QMessageBox.warning(self, "No Results", "Run analysis first.")
            return
        path = os.path.join(self.results['planner'].output_dir, 'Maps', 'runway_usability_chart.png')
        if os.path.exists(path):
            dlg = ImageViewerDialog("Runway Usability Chart  —  ICAO Doc 9157", path, self)
            dlg.exec()
        else:
            # Try regenerating on the fly
            if self.results and self.results.get('wind_results') is not None:
                try:
                    self.results['planner'].generate_usability_chart(self.results['wind_results'])
                    if os.path.exists(path):
                        dlg = ImageViewerDialog("Runway Usability Chart", path, self)
                        dlg.exec()
                        return
                except Exception as e:
                    self.log_message(f"Usability chart regen failed: {e}", "ERROR")
            QMessageBox.warning(self, "Not Found", "Usability chart not found. Run full analysis.")

    def show_obstacle_map(self):
        """Show the improved ICAO OLS obstruction map, generating it if needed."""
        out_dir = None
        orientation = 0.0

        if self.results:
            planner = self.results.get('planner')
            if planner and hasattr(planner, 'output_dir') and planner.output_dir:
                out_dir = planner.output_dir
            cands = self.results.get('candidates', [])
            if cands:
                orientation = cands[0]['orientation']

        if not out_dir:
            out_dir = self.output_dir.text().strip() if hasattr(self, 'output_dir') else ''
        if not out_dir:
            out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder for Obstruction Map")
        if not out_dir:
            return

        maps_dir = os.path.join(out_dir, 'Maps')
        existing  = os.path.join(maps_dir, 'obstruction_map.png')

        # Use improved map if results planner is available
        planner_obj = (self.results.get('planner') if self.results else None) or self.planner
        if planner_obj is None:
            planner_obj = ICAOCompliantAirportRunwayPlanner()

        # Try to use raster obstacle data if available
        if (hasattr(planner_obj, 'obstacle_height_raster_path') and
                planner_obj.obstacle_height_raster_path and
                os.path.exists(planner_obj.obstacle_height_raster_path)):
            layer_name = "Aerial Obstacles"
            if not QgsProject.instance().mapLayersByName(layer_name):
                layer = QgsRasterLayer(planner_obj.obstacle_height_raster_path, layer_name)
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.log_message("Obstacle height raster loaded into QGIS.", "SUCCESS")

        # Generate (or re-generate) improved obstruction map
        result = planner_obj.generate_obstruction_map(maps_dir, orientation)
        if result and os.path.exists(result):
            dlg = ImageViewerDialog("ICAO Obstruction / OLS Map", result, self)
            dlg.exec()
        elif os.path.exists(existing):
            dlg = ImageViewerDialog("Obstacle Map", existing, self)
            dlg.exec()
        else:
            QMessageBox.warning(self, "Not Found",
                                "Could not generate obstruction map.\n"
                                "Please set the output directory and run analysis first.")

    def show_wind_arrows(self):
        path = os.path.join(self.results['planner'].output_dir, 'Maps', 'wind_direction_arrows.png')
        if os.path.exists(path):
            dlg = ImageViewerDialog("Wind Direction Arrows", path, self)
            dlg.exec()
        else:
            QMessageBox.warning(self, "Not Found", "Wind direction arrows diagram not found.")

    def _load_obstacle_raster_ui(self):
        """Load Aerial_Obstacles.tiff into QGIS with colour-ramp renderer."""
        if not self.results:
            return
        planner = self.results['planner']
        raster_path = getattr(planner, 'obstacle_height_raster_path', None)
        if not raster_path or not os.path.exists(raster_path):
            # Try standard location
            raster_path = os.path.join(
                planner.output_dir, 'Obstacle_Analysis', 'Aerial_Obstacles.tiff')
        if not raster_path or not os.path.exists(raster_path):
            QMessageBox.warning(self, "Not Found",
                "Aerial_Obstacles.tiff not found.\nRun analysis with DSM+DTM layers first.")
            return
        planner.obstacle_height_raster_path = raster_path
        planner.load_obstacle_raster_to_qgis()
        self.log_message(f"Obstacle raster loaded: {raster_path}", "SUCCESS")

    def _load_obstacle_points_ui(self):
        """Load Aerial_Obstacles.gpkg points into QGIS with graduated symbology."""
        if not self.results:
            return
        planner = self.results['planner']
        pts_path = getattr(planner, 'obstacle_points_layer_path', None)
        if not pts_path or not os.path.exists(pts_path):
            pts_path = os.path.join(
                planner.output_dir, 'Obstacle_Analysis', 'Aerial_Obstacles.gpkg')
        if not pts_path or not os.path.exists(pts_path):
            QMessageBox.warning(self, "Not Found",
                "Aerial_Obstacles.gpkg not found.\nRun analysis with DSM+DTM layers first.")
            return
        planner.obstacle_points_layer_path = pts_path
        planner.load_obstacle_points_to_qgis()
        self.log_message(f"Obstacle points loaded: {pts_path}", "SUCCESS")

    # ========================================================================
    # SAFETY SURFACES HANDLERS
    # ========================================================================

    def _get_surface_orientation(self):
        """Return orientation to use for surface generation."""
        if self.surfaces_use_top_candidate.isChecked() and self.results:
            candidates = self.results.get('candidates', [])
            if candidates:
                return candidates[0]['orientation']
        return self.surfaces_orientation.value()

    def _require_planner_and_output(self):
        """Ensure planner is initialised and output_dir is set. Returns True if OK."""
        # If analysis has been run, always use the fully-populated results planner
        if self.results and 'planner' in self.results:
            self.planner = self.results['planner']
        elif self.planner is None:
            self.planner = ICAOCompliantAirportRunwayPlanner()

        out_dir = self.output_dir.text().strip()
        if not out_dir:
            # Try to recover output dir from the planner itself
            if self.planner.output_dir:
                out_dir = self.planner.output_dir
            else:
                QMessageBox.warning(self, "Output Directory", "Please set an output directory first.")
                return False
        self.planner.output_dir = out_dir

        # Sync CRS
        crs = self.crs_selector.crs()
        if crs and crs.isValid():
            self.planner.output_crs = crs.authid()

        # Sync AOI / centroid only when the planner doesn't already have one
        if self.planner.wgs84_centroid is None:
            aoi_layer = self.aoi_combo.currentLayer()
            if aoi_layer and isinstance(aoi_layer, QgsVectorLayer):
                try:
                    feat = next(aoi_layer.getFeatures())
                    geom = feat.geometry()
                    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
                    transform = QgsCoordinateTransform(aoi_layer.crs(), wgs84, QgsProject.instance())
                    geom.transform(transform)
                    centroid = geom.centroid().asPoint()
                    self.planner.wgs84_centroid = (centroid.y(), centroid.x())
                    from shapely.geometry import Point as SPoint
                    self.planner.centroid = SPoint(centroid.x(), centroid.y())
                    self.planner.aoi_geometry_original = SPoint(centroid.x(), centroid.y())
                except Exception as e:
                    self.log_message(f"Warning: Could not read AOI centroid: {e}", "WARNING")

        # Sync runway parameters (only override when planner has no real data yet)
        if not self.planner.airport_reference_code or self.planner.airport_reference_code == '---':
            self.planner.runway_code_number = self.code_number.text().strip() or '4'
            self.planner.airport_reference_code = self.arc_display.text().strip() or '4C'
        if not self.planner.approach_type:
            approach_text = self.approach_type.currentText() if hasattr(self, 'approach_type') else 'Non-precision'
            self.planner.approach_type = approach_text
        if self.planner.runway_end_safety_area == (90, 30) or self.planner.runway_end_safety_area is None:
            resa_l = self.resa_length.value()
            resa_w = self.resa_width.value()
            self.planner.runway_end_safety_area = (resa_l, resa_w)
        if self.planner.runway_strip_width == 0:
            self.planner.runway_strip_width = self.strip_width.value()
        if self.planner.runway_width == 0:
            self.planner.runway_width = 45
        if self.planner.taxiway_width == 0:
            self.planner.taxiway_width = 18
        # Only apply fallback runway length if the planner genuinely has nothing
        if self.planner.corrected_runway_length == 0:
            self.planner.corrected_runway_length = 2500
        return True

    def _get_candidate_orientation(self, spin_widget):
        """Return orientation for the candidate number in spin_widget, or fallback."""
        cand_num = spin_widget.value() if spin_widget else 1
        if self.results and 'candidates' in self.results:
            candidates = self.results['candidates']
            idx = cand_num - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]['orientation']
        if self.results:
            cands = self.results.get('candidates', [])
            if cands:
                return cands[0]['orientation']
        return self._get_surface_orientation()

    def generate_safety_surfaces(self):
        """Generate all selected OLS surfaces into a GeoPackage for the chosen candidate."""
        if not self._require_planner_and_output():
            return
        cand_spin = getattr(self, 'ss_candidate_spin', None)
        orientation = self._get_candidate_orientation(cand_spin)
        glide_angle = self.glide_angle_spin.value()
        tch = self.tch_spin.value()

        # Sync approach type from surfaces tab
        ap_type = self.surface_approach_type.currentText()
        self.planner.approach_type = ap_type

        cand_num = cand_spin.value() if cand_spin else 1
        self.surfaces_log.clear()
        self.surfaces_log.append(
            f"Generating safety surfaces for Candidate #{cand_num} "
            f"(orientation {orientation:.1f}°)...")

        out_dir = self.output_dir.text().strip()
        gpkg_path, results = self.planner.generate_safety_surfaces_geopackage(
            orientation, out_dir, glide_angle=glide_angle, tch=tch)

        if gpkg_path and os.path.exists(gpkg_path):
            self.surfaces_log.append(f"✅ GeoPackage saved:\n  {gpkg_path}")
            self.surfaces_log.append(f"Layers generated: {', '.join(results.keys())}")
            self._last_safety_gpkg = gpkg_path
            self._last_safety_results = results
            self.load_safety_surfaces_qgis_btn.setEnabled(True)
            QMessageBox.information(self, "Safety Surfaces Generated",
                                    f"GeoPackage saved to:\n{gpkg_path}\n\n"
                                    f"Layers: {len(results)} surfaces generated.")
        else:
            self.surfaces_log.append("❌ Generation failed. Check that AOI/output directory are set and analysis has been run.")
            QMessageBox.warning(self, "Generation Failed",
                                "Could not generate safety surfaces.\n"
                                "Please ensure:\n"
                                "1. AOI layer is selected\n"
                                "2. Output directory is set\n"
                                "3. Run analysis first (or set orientation manually)")

    def load_safety_surfaces_into_qgis(self):
        """Load all layers from the safety surfaces GeoPackage into QGIS (grouped)."""
        if not hasattr(self, '_last_safety_gpkg') or not self._last_safety_gpkg:
            QMessageBox.warning(self, "No GeoPackage", "Generate safety surfaces first.")
            return
        gpkg_path = self._last_safety_gpkg
        LAYERS = [
            ('glide_slope',             'Glide Slope Surface',             '#FF6B35', 0.40),
            ('takeoff_climb_surface',   'Take-off Climb Surface',          '#FFA500', 0.40),
            ('approach_surface',        'Approach Surface',                '#4CAF50', 0.40),
            ('transitional_surface',    'Transitional Surface',            '#9C27B0', 0.35),
            ('inner_horizontal_surface','Inner Horizontal Surface (45 m)', '#2196F3', 0.30),
            ('conical_surface',         'Conical Surface',                 '#FF9800', 0.30),
            ('outer_horizontal_surface','Outer Horizontal Surface (150 m)','#607D8B', 0.20),
        ]
        root = QgsProject.instance().layerTreeRoot()
        grp  = root.insertGroup(0, "Safety Surfaces")
        loaded = 0
        for lid, dname, color, opac in LAYERS:
            lyr = self._add_layer_to_group(
                gpkg_path, lid, dname, grp, color, color, opac, 'polygon')
            if lyr: loaded += 1
        if loaded:
            self.surfaces_log.append(f"✅ Loaded {loaded} surface layers into 'Safety Surfaces' group.")
            self.log_message(f"Loaded {loaded} safety surface layers into QGIS.", "SUCCESS")
        else:
            QMessageBox.warning(self, "Load Failed",
                                "Could not load layers. GeoPackage may be empty or corrupt.")

    # ========================================================================
    # TERMINAL & UTILITIES HANDLERS
    # ========================================================================

    def generate_terminal_utilities(self):
        """Generate terminal building and all utilities as a GeoPackage for the chosen candidate."""
        if not self._require_planner_and_output():
            return
        cand_spin = getattr(self, 'tu_candidate_spin', None)
        orientation = self._get_candidate_orientation(cand_spin)
        # Prefer planner.output_dir (populated by analysis) over bare UI field
        out_dir = (self.planner.output_dir
                   if self.planner and self.planner.output_dir
                   else self.output_dir.text().strip())
        if not out_dir:
            out_dir = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if not out_dir:
            return

        # Build params from UI
        params = {
            'terminal_length_m': self.term_length.value(),
            'terminal_width_m': self.term_width.value(),
            'terminal_side': 'right' if 'Right' in self.term_side.currentText() else 'left',
            'terminal_offset_m': self.term_offset.value(),
            'n_hangars': self.n_hangars_spin.value(),
            'hangar_length': self.hangar_length.value(),
            'hangar_width': self.hangar_width.value(),
        }

        cand_num = cand_spin.value() if cand_spin else 1
        self.terminal_log.clear()
        self.terminal_log.append(
            f"Generating terminal & utilities for Candidate #{cand_num} "
            f"(orientation {orientation:.1f}°)...")

        gpkg_path, layers = self.planner.generate_terminal_utilities_geopackage(
            orientation, out_dir, params=params)

        if gpkg_path and os.path.exists(gpkg_path):
            self.terminal_log.append(f"✅ GeoPackage saved:\n  {gpkg_path}")
            self.terminal_log.append(f"Layers: {', '.join(layers.keys())}")
            self._last_terminal_gpkg = gpkg_path
            self._last_terminal_layers = layers
            self.load_terminal_qgis_btn.setEnabled(True)
            QMessageBox.information(self, "Terminal & Utilities Generated",
                                    f"GeoPackage saved to:\n{gpkg_path}\n\n"
                                    f"Layers: {len(layers)} facility layers generated.")
        else:
            err_hint = (
                f"Output dir : {out_dir}\n"
                f"Centroid   : {getattr(self.planner,'wgs84_centroid','(not set)')}\n"
                f"Rwy length : {getattr(self.planner,'corrected_runway_length',0):.0f} m\n"
                f"Code number: {getattr(self.planner,'runway_code_number','?')}\n\n"
                "Check the QGIS Python console for the full traceback."
            )
            self.terminal_log.append(f"❌ Generation failed.\n{err_hint}")
            QMessageBox.warning(self, "Generation Failed",
                                f"Could not generate terminal utilities.\n\n{err_hint}")

    def load_terminal_into_qgis(self):
        """Load all terminal/utilities layers from the GeoPackage into QGIS (grouped)."""
        if not hasattr(self, '_last_terminal_gpkg') or not self._last_terminal_gpkg:
            QMessageBox.warning(self, "No GeoPackage", "Generate terminal utilities first.")
            return
        gpkg_path = self._last_terminal_gpkg

        root = QgsProject.instance().layerTreeRoot()
        grp  = root.insertGroup(0, "Terminal & Utilities")

        POLY_LAYERS = [
            ('terminal_building','Terminal Building',    '#3498DB','#2980B9',0.70),
            ('apron',            'Aircraft Apron',        '#95A5A6','#7F8C8D',0.60),
            ('hangars',          'Hangars',               '#E67E22','#D35400',0.70),
            ('fuel_depot',       'Fuel Depot',            '#F39C12','#E67E22',0.70),
            ('cargo_area',       'Cargo Terminal',        '#8E44AD','#6C3483',0.60),
            ('vehicle_parking',  'Vehicle Parking',       '#1ABC9C','#17A589',0.50),
            ('fire_station',     'ARFF / Fire Station',   '#E74C3C','#C0392B',0.80),
        ]
        LINE_LAYERS = [
            ('access_roads','Access Roads','#2ECC71',1.0),
        ]
        TAXIWAY_POLYGON_LAYERS = [
            ('taxiways', 'Taxiways (Parallel + RETs)', '#F1C40F', '#D4A800', 0.75),
        ]
        loaded = 0
        for lname, dname, fill, border, opac in POLY_LAYERS:
            lyr = self._add_layer_to_group(
                gpkg_path, lname, dname, grp, fill, border, opac, 'polygon')
            if lyr: loaded += 1
        for lname, dname, fill, opac in LINE_LAYERS:
            lyr = self._add_layer_to_group(
                gpkg_path, lname, dname, grp, fill, fill, opac, 'line')
            if lyr: loaded += 1
        # Taxiways are buffered polygons (width from ICAO code number)
        for lname, dname, fill, border, opac in TAXIWAY_POLYGON_LAYERS:
            lyr = self._add_layer_to_group(
                gpkg_path, lname, dname, grp, fill, border, opac, 'polygon')
            if lyr: loaded += 1
        # ATC Tower point
        lyr = self._add_vector_label_layer(
            gpkg_path, 'atc_tower', 'ATC Tower', grp, '#E74C3C', size='5')
        if lyr: loaded += 1
        # Facility labels
        self._add_vector_label_layer(
            gpkg_path, 'facility_labels', 'Facility Labels',
            grp, '#00000000', label_field='label')

        if loaded:
            self.terminal_log.append(f"✅ Loaded {loaded} facility layers into 'Terminal & Utilities' group.")
            self.log_message(f"Loaded {loaded} terminal/utilities layers into QGIS.", "SUCCESS")
        else:
            QMessageBox.warning(self, "Load Failed",
                                "Could not load layers. GeoPackage may be empty or corrupt.")
    def calculate_costs(self):
        QMessageBox.information(self, "Costs", "Cost calculation would be performed.")

    def generate_proposal(self):
        QMessageBox.information(self, "Proposal", "Proposal generation would be performed.")

    def view_financial_analysis(self):
        QMessageBox.information(self, "Financial", "Financial analysis would be displayed.")

    def show_help(self):
        QMessageBox.information(self, "Help", "Aerodrome Spatial Planner v1.0\nBased on ICAO Annex 14 and Doc 9157.\nSee documentation for details.")

    def show_tutorial(self):
        QMessageBox.information(self, "Tutorial", "1. Set project details\n2. Select aircraft\n3. Configure runway\n4. Run analysis\n5. Review results")

    def load_project(self):
        QMessageBox.information(self, "Load", "Load project feature placeholder.")

    def save_project(self):
        QMessageBox.information(self, "Save", "Save project feature placeholder.")

    def show_settings(self):
        QMessageBox.information(self, "Settings", f"Settings dialog would appear.\nCurrent UI scale: {int(self.ui_scale*100)}%")

    def set_default_values(self):
        self.project_name.setText("New Airport Project")
        self.client_name.setText("Client Name")
        self.project_id.setText(f"AP-{datetime.now().strftime('%Y%m%d')}")
        self.output_dir.setText(os.path.join(QgsProject.instance().homePath(), "airport_planner_output"))
        self.metric_radio.setChecked(True)
        self.mean_temperature.setValue(15)
        self.precipitation.setValue(1000)
        self.humidity.setValue(70)
        self.magnetic_variation.setValue(0)
        self.num_candidates_spin.setValue(5)
        self.wind_rose_cmap.setCurrentText("viridis")
        self.resa_length.setValue(90)
        self.resa_width.setValue(30)
        self.strip_width.setValue(75)
        self.stopway_length.setValue(0)
        self.clearway_length.setValue(0)
        self.displaced_threshold.setValue(0)
        self.obstacle_threshold.setValue(1.0)
        today = QDate.currentDate()
        self.start_date_edit.setDate(today.addYears(-10))
        self.end_date_edit.setDate(today)
        crs = QgsProject.instance().crs()
        self.crs_selector.setCrs(crs if crs.isValid() else QgsCoordinateReferenceSystem('EPSG:4326'))
        self.log_message("Default values set", "INFO")


    # ========================================================================
    # ENGINEERING DRAWINGS — Runway & Taxiway Drafting-Level PNGs
    # ========================================================================

    def _generate_all_engineering_drawings(self):
        """Generate engineering drawings for every ranked candidate after analysis."""
        if not self.results or 'candidates' not in self.results:
            return
        planner = self.results['planner']
        out_dir = planner.output_dir
        if not out_dir:
            return
        drawings_dir = os.path.join(out_dir, 'drawings')
        os.makedirs(drawings_dir, exist_ok=True)
        candidates = self.results['candidates']
        generated = []
        for cand in candidates:
            try:
                path = self._draw_engineering_plan(cand, planner, drawings_dir)
                if path:
                    generated.append(path)
            except Exception as e:
                self.log_message(f"Drawing error for Candidate {cand['rank']}: {e}", "WARNING")
        if generated:
            self._engineering_drawings = generated
            self.view_drawings_btn.setEnabled(True)
            self.drawings_status.setText(
                f"✅  {len(generated)} drawings in: {drawings_dir}")
            self.drawings_status.setStyleSheet("color:#1a6620;font-weight:bold;padding:4px;")
            self.log_message(
                f"Engineering drawings generated: {len(generated)} files in '{drawings_dir}'",
                "SUCCESS")
        else:
            self.drawings_status.setText("⚠ No drawings generated.")

    # ------------------------------------------------------------------
    def _draw_engineering_plan(self, cand, planner, drawings_dir):
        """
        Render one engineering-drafting-level PNG for a runway candidate.
        Drawing shows: runway, stopway, clearway, RESA, taxiways,
        dimensions, ICAO declared distances, title block, north arrow,
        scale bar and technical notes — all on a white A1-landscape canvas.
        """
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
        from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
        import matplotlib.lines as mlines
        import numpy as np

        rank   = cand['rank']
        desig  = cand['designation']
        tora   = float(cand.get('tora', cand.get('length', 2500)))
        toda   = float(cand.get('toda', tora))
        asda   = float(cand.get('asda', tora))
        lda    = float(cand.get('lda',  tora))
        rwy_w  = float(getattr(planner, 'runway_width', 45))
        strip  = float(getattr(planner, 'runway_strip_width', 75))
        resa_l = float(getattr(planner, 'runway_end_safety_area', (90, 30))[0])
        resa_w = float(getattr(planner, 'runway_end_safety_area', (90, 30))[1])
        swy    = float(getattr(planner, 'stopway_length',  0))
        cwy    = float(getattr(planner, 'clearway_length', 0))
        displ  = float(getattr(planner, 'displaced_threshold', 0))
        twy_w  = float(getattr(planner, 'taxiway_width', 23))
        arc    = str(getattr(planner, 'airport_reference_code', '4C'))
        orient = float(cand.get('orientation', 0))
        mag_or = float(cand.get('magnetic_orientation', orient))
        dry_cov= float(cand.get('dry_coverage', 0))
        proj   = str(getattr(planner, 'project_name', 'Airport Project') or 'Airport Project')
        date   = datetime.now().strftime('%Y-%m-%d')

        # ── Figure setup ─────────────────────────────────────────────────────
        FIG_W, FIG_H = 23.4, 16.54   # A1 landscape in inches at 100 dpi
        DPI = 120
        fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI)
        fig.patch.set_facecolor('#FFFFFF')

        # Two areas: main drawing (left 80%) + title block (right 20%)
        ax  = fig.add_axes([0.02, 0.12, 0.74, 0.84])   # main plan view
        tax = fig.add_axes([0.76, 0.02, 0.23, 0.96])   # title block
        tax.set_axis_off()
        ax.set_aspect('equal')
        ax.set_facecolor('#FAFAFA')

        # ── Coordinate system: drawing in metres, centred on rwy threshold ───
        total_len = tora + resa_l * 2 + max(swy, cwy) + 400
        draw_w    = max(strip * 2 + twy_w * 2 + 300, strip * 3)

        # ── DRAWING — layers from bottom up ──────────────────────────────────

        # 1) Runway strip (graded area)
        strip_rect = patches.Rectangle(
            (-resa_l, -strip), total_len - 2*resa_l + swy, strip * 2,
            linewidth=0.6, edgecolor='#888888', facecolor='#E8F5E9',
            alpha=0.55, zorder=1, label='Runway Strip')
        ax.add_patch(strip_rect)

        # 2) RESA — left end
        resa_l_rect = patches.Rectangle(
            (-resa_l - total_len*0.0, -resa_w/2), resa_l, resa_w,
            linewidth=0.8, edgecolor='#D32F2F', facecolor='#FFCDD2',
            alpha=0.7, zorder=2, label=f'RESA ({resa_l:.0f}×{resa_w:.0f}m)')
        ax.add_patch(resa_l_rect)

        # 3) RESA — right end
        resa_r_rect = patches.Rectangle(
            (tora + swy, -resa_w/2), resa_l, resa_w,
            linewidth=0.8, edgecolor='#D32F2F', facecolor='#FFCDD2',
            alpha=0.7, zorder=2)
        ax.add_patch(resa_r_rect)

        # 4) Stopway
        if swy > 0:
            sw_rect = patches.Rectangle(
                (tora, -rwy_w/2), swy, rwy_w,
                linewidth=0.8, edgecolor='#7B1FA2', facecolor='#E1BEE7',
                alpha=0.85, zorder=3, label=f'Stopway ({swy:.0f}m)')
            ax.add_patch(sw_rect)

        # 5) Clearway (outline only)
        if cwy > 0:
            cw_rect = patches.Rectangle(
                (tora + swy, -strip/2), cwy, strip,
                linewidth=1.0, edgecolor='#1565C0', facecolor='none',
                linestyle='--', zorder=3, label=f'Clearway ({cwy:.0f}m)')
            ax.add_patch(cw_rect)

        # 6) Runway pavement (grey)
        rwy_rect = patches.Rectangle(
            (0, -rwy_w/2), tora, rwy_w,
            linewidth=1.2, edgecolor='#212121', facecolor='#B0BEC5',
            zorder=4, label=f'Runway Pavement {tora:.0f}×{rwy_w:.0f}m')
        ax.add_patch(rwy_rect)

        # 7) Displaced threshold zone (hash)
        if displ > 0:
            disp_rect = patches.Rectangle(
                (0, -rwy_w/2), displ, rwy_w,
                linewidth=0.5, edgecolor='#F57F17', facecolor='#FFF9C4',
                alpha=0.7, zorder=5, label=f'Displaced Threshold ({displ:.0f}m)')
            ax.add_patch(disp_rect)
            for xi in np.arange(0, displ, 30):
                ax.plot([xi, xi + 20], [-rwy_w/2, rwy_w/2],
                        color='#F57F17', lw=0.4, zorder=6)

        # 8) Centreline
        ax.plot([0, tora], [0, 0], color='#FFFFFF', lw=1.0,
                linestyle=(0, (10, 6)), zorder=7, label='Centreline')
        ax.plot([0, tora], [0, 0], color='#F44336', lw=0.5,
                linestyle=(0, (10, 6)), zorder=7)

        # 9) Threshold bars
        for xi in [0, tora]:
            ax.plot([xi, xi], [-rwy_w/2, rwy_w/2],
                    color='#FFFFFF', lw=2.0, zorder=8)
            ax.plot([xi, xi], [-rwy_w/2, rwy_w/2],
                    color='#212121', lw=0.8, zorder=8)

        # 10) Parallel taxiway (both sides optional — draw one for simplicity)
        twy_offset = strip + 15                # gap between strip and twy CL
        for side_sign in [1, -1]:
            twy_y = side_sign * twy_offset
            twy_rect = patches.Rectangle(
                (-50, twy_y - twy_w/2), tora + 100, twy_w,
                linewidth=0.8, edgecolor='#1A237E', facecolor='#C5CAE9',
                alpha=0.7, zorder=3, label='Parallel Taxiway' if side_sign == 1 else None)
            ax.add_patch(twy_rect)
            # Taxiway centreline
            ax.plot([-50, tora + 100], [twy_y, twy_y],
                    color='#FFD600', lw=0.8, linestyle='--', zorder=9)

        # 11) Rapid Exit Taxiway (RET) at ~75% runway length
        ret_x = tora * 0.75
        for side_sign in [1, -1]:
            twy_y = side_sign * twy_offset
            ret_pts_x = [ret_x, ret_x + 150, ret_x + 250]
            ret_pts_y = [rwy_w/2 * side_sign,
                         (rwy_w/2 + twy_offset/2) * side_sign,
                         twy_y]
            ax.plot(ret_pts_x, ret_pts_y, color='#1A237E', lw=0.8,
                    linestyle='-', zorder=9)
            ax.annotate('RET', xy=(ret_x + 120, (rwy_w/2 + 15)*side_sign),
                        fontsize=4.5, color='#1A237E',
                        ha='center', va='center', zorder=10)

        # ── Dimension lines ───────────────────────────────────────────────────
        dim_y_top    = strip + 60
        dim_y_bottom = -strip - 60
        arrowprops = dict(arrowstyle='<->', color='#212121', lw=0.7)

        def dim_line(x1, x2, y, label, color='#212121', above=True):
            """Draw a dimension line with label."""
            off = 8 if above else -8
            ax.annotate('', xy=(x2, y), xytext=(x1, y),
                        arrowprops=arrowprops, zorder=11)
            ax.text((x1+x2)/2, y + off, label,
                    fontsize=5.5, ha='center', va='bottom' if above else 'top',
                    color=color, fontweight='bold',
                    fontfamily='DejaVu Sans', zorder=12)
            ax.plot([x1, x1], [y - 4, y + 4], color=color, lw=0.5, zorder=11)
            ax.plot([x2, x2], [y - 4, y + 4], color=color, lw=0.5, zorder=11)

        # TORA
        dim_line(0, tora, dim_y_top + 0,  f'TORA  {tora:.0f} m', '#1565C0')
        # TODA
        dim_line(0, tora + cwy, dim_y_top + 35, f'TODA  {toda:.0f} m', '#2E7D32')
        # ASDA
        dim_line(0, tora + swy, dim_y_top + 70, f'ASDA  {asda:.0f} m', '#6A1B9A')
        # LDA
        dim_line(displ, tora,   dim_y_top + 105,f'LDA   {lda:.0f} m',  '#BF360C')
        # Runway width dimension
        dim_line(-rwy_w/2, rwy_w/2,   # repurpose as a vertical dim
                 -(resa_l + 30), f'RWY W  {rwy_w:.0f} m', '#37474F')
        # RESA left
        dim_line(-resa_l, 0, dim_y_bottom, f'RESA  {resa_l:.0f} m', '#D32F2F', above=False)
        # RESA right
        dim_line(tora + swy, tora + swy + resa_l, dim_y_bottom,
                 f'RESA  {resa_l:.0f} m', '#D32F2F', above=False)
        if swy > 0:
            dim_line(tora, tora + swy, dim_y_bottom - 35,
                     f'SWY  {swy:.0f} m', '#7B1FA2', above=False)
        if cwy > 0:
            dim_line(tora + swy, tora + swy + cwy, dim_y_bottom - 70,
                     f'CWY  {cwy:.0f} m', '#0D47A1', above=False)

        # ── Runway designation labels ─────────────────────────────────────────
        parts = desig.split('/')
        low  = parts[0] if len(parts) > 0 else '—'
        high = parts[1] if len(parts) > 1 else '—'
        ax.text(tora*0.05, 0, low,
                fontsize=11, ha='center', va='center', color='#FFFFFF',
                fontweight='bold', rotation=0, zorder=14,
                bbox=dict(boxstyle='round,pad=0.3', fc='#37474F', ec='none'))
        ax.text(tora*0.95, 0, high,
                fontsize=11, ha='center', va='center', color='#FFFFFF',
                fontweight='bold', rotation=0, zorder=14,
                bbox=dict(boxstyle='round,pad=0.3', fc='#37474F', ec='none'))

        # ── Grid (light) ──────────────────────────────────────────────────────
        ax.set_xlim(-resa_l - 150, tora + max(swy, cwy) + resa_l + 150)
        ax.set_ylim(-strip - 200, strip + 200)
        ax.grid(True, which='major', color='#BDBDBD', lw=0.25, alpha=0.5)
        ax.set_xlabel('Distance along runway centreline (m)',
                      fontsize=7, labelpad=4)
        ax.set_ylabel('Lateral offset from centreline (m)',
                      fontsize=7, labelpad=4)
        ax.tick_params(labelsize=6)
        ax.xaxis.set_major_locator(plt.MultipleLocator(200))
        ax.yaxis.set_major_locator(plt.MultipleLocator(50))

        # ── North arrow ───────────────────────────────────────────────────────
        na_x, na_y = 0.76, 0.92   # figure coords
        ax_n = fig.add_axes([na_x, na_y, 0.035, 0.065])
        ax_n.set_xlim(-1, 1); ax_n.set_ylim(-1, 1.2)
        ax_n.set_aspect('equal'); ax_n.axis('off')
        ax_n.annotate('', xy=(0, 1), xytext=(0, -1),
                      arrowprops=dict(arrowstyle='->', color='#212121', lw=1.5))
        ax_n.text(0, 1.1, 'N', ha='center', va='bottom',
                  fontsize=9, fontweight='bold', color='#212121')
        # Orient arrow per runway true heading
        import matplotlib.transforms as mtransforms
        rot = mtransforms.Affine2D().rotate_deg(-orient)
        ax_n.transData = rot + ax_n.transData

        # ── Legend ────────────────────────────────────────────────────────────
        ax.legend(loc='lower left', fontsize=5.5, ncol=2,
                  framealpha=0.9, edgecolor='#BDBDBD',
                  fancybox=False, frameon=True)

        # ── Title block (right panel) ─────────────────────────────────────────
        tb = tax
        tb.set_xlim(0, 1); tb.set_ylim(0, 1)

        # Outer border
        border = patches.FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
            boxstyle='square,pad=0', linewidth=1.2,
            edgecolor='#212121', facecolor='#FFFFFF')
        tb.add_patch(border)

        # Header band
        header = patches.Rectangle((0.02, 0.88), 0.96, 0.10,
            facecolor='#1565C0', edgecolor='#212121', lw=0.8)
        tb.add_patch(header)
        tb.text(0.5, 0.93, 'AERODROME SPATIAL PLANNER',
                ha='center', va='center', color='#FFFFFF',
                fontsize=10, fontweight='bold', fontfamily='DejaVu Sans')
        tb.text(0.5, 0.897, 'ICAO Annex 14 / Doc 9157 Compliant',
                ha='center', va='center', color='#B3E5FC',
                fontsize=7, fontfamily='DejaVu Sans')

        # Title block rows helper
        def tb_row(y, label, value, lbl_size=7, val_size=8, sep=0.38):
            tb.text(0.05, y, label, ha='left', va='center',
                    fontsize=lbl_size, color='#424242', fontfamily='DejaVu Sans')
            tb.text(sep, y, value, ha='left', va='center',
                    fontsize=val_size, color='#000000', fontweight='bold',
                    fontfamily='DejaVu Sans')
            tb.plot([0.02, 0.98], [y - 0.022, y - 0.022],
                    color='#BDBDBD', lw=0.4)

        y0 = 0.855
        dy = 0.050
        tb_row(y0 - dy*0,  'PROJECT',      proj)
        tb_row(y0 - dy*1,  'DRAWING TITLE', f'CANDIDATE {rank:02d}  —  RWY {desig}')
        tb_row(y0 - dy*2,  'TRUE HDG.',    f'{orient:.1f}°  (Mag {mag_or:.1f}°)')
        tb_row(y0 - dy*3,  'ICAO ARC',     arc)
        tb_row(y0 - dy*4,  'RWY DIMENSIONS', f'{tora:.0f} m × {rwy_w:.0f} m')
        tb_row(y0 - dy*5,  'TORA',         f'{tora:.0f} m')
        tb_row(y0 - dy*6,  'TODA',         f'{toda:.0f} m')
        tb_row(y0 - dy*7,  'ASDA',         f'{asda:.0f} m')
        tb_row(y0 - dy*8,  'LDA',          f'{lda:.0f} m')
        tb_row(y0 - dy*9,  'STOPWAY',      f'{swy:.0f} m')
        tb_row(y0 - dy*10, 'CLEARWAY',     f'{cwy:.0f} m')
        tb_row(y0 - dy*11, 'RESA',         f'{resa_l:.0f} m × {resa_w:.0f} m')
        tb_row(y0 - dy*12, 'STRIP WIDTH',  f'±{strip:.0f} m c/l')
        tb_row(y0 - dy*13, 'TAXIWAY W.',   f'{twy_w:.0f} m')
        tb_row(y0 - dy*14, 'WIND COVER.',  f'{dry_cov:.1f}% (dry)')

        # ── Compliance badge ──────────────────────────────────────────────────
        badge_y = y0 - dy*16
        compliant = cand.get('icao_compliant', False)
        b_col = '#1B5E20' if compliant else '#B71C1C'
        b_fc  = '#E8F5E9' if compliant else '#FFEBEE'
        b_txt = '✓  ICAO COMPLIANT' if compliant else '✗  NON-COMPLIANT'
        badge = patches.FancyBboxPatch(
            (0.05, badge_y - 0.018), 0.90, 0.038,
            boxstyle='round,pad=0.005',
            facecolor=b_fc, edgecolor=b_col, lw=1.0)
        tb.add_patch(badge)
        tb.text(0.50, badge_y, b_txt, ha='center', va='center',
                fontsize=8, fontweight='bold', color=b_col)

        # ── Drawing metadata footer ───────────────────────────────────────────
        y_foot = 0.14
        dy_f   = 0.030
        tb_row(y_foot + dy_f*2, 'DRAWN BY', 'Aerodrome Spatial Planner v1.0', val_size=7)
        tb_row(y_foot + dy_f*1, 'STANDARD', 'ICAO Annex 14, Vol I + Doc 9157', val_size=7)
        tb_row(y_foot + dy_f*0, 'DATE',     date, val_size=7)

        # Scale note
        tb.text(0.50, 0.05, 'SCALE: NOT TO SCALE — FOR PLANNING PURPOSES ONLY',
                ha='center', va='center', fontsize=5.5, color='#757575',
                style='italic')
        tb.text(0.50, 0.025,
                'This drawing is generated by Aerodrome Spatial Planner. '
                'Verify with official ICAO publications.',
                ha='center', va='center', fontsize=4.5, color='#9E9E9E',
                wrap=True)

        # ── Main drawing title strip at bottom ───────────────────────────────
        fig.text(0.38, 0.04,
                 f'RUNWAY & TAXIWAY LAYOUT PLAN  —  CANDIDATE {rank:02d}  ({desig})'
                 f'  |  TRUE HEADING {orient:.1f}°  |  TORA {tora:.0f} m  |  ARC {arc}',
                 ha='center', va='center', fontsize=8, fontweight='bold',
                 color='#1565C0', fontfamily='DejaVu Sans')
        fig.text(0.38, 0.015,
                 f'ICAO Annex 14 Obstacle Limitation Surfaces & Declared Distances  '
                 f'|  Wind usability (dry): {dry_cov:.1f}%'
                 f'  |  Ref: ICAO Doc 9157 Aerodrome Design Manual',
                 ha='center', va='center', fontsize=6.5, color='#424242')

        # ── Border lines on main canvas ───────────────────────────────────────
        for spine in ax.spines.values():
            spine.set_linewidth(0.8)
            spine.set_edgecolor('#424242')

        # ── Save ──────────────────────────────────────────────────────────────
        desig_safe = desig.replace('/', '_')
        out_path = os.path.join(
            drawings_dir,
            f"Candidate_{rank:02d}_{desig_safe}_Engineering_Drawing.png")
        fig.savefig(out_path, dpi=DPI, bbox_inches='tight',
                    facecolor='#FFFFFF', edgecolor='none')
        plt.close(fig)
        return out_path

    # ── View drawings dialog ─────────────────────────────────────────────────
    def view_engineering_drawings(self):
        """Show a picker dialog and open the selected engineering drawing."""
        drawings = getattr(self, '_engineering_drawings', [])
        if not drawings:
            # Try to find drawings in output folder
            if self.results and 'planner' in self.results:
                d = os.path.join(self.results['planner'].output_dir, 'drawings')
                if os.path.isdir(d):
                    drawings = sorted([
                        os.path.join(d, f) for f in os.listdir(d)
                        if f.endswith('.png')
                    ])
        if not drawings:
            QMessageBox.warning(self, 'No Drawings',
                'Engineering drawings not yet generated.\nRun analysis first.')
            return
        # Build a simple list of names
        names = [os.path.basename(p) for p in drawings]
        if len(drawings) == 1:
            dlg = ImageViewerDialog(names[0], drawings[0], self)
            dlg.exec()
            return
        choice, ok = QInputDialog.getItem(
            self, 'Engineering Drawings',
            'Select a drawing to view:', names, 0, False)
        if ok:
            idx = names.index(choice)
            dlg = ImageViewerDialog(choice, drawings[idx], self)
            dlg.exec()


# ============================================================================
# PLUGIN INITIALIZATION
# ============================================================================

class AirportPlannerSuite:
    def __init__(self, iface):
        self.iface = iface
        self.toolbox = None
        self.actions = []
        self.menu = 'Aerodrome Spatial Planner'

    def initGui(self):
        icon_path = ':/plugins/airport_planner/icon.png'
        self.toolbox_action = QAction(QIcon(icon_path), "Aerodrome Spatial Planner v1.0", self.iface.mainWindow())
        self.toolbox_action.setObjectName("AirportPlannerToolbox")
        self.toolbox_action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.toolbox_action)
        self.iface.addPluginToMenu(self.menu, self.toolbox_action)
        self.iface.vectorMenu().addAction(self.toolbox_action)
        self.actions.append(self.toolbox_action)
        print("Aerodrome Spatial Planner v1.0 loaded")

    def unload(self):
        for a in self.actions:
            self.iface.removePluginMenu(self.menu, a)
            self.iface.removeToolBarIcon(a)

    def run(self):
        if self.toolbox is None:
            self.toolbox = ProfessionalAirportPlannerToolbox(self.iface)
        self.toolbox.show()
        self.toolbox.raise_()
        self.toolbox.activateWindow()


def classFactory(iface):
    return AirportPlannerSuite(iface)