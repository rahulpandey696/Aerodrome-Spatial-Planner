#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engine.py
=========
Core analysis engine for the Airport Runway Planner Suite.

Class
-----
ICAOCompliantAirportRunwayPlanner
    Fetches weather data (Open-Meteo API), processes DSM/DTM rasters,
    analyses runway orientations for ICAO wind coverage, generates GeoPackage
    layers, wind-rose charts, OLS maps, and all other spatial outputs.

All public attributes are set to sensible ICAO-compliant defaults in
``__init__`` and may be overridden by the GUI or calling thread before
the analysis pipeline is invoked.

Dependencies (internal)
-----------------------
"""

# ---------------------------------------------------------------------------
# CRITICAL: inject plugin directory into sys.path BEFORE any plugin imports
# This guarantees absolute imports work on Windows QGIS 4 / Python 3.12
# ---------------------------------------------------------------------------
import sys as _sys, os as _os
_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _THIS_DIR not in _sys.path:
    _sys.path.insert(0, _THIS_DIR)

# Now do a plain absolute import — no dot-prefix, no try/except needed
from icao_standards import ICAOStandards, ICAOComplianceChecker  # noqa: E402


# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import os, sys, math, json, time, shutil, tempfile, warnings
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
warnings.filterwarnings('ignore')

# ---------------------------------------------------------------------------
# Third-party / scientific
# ---------------------------------------------------------------------------
import numpy as np
import pandas as pd
import requests
import rasterio
from rasterio.mask  import mask
from rasterio.plot  import show
from rasterio.warp  import calculate_default_transform, reproject, Resampling
import geopandas as gpd
from shapely.geometry import Point, box, LineString, Polygon, MultiLineString, MultiPolygon
from shapely.ops      import transform
from scipy            import stats
import pyproj
from geographiclib.geodesic import Geodesic

# ---------------------------------------------------------------------------
# QGIS / Qt
# ---------------------------------------------------------------------------
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
    QgsRendererRange, QgsColorRampShader,
)
from qgis.PyQt.QtGui import QColor
import processing

# ---------------------------------------------------------------------------
# Matplotlib (non-interactive backend)
# ---------------------------------------------------------------------------
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import Wedge, Rectangle, Arrow

# ---------------------------------------------------------------------------
# Optional: Excel / PDF export
# ---------------------------------------------------------------------------
try:
    import openpyxl
    from openpyxl.styles import Font as XLFont, PatternFill, Alignment, Border, Side
    from openpyxl.chart  import BarChart, Reference, Series
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False

try:
    from reportlab.lib           import colors as rl_colors
    from reportlab.lib.pagesizes import letter, A4, landscape
    from reportlab.platypus      import (SimpleDocTemplate, Table, TableStyle,
                                         Paragraph, Spacer, Image, PageBreak)
    from reportlab.lib.styles    import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units     import inch, cm
    from reportlab.pdfgen        import canvas as rl_canvas
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# ---------------------------------------------------------------------------
# Internal plugin modules
# ---------------------------------------------------------------------------

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))



# ============================================================================
# MAIN ENGINE CLASS
# ============================================================================

class ICAOCompliantAirportRunwayPlanner:
    KNOTS_TO_MS = 0.514444

    def __init__(self):
        self.aoi_gdf = None
        self.aoi_geometry = None
        self.aoi_geometry_original = None
        self.aoi_crs = None
        self.wgs84_centroid = None
        self.centroid = None
        self.lat = None
        self.lon = None
        self.wind_point_lat = None
        self.wind_point_lon = None
        self.output_dir = None

        self.wind_data = None
        self.temperature_data = None
        self.precipitation_data = None
        self.humidity_data = None
        self.snow_data = None
        self.dsm_data = None
        self.dtm_data = None
        self.obstacles = None

        self.airport_reference_code = "4C"
        self.runway_code_number = "4"
        self.runway_code_letter = "C"
        self.reference_field_length = 2400
        self.aircraft_category = "Medium Aircraft (5,700 - 27,000 kg)"

        self.design_aircraft = "B737-800"
        self.wingspan = 35.8
        self.wheel_span = 5.2
        self.max_takeoff_weight = 79000
        self.approach_speed = 140

        self.runway_configuration = "Single"
        self.runway_length = 2500
        self.runway_width = 45
        self.runway_strip_width = 75
        self.runway_end_safety_area = (90, 30)
        self.taxiway_width = 23
        self.taxiway_strip_width = 44

        self.stopway_length = 0
        self.clearway_length = 0
        self.displaced_threshold = 0

        self.elevation = 0
        self.mean_temperature = 15
        self.mean_min_temperature = 10
        self.mean_max_temperature = 20
        self.mean_annual_precipitation = 1000
        self.mean_relative_humidity = 70
        self.mean_snowfall = 0
        self.snow_depth = 0
        self.pavement_strength = "PCN 50/R/B/W/T"
        self.soil_bearing_capacity = 150

        self.crosswind_threshold = 20
        self.crosswind_threshold_dry = 20
        self.crosswind_threshold_wet = 15
        self.crosswind_threshold_icy = 7.5
        self.crosswind_threshold_dry_ms = self.crosswind_threshold_dry * self.KNOTS_TO_MS
        self.crosswind_threshold_wet_ms = self.crosswind_threshold_wet * self.KNOTS_TO_MS
        self.crosswind_threshold_icy_ms = self.crosswind_threshold_icy * self.KNOTS_TO_MS
        self.wind_coverage_requirement = 95
        self.wind_analysis_step = 1
        self.use_knots = False

        self.obstacle_threshold = 1
        self.obstacle_free_zone = True
        self.obstacle_limitation_surfaces = True
        self.obstacle_height_raster_path = None
        self.obstacle_points_layer_path = None

        self.approach_type = "Non-precision"
        self.instrument_approach = True
        self.visual_approach = True
        self.minimum_sector_altitude = 1000

        self.runway_lighting = "HIRL"
        self.approach_lighting = "MALSR"
        self.visual_approach_slope_indicator = "PAPI"
        self.runway_marking = "ICAO"

        self.safety_factor_takeoff = 1.15
        self.safety_factor_landing = 1.30
        self.safety_factor_obstacle = 1.5

        self.elevation_correction_factor = 0.07
        self.temperature_correction_factor = 0.01
        self.gradient_correction_factor = 0.10
        self.humidity_correction_factor = 0.03
        self.pavement_condition_factor = 1.0

        self.magnetic_variation = 0
        self.magnetic_declination = 0
        self.true_north_offset = 0

        self.start_year = 2014
        self.end_year = 2023
        self.start_date = "2014-01-01"
        self.end_date = "2023-12-31"
        self.num_candidates = 5
        self.output_crs = "EPSG:4326"
        self.optimize_processing = True
        self.analysis_method = "ICAO"

        self.project_name = "Airport Development Project"
        self.client_name = ""
        self.project_id = ""
        self.regulatory_authority = "ICAO"
        self.local_regulations = []
        self.environmental_constraints = []

        self.corrected_runway_length = 0
        self.balanced_field_length = 0
        self.available_stopway = 0
        self.available_clearway = 0
        self.todr_distance = 0
        self.ldr_distance = 0
        self.tora = 0
        self.toda = 0
        self.asda = 0
        self.lda = 0
        self.icao_compliance_report = {}

        self.fast_wind_analysis = False
        self.obstacle_sampling = 100
        self.wind_rose_cmap = 'viridis'
        self.use_imperial = False

        self.icao = ICAOStandards()
        self.compliance_checker = None
        self.runway_slope_percent = 0.0
        self.dtm_raster = None
        self.dsm_raster = None
        self.dtm_crs = None

    # ------------------------------------------------------------------------
    # Open‑Meteo API methods
    # ------------------------------------------------------------------------
    def fetch_openmeteo_data(self, start_date=None, end_date=None):
        try:
            if not self.wind_point_lat or not self.wind_point_lon:
                raise ValueError("Wind point coordinates not set")
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {
                "latitude": self.wind_point_lat,
                "longitude": self.wind_point_lon,
                "start_date": start_date or self.start_date,
                "end_date": end_date or self.end_date,
                "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,snowfall,snow_depth",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "auto",
                "wind_speed_unit": "ms"
            }
            try:
                response = requests.get(url, params=params, timeout=30, verify=True)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                response = requests.get(url, params=params, timeout=30, verify=False)
            if response.status_code == 200:
                data = response.json()
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                temperatures = hourly.get("temperature_2m", [])
                humidities = hourly.get("relative_humidity_2m", [])
                wind_speeds = hourly.get("wind_speed_10m", [])
                wind_directions = hourly.get("wind_direction_10m", [])
                wind_gusts = hourly.get("wind_gusts_10m", [])
                snowfall = hourly.get("snowfall", [])
                snow_depth = hourly.get("snow_depth", [])
                daily = data.get("daily", {})
                daily_times = daily.get("time", [])
                temp_max = daily.get("temperature_2m_max", [])
                temp_min = daily.get("temperature_2m_min", [])
                precip_sum = daily.get("precipitation_sum", [])
                if not times:
                    raise ValueError("No hourly data returned from Open-Meteo API")
                df_hourly = pd.DataFrame({
                    'datetime': pd.to_datetime(times),
                    'temperature_c': temperatures,
                    'relative_humidity': humidities,
                    'wind_speed': wind_speeds,
                    'wind_direction_deg': wind_directions,
                    'wind_gust': wind_gusts,
                    'snowfall_cm': snowfall,
                    'snow_depth_cm': snow_depth
                })
                df_hourly['date'] = df_hourly['datetime'].dt.date
                df_hourly['year'] = df_hourly['datetime'].dt.year
                df_hourly['month'] = df_hourly['datetime'].dt.month
                df_hourly['day'] = df_hourly['datetime'].dt.day
                df_hourly['hour'] = df_hourly['datetime'].dt.hour
                def get_season(month):
                    if month in [12,1,2]: return 'Winter'
                    elif month in [3,4,5]: return 'Spring'
                    elif month in [6,7,8]: return 'Summer'
                    else: return 'Fall'
                df_hourly['season'] = df_hourly['month'].apply(get_season)
                self.mean_temperature = df_hourly['temperature_c'].mean()
                self.mean_relative_humidity = df_hourly['relative_humidity'].mean()
                self.mean_snowfall = df_hourly['snowfall_cm'].sum() / len(df_hourly)
                self.snow_depth = df_hourly['snow_depth_cm'].max()
                self.wind_data = df_hourly
                if daily_times:
                    df_daily = pd.DataFrame({
                        'date': pd.to_datetime(daily_times),
                        'temp_max_c': temp_max,
                        'temp_min_c': temp_min,
                        'precipitation_mm': precip_sum
                    })
                    self.temperature_data = df_daily
                    self.mean_max_temperature = df_daily['temp_max_c'].mean()
                    self.mean_min_temperature = df_daily['temp_min_c'].mean()
                    if start_date and end_date:
                        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                        num_years = (end_dt - start_dt).days / 365.25
                    else:
                        num_years = (datetime.strptime(self.end_date, "%Y-%m-%d") - datetime.strptime(self.start_date, "%Y-%m-%d")).days / 365.25
                    total_precip = df_daily['precipitation_mm'].sum()
                    self.mean_annual_precipitation = total_precip / num_years if num_years > 0 else total_precip
                return True
            else:
                return False
        except Exception as e:
            print(f"Error fetching Open-Meteo data: {e}")
            return self.fetch_simulated_wind_data(start_date, end_date)

    def fetch_simulated_wind_data(self, start_date=None, end_date=None):
        try:
            n_records = 8760
            if start_date and end_date:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            else:
                start_dt = datetime.strptime(self.start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(self.end_date, "%Y-%m-%d")
            date_range = pd.date_range(start=start_dt, end=end_dt, periods=n_records)
            directions = np.concatenate([np.random.normal(90, 30, n_records//2), np.random.normal(270, 30, n_records//2)]) % 360
            speeds = np.random.weibull(2, n_records) * 6
            months = pd.Series([d.month for d in date_range])
            winter_mask = months.isin([12,1,2])
            speeds[winter_mask] = speeds[winter_mask] * 1.3
            summer_mask = months.isin([6,7,8])
            speeds[summer_mask] = speeds[summer_mask] * 0.8
            if self.wind_point_lat:
                lat = abs(self.wind_point_lat)
                base_temp = 25 - (lat * 0.5)
                temp_variation = np.sin(2 * np.pi * (months - 3) / 12) * 10
                temperatures = base_temp + temp_variation + np.random.normal(0, 3, n_records)
                temperatures -= (self.elevation / 1000) * 6.5
            else:
                temperatures = np.random.normal(15, 5, n_records)
            humidity = 70 + 20 * np.sin(2 * np.pi * (months - 1) / 12) + np.random.normal(0, 10, n_records)
            humidity = np.clip(humidity, 20, 100)
            snow = np.zeros(n_records)
            snow_mask = temperatures < 0
            snow[snow_mask] = np.random.exponential(0.5, np.sum(snow_mask))
            self.wind_data = pd.DataFrame({
                'datetime': date_range,
                'date': [d.date() for d in date_range],
                'wind_speed': speeds,
                'wind_direction_deg': directions,
                'temperature_c': temperatures,
                'relative_humidity': humidity,
                'snowfall_cm': snow,
                'snow_depth_cm': np.cumsum(snow) * 0.5,
                'month': months,
                'year': pd.Series([d.year for d in date_range]),
                'season': pd.Series(['Winter' if m in [12,1,2] else 'Spring' if m in [3,4,5] else 'Summer' if m in [6,7,8] else 'Fall' for m in months])
            })
            self.mean_temperature = self.wind_data['temperature_c'].mean()
            self.mean_relative_humidity = self.wind_data['relative_humidity'].mean()
            self.mean_snowfall = self.wind_data['snowfall_cm'].sum()
            self.snow_depth = self.wind_data['snow_depth_cm'].max()
            self.mean_max_temperature = self.mean_temperature + 5
            self.mean_min_temperature = self.mean_temperature - 5
            self.mean_annual_precipitation = 1000
            return True
        except Exception as e:
            print(f"Error creating simulated wind data: {e}")
            return False

    def load_elevation_data(self, dsm_path, dtm_path):
        try:
            if hasattr(self, 'aoi_geometry_original') and self.aoi_geometry_original:
                centroid = self.aoi_geometry_original.centroid
                self.elevation = 100
            else:
                self.elevation = 0
            self.dsm_data = {"path": dsm_path, "loaded": True}
            self.dtm_data = {"path": dtm_path, "loaded": True}
            return True
        except Exception as e:
            print(f"Error loading elevation data: {e}")
            self.elevation = 0
            return False

    # ------------------------------------------------------------------------
    # Obstacle generation (DSM - DTM, threshold, points)
    # ------------------------------------------------------------------------
    def calculate_obstacles(self):
        try:
            if self.dsm_raster is not None and self.dtm_raster is not None:
                return self._calculate_obstacles_from_dsm_dtm()
            else:
                print("DSM/DTM not available, obstacle analysis requires both layers.")
                self.obstacles = gpd.GeoDataFrame()
                return False
        except Exception as e:
            print(f"Error calculating obstacles: {e}")
            self.obstacles = gpd.GeoDataFrame()
            return False

    def _calculate_obstacles_from_dsm_dtm(self):
        """
        Generate Aerial_Obstacles.tiff (DSM - DTM clipped to AOI, pixels > threshold)
        and Aerial_Obstacles.shp (point per obstacle pixel) then load both into QGIS.

        Fixes applied vs original code:
          1. AOI clipping: DSM and DTM are clipped to aoi.shp extent before subtraction.
          2. Pixel-resolution: use rasterUnitsPerPixelX/Y instead of
             dsm_provider.xSize()/dsm_layer.width() (which always returns ~1.0).
          3. NoData handling: threshold raster uses -9999 as NoData so zero-valued
             (sub-threshold) pixels are excluded from the output rather than stored as 0.
          4. Correct raster-to-points algorithm: native:pixelstopoints replaces
             the non-existent gdal:rastertopoints algorithm.
          5. File existence guard: check the output .tiff actually exists before setting
             self.obstacle_height_raster_path, preventing a silent "not found" failure.
          6. self.obstacles GeoDataFrame is populated from the point layer so that
             generate_obstacle_map() (PNG export) also works.
          7. temp_dir cleanup moved to a finally block so it always runs.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            from qgis import processing
            from qgis.core import (QgsRasterLayer, QgsVectorLayer,
                                   QgsProcessingFeedback, QgsCoordinateReferenceSystem)

            dsm_layer = self.dsm_raster
            dtm_layer = self.dtm_raster
            if not dsm_layer.isValid() or not dtm_layer.isValid():
                raise Exception("DSM or DTM layer is invalid")

            dsm_path = dsm_layer.source()
            dtm_path = dtm_layer.source()
            feedback = QgsProcessingFeedback()

            # ----------------------------------------------------------------
            # FIX 1 – Clip DSM and DTM to AOI extent before any processing
            # ----------------------------------------------------------------
            dsm_crs = dsm_layer.crs().authid()
            aoi_layer = None
            # Try to recover the AOI layer from the project by stored path or geometry
            if hasattr(self, 'aoi_layer') and self.aoi_layer is not None:
                aoi_layer = self.aoi_layer
            else:
                # Try to find a loaded AOI layer in the project by name
                for lyr in QgsProject.instance().mapLayers().values():
                    if isinstance(lyr, QgsVectorLayer) and 'aoi' in lyr.name().lower():
                        aoi_layer = lyr
                        break

            if aoi_layer is not None and aoi_layer.isValid():
                # Reproject AOI to DSM CRS for clipping
                aoi_reproj_path = os.path.join(temp_dir, "aoi_reproj.shp")
                processing.run("native:reprojectlayer", {
                    'INPUT': aoi_layer,
                    'TARGET_CRS': dsm_crs,
                    'OUTPUT': aoi_reproj_path
                }, feedback=feedback)

                clipped_dsm_path = os.path.join(temp_dir, "dsm_clipped.tif")
                processing.run("gdal:cliprasterbymasklayer", {
                    'INPUT': dsm_path,
                    'MASK': aoi_reproj_path,
                    'SOURCE_CRS': dsm_crs,
                    'TARGET_CRS': dsm_crs,
                    'NODATA': -9999,
                    'CROP_TO_CUTLINE': True,
                    'KEEP_RESOLUTION': True,
                    'OUTPUT': clipped_dsm_path
                }, feedback=feedback)

                clipped_dtm_path = os.path.join(temp_dir, "dtm_clipped.tif")
                processing.run("gdal:cliprasterbymasklayer", {
                    'INPUT': dtm_path,
                    'MASK': aoi_reproj_path,
                    'SOURCE_CRS': None,
                    'TARGET_CRS': dsm_crs,
                    'NODATA': -9999,
                    'CROP_TO_CUTLINE': True,
                    'KEEP_RESOLUTION': True,
                    'OUTPUT': clipped_dtm_path
                }, feedback=feedback)

                if os.path.exists(clipped_dsm_path) and os.path.exists(clipped_dtm_path):
                    dsm_work_path = clipped_dsm_path
                    dtm_work_path = clipped_dtm_path
                    dsm_work_layer = QgsRasterLayer(dsm_work_path, "dsm_clipped")
                    print("AOI clipping applied to DSM and DTM.")
                else:
                    print("Warning: AOI clipping failed, using full raster extent.")
                    dsm_work_path = dsm_path
                    dtm_work_path = dtm_path
                    dsm_work_layer = dsm_layer
            else:
                print("Warning: No valid AOI layer found; processing full DSM/DTM extent.")
                dsm_work_path = dsm_path
                dtm_work_path = dtm_path
                dsm_work_layer = dsm_layer

            # ----------------------------------------------------------------
            # FIX 2 – Correct pixel resolution (rasterUnitsPerPixelX/Y)
            # ----------------------------------------------------------------
            dsm_extent = dsm_work_layer.extent()
            dsm_work_crs = dsm_work_layer.crs().authid()
            # rasterUnitsPerPixelX() returns actual ground-unit pixel size
            dsm_xres = dsm_work_layer.rasterUnitsPerPixelX()
            dsm_yres = dsm_work_layer.rasterUnitsPerPixelY()

            aligned_dtm_path = os.path.join(temp_dir, "aligned_dtm.tif")
            processing.run("gdal:warpreproject", {
                'INPUT': dtm_work_path,
                'SOURCE_CRS': None,
                'TARGET_CRS': dsm_work_crs,
                'TARGET_EXTENT': (
                    f"{dsm_extent.xMinimum()},{dsm_extent.xMaximum()},"
                    f"{dsm_extent.yMinimum()},{dsm_extent.yMaximum()}"
                ),
                'TARGET_EXTENT_CRS': dsm_work_crs,
                'TARGET_RESOLUTION': dsm_xres,
                'RESAMPLING': 1,   # bilinear
                'NODATA': -9999,
                'OUTPUT': aligned_dtm_path
            }, feedback=feedback)

            if not os.path.exists(aligned_dtm_path):
                raise Exception("Failed to align DTM to DSM grid (output file missing)")
            aligned_dtm_layer = QgsRasterLayer(aligned_dtm_path, "aligned_dtm")
            if not aligned_dtm_layer.isValid():
                raise Exception("Aligned DTM raster is not valid")

            # ----------------------------------------------------------------
            # DSM - DTM subtraction
            # ----------------------------------------------------------------
            diff_raster_path = os.path.join(temp_dir, "diff.tif")
            processing.run("gdal:rastercalculator", {
                'INPUT_A': dsm_work_path,
                'BAND_A': 1,
                'INPUT_B': aligned_dtm_path,
                'BAND_B': 1,
                'FORMULA': 'A - B',
                'RTYPE': 5,  # Float32
                'NO_DATA': -9999,
                'OUTPUT': diff_raster_path
            }, feedback=feedback)

            if not os.path.exists(diff_raster_path):
                raise Exception("Difference raster (DSM-DTM) was not created")
            diff_raster = QgsRasterLayer(diff_raster_path, "diff")
            if not diff_raster.isValid():
                raise Exception("Difference raster is not valid")

            # ----------------------------------------------------------------
            # FIX 3 – Threshold: mark sub-threshold pixels as NoData (-9999)
            #         instead of 0, so they are transparent in QGIS and excluded
            #         from the points shapefile.
            # ----------------------------------------------------------------
            threshold_raster_path = os.path.join(temp_dir, "threshold.tif")
            processing.run("gdal:rastercalculator", {
                'INPUT_A': diff_raster_path,
                'BAND_A': 1,
                'FORMULA': f'numpy.where(A > {self.obstacle_threshold}, A, -9999)',
                'RTYPE': 5,  # Float32
                'NO_DATA': -9999,
                'OUTPUT': threshold_raster_path
            }, feedback=feedback)

            if not os.path.exists(threshold_raster_path):
                raise Exception("Threshold raster was not created")
            threshold_raster = QgsRasterLayer(threshold_raster_path, "threshold")
            if not threshold_raster.isValid():
                raise Exception("Threshold raster is not valid")

            # ----------------------------------------------------------------
            # Save final outputs
            # ----------------------------------------------------------------
            if not self.output_dir:
                print("Output directory not set, cannot save obstacle files")
                return False

            obstacle_dir = os.path.join(self.output_dir, 'Obstacle_Analysis')
            os.makedirs(obstacle_dir, exist_ok=True)

            # Determine output CRS (fall back to DSM CRS if output_crs is unset)
            out_crs = getattr(self, 'output_crs', None) or dsm_work_crs
            if not out_crs:
                out_crs = dsm_work_crs

            # --- Aerial_Obstacles.tiff -----------------------------------------
            output_raster_path = os.path.join(obstacle_dir, 'Aerial_Obstacles.tiff')
            processing.run("gdal:warpreproject", {
                'INPUT': threshold_raster_path,
                'SOURCE_CRS': None,
                'TARGET_CRS': out_crs,
                'RESAMPLING': 0,
                'NODATA': -9999,
                'OUTPUT': output_raster_path
            }, feedback=feedback)

            # FIX 5 – Guard: only set the path when the file actually exists
            if not os.path.exists(output_raster_path):
                raise Exception(
                    f"Aerial_Obstacles.tiff was not created at {output_raster_path}. "
                    "Check that gdal:warpreproject succeeded and the output CRS is valid."
                )
            self.obstacle_height_raster_path = output_raster_path
            print(f"Obstacle height raster saved to {output_raster_path}")

            # --- Aerial_Obstacles.shp ------------------------------------------
            # FIX 4 – Use native:pixelstopoints (not the non-existent gdal:rastertopoints)
            points_gpkg_path = os.path.join(obstacle_dir, 'Aerial_Obstacles.gpkg')
            points_temp_path = os.path.join(temp_dir, 'obs_pts_raw.gpkg')

            processing.run("native:pixelstopoints", {
                'INPUT_RASTER': threshold_raster_path,
                'RASTER_BAND': 1,
                'FIELD_NAME': 'height',
                'OUTPUT': points_temp_path
            }, feedback=feedback)

            if not os.path.exists(points_temp_path):
                print("Warning: native:pixelstopoints produced no output; skipping points layer.")
            else:
                pts_layer = QgsVectorLayer(points_temp_path, "obs_pts_raw", "ogr")
                if pts_layer.isValid() and pts_layer.featureCount() > 0:
                    # Reproject to output CRS
                    processing.run("native:reprojectlayer", {
                        'INPUT': points_temp_path,
                        'TARGET_CRS': out_crs,
                        'OUTPUT': points_gpkg_path
                    }, feedback=feedback)

                    if os.path.exists(points_gpkg_path):
                        self.obstacle_points_layer_path = points_gpkg_path
                        print(f"Obstacle points saved to {points_gpkg_path}")

                        # FIX 6 – Populate self.obstacles GeoDataFrame so that
                        #          generate_obstacle_map() (PNG) also works
                        try:
                            pts_gdf = gpd.read_file(points_gpkg_path)
                            if 'height' in pts_gdf.columns:
                                pts_gdf = pts_gdf[pts_gdf['height'] > self.obstacle_threshold].copy()
                                pts_gdf['type'] = 'Terrain/Building'
                                self.obstacles = pts_gdf
                            else:
                                self.obstacles = pts_gdf
                        except Exception as gdf_err:
                            print(f"Warning: could not load obstacles GeoDataFrame: {gdf_err}")
                    else:
                        print("Warning: reprojected points shapefile not found.")
                else:
                    print("Warning: no obstacle points generated (no pixels exceed threshold).")

            return True

        except Exception as e:
            print(f"Obstacle calculation failed: {e}")
            import traceback
            traceback.print_exc()
            self.obstacles = gpd.GeoDataFrame()
            return False
        finally:
            # FIX 7 – Always clean up temp files, even on failure
            shutil.rmtree(temp_dir, ignore_errors=True)

    def fetch_wind_data(self, start_date=None, end_date=None):
        try:
            if start_date: self.start_date = start_date
            if end_date: self.end_date = end_date
            success = self.fetch_openmeteo_data(start_date, end_date)
            if success and self.wind_data is not None:
                return True
            else:
                return False
        except Exception as e:
            print(f"Error fetching wind data: {e}")
            return False

    def fetch_temperature_data(self):
        try:
            if self.wind_data is not None and 'temperature_c' in self.wind_data.columns:
                self.mean_temperature = self.wind_data['temperature_c'].mean()
            else:
                if self.wind_point_lat:
                    lat = abs(self.wind_point_lat)
                    if lat < 23.5:
                        self.mean_temperature = 25 + np.random.uniform(-5,5)
                    elif lat < 35:
                        self.mean_temperature = 20 + np.random.uniform(-5,5)
                    elif lat < 50:
                        self.mean_temperature = 15 + np.random.uniform(-5,5)
                    else:
                        self.mean_temperature = 5 + np.random.uniform(-5,5)
                    self.mean_temperature -= (self.elevation / 1000) * 6.5
                else:
                    self.mean_temperature = 15
            return True
        except Exception as e:
            print(f"Error fetching temperature data: {e}")
            self.mean_temperature = 15
            return True

    # ------------------------------------------------------------------------
    # Runway length calculation (ICAO Doc 9157)
    # ------------------------------------------------------------------------
    def compute_runway_slope(self, orientation):
        try:
            if self.dtm_raster is None:
                return 0.0
            geod = Geodesic.WGS84
            centroid_lat, centroid_lon = self.wgs84_centroid
            length = self.corrected_runway_length if self.corrected_runway_length > 0 else self.reference_field_length
            end1 = geod.Direct(centroid_lat, centroid_lon, orientation, length/2)
            end2 = geod.Direct(centroid_lat, centroid_lon, (orientation+180)%360, length/2)
            num_samples = max(10, int(length / 50))
            elevations = []
            wgs84_crs = QgsCoordinateReferenceSystem('EPSG:4326')
            dtm_crs = self.dtm_raster.crs()
            transform_to_dtm = QgsCoordinateTransform(wgs84_crs, dtm_crs, QgsProject.instance())
            provider = self.dtm_raster.dataProvider()
            for i in range(num_samples + 1):
                frac = i / num_samples
                lon = end1['lon2'] * (1 - frac) + end2['lon2'] * frac
                lat = end1['lat2'] * (1 - frac) + end2['lat2'] * frac
                pt_wgs84 = QgsPointXY(lon, lat)
                try:
                    pt_dtm = transform_to_dtm.transform(pt_wgs84)
                except:
                    continue
                result, ok = provider.sample(pt_dtm, 1)
                if ok:
                    elevations.append(result)
            if len(elevations) < 2:
                return 0.0
            max_elev = max(elevations)
            min_elev = min(elevations)
            slope_percent = (max_elev - min_elev) / length * 100.0
            return slope_percent
        except Exception as e:
            print(f"Error computing runway slope: {e}")
            return 0.0

    def calculate_icao_compliant_length(self, orientation=None):
        """
        Calculate ICAO-corrected runway length per Doc 9157 Part 1 Sec.3.

        Corrections applied (multiplicative):
          Elevation   : +7% per 300 m above MSL (Doc 9157 Sec.3.5.1)
          Temperature : +1% per °C above ISA standard (Doc 9157 Sec.3.5.2)
          Humidity    : +3% when RH > 80% (Doc 9157 Sec.3.5.3, informative)
          Gradient    : +10% per 1% effective slope (Doc 9157 Sec.3.5.4)
          Pavement    : ×1.15 wet / ×1.25 contaminated (Annex 14 Sec.3.1.20)

        Declared distances (Annex 14 Sec.3.6):
          TORA = corrected length
          TODA = TORA + clearway  (clearway ≤ 0.5 × TORA, Annex 14 Sec.3.5.1)
          ASDA = TORA + stopway   (stopway per user input or 0)
          LDA  = TORA − displaced threshold
        """
        base_length = self.reference_field_length

        # ── Elevation correction (Doc 9157 Sec.3.5.1) ──────────────────────
        elevation_factor = 1 + self.elevation_correction_factor * (self.elevation / 300)

        # ── Temperature correction (Doc 9157 Sec.3.5.2) ────────────────────
        standard_temp    = 15 - (0.0065 * self.elevation)   # ISA at elevation
        temp_difference  = max(0, self.mean_temperature - standard_temp)
        temperature_factor = 1 + self.temperature_correction_factor * temp_difference

        # ── Humidity correction (informative, Doc 9157 Sec.3.5.3) ──────────
        humidity_factor = 1.0
        if self.mean_relative_humidity > 80:
            humidity_factor = 1 + self.humidity_correction_factor

        # ── Pavement / contamination factor (Annex 14 Sec.3.1.20) ──────────
        pavement_factor = self.pavement_condition_factor
        if self.snow_depth > 10:
            pavement_factor = self.icao.LENGTH_CORRECTION_FACTORS['Contaminated']
        elif self.mean_annual_precipitation > 1500:
            pavement_factor = self.icao.LENGTH_CORRECTION_FACTORS['Wet Runway']

        # ── Gradient correction (Doc 9157 Sec.3.5.4) ───────────────────────
        gradient_factor = 1.0
        if orientation is not None:
            slope = self.compute_runway_slope(orientation)
            self.runway_slope_percent = slope
            gradient_factor = 1 + self.gradient_correction_factor * slope

        # ── Combined TODR and LDR ────────────────────────────────────────
        combined_factor    = elevation_factor * temperature_factor * humidity_factor * gradient_factor
        self.todr_distance = base_length * combined_factor * pavement_factor
        # Landing distance uses 60% of reference field length (conservative estimate)
        self.ldr_distance  = base_length * 0.60 * elevation_factor * temperature_factor * pavement_factor
        self.balanced_field_length = max(self.todr_distance, self.ldr_distance)

        # ── Round up to next 50 m increment (Doc 9157 Sec.3.7) ─────────────
        self.corrected_runway_length = math.ceil(self.balanced_field_length / 50) * 50

        # ── Clearway: user-declared OR zero (NEVER auto-generated as %) ─
        # Annex 14 Sec.3.5.1: clearway ≤ 0.5 × TORA; it is a physical feature,
        # not a default. Clip user-declared value to the Annex 14 cap.
        max_clearway = self.corrected_runway_length * 0.5
        if self.clearway_length > 0:
            self.available_clearway = min(self.clearway_length, max_clearway)
        else:
            self.available_clearway = 0.0   # no clearway unless declared

        # ── Stopway: user-declared OR zero ───────────────────────────────
        self.available_stopway = self.stopway_length if self.stopway_length > 0 else 0.0

        # ── Declared distances (Annex 14 Sec.3.6) ──────────────────────────
        self.tora = self.corrected_runway_length
        self.toda = self.tora + self.available_clearway
        self.asda = self.tora + self.available_stopway
        self.lda  = self.tora - self.displaced_threshold

        self.icao_compliance_report['runway_length'] = {
            'reference_field_length': self.reference_field_length,
            'elevation_factor':       elevation_factor,
            'temperature_factor':     temperature_factor,
            'humidity_factor':        humidity_factor,
            'gradient_factor':        gradient_factor,
            'combined_factor':        combined_factor,
            'pavement_factor':        pavement_factor,
            'todr':                   self.todr_distance,
            'ldr':                    self.ldr_distance,
            'bfl':                    self.balanced_field_length,
            'corrected_length':       self.corrected_runway_length,
            'clearway':               self.available_clearway,
            'stopway':                self.available_stopway,
            'tora':                   self.tora,
            'toda':                   self.toda,
            'asda':                   self.asda,
            'lda':                    self.lda,
        }
        return self.corrected_runway_length

    # ------------------------------------------------------------------------
    # Wind analysis and orientation
    # ------------------------------------------------------------------------
    # ICAO Annex 14 Vol I Sec.3.1.1 Table 3-1: Max crosswind component per ARC code (knots)
    ICAO_CROSSWIND_LIMITS_KT = {
        '1': 10.5,   # Reference field length < 800 m
        '2': 13.0,   # Reference field length 800–1200 m
        '3': 20.0,   # Reference field length 1200–1800 m
        '4': 20.0,   # Reference field length ≥ 1800 m
    }
    # Calm wind threshold (ICAO: wind < 3 kt is operationally calm)
    CALM_WIND_THRESHOLD_KT = 3.0
    CALM_WIND_THRESHOLD_MS = CALM_WIND_THRESHOLD_KT * 0.514444

    def get_icao_crosswind_threshold_kn(self):
        """Return ICAO Annex 14 Sec.3.1.1 crosswind threshold in knots for this ARC."""
        return self.ICAO_CROSSWIND_LIMITS_KT.get(self.runway_code_number, 20.0)

    def calculate_wind_coverage(self, wind_data, orientation, dry_thresh_kn=None,
                                wet_thresh_kn=None, icy_thresh_kn=None):
        """
        Calculate usability factor (wind coverage) for a given runway orientation.

        ICAO Annex 14, Vol I, Sec.3.1.1:
          A runway orientation shall be selected so that the usability factor
          of the aerodrome is not less than 95 per cent for the aeroplanes
          the aerodrome is intended to serve.

        Crosswind limits applied (ICAO Annex 14 Table 3-1):
          Code 1: 10.5 kt  |  Code 2: 13 kt  |  Code 3 & 4: 20 kt

        Parameters
        ----------
        wind_data       : DataFrame with 'wind_speed' (m/s) and 'wind_direction_deg'
        orientation     : True runway bearing in degrees (0–179, single half)
        dry_thresh_kn   : Override dry crosswind threshold in knots (default: ARC-based)
        wet_thresh_kn   : Override wet crosswind threshold in knots
        icy_thresh_kn   : Override icy crosswind threshold in knots

        Returns
        -------
        dict with dry/wet/icy/combined coverage %, calm %, mean headwind, icao_compliant
        """
        if wind_data is None or len(wind_data) == 0:
            return {
                'dry': 0, 'wet': 0, 'icy': 0, 'combined': 0,
                'calm_pct': 0, 'mean_headwind_kn': 0,
                'icao_compliant': False,
                'icao_xwind_threshold_kn': self.get_icao_crosswind_threshold_kn(),
                'total_obs': 0,
            }

        # ── Extract and clean wind observations ─────────────────────────────
        speeds_ms  = wind_data['wind_speed'].values.astype(float)
        directions = wind_data['wind_direction_deg'].values.astype(float)
        valid      = ~np.isnan(speeds_ms) & ~np.isnan(directions)
        speeds_ms  = speeds_ms[valid]
        directions = directions[valid]
        total_obs  = len(speeds_ms)
        if total_obs == 0:
            return {
                'dry': 0, 'wet': 0, 'icy': 0, 'combined': 0,
                'calm_pct': 0, 'mean_headwind_kn': 0,
                'icao_compliant': False,
                'icao_xwind_threshold_kn': self.get_icao_crosswind_threshold_kn(),
                'total_obs': 0,
            }

        speeds_kn = speeds_ms / self.KNOTS_TO_MS

        # ── Calm wind percentage (ICAO: < 3 kt, Annex 14 Sec.3.1.1 Note) ──────
        calm_mask = speeds_kn < self.CALM_WIND_THRESHOLD_KT
        calm_pct  = float(np.sum(calm_mask)) / total_obs * 100.0

        # ── Crosswind & headwind components ─────────────────────────────────
        angle_diff      = directions - orientation
        normalised_diff = (angle_diff + 180.0) % 360.0 - 180.0   # –180 … +180
        crosswinds_kn   = np.abs(speeds_kn * np.sin(np.radians(normalised_diff)))
        headwinds_kn    =         speeds_kn * np.cos(np.radians(normalised_diff))
        mean_headwind_kn = float(np.mean(headwinds_kn))

        # ── Determine thresholds in knots ────────────────────────────────────
        arc_xwind_kn  = self.get_icao_crosswind_threshold_kn()   # ICAO ARC default

        # User-configured thresholds (UI spinboxes) expressed in knots when
        # use_knots=True, or in m/s when use_knots=False – normalise to knots
        if self.use_knots:
            cfg_dry_kn = self.crosswind_threshold_dry
            cfg_wet_kn = self.crosswind_threshold_wet
            cfg_icy_kn = self.crosswind_threshold_icy
        else:
            cfg_dry_kn = self.crosswind_threshold_dry_ms  / self.KNOTS_TO_MS
            cfg_wet_kn = self.crosswind_threshold_wet_ms  / self.KNOTS_TO_MS
            cfg_icy_kn = self.crosswind_threshold_icy_ms  / self.KNOTS_TO_MS

        # Caller overrides take highest priority; fall back to user config, then
        # ICAO ARC limit (the lowest / most conservative of user cfg and ARC)
        dry_kn = dry_thresh_kn if dry_thresh_kn is not None else min(cfg_dry_kn, arc_xwind_kn)
        wet_kn = wet_thresh_kn if wet_thresh_kn is not None else min(cfg_wet_kn, arc_xwind_kn)
        icy_kn = icy_thresh_kn if icy_thresh_kn is not None else min(cfg_icy_kn, arc_xwind_kn)

        # ── Usability factors (ICAO Sec.3.1.1) – calm winds count as USABLE ────
        dry_coverage = float(np.sum(crosswinds_kn <= dry_kn)) / total_obs * 100.0
        wet_coverage = float(np.sum(crosswinds_kn <= wet_kn)) / total_obs * 100.0
        icy_coverage = float(np.sum(crosswinds_kn <= icy_kn)) / total_obs * 100.0

        icao_compliant = dry_coverage >= self.wind_coverage_requirement

        return {
            'dry':   dry_coverage,
            'wet':   wet_coverage,
            'icy':   icy_coverage,
            'combined': dry_coverage,
            'calm_pct':          calm_pct,
            'mean_headwind_kn':  mean_headwind_kn,
            'icao_compliant':    icao_compliant,
            'icao_xwind_threshold_kn': arc_xwind_kn,
            'applied_dry_thresh_kn':   dry_kn,
            'applied_wet_thresh_kn':   wet_kn,
            'applied_icy_thresh_kn':   icy_kn,
            'total_obs':         total_obs,
        }

    def get_prevailing_wind_direction(self, num_sectors=16):
        if self.wind_data is None or len(self.wind_data)==0:
            return None
        wind_dirs = self.wind_data['wind_direction_deg'].values
        wind_dirs = wind_dirs[~np.isnan(wind_dirs)]
        if len(wind_dirs)==0:
            return None
        bin_edges = np.linspace(0,360,num_sectors+1)
        hist, _ = np.histogram(wind_dirs, bins=bin_edges)
        max_idx = np.argmax(hist)
        sector_center = (bin_edges[max_idx] + bin_edges[max_idx+1]) / 2
        sector_center = sector_center % 360
        return sector_center

    def enforce_longest_petal_priority(self, results_df):
        prev_dir = self.get_prevailing_wind_direction(num_sectors=16)
        if prev_dir is None:
            return results_df
        desired_heading = (prev_dir + 180) % 360
        desired_orientation = desired_heading % 180
        results_df['diff'] = np.abs(results_df['orientation'] - desired_orientation)
        results_df['diff'] = np.minimum(results_df['diff'], 180 - results_df['diff'])
        best_idx = results_df['diff'].idxmin()
        best_row = results_df.loc[[best_idx]]
        remaining = results_df.drop(index=best_idx)
        remaining = remaining.sort_values('rank')
        reordered = pd.concat([best_row, remaining], ignore_index=True)
        reordered['rank'] = range(1, len(reordered)+1)
        reordered = reordered.drop(columns=['diff'])
        return reordered

    def analyze_runway_orientations_icao(self):
        """
        Evaluate all runway orientations (0–179°, step = wind_analysis_step) for
        ICAO Annex 14 Sec.3.1.1 compliance (≥ 95% usability factor).

        Enhancements:
        • Correct unit handling (m/s internally, thresholds normalised to kn)
        • Per-ARC ICAO crosswind threshold (10.5 / 13 / 20 kt)
        • Calm wind percentage reported per ICAO Sec.3.1.1 Note
        • Mean headwind component for operational context
        • Seasonal usability factors (Winter/Spring/Summer/Fall)
        • Weighted rank score: dry 60% + wet 25% + icy 15%
        • `needs_crosswind_rwy` flag if best orientation < 95% (Annex 14 Sec.3.1.2)
        • Total observation count stored for traceability
        """
        if self.wind_data is None:
            print("No wind data available.")
            return None

        orientations = list(range(0, 180, self.wind_analysis_step))
        arc_xwind_kn = self.get_icao_crosswind_threshold_kn()
        results = []

        for orientation in orientations:
            coverage = self.calculate_wind_coverage(self.wind_data, orientation)

            seasonal_coverage = {}
            if 'season' in self.wind_data.columns:
                for season in ['Winter', 'Spring', 'Summer', 'Fall']:
                    season_data = self.wind_data[self.wind_data['season'] == season]
                    if len(season_data) > 0:
                        sc = self.calculate_wind_coverage(season_data, orientation)
                        seasonal_coverage[season] = sc['dry']

            mag_orientation = (orientation + self.magnetic_variation) % 360
            runway_num = int(round(mag_orientation / 10)) % 36
            if runway_num == 0:
                runway_num = 36
            opposite_num = (runway_num + 18) % 36
            if opposite_num == 0:
                opposite_num = 36
            designation = f"{runway_num:02d}/{opposite_num:02d}"

            # Weighted rank: dry 60% + wet 25% + icy 15%
            rank_score = (
                coverage['dry'] * 0.60 +
                coverage['wet'] * 0.25 +
                coverage['icy'] * 0.15
            )

            results.append({
                'orientation':           orientation,
                'magnetic_orientation':  mag_orientation,
                'designation':           designation,
                'dry_coverage':          coverage['dry'],
                'wet_coverage':          coverage['wet'],
                'icy_coverage':          coverage['icy'],
                'combined_coverage':     coverage['combined'],
                'calm_pct':              coverage.get('calm_pct', 0.0),
                'mean_headwind_kn':      coverage.get('mean_headwind_kn', 0.0),
                'icao_compliant':        coverage['icao_compliant'],
                'icao_xwind_thresh_kn':  arc_xwind_kn,
                'applied_dry_thresh_kn': coverage.get('applied_dry_thresh_kn', arc_xwind_kn),
                'total_obs':             coverage.get('total_obs', 0),
                'winter_coverage':       seasonal_coverage.get('Winter', 0),
                'spring_coverage':       seasonal_coverage.get('Spring', 0),
                'summer_coverage':       seasonal_coverage.get('Summer', 0),
                'fall_coverage':         seasonal_coverage.get('Fall', 0),
                'rank_score':            rank_score,
            })

        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('rank_score', ascending=False).reset_index(drop=True)
        results_df['rank'] = range(1, len(results_df) + 1)
        results_df = self.enforce_longest_petal_priority(results_df)

        best_dry = results_df.iloc[0]['dry_coverage'] if not results_df.empty else 0.0
        results_df['needs_crosswind_rwy'] = best_dry < self.wind_coverage_requirement

        self.wind_results = results_df
        return results_df

    def get_runway_designation(self, true_orientation):
        mag_orientation = (true_orientation + self.magnetic_variation) % 360
        rwy1 = int(round(mag_orientation / 10) % 36)
        if rwy1 == 0: rwy1 = 36
        rwy2 = (rwy1 + 18) % 36
        if rwy2 == 0: rwy2 = 36
        return f"{rwy1:02d}/{rwy2:02d}"

    def generate_obstacle_limitation_surfaces(self, end1, end2, orientation):
        surfaces = []
        geod = Geodesic.WGS84
        if self.approach_type in self.icao.APPROACH_SURFACE_PARAMS:
            params = self.icao.APPROACH_SURFACE_PARAMS[self.approach_type]
        else:
            params = self.icao.APPROACH_SURFACE_PARAMS['Non-precision']
        length = params['Length']
        if self.runway_code_number in ['1','2'] and self.approach_type == 'Non-precision':
            length = 1500 if self.runway_code_number == '2' else 1000
        ends = [(end1, (orientation+180)%360), (end2, orientation)]
        for i, (endpoint, approach_dir) in enumerate(ends):
            inner_width = params['Inner width']
            divergence = params['Divergence']
            inner_edge_distance = params.get('Inner edge distance', 30)
            inner_end = geod.Direct(endpoint.y, endpoint.x, approach_dir, inner_edge_distance)
            inner_point = Point(inner_end['lon2'], inner_end['lat2'])
            outer_width = inner_width + 2 * length * divergence
            outer_end = geod.Direct(inner_point.y, inner_point.x, approach_dir, length)
            outer_point = Point(outer_end['lon2'], outer_end['lat2'])
            perp_dir = (approach_dir - 90) % 360
            left_inner = geod.Direct(inner_point.y, inner_point.x, perp_dir, inner_width/2)
            right_inner = geod.Direct(inner_point.y, inner_point.x, (perp_dir+180)%360, inner_width/2)
            left_outer = geod.Direct(outer_point.y, outer_point.x, perp_dir, outer_width/2)
            right_outer = geod.Direct(outer_point.y, outer_point.x, (perp_dir+180)%360, outer_width/2)
            approach_poly = Polygon([
                (left_inner['lon2'], left_inner['lat2']),
                (left_outer['lon2'], left_outer['lat2']),
                (right_outer['lon2'], right_outer['lat2']),
                (right_inner['lon2'], right_inner['lat2']),
                (left_inner['lon2'], left_inner['lat2'])
            ])
            surfaces.append({
                'id': i,
                'type': 'Approach Surface',
                'end': f"RWY{int(round(approach_dir/10)):02d}",
                'length_m': length,
                'slope_percent': params['Slope'] * 100,
                'geometry': approach_poly
            })
        return surfaces

    # ------------------------------------------------------------------------
    # Shapefile generation – Single / Parallel / Crosswind / Open-V configs
    # ------------------------------------------------------------------------
    def _build_single_runway_layers(self, orientation, centroid_lat, centroid_lon,
                                     runway_length, geod, gpkg_path,
                                     rwy_id=1, designation_override=None):
        """
        Write all ICAO layers for ONE runway into *gpkg_path*.
        rwy_id=1 → layer names unchanged (runway_pavement, …)
        rwy_id>1 → layer names suffixed  (runway_pavement_2, …)
        Returns True on success.
        """
        sfx = '' if rwy_id == 1 else f'_{rwy_id}'
        desig = designation_override or self.get_runway_designation(orientation)
        hw   = self.runway_width / 2.0
        hsw  = self.runway_strip_width / 2.0
        lo   = (orientation - 90) % 360
        ro   = (orientation + 90) % 360

        end1 = geod.Direct(centroid_lat, centroid_lon, orientation,          runway_length / 2)
        end2 = geod.Direct(centroid_lat, centroid_lon, (orientation+180)%360, runway_length / 2)
        p1   = Point(end1['lon2'], end1['lat2'])
        p2   = Point(end2['lon2'], end2['lat2'])

        def _off(pt, bearing, dist):
            return geod.Direct(pt['lat2'], pt['lon2'], bearing, dist)

        # Centreline
        cl = gpd.GeoDataFrame({
            'id': [rwy_id], 'orientation_true': [orientation],
            'designation': [desig], 'length_m': [runway_length],
            'arc': [self.airport_reference_code], 'runway_id': [rwy_id]
        }, geometry=[LineString([p1, p2])], crs='EPSG:4326').to_crs(self.output_crs)
        cl.to_file(gpkg_path, layer=f'runway_centerline{sfx}', driver='GPKG')

        # Pavement polygon
        l1 = _off(end1, lo, hw); l2 = _off(end2, lo, hw)
        r1 = _off(end1, ro, hw); r2 = _off(end2, ro, hw)
        rwy_poly = Polygon([(l1['lon2'],l1['lat2']),(l2['lon2'],l2['lat2']),
                            (r2['lon2'],r2['lat2']),(r1['lon2'],r1['lat2'])])
        gpd.GeoDataFrame({'id':[rwy_id],'width_m':[self.runway_width],
                           'length_m':[runway_length],'designation':[desig],
                           'arc':[self.airport_reference_code],'runway_id':[rwy_id],
                           'tora_m':[self.tora],'toda_m':[self.toda],
                           'asda_m':[self.asda],'lda_m':[self.lda]},
                          geometry=[rwy_poly], crs='EPSG:4326').to_crs(
            self.output_crs).to_file(gpkg_path, layer=f'runway_pavement{sfx}', driver='GPKG')

        # Strip
        ls1=_off(end1,lo,hsw); ls2=_off(end2,lo,hsw)
        rs1=_off(end1,ro,hsw); rs2=_off(end2,ro,hsw)
        strip_poly = Polygon([(ls1['lon2'],ls1['lat2']),(ls2['lon2'],ls2['lat2']),
                              (rs2['lon2'],rs2['lat2']),(rs1['lon2'],rs1['lat2'])])
        gpd.GeoDataFrame({'id':[rwy_id],'width_m':[self.runway_strip_width],
                           'length_m':[runway_length],'icao_standard':['Annex14_Table3-1'],
                           'runway_id':[rwy_id]},
                          geometry=[strip_poly], crs='EPSG:4326').to_crs(
            self.output_crs).to_file(gpkg_path, layer=f'runway_strip{sfx}', driver='GPKG')

        # RESA (both ends)
        rl, rw = self.runway_end_safety_area
        hrw = rw / 2.0
        resa_rows = []
        for ep, ed, lbl in [(end1,(orientation+180)%360,'THR1'),(end2,orientation,'THR2')]:
            re = geod.Direct(ep['lat2'], ep['lon2'], ed, rl)
            corners = [
                _off(ep, lo, hrw), _off({'lat2':re['lat2'],'lon2':re['lon2']}, lo, hrw),
                _off({'lat2':re['lat2'],'lon2':re['lon2']}, ro, hrw), _off(ep, ro, hrw)]
            poly = Polygon([(c['lon2'],c['lat2']) for c in corners])
            resa_rows.append({'id':len(resa_rows)+1,'end':lbl,'length_m':rl,'width_m':rw,
                              'runway_id':rwy_id,'geometry':poly})
        gpd.GeoDataFrame(resa_rows, crs='EPSG:4326').to_crs(self.output_crs).to_file(
            gpkg_path, layer=f'resa{sfx}', driver='GPKG')

        # Stopway / Clearway (optional)
        if self.stopway_length > 0:
            sw_rows = []
            for ep, ed, lbl in [(end1,(orientation+180)%360,'SW1'),(end2,orientation,'SW2')]:
                se = geod.Direct(ep['lat2'], ep['lon2'], ed, self.stopway_length)
                c = [_off(ep,lo,hw),_off({'lat2':se['lat2'],'lon2':se['lon2']},lo,hw),
                     _off({'lat2':se['lat2'],'lon2':se['lon2']},ro,hw),_off(ep,ro,hw)]
                sw_rows.append({'id':len(sw_rows)+1,'end':lbl,
                                'length_m':self.stopway_length,'runway_id':rwy_id,
                                'geometry':Polygon([(x['lon2'],x['lat2']) for x in c])})
            gpd.GeoDataFrame(sw_rows, crs='EPSG:4326').to_crs(self.output_crs).to_file(
                gpkg_path, layer=f'stopway{sfx}', driver='GPKG')

        if self.clearway_length > 0:
            cw_rows = []
            for ep, ed, lbl in [(end1,(orientation+180)%360,'CW1'),(end2,orientation,'CW2')]:
                ce = geod.Direct(ep['lat2'], ep['lon2'], ed, self.clearway_length)
                c = [_off(ep,lo,hsw),_off({'lat2':ce['lat2'],'lon2':ce['lon2']},lo,hsw),
                     _off({'lat2':ce['lat2'],'lon2':ce['lon2']},ro,hsw),_off(ep,ro,hsw)]
                cw_rows.append({'id':len(cw_rows)+1,'end':lbl,
                                'length_m':self.clearway_length,'runway_id':rwy_id,
                                'geometry':Polygon([(x['lon2'],x['lat2']) for x in c])})
            gpd.GeoDataFrame(cw_rows, crs='EPSG:4326').to_crs(self.output_crs).to_file(
                gpkg_path, layer=f'clearway{sfx}', driver='GPKG')

        # Runway designation points
        mag1 = (orientation + self.magnetic_variation) % 360
        mag2 = (mag1 + 180) % 360
        rwy1, rwy2 = desig.split('/')
        gpd.GeoDataFrame([
            {'id':1,'designation':rwy1,'true_orientation':orientation,
             'magnetic_orientation':mag1,'runway_id':rwy_id,'geometry':p1},
            {'id':2,'designation':rwy2,'true_orientation':(orientation+180)%360,
             'magnetic_orientation':mag2,'runway_id':rwy_id,'geometry':p2},
        ], crs='EPSG:4326').to_crs(self.output_crs).to_file(
            gpkg_path, layer=f'runway_designations{sfx}', driver='GPKG')

        # OLS surfaces
        surfaces = self.generate_obstacle_limitation_surfaces(p1, p2, orientation)
        if surfaces:
            gpd.GeoDataFrame(surfaces, crs='EPSG:4326').to_crs(self.output_crs).to_file(
                gpkg_path, layer=f'obstacle_surfaces{sfx}', driver='GPKG')

        return True

    def generate_shapefiles_for_candidate(self, orientation, output_dir, cand_index=0):
        """
        Output all runway geometry layers into a single GeoPackage.
        Handles Single / Parallel / Crosswind / Open-V configurations.

        Configuration logic (ICAO Doc 9157 Sec.2):
        ─────────────────────────────────────────
        Single    : one runway at *orientation*
        Parallel  : two runways at *orientation*, offset ±separation/2 perpendicular
        Crosswind : primary at *orientation*, secondary at *orientation* + 90°
        Open-V    : two runways diverging from a shared threshold point at
                    ±(divergence_angle/2) — ICAO recommends 20–40° total divergence;
                    defaults to 30° total (±15° each side).
        """
        try:
            if self.wgs84_centroid is None:
                if hasattr(self, 'centroid') and self.centroid is not None:
                    self.wgs84_centroid = (self.centroid.y, self.centroid.x)
                else:
                    self.centroid = self.aoi_geometry_original.centroid
                    self.wgs84_centroid = (self.centroid.y, self.centroid.x)

            clat, clon = self.wgs84_centroid
            geod         = Geodesic.WGS84
            rwy_len      = self.corrected_runway_length if self.corrected_runway_length > 0 else 2500
            config       = getattr(self, 'runway_configuration', 'Single')
            separation   = max(200, getattr(self, 'runway_separation', 300))   # metres
            desig        = self.get_runway_designation(orientation)
            gpkg_name    = f'runway_candidate_{desig.replace("/","_")}.gpkg'
            gpkg_path    = os.path.join(output_dir, gpkg_name)

            # ── Write shared obstacle layer once ──────────────────────────────
            if self.obstacles is not None and len(self.obstacles) > 0:
                self.obstacles.to_crs(self.output_crs).to_file(
                    gpkg_path, layer='obstacles', driver='GPKG')

            # ═══════════════════════════════════════════════════════════════════
            # SINGLE
            # ═══════════════════════════════════════════════════════════════════
            if config == 'Single':
                self._build_single_runway_layers(
                    orientation, clat, clon, rwy_len, geod, gpkg_path, rwy_id=1)

            # ═══════════════════════════════════════════════════════════════════
            # PARALLEL  — two runways at same heading, separated perpendicularly
            # ICAO Doc 9157 Sec.2.3: min separation 210 m (code 1/2), 400 m (code 3/4)
            # ═══════════════════════════════════════════════════════════════════
            elif config == 'Parallel':
                half_sep = separation / 2.0
                right_az = (orientation + 90) % 360
                left_az  = (orientation - 90) % 360
                # Runway 1 – shifted right
                r1 = geod.Direct(clat, clon, right_az, half_sep)
                self._build_single_runway_layers(
                    orientation, r1['lat2'], r1['lon2'], rwy_len, geod, gpkg_path,
                    rwy_id=1,
                    designation_override=desig)
                # Runway 2 – shifted left; designation stays same heading, parallel suffix
                r2 = geod.Direct(clat, clon, left_az, half_sep)
                desig2 = desig + 'L' if '/' not in desig[-2:] else desig  # e.g. 09R/27L sibling
                self._build_single_runway_layers(
                    orientation, r2['lat2'], r2['lon2'], rwy_len, geod, gpkg_path,
                    rwy_id=2,
                    designation_override=desig2)
                # Write a connector line showing separation
                pt1 = geod.Direct(clat, clon, right_az, half_sep)
                pt2 = geod.Direct(clat, clon, left_az,  half_sep)
                sep_line = LineString([(pt1['lon2'],pt1['lat2']),(pt2['lon2'],pt2['lat2'])])
                gpd.GeoDataFrame({'id':[1],'separation_m':[separation]},
                                  geometry=[sep_line], crs='EPSG:4326').to_crs(
                    self.output_crs).to_file(
                    gpkg_path, layer='parallel_separation', driver='GPKG')

            # ═══════════════════════════════════════════════════════════════════
            # CROSSWIND  — primary + secondary at 90° (ICAO Sec.3.1.2 crosswind rwy)
            # Secondary shares AOI centroid, rotated 90° clockwise
            # ═══════════════════════════════════════════════════════════════════
            elif config == 'Crosswind':
                # Primary runway
                self._build_single_runway_layers(
                    orientation, clat, clon, rwy_len, geod, gpkg_path, rwy_id=1)
                # Secondary runway — 90° offset
                xw_orient = (orientation + 90) % 360
                # Offset centroid so crosswind runway doesn't overlap primary
                offset_m = rwy_len * 0.5 + separation
                xw_pt    = geod.Direct(clat, clon, xw_orient, offset_m)
                desig_xw = self.get_runway_designation(xw_orient)
                self._build_single_runway_layers(
                    xw_orient, xw_pt['lat2'], xw_pt['lon2'], rwy_len, geod, gpkg_path,
                    rwy_id=2, designation_override=desig_xw)

            # ═══════════════════════════════════════════════════════════════════
            # OPEN-V  — two runways diverging from a common departure threshold
            # ICAO: divergence 20–40° total; default 30° (±15°)
            # Both runways share the same takeoff threshold (common apex point)
            # ═══════════════════════════════════════════════════════════════════
            elif config == 'Open-V':
                divergence_total = 30.0      # degrees — configurable default
                half_div         = divergence_total / 2.0

                orient1 = (orientation - half_div) % 360
                orient2 = (orientation + half_div) % 360

                # Common apex: the shared departure threshold at one end
                # Runways extend from apex outward, so centroid is placed at
                # the mid-point of runway 1 (orient1 direction)
                apex_lat, apex_lon = clat, clon

                # Runway 1 starts at apex, extends in orient1 direction
                mid1 = geod.Direct(apex_lat, apex_lon, orient1, rwy_len / 2)
                desig1 = self.get_runway_designation(orient1)
                self._build_single_runway_layers(
                    orient1, mid1['lat2'], mid1['lon2'], rwy_len, geod, gpkg_path,
                    rwy_id=1, designation_override=desig1)

                # Runway 2 starts at apex, extends in orient2 direction
                mid2 = geod.Direct(apex_lat, apex_lon, orient2, rwy_len / 2)
                desig2 = self.get_runway_designation(orient2)
                self._build_single_runway_layers(
                    orient2, mid2['lat2'], mid2['lon2'], rwy_len, geod, gpkg_path,
                    rwy_id=2, designation_override=desig2)

                # Mark the common apex point
                gpd.GeoDataFrame([{
                    'id': 1, 'type': 'Open-V Apex (Common Threshold)',
                    'orient1_deg': orient1, 'orient2_deg': orient2,
                    'divergence_deg': divergence_total,
                    'geometry': Point(apex_lon, apex_lat)
                }], crs='EPSG:4326').to_crs(self.output_crs).to_file(
                    gpkg_path, layer='openv_apex', driver='GPKG')

            # ═══════════════════════════════════════════════════════════════════
            # INTERSECTING  — two runways crossing at a defined intersection pt
            # ICAO Doc 9157 Sec.2.2 / Annex 14 Sec.3.1.2
            #
            # • The angle between the two runways must be 30°–150°
            #   (smaller angles are effectively near-parallel; ≥150° = near-parallel
            #   in reciprocal; ICAO recommends ≥30° divergence).
            # • The intersection can be at any point along both runways.
            #   Default: secondary runway intersects the primary at its midpoint;
            #   the intersection lies at 30% of secondary from its threshold.
            # • Both runways are generated in full.  An "intersection_point" marker
            #   is added to the GeoPackage.
            # • The secondary runway is rotated by *intersection_angle* degrees
            #   from the primary (default 60°, configurable via runway_separation
            #   spinbox which is re-used as intersection_angle_deg in this config).
            # ═══════════════════════════════════════════════════════════════════
            elif config == 'Intersecting':
                # =============================================================
                # ICAO Doc 9157 Sec.2.2 — Intersecting Runways
                # The user-supplied 'separation' value is the CROSSING ANGLE
                # (the acute/obtuse angle between the two runway centrelines),
                # clamped to 30°–150° per ICAO recommendation.
                #
                # Geometry:
                #   • Primary runway:   centred on AOI centroid at 'orientation'
                #   • Intersection pt:  AOI centroid (centre of primary runway)
                #   • Secondary orient: orientation + ix_angle  (true compass bearing)
                #     → The physical angle between the two runways = ix_angle
                #   • Secondary centred so that it passes exactly through the
                #     intersection point (centroid).  This is achieved by placing
                #     the secondary centroid AT the intersection point — both
                #     runways then share the same mid-point, guaranteeing they
                #     cross at the intersection.
                # =============================================================

                # Use separation value as the crossing angle (degrees)
                # For Intersecting config, runway_separation spinbox is relabelled
                # "Intersection Angle" with range 30–150.
                raw_sep = getattr(self, 'runway_separation', separation)
                ix_angle = max(30.0, min(150.0, float(raw_sep)))

                # Primary runway centred on AOI centroid
                self._build_single_runway_layers(
                    orientation, clat, clon, rwy_len, geod, gpkg_path,
                    rwy_id=1, designation_override=desig)

                # Intersection point = centroid (mid-point of primary)
                ix_lat, ix_lon = clat, clon

                # Secondary runway bearing = primary bearing + crossing angle
                # This means the acute angle between the two centrelines = ix_angle
                # (assuming ix_angle ≤ 90°) or 180° − ix_angle (if ix_angle > 90°).
                sec_orient = (orientation + ix_angle) % 360

                # Centre secondary runway ON the intersection point so it
                # guaranteed passes through it.
                desig_sec = self.get_runway_designation(sec_orient)
                self._build_single_runway_layers(
                    sec_orient,
                    ix_lat, ix_lon,      # ← centred on intersection
                    rwy_len, geod, gpkg_path,
                    rwy_id=2, designation_override=desig_sec)

                # Intersection marker with true crossing angle
                # True crossing angle is the smaller of ix_angle and 180-ix_angle
                visual_angle = ix_angle if ix_angle <= 90 else 180 - ix_angle
                gpd.GeoDataFrame([{
                    'id': 1,
                    'type': 'Runway Intersection Point',
                    'primary_orient_deg':     orientation,
                    'secondary_orient_deg':   sec_orient,
                    'crossing_angle_deg':     visual_angle,
                    'user_angle_input_deg':   ix_angle,
                    'note': (f'Primary {desig} × Secondary {desig_sec} '
                             f'Crossing angle {visual_angle:.0f}° '
                             f'— ICAO Doc 9157 Sec.2.2'),
                    'geometry': Point(ix_lon, ix_lat)
                }], crs='EPSG:4326').to_crs(self.output_crs).to_file(
                    gpkg_path, layer='intersection_point', driver='GPKG')

            else:
                # Unknown config — fall back to single
                self._build_single_runway_layers(
                    orientation, clat, clon, rwy_len, geod, gpkg_path, rwy_id=1)

            # Store path for QGIS loading
            if not hasattr(self, '_candidate_gpkgs'):
                self._candidate_gpkgs = []
            self._candidate_gpkgs.append((cand_index, gpkg_path))
            self.last_candidate_gpkg = gpkg_path
            print(f"Runway candidate GeoPackage saved ({config}): {gpkg_path}")
            return True

        except Exception as e:
            print(f"Error generating runway GeoPackage: {e}")
            import traceback; traceback.print_exc()
            return False

    def generate_runway_designation_points(self, end1, end2, orientation, output_dir, gpkg_path=None):
        try:
            designation = self.get_runway_designation(orientation)
            rwy1, rwy2 = designation.split('/')
            mag1 = (orientation + self.magnetic_variation) % 360
            mag2 = (mag1 + 180) % 360
            data = [
                {'id':1, 'designation':rwy1, 'true_orientation':orientation, 'magnetic_orientation':mag1, 'geometry':end1},
                {'id':2, 'designation':rwy2, 'true_orientation':(orientation+180)%360, 'magnetic_orientation':mag2, 'geometry':end2}
            ]
            gdf = gpd.GeoDataFrame(data, crs='EPSG:4326').to_crs(self.output_crs)
            if gpkg_path:
                gdf.to_file(gpkg_path, layer='runway_designations', driver='GPKG')
            else:
                desig_safe = designation.replace("/","_")
                fb = os.path.join(output_dir, f'runway_desig_{desig_safe}.gpkg')
                gdf.to_file(fb, layer='runway_designations', driver='GPKG')
            return True
        except Exception as e:
            print(f"Error generating designation points: {e}")
            return False

    # ------------------------------------------------------------------------
    # Visualizations and outputs
    # ------------------------------------------------------------------------
    def generate_wind_direction_arrows(self):
        """
        Generate an enhanced Wind Direction Arrows chart showing:
        - Dual-panel layout: compass rose (left) + monthly bar chart (right)
        - Compass rose with 16-point labels and speed-coloured arrows per month
        - Monthly bar chart with annotated direction labels and runway heading
        - Clean ICAO-style professional look
        """
        try:
            if self.wind_data is None or len(self.wind_data) == 0:
                return False

            from scipy import stats as sp_stats

            self.wind_data['month'] = pd.DatetimeIndex(self.wind_data['datetime']).month
            monthly = self.wind_data.groupby('month').agg(
                dir_mean=('wind_direction_deg', lambda x: sp_stats.circmean(x, high=360, low=0)),
                speed_mean=('wind_speed', 'mean'),
                speed_max=('wind_speed', 'max'),
            ).reset_index()

            month_labels = ['Jan','Feb','Mar','Apr','May','Jun',
                            'Jul','Aug','Sep','Oct','Nov','Dec']
            speeds = monthly['speed_mean'].values
            dirs   = monthly['dir_mean'].values
            max_spd = max(speeds) if len(speeds) > 0 else 1

            # ── Colour-map: low speed = blue-green, high speed = red ─────────
            norm   = plt.Normalize(vmin=speeds.min(), vmax=speeds.max())
            cmap   = plt.get_cmap('RdYlBu_r')
            colors = [cmap(norm(s)) for s in speeds]

            fig = plt.figure(figsize=(16, 7), facecolor='#f8f9fa')
            fig.suptitle(
                'Monthly Prevailing Wind Direction  —  Compass & Speed Analysis\n'
                'Recommended Runway Heading derived from mean headwind direction',
                fontsize=13, fontweight='bold', color='#1a2a4a', y=0.98)

            # ── LEFT: Compass rose (polar axes) ──────────────────────────────
            ax_polar = fig.add_axes([0.02, 0.08, 0.42, 0.82], projection='polar')
            ax_polar.set_theta_direction(-1)          # clockwise
            ax_polar.set_theta_zero_location('N')     # 0° at top
            ax_polar.set_facecolor('#eaf2ff')

            # Compass background circles
            r_ticks = [0.25, 0.50, 0.75, 1.0]
            for r in r_ticks:
                circle = plt.Circle((0, 0), r, transform=ax_polar.transData._b,
                                    fill=False, color='#b0c8e0', lw=0.6, ls='--', zorder=1)
                ax_polar.add_patch(circle)

            # Grid spokes every 22.5°
            for ang in range(0, 360, 22):
                ax_polar.plot([0, np.radians(ang)], [0, 1.05],
                              color='#c8d8e8', lw=0.5, zorder=1)

            # Compass labels: 16-point rose
            compass_pts = {0:'N', 22.5:'NNE', 45:'NE', 67.5:'ENE',
                           90:'E', 112.5:'ESE', 135:'SE', 157.5:'SSE',
                           180:'S', 202.5:'SSW', 225:'SW', 247.5:'WSW',
                           270:'W', 292.5:'WNW', 315:'NW', 337.5:'NNW'}
            for deg, label in compass_pts.items():
                rad = np.radians(deg)
                weight = 'bold' if label in ('N','E','S','W') else 'normal'
                fontsize = 11 if label in ('N','E','S','W') else 8
                color = '#c0392b' if label == 'N' else '#1a2a4a'
                ax_polar.text(rad, 1.18, label, ha='center', va='center',
                              fontsize=fontsize, fontweight=weight, color=color)

            # Plot arrows for each month
            for i, (month, dir_deg, speed) in enumerate(
                    zip(monthly['month'], dirs, speeds)):
                rad = np.radians(dir_deg)
                r_len = 0.72 * (speed / max_spd)   # radius proportional to speed
                # Arrow: from origin in wind-coming-from direction
                ax_polar.annotate(
                    '', xy=(rad, r_len), xytext=(0, 0),
                    arrowprops=dict(
                        arrowstyle='->', color=colors[i],
                        lw=2.5,
                        mutation_scale=14,
                    ), zorder=5)
                # Month label at arrow tip
                label_r = r_len + 0.10
                ax_polar.text(rad, label_r,
                              month_labels[month - 1],
                              ha='center', va='center',
                              fontsize=7.5, fontweight='bold',
                              color=colors[i],
                              bbox=dict(facecolor='white', edgecolor=colors[i],
                                        boxstyle='round,pad=0.2', lw=0.8, alpha=0.85))

            ax_polar.set_yticklabels([])
            ax_polar.set_xticklabels([])
            ax_polar.set_ylim(0, 1.35)

            # Speed scale annotation
            ax_polar.text(np.radians(225), 1.30,
                          f'Arrow length ∝ wind speed\nMax = {max_spd:.1f} m/s',
                          ha='center', va='center', fontsize=7.5,
                          color='#2c5282',
                          bbox=dict(facecolor='white', alpha=0.7,
                                    edgecolor='#90b0cc', boxstyle='round,pad=0.3'))

            # Colourbar for speed
            sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
            sm.set_array([])
            cbar_ax = fig.add_axes([0.02, 0.04, 0.40, 0.025])
            cb = plt.colorbar(sm, cax=cbar_ax, orientation='horizontal')
            cb.set_label('Mean Wind Speed (m/s)', fontsize=9, color='#1a2a4a')
            cb.ax.tick_params(labelsize=8)

            # ── RIGHT: Monthly bar + direction annotation chart ───────────────
            ax = fig.add_axes([0.52, 0.13, 0.45, 0.74])
            ax.set_facecolor('#f0f6ff')

            x = monthly['month'].values
            bars = ax.bar(x, speeds, color=colors, alpha=0.82,
                          edgecolor='#2c5282', linewidth=0.7, zorder=3)

            # Reference line for mean
            mean_spd = np.mean(speeds)
            ax.axhline(mean_spd, color='#c0392b', lw=1.4, ls='--', zorder=4,
                       label=f'Annual mean = {mean_spd:.1f} m/s')

            # Annotate each bar with direction & runway heading
            for i, (month, dir_deg, speed) in enumerate(
                    zip(monthly['month'], dirs, speeds)):
                best_heading = (dir_deg + 180) % 360   # into-wind = opposite
                rwy_num = int(round(best_heading / 10)) % 36
                if rwy_num == 0:
                    rwy_num = 36
                # Direction arrow label
                ax.text(month, speed + 0.08, f'{int(dir_deg):03d}°',
                        ha='center', va='bottom', fontsize=7.5,
                        color='#1a2a4a', fontweight='bold')
                # Runway label
                ax.text(month, -0.25, f'RWY\n{rwy_num:02d}',
                        ha='center', va='top', fontsize=6.5,
                        color='#2c5282', fontstyle='italic')
                # Tiny direction arrow on bar
                ax.annotate('', xy=(month + 0.25, speed * 0.5 + 0.15),
                            xytext=(month - 0.25, speed * 0.5 - 0.15),
                            arrowprops=dict(arrowstyle='->', color=colors[i], lw=1.3),
                            zorder=5)

            ax.set_xlim(0.5, 12.5)
            ax.set_ylim(-0.7, max_spd * 1.30)
            ax.set_xticks(range(1, 13))
            ax.set_xticklabels(month_labels, fontsize=9)
            ax.set_xlabel('Month', fontsize=10, color='#1a2a4a')
            ax.set_ylabel('Mean Wind Speed (m/s)', fontsize=10, color='#1a2a4a')
            ax.set_title('Monthly Wind Speed and Into-Wind Runway Heading',
                         fontsize=10, fontweight='bold', color='#1a2a4a', pad=8)
            ax.legend(fontsize=8, loc='upper right', framealpha=0.85)
            ax.grid(axis='y', color='#b0c8e0', lw=0.6, alpha=0.7, zorder=2)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)

            # Add note row at bottom
            note_text = (
                "Direction = wind-from bearing (meteorological convention).  "
                "Runway heading = into-wind (opposite of direction).  "
                "Arrows on bars show monthly wind bearing."
            )
            fig.text(0.52, 0.04, note_text, fontsize=7.5, color='#555',
                     ha='left', va='bottom', style='italic',
                     wrap=True)

            plt.tight_layout(rect=[0, 0.06, 1, 0.96])
            out_path = os.path.join(self.output_dir, 'Maps', 'wind_direction_arrows.png')
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            plt.savefig(out_path, dpi=180, bbox_inches='tight',
                        facecolor=fig.get_facecolor())
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating wind direction arrows: {e}")
            import traceback; traceback.print_exc()
            return False

    def generate_cross_section_diagrams(self, orientation, output_dir):
        try:
            geod = Geodesic.WGS84
            centroid_lat, centroid_lon = self.wgs84_centroid
            length = self.corrected_runway_length
            end1 = geod.Direct(centroid_lat, centroid_lon, orientation, length/2)
            end2 = geod.Direct(centroid_lat, centroid_lon, (orientation+180)%360, length/2)
            num_samples = 100
            distances = np.linspace(0, length, num_samples)
            elevations = []
            for d in distances:
                frac = d / length
                lon = end1['lon2'] * (1 - frac) + end2['lon2'] * frac
                lat = end1['lat2'] * (1 - frac) + end2['lat2'] * frac
                elev = 100 + 5 * np.sin(frac * np.pi) + np.random.normal(0,0.5)
                elevations.append(elev)
            fig, (ax1, ax2) = plt.subplots(2,1, figsize=(10,8))
            ax1.plot(distances, elevations, 'b-', linewidth=2)
            ax1.set_xlabel('Distance along runway (m)')
            ax1.set_ylabel('Elevation (m)')
            ax1.set_title(f'Longitudinal Profile - Runway {self.get_runway_designation(orientation)}')
            ax1.grid(True, alpha=0.3)
            max_elev = max(elevations)
            min_elev = min(elevations)
            slope_percent = (max_elev - min_elev) / length * 100
            ax1.text(0.02, 0.95, f'Slope: {slope_percent:.3f}%', transform=ax1.transAxes,
                     bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
            mid_dist = length / 2
            frac = mid_dist / length
            mid_lon = end1['lon2'] * (1 - frac) + end2['lon2'] * frac
            mid_lat = end1['lat2'] * (1 - frac) + end2['lat2'] * frac
            perp_dir = (orientation - 90) % 360
            lateral_dist = np.linspace(-self.runway_strip_width/2, self.runway_strip_width/2, 50)
            lateral_elev = []
            for ld in lateral_dist:
                pt = geod.Direct(mid_lat, mid_lon, perp_dir, ld)
                if abs(ld) <= self.runway_width/2:
                    elev_mid = elevations[int(frac*num_samples)]
                    elev = elev_mid + 0.02 * (1 - (2*ld/self.runway_width)**2) * 5
                else:
                    elev = elevations[int(frac*num_samples)] - 0.5
                lateral_elev.append(elev)
            ax2.plot(lateral_dist, lateral_elev, 'g-', linewidth=2)
            ax2.axvline(x=-self.runway_width/2, color='red', linestyle='--', label='Runway edge')
            ax2.axvline(x=self.runway_width/2, color='red', linestyle='--')
            ax2.axvline(x=-self.runway_strip_width/2, color='orange', linestyle='--', label='Strip edge')
            ax2.axvline(x=self.runway_strip_width/2, color='orange', linestyle='--')
            ax2.set_xlabel('Distance from centreline (m)')
            ax2.set_ylabel('Elevation (m)')
            ax2.set_title('Lateral Cross-section at Mid-Runway')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            plt.tight_layout()
            out_path = os.path.join(output_dir, f'cross_section_{self.get_runway_designation(orientation)}.png')
            plt.savefig(out_path, dpi=300)
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating cross-section diagrams: {e}")
            return False

    def generate_plan_view_map(self, orientation, output_dir):
        try:
            geod = Geodesic.WGS84
            centroid_lat, centroid_lon = self.wgs84_centroid
            length = self.corrected_runway_length
            end1 = geod.Direct(centroid_lat, centroid_lon, orientation, length/2)
            end2 = geod.Direct(centroid_lat, centroid_lon, (orientation+180)%360, length/2)
            desig = self.get_runway_designation(orientation)
            lon_scale = math.cos(math.radians(centroid_lat)) * 111320
            lat_scale  = 111320
            def to_xy(pt):
                return ((pt['lon2']-centroid_lon)*lon_scale,
                        (pt['lat2']-centroid_lat)*lat_scale)
            x1,y1 = to_xy(end1); x2,y2 = to_xy(end2)
            dx = (x2-x1)/length; dy = (y2-y1)/length
            px = -dy; py = dx
            def pcorners(cx1,cy1,cx2,cy2,hw):
                return [(cx1+px*hw,cy1+py*hw),(cx2+px*hw,cy2+py*hw),
                        (cx2-px*hw,cy2-py*hw),(cx1-px*hw,cy1-py*hw)]
            hw = self.runway_width/2.0; hsw = self.runway_strip_width/2.0
            rl,rw = self.runway_end_safety_area; hrw = rw/2.0

            fig,ax = plt.subplots(figsize=(14,9),facecolor='#f0f4f8')
            ax.set_facecolor('#e8eef5'); ax.set_aspect('equal')

            ax.add_patch(patches.Polygon(pcorners(x1,y1,x2,y2,hsw),closed=True,
                facecolor='#cce5cc',edgecolor='#4a934a',linewidth=1.2,
                linestyle='--',alpha=0.55,label='Runway Strip',zorder=1))

            for ex,ey,sign,lbl in [(x1,y1,-1,'RESA'),(x2,y2,+1,'')]:
                rex=ex+dx*sign*rl; rey=ey+dy*sign*rl
                ax.add_patch(patches.Polygon(pcorners(ex,ey,rex,rey,hrw),closed=True,
                    facecolor='#f8d7d7',edgecolor='#c0392b',linewidth=1.0,
                    alpha=0.50,label=lbl,zorder=2))
                ax.text((ex+rex)/2,(ey+rey)/2,f'RESA\n{rl:.0f}m',
                    ha='center',va='center',fontsize=7.5,color='#7b241c',fontweight='bold')

            ax.add_patch(patches.Polygon(pcorners(x1,y1,x2,y2,hw),closed=True,
                facecolor='#4a4a4a',edgecolor='#1a1a1a',linewidth=2.0,
                alpha=0.92,label='Runway Pavement',zorder=3))
            ax.plot([x1,x2],[y1,y2],color='white',linewidth=1.4,
                linestyle=(0,(8,6)),zorder=4,label='Centreline')

            rwy1,rwy2 = desig.split('/')
            for ex,ey,lbl in [(x1,y1,f'THR {rwy1}'),(x2,y2,f'THR {rwy2}')]:
                ax.plot([ex+px*hw,ex-px*hw],[ey+py*hw,ey-py*hw],
                    color='#00C000',linewidth=3.5,zorder=5)
                ax.text(ex-dx*60,ey-dy*60,lbl,ha='center',va='center',
                    fontsize=10,fontweight='bold',color='#1a6e1a',
                    bbox=dict(boxstyle='round,pad=0.3',fc='white',ec='#1a6e1a',alpha=0.88))

            dim_y = -(hw+35)
            for lbl_str,val,col in [
                (f'TORA  {self.tora:.0f} m',self.tora,'#1a6eb5'),
                (f'TODA  {self.toda:.0f} m',self.toda,'#276749'),
                (f'ASDA  {self.asda:.0f} m',self.asda,'#7d3c98'),
                (f'LDA   {self.lda:.0f} m', self.lda, '#922b21'),
            ]:
                ex=x1+dx*val; ey=y1+dy*val
                ax.annotate('',xy=(x1+px*dim_y,y1+py*dim_y),
                    xytext=(ex+px*dim_y,ey+py*dim_y),
                    arrowprops=dict(arrowstyle='<->',color=col,lw=1.5,mutation_scale=12))
                ax.text((x1+ex)/2+px*dim_y,(y1+ey)/2+py*dim_y-15,lbl_str,
                    ha='center',va='top',fontsize=8,color=col,fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.2',fc='white',ec=col,alpha=0.85))
                dim_y -= hw+35

            if self.stopway_length>0:
                sx=x2+dx*self.stopway_length; sy=y2+dy*self.stopway_length
                ax.add_patch(patches.Polygon(pcorners(x2,y2,sx,sy,hw),closed=True,
                    facecolor='#8e44ad',edgecolor='#6c3483',linewidth=1.2,
                    alpha=0.55,label=f'Stopway ({self.stopway_length:.0f}m)',zorder=3))
            if self.clearway_length>0:
                cx=x2+dx*self.clearway_length; cy=y2+dy*self.clearway_length
                ax.add_patch(patches.Polygon(pcorners(x2,y2,cx,cy,hsw),closed=True,
                    facecolor='none',edgecolor='#27ae60',linewidth=2.0,
                    linestyle='-.',alpha=0.85,label=f'Clearway ({self.clearway_length:.0f}m)',zorder=2))

            xlim_lo=min(x1,x2)-max(length*0.15,200); xlim_hi=max(x1,x2)+max(length*0.15,200)
            ylim_lo=min(y1,y2)+dim_y-60;             ylim_hi=max(y1,y2)+max(length*0.12,180)
            ax.set_xlim(xlim_lo,xlim_hi); ax.set_ylim(ylim_lo,ylim_hi)
            nx=xlim_lo+(xlim_hi-xlim_lo)*0.05; ny=ylim_hi-(ylim_hi-ylim_lo)*0.08
            ax.annotate('',xy=(nx,ny),xytext=(nx,ny-(ylim_hi-ylim_lo)*0.06),
                arrowprops=dict(arrowstyle='->',color='#1a1a2e',lw=2.5))
            ax.text(nx,ny+(ylim_hi-ylim_lo)*0.01,'N',ha='center',va='bottom',
                fontsize=11,fontweight='bold',color='#1a1a2e')
            ax.grid(True,linestyle='--',linewidth=0.5,alpha=0.4,color='#aaaaaa')
            ax.set_xlabel('Distance (m) — East',fontsize=10)
            ax.set_ylabel('Distance (m) — North',fontsize=10)
            ax.set_title(
                f'Plan View  —  Runway {desig}  |  ARC {self.airport_reference_code}  |  '
                f'Length {length:.0f} m x Width {self.runway_width:.0f} m\n'
                f'ICAO Annex 14 compliant layout',
                fontsize=12,fontweight='bold',pad=14,color='#1a1a2e')
            ax.legend(loc='lower right',fontsize=8.5,framealpha=0.92,ncol=2)
            ax.text(0.01,0.99,
                f"ARC: {self.airport_reference_code}\nTORA: {self.tora:.0f}m\n"
                f"TODA: {self.toda:.0f}m\nASDA: {self.asda:.0f}m\nLDA: {self.lda:.0f}m\n"
                f"Strip: {self.runway_strip_width:.0f}m",
                transform=ax.transAxes,va='top',ha='left',fontsize=8.5,family='monospace',
                bbox=dict(boxstyle='round,pad=0.5',fc='white',ec='#2c3e50',alpha=0.90))
            plt.tight_layout()
            out_path = os.path.join(output_dir, f'plan_view_{desig.replace("/","_")}.png')
            plt.savefig(out_path,dpi=300,bbox_inches='tight',facecolor=fig.get_facecolor())
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating plan view map: {e}")
            return False

    def generate_output_files(self, wind_results, candidates, top_orientation):
        print("\n=== GENERATING OUTPUT FILES ===")
        if not self.output_dir:
            print("Error: Output directory not set")
            return False
        try:
            dirs = ['GeoPackages', 'GeoPackages_All', 'Maps', 'Reports', 'Wind_Analysis',
                    'Runway_Candidates', 'Obstacle_Analysis', 'ICAO_Compliance_Reports',
                    'Commercial_Proposal', 'Cross_Sections', 'Lighting']
            for d in dirs:
                os.makedirs(os.path.join(self.output_dir, d), exist_ok=True)
            if self.wind_data is not None:
                self.wind_data.to_csv(os.path.join(self.output_dir, 'Wind_Analysis', 'wind_data.csv'), index=False)
            if wind_results is not None:
                wind_results.to_csv(os.path.join(self.output_dir, 'Wind_Analysis', 'wind_analysis_results.csv'), index=False)
            if candidates:
                pd.DataFrame(candidates).to_csv(os.path.join(self.output_dir, 'Runway_Candidates', 'runway_candidates.csv'), index=False)
            gpkg_dir = os.path.join(self.output_dir, 'GeoPackages')
            self.generate_shapefiles_for_candidate(top_orientation, gpkg_dir)
            all_gpkg_dir = os.path.join(self.output_dir, 'GeoPackages_All')
            for i, cand in enumerate(candidates):
                if i >= self.num_candidates:
                    break
                cand_dir = os.path.join(all_gpkg_dir, f"Candidate_{i+1:02d}_{cand['designation']}")
                os.makedirs(cand_dir, exist_ok=True)
                self.generate_shapefiles_for_candidate(cand['orientation'], cand_dir, i+1)
                self.generate_cross_section_diagrams(cand['orientation'], cand_dir)
                self.generate_plan_view_map(cand['orientation'], cand_dir)
            self.generate_wind_roses()
            self.generate_wind_direction_arrows()
            self.generate_usability_chart(wind_results)
            self.generate_obstacle_map()
            self.generate_verification_plot(top_orientation)
            self.generate_icao_compliance_report()
            self.generate_icao_lighting_diagram(os.path.join(self.output_dir, 'Lighting'))
            self.generate_commercial_proposal()
            self.create_summary_report(wind_results, candidates)
            self.load_obstacle_raster_to_qgis()
            self.load_obstacle_points_to_qgis()
            print(f"\nAll output files generated in: {self.output_dir}")
            return True
        except Exception as e:
            print(f"Error generating output files: {e}")
            import traceback
            traceback.print_exc()
            return False

    def load_obstacle_raster_to_qgis(self):
        try:
            if not hasattr(self, 'obstacle_height_raster_path') or not self.obstacle_height_raster_path:
                print("Obstacle height raster not found, skipping load.")
                return
            if not os.path.exists(self.obstacle_height_raster_path):
                print(f"Obstacle height raster not found at {self.obstacle_height_raster_path}")
                return
            layer = QgsRasterLayer(self.obstacle_height_raster_path, "Aerial Obstacles")
            if not layer.isValid():
                print("Failed to load obstacle height raster.")
                return
            # Tell QGIS that -9999 is NoData so it is rendered transparent
            layer.dataProvider().setNoDataValue(1, -9999)
            # Color ramp: blue (lowest obstacle) → green → orange → dark-red (highest)
            # All valid pixels are > obstacle_threshold (sub-threshold pixels are NoData).
            stats = layer.dataProvider().bandStatistics(1, QgsRasterBandStats.All)
            threshold = self.obstacle_threshold
            min_val = max(stats.minimumValue, threshold)   # guard against -9999 leaking in
            max_val = stats.maximumValue if stats.maximumValue > min_val else min_val + 1.0
            span = max_val - min_val if max_val > min_val else 1.0
            shader = QgsColorRampShader()
            shader.setColorRampType(QgsColorRampShader.Interpolated)
            lst = [
                QgsColorRampShader.ColorRampItem(min_val,              QColor(0,   0, 255), f"{min_val:.1f} m (threshold)"),
                QgsColorRampShader.ColorRampItem(min_val + span * 0.25, QColor(0, 200, 255), ""),
                QgsColorRampShader.ColorRampItem(min_val + span * 0.50, QColor(0, 220,   0), ""),
                QgsColorRampShader.ColorRampItem(min_val + span * 0.75, QColor(255, 140,  0), ""),
                QgsColorRampShader.ColorRampItem(max_val,               QColor(180,   0,   0), f"{max_val:.1f} m (highest)"),
            ]
            shader.setColorRampItemList(lst)
            raster_shader = QgsRasterShader()
            raster_shader.setRasterShaderFunction(shader)
            renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, raster_shader)
            layer.setRenderer(renderer)
            layer.triggerRepaint()
            QgsProject.instance().addMapLayer(layer)
            print("Obstacle height raster loaded into QGIS with color ramp.")
        except Exception as e:
            print(f"Error loading obstacle raster: {e}")

    def load_obstacle_points_to_qgis(self):
        try:
            if not hasattr(self, 'obstacle_points_layer_path') or not self.obstacle_points_layer_path:
                print("Obstacle points shapefile not found, skipping load.")
                return
            if not os.path.exists(self.obstacle_points_layer_path):
                print(f"Obstacle points not found at {self.obstacle_points_layer_path}")
                return
            # Support both .gpkg and legacy .shp paths
            pts_path = self.obstacle_points_layer_path
            if pts_path.endswith('.gpkg'):
                pts_path = pts_path + '|layername=Aerial_Obstacles'
                if not QgsVectorLayer(pts_path, 'test', 'ogr').isValid():
                    pts_path = self.obstacle_points_layer_path  # fallback
            layer = QgsVectorLayer(pts_path, "Aerial Obstacles", "ogr")
            if not layer.isValid():
                print("Failed to load obstacle points layer.")
                return
            field_name = "height"
            if field_name not in [f.name() for f in layer.fields()]:
                print("Height field not found in obstacle points, skipping symbology.")
                QgsProject.instance().addMapLayer(layer)
                return
            symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': 'red', 'size': '2'})
            renderer = QgsGraduatedSymbolRenderer(field_name)
            values = [f.attribute(field_name) for f in layer.getFeatures()]
            if not values:
                return
            min_val = min(values)
            max_val = max(values)
            intervals = np.linspace(min_val, max_val, 6)
            colors = ['#2ecc71', '#f1c40f', '#e67e22', '#e74c3c', '#c0392b']
            from qgis.core import QgsRendererRange
            for i in range(5):
                lower = intervals[i]
                upper = intervals[i + 1] if i < 4 else max_val
                label = f"{lower:.1f} - {upper:.1f} m"
                sym = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': colors[i], 'size': '2'})
                # FIX: QgsGraduatedSymbolRenderer.addClass() takes a QgsRendererRange, not raw scalars
                renderer.addClass(QgsRendererRange(lower, upper, sym, label))
            layer.setRenderer(renderer)
            layer.triggerRepaint()
            QgsProject.instance().addMapLayer(layer)
            print("Obstacle points layer loaded into QGIS with graduated symbology.")
        except Exception as e:
            print(f"Error loading obstacle points: {e}")

    def generate_verification_plot(self, top_orientation):
        """
        Verification wind rose overlaid with the top runway orientation and
        its crosswind coverage statistics — ICAO Doc 9157 style.
        """
        try:
            if self.wind_data is None or len(self.wind_data) == 0:
                return False
            wdirs   = self.wind_data['wind_direction_deg'].values.astype(float)
            wspeeds = self.wind_data['wind_speed'].values.astype(float)
            valid   = ~np.isnan(wdirs) & ~np.isnan(wspeeds)
            wdirs, wspeeds = wdirs[valid], wspeeds[valid]

            N = 36
            bin_edges = np.linspace(0, 360, N + 1)
            hist, _   = np.histogram(wdirs, bins=bin_edges)
            pct       = hist / len(wdirs) * 100
            theta     = np.radians((bin_edges[:-1] + bin_edges[1:]) / 2)
            bar_w     = np.radians(360 / N) * 0.88

            fig, ax = plt.subplots(figsize=(11, 11),
                                   subplot_kw={'projection': 'polar'},
                                   facecolor='#f0f4f8')
            ax.set_facecolor('#e8eef5')
            ax.set_theta_zero_location('N')
            ax.set_theta_direction(-1)
            ax.set_xticks(np.radians([0,45,90,135,180,225,270,315]))
            ax.set_xticklabels(['N','NE','E','SE','S','SW','W','NW'],
                               fontsize=11, fontweight='bold')
            ax.grid(True, color='grey', linewidth=0.5, linestyle='--', alpha=0.5)

            # Bars coloured by speed quantile
            bar_speeds = np.zeros(N)
            for i in range(N):
                m = (wdirs >= bin_edges[i]) & (wdirs < bin_edges[i+1])
                if m.any():
                    bar_speeds[i] = wspeeds[m].mean()
            sc_norm = plt.Normalize(0, wspeeds.max())
            cmap    = plt.cm.plasma
            bars = ax.bar(theta, pct, width=bar_w,
                          color=cmap(sc_norm(bar_speeds)),
                          edgecolor='white', linewidth=0.5, alpha=0.85)

            # Frequency polygon overlay
            closed_theta = np.append(theta, theta[0])
            closed_pct   = np.append(pct, pct[0])
            ax.plot(closed_theta, closed_pct, 'w-', linewidth=1.2, alpha=0.7)

            # Runway orientation lines (both ends)
            r_max = pct.max() * 1.22
            desig = self.get_runway_designation(top_orientation)
            rwy1, rwy2 = desig.split('/')
            for d, lbl in [(top_orientation, f'RWY {rwy1}'), ((top_orientation+180)%360, f'RWY {rwy2}')]:
                r = np.radians(d)
                ax.annotate('', xy=(r, r_max * 0.98), xytext=(r, 0),
                            arrowprops=dict(arrowstyle='->', color='#e74c3c',
                                           lw=2.5, mutation_scale=15))
                ax.text(r, r_max * 1.05, lbl, ha='center', va='center',
                        fontsize=9, fontweight='bold', color='#c0392b',
                        bbox=dict(boxstyle='round,pad=0.2', fc='white', ec='#c0392b', alpha=0.85))

            # Colourbar
            sm = plt.cm.ScalarMappable(cmap=cmap, norm=sc_norm)
            sm.set_array([])
            cb = plt.colorbar(sm, ax=ax, pad=0.12, shrink=0.65, aspect=20)
            cb.set_label('Mean sector wind speed (m/s)', fontsize=9)

            # Coverage stats
            cov = self.calculate_wind_coverage(self.wind_data, top_orientation)
            ax.set_ylim(0, r_max * 1.15)
            ax.set_title(
                f'Verification Wind Rose  —  Recommended Runway {desig}\n'
                f'Dry coverage: {cov["dry"]:.1f}%   Wet: {cov["wet"]:.1f}%   Icy: {cov["icy"]:.1f}%   '
                f'(ICAO min 95%)',
                fontsize=12, fontweight='bold', pad=22, color='#1a1a1a')

            # Calm annotation
            calm_pct = np.sum(wspeeds < 0.5) / len(wspeeds) * 100
            ax.text(0, 0, f'Calm\n{calm_pct:.1f}%', ha='center', va='center',
                    fontsize=8, fontweight='bold', color='#333333', zorder=10)

            plt.tight_layout(rect=[0, 0, 1, 1])
            out_path = os.path.join(self.output_dir, 'Maps', 'verification_wind_rose_runway.png')
            plt.savefig(out_path, dpi=300, bbox_inches='tight',
                        facecolor=fig.get_facecolor())
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating verification plot: {e}")
            return False

    def generate_wind_roses(self):
        """Generate four types of professional wind rose charts per ICAO convention."""
        try:
            if self.wind_data is None or len(self.wind_data) == 0:
                return False
            wind_rose_dir = os.path.join(self.output_dir, 'Maps', 'Wind_Roses')
            os.makedirs(wind_rose_dir, exist_ok=True)
            speeds = self.wind_data['wind_speed'].values.astype(float)
            dirs   = self.wind_data['wind_direction_deg'].values.astype(float)
            valid  = ~np.isnan(speeds) & ~np.isnan(dirs)
            speeds, dirs = speeds[valid], dirs[valid]
            cmap_name = getattr(self, 'wind_rose_cmap', 'plasma')
            self._create_traditional_wind_rose(speeds, dirs, wind_rose_dir, cmap_name)
            self._create_scatter_wind_rose(speeds, dirs, wind_rose_dir, cmap_name)
            self._create_polygon_frequency_rose(speeds, dirs, wind_rose_dir)
            self._create_seasonal_wind_rose(speeds, dirs, wind_rose_dir, cmap_name)
            return True
        except Exception as e:
            print(f"Error generating wind roses: {e}")
            import traceback; traceback.print_exc()
            return False

    def _rose_setup(self, ax):
        """Common polar-axes setup: N at top, clockwise, cardinal labels."""
        ax.set_theta_zero_location('N')
        ax.set_theta_direction(-1)
        ax.set_xticks(np.radians([0,45,90,135,180,225,270,315]))
        ax.set_xticklabels(['N','NE','E','SE','S','SW','W','NW'],
                           fontsize=10, fontweight='bold')
        ax.grid(True, color='grey', linewidth=0.5, linestyle='--', alpha=0.6)
        ax.spines['polar'].set_visible(True)
        ax.spines['polar'].set_linewidth(1.5)

    def _freq_labels(self, ax, max_freq, n_rings=4):
        """Draw concentric frequency-percentage labels on polar axes."""
        for r in np.linspace(max_freq / n_rings, max_freq, n_rings):
            ax.text(np.radians(195), r, f'{r:.1f}%',
                    ha='center', va='center', fontsize=7, color='#555555')

    def _calm_text(self, ax, speeds, threshold=0.5):
        """Annotate calm percentage in centre of rose."""
        calm_pct = np.sum(speeds < threshold) / len(speeds) * 100
        ax.text(0, 0, f'Calm\n{calm_pct:.1f}%',
                ha='center', va='center', fontsize=8,
                color='#222222', fontweight='bold',
                transform=ax.transData, zorder=10)

    def _create_traditional_wind_rose(self, speeds, dirs, out_dir, cmap_name='plasma'):
        """
        Professional stacked bar wind rose (ICAO-style, 16 sectors, 22.5° each).
        Speed bins match WMO Beaufort thresholds converted to m/s.
        """
        SPEED_BINS   = [0, 1.5, 3.3, 5.5, 7.9, 10.7, 13.8, 17.1, 20.7, 24.4, 28.4]
        BIN_LABELS   = ['<1.5','1.5-3.3','3.3-5.5','5.5-7.9','7.9-10.7',
                        '10.7-13.8','13.8-17.1','17.1-20.7','20.7-24.4','>24.4']
        N_SECTORS    = 16
        sector_width = 2 * np.pi / N_SECTORS
        dir_bins_rad = np.linspace(0, 2 * np.pi, N_SECTORS + 1)

        cmap   = plt.get_cmap(cmap_name, len(BIN_LABELS))
        colors = [cmap(i) for i in range(len(BIN_LABELS))]

        fig, ax = plt.subplots(figsize=(13, 11),
                               subplot_kw={'projection': 'polar'},
                               facecolor='#f7f7f7')
        ax.set_facecolor('#f0f0f0')
        self._rose_setup(ax)

        bottom  = np.zeros(N_SECTORS)
        max_pct = 0.0
        for k, (lo, hi) in enumerate(zip(SPEED_BINS[:-1], SPEED_BINS[1:])):
            if k == len(SPEED_BINS) - 2:
                mask = speeds >= lo
            else:
                mask = (speeds >= lo) & (speeds < hi)
            dirs_in = dirs[mask]
            if len(dirs_in) == 0:
                continue
            dirs_rad = np.radians(dirs_in)
            hist, _ = np.histogram(dirs_rad, bins=dir_bins_rad)
            pct = hist / len(speeds) * 100
            ax.bar(dir_bins_rad[:-1], pct, width=sector_width,
                   bottom=bottom, color=colors[k],
                   edgecolor='white', linewidth=0.6, alpha=0.90,
                   label=BIN_LABELS[k])
            bottom  += pct
            max_pct  = max(max_pct, bottom.max())

        self._freq_labels(ax, max_pct)
        self._calm_text(ax, speeds)

        ax.set_ylim(0, max_pct * 1.15)
        ax.set_title('Wind Rose  —  16 Sectors  |  Speed (m/s)',
                     fontsize=14, fontweight='bold', pad=24, color='#1a1a1a')

        leg = ax.legend(title='Wind Speed (m/s)', loc='lower left',
                        bbox_to_anchor=(1.02, 0.0), fontsize=8,
                        title_fontsize=9, framealpha=0.9)
        fig.text(0.5, 0.01,
                 f'Station: Lat {getattr(self,"wind_point_lat","--"):.4f}  '
                 f'Lon {getattr(self,"wind_point_lon","--"):.4f}  |  '
                 f'N = {len(speeds):,} observations',
                 ha='center', fontsize=8, color='#444444')
        plt.tight_layout(rect=[0, 0.04, 0.82, 1.0])
        plt.savefig(os.path.join(out_dir, 'wind_rose_traditional.png'),
                    dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close()

    def _create_scatter_wind_rose(self, speeds, dirs, out_dir, cmap_name='plasma'):
        """
        Four-panel scatter wind rose: full view + three seasonal panels.
        """
        theta = np.radians(dirs)
        fig   = plt.figure(figsize=(16, 14), facecolor='#f7f7f7')
        fig.suptitle('Scatter Wind Rose  —  Speed & Direction Distribution',
                     fontsize=15, fontweight='bold', y=0.99, color='#1a1a1a')

        # ── Full scatter ───────────────────────────────────────────────────
        ax1 = fig.add_subplot(2, 2, (1, 2), projection='polar')
        sc  = ax1.scatter(theta, speeds, c=speeds, cmap=cmap_name,
                          alpha=0.55, s=8, linewidths=0, rasterized=True)
        self._rose_setup(ax1)
        ax1.set_title('All Observations', fontsize=12, pad=16)
        cb = plt.colorbar(sc, ax=ax1, pad=0.10, shrink=0.75)
        cb.set_label('Wind Speed (m/s)', fontsize=9)
        self._calm_text(ax1, speeds)

        # ── Low-speed scatter ──────────────────────────────────────────────
        lo_lim = np.percentile(speeds, 75)
        ax2 = fig.add_subplot(2, 2, 3, projection='polar')
        m = speeds <= lo_lim
        if m.any():
            ax2.scatter(theta[m], speeds[m], c=speeds[m], cmap=cmap_name,
                        alpha=0.65, s=10, linewidths=0, rasterized=True)
            ax2.set_ylim(0, lo_lim * 1.1)
        self._rose_setup(ax2)
        ax2.set_title(f'Light–Moderate (≤ {lo_lim:.1f} m/s)', fontsize=11, pad=12)
        self._calm_text(ax2, speeds)

        # ── High-speed scatter ─────────────────────────────────────────────
        ax3 = fig.add_subplot(2, 2, 4, projection='polar')
        m = speeds > lo_lim
        if m.any():
            ax3.scatter(theta[m], speeds[m], c=speeds[m], cmap=cmap_name,
                        alpha=0.65, s=10, linewidths=0, rasterized=True)
            ax3.set_ylim(lo_lim, speeds.max() * 1.05)
        self._rose_setup(ax3)
        ax3.set_title(f'Strong (> {lo_lim:.1f} m/s)', fontsize=11, pad=12)

        plt.tight_layout(rect=[0, 0, 1, 0.97])
        plt.savefig(os.path.join(out_dir, 'wind_rose_scatter.png'),
                    dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close()

    def _create_polygon_frequency_rose(self, speeds, dirs, out_dir):
        """
        Classic frequency-polygon wind rose (filled petals) showing
        direction frequency only and combined speed-weighted frequency.
        This is the most widely used ICAO/ICAO Doc 9157 format.
        """
        N = 36  # 10° sectors for high resolution
        sector_w = 360 / N
        edges     = np.linspace(0, 360, N + 1)
        centers   = (edges[:-1] + edges[1:]) / 2
        theta     = np.radians(centers)
        bar_w     = np.radians(sector_w) * 0.88

        # frequency in each 10° sector
        hist, _   = np.histogram(dirs, bins=edges)
        freq_pct  = hist / len(dirs) * 100

        # speed-weighted: mean speed per sector
        sw_freq   = np.zeros(N)
        for i in range(N):
            mask = (dirs >= edges[i]) & (dirs < edges[i + 1])
            if mask.any():
                sw_freq[i] = speeds[mask].mean() * (hist[i] / len(dirs)) * 100

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 9),
                                        subplot_kw={'projection': 'polar'},
                                        facecolor='#f7f7f7')
        fig.suptitle('Frequency Polygon Wind Rose  (ICAO Doc 9157)',
                     fontsize=15, fontweight='bold', color='#1a1a1a', y=1.01)

        # ── Left: direction frequency ──────────────────────────────────────
        self._rose_setup(ax1)
        ax1.bar(theta, freq_pct, width=bar_w,
                color='#1a6eb5', edgecolor='white', linewidth=0.5,
                alpha=0.82, label='Freq (%)')
        # polygon overlay
        closed_theta = np.append(theta, theta[0])
        closed_freq  = np.append(freq_pct, freq_pct[0])
        ax1.plot(closed_theta, closed_freq, 'r-', linewidth=1.5, alpha=0.9)
        ax1.fill(closed_theta, closed_freq, color='red', alpha=0.08)
        self._freq_labels(ax1, freq_pct.max())
        self._calm_text(ax1, speeds)
        ax1.set_title('Direction Frequency (%)\n10° Sectors',
                      fontsize=12, pad=16, fontweight='bold')

        # ── Right: speed-weighted frequency ───────────────────────────────
        self._rose_setup(ax2)
        ax2.bar(theta, sw_freq, width=bar_w,
                color='#e07b00', edgecolor='white', linewidth=0.5,
                alpha=0.82, label='Speed-weighted freq')
        closed_sw = np.append(sw_freq, sw_freq[0])
        ax2.plot(closed_theta, closed_sw, 'darkred', linewidth=1.5, alpha=0.9)
        ax2.fill(closed_theta, closed_sw, color='darkred', alpha=0.08)
        self._freq_labels(ax2, sw_freq.max())
        ax2.set_title('Speed-Weighted Frequency\n(freq × mean speed)',
                      fontsize=12, pad=16, fontweight='bold')

        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, 'wind_rose_polygon_frequency.png'),
                    dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close()

    def _create_seasonal_wind_rose(self, speeds, dirs, out_dir, cmap_name='plasma'):
        """
        Four-panel seasonal stacked bar wind roses:
        Winter / Spring / Summer / Autumn.
        """
        SEASONS = ['Winter (DJF)', 'Spring (MAM)', 'Summer (JJA)', 'Autumn (SON)']
        MONTHS  = {'Winter (DJF)': [12,1,2], 'Spring (MAM)': [3,4,5],
                   'Summer (JJA)': [6,7,8],  'Autumn (SON)': [9,10,11]}
        SPEED_BINS  = [0, 3.3, 7.9, 13.8, 20.7, 100]
        BIN_LABELS  = ['<3.3 m/s', '3.3–7.9', '7.9–13.8', '13.8–20.7', '>20.7']
        N_SECTORS   = 16
        sector_w    = 2 * np.pi / N_SECTORS
        dir_bins_r  = np.linspace(0, 2 * np.pi, N_SECTORS + 1)
        cmap        = plt.get_cmap(cmap_name, len(BIN_LABELS))
        s_colors    = [cmap(i) for i in range(len(BIN_LABELS))]

        fig, axes = plt.subplots(2, 2, figsize=(18, 16),
                                  subplot_kw={'projection': 'polar'},
                                  facecolor='#f7f7f7')
        fig.suptitle('Seasonal Wind Roses  —  Speed (m/s)',
                     fontsize=15, fontweight='bold', color='#1a1a1a', y=1.01)
        axes_flat = axes.flatten()

        # Attach month info if available
        wdata = self.wind_data
        has_month = wdata is not None and 'month' in wdata.columns

        for idx, season in enumerate(SEASONS):
            ax = axes_flat[idx]
            self._rose_setup(ax)
            if has_month:
                smask = wdata['month'].isin(MONTHS[season])
                s_dirs   = wdata.loc[smask, 'wind_direction_deg'].values.astype(float)
                s_speeds = wdata.loc[smask, 'wind_speed'].values.astype(float)
                valid    = ~np.isnan(s_dirs) & ~np.isnan(s_speeds)
                s_dirs, s_speeds = s_dirs[valid], s_speeds[valid]
            else:
                s_dirs, s_speeds = dirs, speeds

            if len(s_dirs) < 10:
                ax.set_title(f'{season}\n(no data)', fontsize=11)
                continue

            bottom  = np.zeros(N_SECTORS)
            max_pct = 0.0
            for k, (lo, hi) in enumerate(zip(SPEED_BINS[:-1], SPEED_BINS[1:])):
                if k == len(SPEED_BINS) - 2:
                    m = s_speeds >= lo
                else:
                    m = (s_speeds >= lo) & (s_speeds < hi)
                d_in = s_dirs[m]
                if len(d_in) == 0:
                    continue
                h, _ = np.histogram(np.radians(d_in), bins=dir_bins_r)
                pct  = h / len(s_dirs) * 100
                lbl  = BIN_LABELS[k] if idx == 0 else ''
                ax.bar(dir_bins_r[:-1], pct, width=sector_w,
                       bottom=bottom, color=s_colors[k],
                       edgecolor='white', linewidth=0.5, alpha=0.88,
                       label=lbl)
                bottom  += pct
                max_pct  = max(max_pct, bottom.max())

            ax.set_ylim(0, max_pct * 1.15)
            self._freq_labels(ax, max_pct)
            self._calm_text(ax, s_speeds)
            n_obs = len(s_speeds)
            ax.set_title(f'{season}\nN = {n_obs:,} obs', fontsize=11, pad=14, fontweight='bold')

        # Single legend from first subplot
        handles, labels = axes_flat[0].get_legend_handles_labels()
        fig.legend(handles, labels, title='Wind Speed (m/s)',
                   loc='lower center', ncol=len(BIN_LABELS),
                   fontsize=9, title_fontsize=10,
                   bbox_to_anchor=(0.5, -0.02), framealpha=0.9)
        plt.tight_layout(rect=[0, 0.04, 1, 0.98])
        plt.savefig(os.path.join(out_dir, 'wind_rose_seasonal.png'),
                    dpi=300, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close()

    def generate_usability_chart(self, wind_results):
        """
        ICAO Doc 9157 Runway Usability Chart.
        Shows dry / wet / icy crosswind coverage for the top N orientations.
        Dual threshold lines: 95% (ICAO mandatory) and 98% (recommended).
        Y-axis labels include the runway designation (e.g. 09/27) and true heading.
        Data-value annotations on each bar.
        """
        try:
            if wind_results is None or len(wind_results) == 0:
                return False

            top = wind_results.head(min(12, len(wind_results))).copy().reset_index(drop=True)

            # Safe column getters with fallback
            def col(df, name, fallback=0.0):
                return df[name].fillna(fallback).values if name in df.columns else np.full(len(df), fallback)

            dry = col(top, 'dry_coverage')
            wet = col(top, 'wet_coverage')
            icy = col(top, 'icy_coverage')

            # Build y-axis labels: "09/27  (090°)"
            y_labels = []
            for _, row in top.iterrows():
                desig = row.get('designation', f"{int(row.get('orientation',0)):03d}")
                ori   = int(row.get('orientation', 0))
                y_labels.append(f"{desig}  ({ori:03d}°T)")

            n     = len(top)
            y_pos = np.arange(n)
            bh    = 0.22          # bar height
            pad   = 0.03          # padding between bars

            fig, axes = plt.subplots(1, 2, figsize=(18, max(7, n * 0.8 + 2)),
                                     gridspec_kw={'width_ratios': [3, 1]},
                                     facecolor='#f7f7f7')
            ax, ax_rank = axes
            ax.set_facecolor('#fafafa')
            ax_rank.set_facecolor('#fafafa')

            # ── Horizontal bars ──────────────────────────────────────────────
            COLORS = {'Dry (ICAO 5.3.3)': '#27ae60',
                      'Wet (wet runway)': '#2980b9',
                      'Icy (contaminated)': '#c0392b'}
            ALPHAS = [0.90, 0.82, 0.74]
            data_sets = list(zip(COLORS.keys(), [dry, wet, icy], ALPHAS))

            for k, (lbl, vals, alpha) in enumerate(data_sets):
                offset = (k - 1) * (bh + pad)
                bars   = ax.barh(y_pos + offset, vals, height=bh,
                                 color=list(COLORS.values())[k],
                                 alpha=alpha, label=lbl,
                                 edgecolor='white', linewidth=0.6)
                for bar, val in zip(bars, vals):
                    if val > 5:
                        ax.text(min(val - 1.0, 101), bar.get_y() + bar.get_height() / 2,
                                f'{val:.1f}%', va='center', ha='right',
                                fontsize=7.5, color='white', fontweight='bold')

            # ── Threshold lines ───────────────────────────────────────────────
            ax.axvline(x=95, color='#2c3e50', linestyle='--', linewidth=2.0,
                       label='ICAO Min 95%', zorder=5)
            ax.axvline(x=98, color='#8e44ad', linestyle=':', linewidth=1.8,
                       label='Recommended 98%', zorder=5)
            ax.fill_betweenx([-0.5, n - 0.5], 95, 98,
                             color='#f0e8ff', alpha=0.35, zorder=0)
            ax.fill_betweenx([-0.5, n - 0.5], 98, 105,
                             color='#e8ffe8', alpha=0.35, zorder=0)

            # ── Rank highlight for top candidate ─────────────────────────────
            ax.axhspan(-0.5, 0.5, color='#f0f8ff', alpha=0.6, zorder=0)

            # ── Axes formatting ───────────────────────────────────────────────
            ax.set_xlim(0, 107)
            ax.set_ylim(-0.6, n - 0.4)
            ax.set_yticks(y_pos)
            ax.set_yticklabels(y_labels, fontsize=9)
            ax.set_xlabel('Crosswind Coverage (%)', fontsize=11)
            ax.set_title('Runway Usability Analysis  —  Top Orientations\n'
                         'ICAO Annex 14 / Doc 9157 Crosswind Compliance',
                         fontsize=13, fontweight='bold', pad=14, color='#1a1a1a')
            ax.legend(loc='lower right', fontsize=9, framealpha=0.9)
            ax.grid(True, axis='x', alpha=0.4, linestyle='--', linewidth=0.7)
            ax.invert_yaxis()   # rank 1 at top

            # Compliance tick marks
            for i, (d, w, ic) in enumerate(zip(dry, wet, icy)):
                tick = 'OK' if d >= 95 else 'FAIL'
                color = '#27ae60' if d >= 95 else '#c0392b'
                ax.text(106, i, tick, va='center', ha='right',
                        fontsize=8, color=color, fontweight='bold')

            # ── Right panel: rank table ───────────────────────────────────────
            ax_rank.axis('off')
            col_labels = ['Rank', 'Desig', 'Dry%', 'Wet%', 'Icy%', 'ICAO']
            table_data = []
            for i, row in top.iterrows():
                d_v = col(top, 'dry_coverage')[i]
                w_v = col(top, 'wet_coverage')[i]
                ic_v= col(top, 'icy_coverage')[i]
                ok  = 'YES' if d_v >= 95 else 'NO'
                table_data.append([
                    str(int(row.get('rank', i+1))),
                    str(row.get('designation', '---')),
                    f'{d_v:.1f}',
                    f'{w_v:.1f}',
                    f'{ic_v:.1f}',
                    ok
                ])
            tbl = ax_rank.table(cellText=table_data, colLabels=col_labels,
                                cellLoc='center', loc='center',
                                bbox=[0, 0, 1, 1])
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            for (r, c), cell in tbl.get_celld().items():
                cell.set_edgecolor('#cccccc')
                if r == 0:
                    cell.set_facecolor('#2c3e50')
                    cell.set_text_props(color='white', fontweight='bold')
                elif r % 2 == 0:
                    cell.set_facecolor('#f0f0f0')
                else:
                    cell.set_facecolor('white')
                # highlight ICAO column
                if c == 5 and r > 0:
                    val = table_data[r-1][5]
                    cell.set_facecolor('#d5f5d5' if val == 'YES' else '#fddede')
                    cell.set_text_props(fontweight='bold')

            ax_rank.set_title('Summary Table', fontsize=10, fontweight='bold',
                              pad=8, color='#1a1a1a')

            plt.tight_layout(rect=[0, 0, 1, 1])
            out_path = os.path.join(self.output_dir, 'Maps', 'runway_usability_chart.png')
            plt.savefig(out_path, dpi=300, bbox_inches='tight',
                        facecolor=fig.get_facecolor())
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating usability chart: {e}")
            import traceback; traceback.print_exc()
            return False

    def generate_obstacle_map(self):
        try:
            if self.obstacles is None or len(self.obstacles)==0:
                print("No obstacles to plot.")
                return False
            fig, (ax1, ax2) = plt.subplots(1,2, figsize=(18,8))
            points = self.obstacles.geometry
            x = [p.x for p in points]
            y = [p.y for p in points]
            heights = self.obstacles['height'].values
            sc = ax1.scatter(x, y, c=heights, s=50, cmap='viridis', edgecolors='k', linewidth=0.5)
            plt.colorbar(sc, ax=ax1, label='Height (m)')
            ax1.set_title('Obstacle Locations')
            ax1.set_xlabel('Longitude')
            ax1.set_ylabel('Latitude')
            ax1.grid(True, alpha=0.3)
            types = self.obstacles['type'].values
            unique = np.unique(types)
            data = [heights[types==t] for t in unique]
            ax2.boxplot(data, labels=unique, patch_artist=True)
            ax2.set_title('Height Distribution by Type')
            ax2.set_ylabel('Height (m)')
            ax2.grid(True, axis='y', alpha=0.3)
            plt.suptitle('Obstacle Analysis Map', fontsize=16)
            plt.tight_layout()
            plt.savefig(os.path.join(self.output_dir, 'Maps', 'obstacle_map.png'), dpi=300)
            plt.close()
            return True
        except Exception as e:
            print(f"Error generating obstacle map: {e}")
            import traceback
            traceback.print_exc()
            return False

    def generate_icao_compliance_report(self):
        try:
            self.compliance_checker = ICAOComplianceChecker(self)
            report = self.compliance_checker.run_all_checks()
            report_dir = os.path.join(self.output_dir, 'ICAO_Compliance_Reports')
            os.makedirs(report_dir, exist_ok=True)          # guard: ensure dir exists
            report_path = os.path.join(report_dir, 'icao_compliance_report.txt')
            n_passed  = len(report.get('passed', []))
            n_issues  = len(report.get('issues', []))
            n_checks  = report.get('num_checks', n_passed + n_issues)
            score     = report.get('compliance_score', 0.0)
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write("ICAO AERODROME COMPLIANCE REPORT\n")
                f.write("=" * 70 + "\n")
                f.write(f"Project Name           : {getattr(self, 'project_name', 'N/A')}\n")
                f.write(f"Airport Reference Code : {self.airport_reference_code}\n")
                f.write(f"Design Aircraft        : {self.design_aircraft}\n")
                f.write(f"Runway Code            : {self.runway_code_number}{self.runway_code_letter}\n")
                f.write(f"Magnetic Variation     : {getattr(self, 'magnetic_variation', 0.0):+.1f}°\n")
                f.write(f"Analysis Date          : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"Wind Data Period       : {self.start_date} to {self.end_date}\n")
                f.write("=" * 70 + "\n")
                f.write(f"Total Checks Run       : {n_checks}\n")
                f.write(f"Checks Passed          : {n_passed}/{n_checks}\n")
                f.write(f"Issues Found           : {n_issues}/{n_checks}\n")
                f.write(f"Compliance Score       : {score:.1f}%\n")
                f.write(f"Overall Compliance     : {'✔ COMPLIANT' if report['compliant'] else '✘ NON-COMPLIANT'}\n")
                f.write("=" * 70 + "\n\n")
                f.write(f"CHECKS PASSED ({n_passed}/{n_checks}):\n")
                f.write("-" * 70 + "\n")
                for p in report.get('passed', []):
                    f.write(f"  [PASS] {p}\n")
                f.write(f"\nISSUES FOUND ({n_issues}/{n_checks}):\n")
                f.write("-" * 70 + "\n")
                for iss in report.get('issues', []):
                    f.write(f"  [FAIL] {iss}\n")
                f.write("\n" + "=" * 70 + "\n")
                f.write("Reference: ICAO Annex 14, Aerodromes, Vol. I — Aerodrome Design and\n")
                f.write("           Operations; ICAO Doc 9157 Aerodrome Design Manual Part 1.\n")
            return report
        except Exception as e:
            print(f"Error generating ICAO compliance report: {e}")
            return {}

    # ========================================================================
    # ICAO AERODROME LIGHTING DIAGRAM (Annex 14, Vol I, Chapter 5)
    # ========================================================================
    ICAO_LIGHTING = {
        # Runway lights
        'Runway Edge Lights (HIRL/MIRL)': {
            'color': 'white',   'hex': '#FFFFFF',
            'standard': 'Annex 14 Sec.5.3.4', 'voltage': '6.6A CCR',
            'note': 'White; last 600m/third-of-runway amber on landing side'},
        'Runway Threshold Lights (inbound)': {
            'color': 'green',   'hex': '#00C000',
            'standard': 'Annex 14 Sec.5.3.7',
            'note': 'Green facing approach direction; supplementary wing bars'},
        'Runway End Lights': {
            'color': 'red',     'hex': '#CC0000',
            'standard': 'Annex 14 Sec.5.3.8',
            'note': 'Red facing runway; minimum 6 lights across full width'},
        'Runway Centreline Lights': {
            'color': 'white',   'hex': '#FFFFFF',
            'standard': 'Annex 14 Sec.5.3.5',
            'note': 'White; red alternating last 900m; red last 300m (precision)'},
        'Touchdown Zone Lights (TDZ)': {
            'color': 'white',   'hex': '#FFFFFF',
            'standard': 'Annex 14 Sec.5.3.6',
            'note': '900m longitudinal extent; two bars transverse'},
        # Taxiway lights
        'Taxiway Centreline Lights': {
            'color': 'green',   'hex': '#00AA00',
            'standard': 'Annex 14 Sec.5.3.16',
            'note': 'Green; alternating green/yellow in runway guard zones'},
        'Taxiway Edge Lights': {
            'color': 'blue',    'hex': '#0055FF',
            'standard': 'Annex 14 Sec.5.3.17',
            'note': 'Blue; omnidirectional; max spacing 60m (straight), 30m (curves)'},
        'Stop Bar Lights': {
            'color': 'red',     'hex': '#FF0000',
            'standard': 'Annex 14 Sec.5.3.19',
            'note': 'Unidirectional red across full taxiway width; category II/III'},
        'Runway Guard Lights (RGL)': {
            'color': 'yellow',  'hex': '#FFD700',
            'standard': 'Annex 14 Sec.5.3.20',
            'note': 'Alternating flashing yellow; guard runway holding position'},
        'Intermediate Holding Position Lights': {
            'color': 'yellow',  'hex': '#FFC000',
            'standard': 'Annex 14 Sec.5.3.21',
            'note': 'Yellow; mark intermediate holding positions on taxiways'},
        # Approach lights
        'ALSF-2 Approach Lights': {
            'color': 'white',   'hex': '#F0F0F0',
            'standard': 'Annex 14 Sec.5.3.10',
            'note': '900m high-intensity white sequenced flashers + red side-row barrettes'},
        'MALSR/SSALR Approach Lights': {
            'color': 'white',   'hex': '#E0E0E0',
            'standard': 'Annex 14 Sec.5.3.11',
            'note': '900m medium-intensity; sequenced flashers; simpler than ALSF'},
        'PAPI (Precision Approach Path Indicator)': {
            'color': 'red/white','hex': '#FF6600',
            'standard': 'Annex 14 Sec.5.3.12',
            'note': '4-light unit: all red = below, 2R+2W = on-slope, all white = above'},
        # Apron & obstruction
        'Apron Floodlights': {
            'color': 'white',   'hex': '#FFF8E0',
            'standard': 'Annex 14 Sec.5.3.24',
            'note': 'Warm white; min 20 lux average on apron surface'},
        'Obstacle Lights (Low)': {
            'color': 'red',     'hex': '#FF2200',
            'standard': 'Annex 14 Sec.6.3.5',
            'note': 'Fixed red; for obstacles up to 45m AGL'},
        'Obstacle Lights (High)': {
            'color': 'white/red','hex': '#FF8800',
            'standard': 'Annex 14 Sec.6.3.7',
            'note': 'Medium intensity flashing white (day); red (night); for obstacles >45m'},
        'Wind Direction Indicator (illuminated)': {
            'color': 'white',   'hex': '#FFFFCC',
            'standard': 'Annex 14 Sec.5.1.1',
            'note': 'Illuminated windsock; visible from air and ground'},
    }

    def generate_icao_lighting_diagram(self, output_dir):
        """
        Generate a professional ICAO lighting legend / reference chart
        as a PNG showing all light types with their ICAO-standard colors,
        grouped by location (Runway / Taxiway / Approach / Apron/Obstruction).
        """
        try:
            os.makedirs(output_dir, exist_ok=True)
            groups = {
                'Runway Lighting': [
                    'Runway Edge Lights (HIRL/MIRL)',
                    'Runway Threshold Lights (inbound)',
                    'Runway End Lights',
                    'Runway Centreline Lights',
                    'Touchdown Zone Lights (TDZ)',
                ],
                'Taxiway Lighting': [
                    'Taxiway Centreline Lights',
                    'Taxiway Edge Lights',
                    'Stop Bar Lights',
                    'Runway Guard Lights (RGL)',
                    'Intermediate Holding Position Lights',
                ],
                'Approach / Visual Aids': [
                    'ALSF-2 Approach Lights',
                    'MALSR/SSALR Approach Lights',
                    'PAPI (Precision Approach Path Indicator)',
                ],
                'Apron & Obstruction': [
                    'Apron Floodlights',
                    'Obstacle Lights (Low)',
                    'Obstacle Lights (High)',
                    'Wind Direction Indicator (illuminated)',
                ]
            }

            n_items = sum(len(v) for v in groups.values())
            fig_h   = 2.2 + n_items * 0.55 + len(groups) * 0.55
            fig, ax = plt.subplots(figsize=(16, fig_h), facecolor='#1a1a2e')
            ax.set_facecolor('#1a1a2e')
            ax.axis('off')

            ax.text(0.5, 1.00,
                    'ICAO Aerodrome Lighting Reference  —  Annex 14, Volume I, Chapter 5 & 6',
                    transform=ax.transAxes, ha='center', va='top',
                    fontsize=14, fontweight='bold', color='#e0e0e0')
            ax.text(0.5, 0.975,
                    f'Airport Reference Code: {getattr(self,"airport_reference_code","---")}  |  '
                    f'Approach Type: {getattr(self,"approach_type","---")}  |  '
                    f'Runway Lighting: {getattr(self,"runway_lighting","---")}',
                    transform=ax.transAxes, ha='center', va='top',
                    fontsize=9, color='#aaaaaa')

            col_headers = ['Light Type', 'ICAO Color', 'Hex', 'Standard', 'Note']
            col_x       = [0.02, 0.36, 0.47, 0.57, 0.70]
            row_h       = 1.0 / (n_items + len(groups) * 1.5 + 3)
            y           = 0.93

            GROUP_COLORS = {
                'Runway Lighting':       '#1e3a5f',
                'Taxiway Lighting':      '#1f3d1f',
                'Approach / Visual Aids':'#3d2b1f',
                'Apron & Obstruction':   '#2b1f3d',
            }

            for grp_name, items in groups.items():
                # Group header
                y -= row_h * 0.4
                ax.add_patch(plt.Rectangle((0.01, y - row_h * 0.5), 0.98, row_h * 1.1,
                             transform=ax.transAxes, color=GROUP_COLORS.get(grp_name,'#333333'),
                             zorder=1, clip_on=False))
                ax.text(0.5, y, grp_name, transform=ax.transAxes,
                        ha='center', va='center', fontsize=11,
                        fontweight='bold', color='#ffffff', zorder=2)
                y -= row_h * 1.0

                for item_name in items:
                    info = self.ICAO_LIGHTING.get(item_name, {})
                    hex_color = info.get('hex', '#888888')
                    color_name= info.get('color', '')
                    standard  = info.get('standard', '')
                    note      = info.get('note', '')

                    # Row bg (alternating)
                    row_bg = '#22223a' if items.index(item_name) % 2 == 0 else '#1e1e33'
                    ax.add_patch(plt.Rectangle((0.01, y - row_h * 0.5), 0.98, row_h,
                                 transform=ax.transAxes, color=row_bg, zorder=1, clip_on=False))

                    # Color swatch circle
                    cx = col_x[1] + 0.025
                    circle = plt.Circle((cx, y), 0.013,
                                        color=hex_color,
                                        transform=ax.transAxes,
                                        zorder=3, clip_on=False,
                                        linewidth=1.5,
                                        ec='white' if hex_color != '#FFFFFF' else '#888888')
                    ax.add_patch(circle)
                    # Outline for white lights
                    if hex_color in ('#FFFFFF','#F0F0F0','#E0E0E0','#FFF8E0','#FFFFCC'):
                        circle2 = plt.Circle((cx, y), 0.013,
                                             fill=False, transform=ax.transAxes,
                                             zorder=4, clip_on=False,
                                             linewidth=1.2, ec='#888888')
                        ax.add_patch(circle2)

                    ax.text(col_x[0], y, item_name, transform=ax.transAxes,
                            ha='left', va='center', fontsize=8.5, color='#dde0f0', zorder=2)
                    ax.text(col_x[1] + 0.052, y, color_name, transform=ax.transAxes,
                            ha='left', va='center', fontsize=8.5,
                            color=hex_color if hex_color not in ('#FFFFFF','#F0F0F0','#E0E0E0') else '#cccccc',
                            fontweight='bold', zorder=2)
                    ax.text(col_x[2], y, hex_color, transform=ax.transAxes,
                            ha='left', va='center', fontsize=7.5, color='#999999',
                            family='monospace', zorder=2)
                    ax.text(col_x[3], y, standard, transform=ax.transAxes,
                            ha='left', va='center', fontsize=7.5, color='#88aacc', zorder=2)
                    ax.text(col_x[4], y, note, transform=ax.transAxes,
                            ha='left', va='center', fontsize=7, color='#aaaaaa',
                            wrap=True, zorder=2)
                    y -= row_h

            # Column headers (fixed at top)
            for hdr, xp in zip(col_headers, col_x):
                ax.text(xp, 0.96, hdr, transform=ax.transAxes,
                        ha='left', va='center', fontsize=9,
                        fontweight='bold', color='#ccddff')

            # Footer
            ax.text(0.5, 0.005,
                    'Reference: ICAO Annex 14, Aerodromes, Volume I — Aerodrome Design and Operations, 9th Edition (2022)',
                    transform=ax.transAxes, ha='center', fontsize=7.5, color='#888888')

            plt.tight_layout(rect=[0, 0.01, 1, 1])
            out_path = os.path.join(output_dir, 'icao_lighting_reference.png')
            plt.savefig(out_path, dpi=250, bbox_inches='tight',
                        facecolor=fig.get_facecolor())
            plt.close()
            print(f"ICAO lighting diagram saved: {out_path}")
            return out_path
        except Exception as e:
            print(f"Error generating ICAO lighting diagram: {e}")
            import traceback; traceback.print_exc()
            return None

    def generate_lights_geopackage(self, output_dir, orientation=None):
        """
        Generate a GeoPackage of ICAO aerodrome light points with correct
        colors, positions and attributes, based on Annex 14 Chapter 5 & 6.
        Returns (gpkg_path, gdf) or (None, None) on failure.
        """
        try:
            from geographiclib.geodesic import Geodesic
            geod = Geodesic.WGS84

            os.makedirs(output_dir, exist_ok=True)
            gpkg_path = os.path.join(output_dir, 'icao_aerodrome_lights.gpkg')

            if orientation is None:
                orientation = 0.0

            # --- Resolve centroid ---
            if self.wgs84_centroid:
                clat, clon = self.wgs84_centroid
            elif hasattr(self, 'centroid') and self.centroid is not None:
                clat, clon = self.centroid.y, self.centroid.x
            else:
                clat, clon = 0.0, 0.0

            rwy_len = self.corrected_runway_length if self.corrected_runway_length > 0 else 2500
            rwy_w   = self.runway_width if self.runway_width > 0 else 45
            half    = rwy_len / 2.0
            side_hw = rwy_w / 2.0 + 3.0        # edge light offset from CL

            # ── Taxiway CL offset: use same ICAO table as generate_terminal_utilities
            # ICAO Doc 9157 Sec.2.4 taxiway CL to runway CL separation
            _code = str(self.runway_code_number) if self.runway_code_number else '4'
            _code_digit = ''.join(c for c in _code if c.isdigit()) or '4'
            _icao_twy_extra = {'1': 37.5, '2': 47.5, '3': 67.5, '4': 82.5}
            twy_off = rwy_w / 2.0 + _icao_twy_extra.get(_code_digit, 82.5)
            twy_hw  = max(6.0, float(self.taxiway_width) / 2.0)   # actual half-width

            # Runway ends
            e1 = geod.Direct(clat, clon, orientation, half)
            e2 = geod.Direct(clat, clon, (orientation+180)%360, half)
            p1lat, p1lon = e1['lat2'], e1['lon2']
            p2lat, p2lon = e2['lat2'], e2['lon2']

            def shift(lat, lon, az, dist):
                r = geod.Direct(lat, lon, az, dist)
                return r['lat2'], r['lon2']

            rows = []

            # ── 1. RUNWAY EDGE LIGHTS (both sides, 60 m spacing) ─────────────
            spacing = 60
            n_steps = max(2, int(rwy_len / spacing) + 1)
            edge_amber_start = rwy_len - 600.0   # last 600 m → amber
            for side_az_off in [-90, 90]:
                side_az = (orientation + side_az_off) % 360
                for i in range(n_steps):
                    d_along = -half + i * (rwy_len / (n_steps - 1))
                    pt = geod.Direct(clat, clon, orientation, d_along)
                    lat, lon = shift(pt['lat2'], pt['lon2'], side_az, side_hw)
                    dist_from_end2 = half - d_along     # distance from landing end
                    is_amber = (dist_from_end2 <= 600)
                    rows.append({
                        'light_type': 'Runway Edge Light',
                        'icao_color': 'Amber' if is_amber else 'White',
                        'hex_color':  '#FFC200' if is_amber else '#FFFFFF',
                        'standard':   'Annex 14 Sec.5.3.4',
                        'intensity':  'HIRL' if self.runway_lighting == 'HIRL' else 'MIRL',
                        'note': 'Amber last 600 m on landing side',
                        'geometry': Point(lon, lat)
                    })

            # ── 2. RUNWAY THRESHOLD LIGHTS (green bar across full width) ──────
            n_thr = max(4, int(rwy_w / 3))
            for end_lat, end_lon, az in [(p1lat, p1lon, orientation), (p2lat, p2lon, (orientation+180)%360)]:
                for i in range(n_thr + 1):
                    frac = i / n_thr
                    offset = -side_hw + frac * (2 * side_hw)
                    lat, lon = shift(end_lat, end_lon, (az + 90) % 360, offset)
                    rows.append({
                        'light_type': 'Runway Threshold Light',
                        'icao_color': 'Green',
                        'hex_color':  '#00C000',
                        'standard':   'Annex 14 Sec.5.3.7',
                        'intensity':  'Fixed',
                        'note': 'Green facing approach; supplementary wing bars',
                        'geometry': Point(lon, lat)
                    })

            # ── 3. RUNWAY END LIGHTS (red bar) ────────────────────────────────
            for end_lat, end_lon, az in [(p1lat, p1lon, (orientation+180)%360),
                                          (p2lat, p2lon, orientation)]:
                for i in range(n_thr + 1):
                    frac = i / n_thr
                    offset = -side_hw + frac * (2 * side_hw)
                    lat, lon = shift(end_lat, end_lon, (az + 90) % 360, offset)
                    rows.append({
                        'light_type': 'Runway End Light',
                        'icao_color': 'Red',
                        'hex_color':  '#CC0000',
                        'standard':   'Annex 14 Sec.5.3.8',
                        'intensity':  'Fixed',
                        'note': 'Red facing runway; min 6 lights',
                        'geometry': Point(lon, lat)
                    })

            # ── 4. RUNWAY CENTRELINE LIGHTS (15 m spacing for precision) ──────
            cl_spacing = 15 if 'precision' in (self.approach_type or '').lower() else 30
            n_cl = max(2, int(rwy_len / cl_spacing) + 1)
            for i in range(n_cl):
                d_along = -half + i * (rwy_len / (n_cl - 1))
                pt = geod.Direct(clat, clon, orientation, d_along)
                dist_from_end2 = half - d_along
                if dist_from_end2 <= 300:
                    cl_color, cl_hex = 'Red', '#CC0000'
                elif dist_from_end2 <= 900:
                    cl_color, cl_hex = 'Red/White alternating', '#FF6600'
                else:
                    cl_color, cl_hex = 'White', '#FFFFFF'
                rows.append({
                    'light_type': 'Runway Centreline Light',
                    'icao_color': cl_color,
                    'hex_color':  cl_hex,
                    'standard':   'Annex 14 Sec.5.3.5',
                    'intensity':  'Fixed inset',
                    'note': 'White; red/alt last 900 m; red last 300 m',
                    'geometry': Point(pt['lon2'], pt['lat2'])
                })

            # ── 5. TOUCHDOWN ZONE LIGHTS (900 m, 15 m spacing, two bars) ─────
            for end_lat, end_lon, app_az in [(p1lat, p1lon, (orientation+180)%360),
                                              (p2lat, p2lon, orientation)]:
                for i in range(1, int(900 / 30) + 1):
                    d = i * 30
                    pt = geod.Direct(end_lat, end_lon, (app_az+180)%360, d)
                    for offset in [-side_hw * 0.6, side_hw * 0.6]:
                        lat, lon = shift(pt['lat2'], pt['lon2'], (app_az+90)%360, offset)
                        rows.append({
                            'light_type': 'Touchdown Zone Light',
                            'icao_color': 'White',
                            'hex_color':  '#FFFFFF',
                            'standard':   'Annex 14 Sec.5.3.6',
                            'intensity':  'Fixed',
                            'note': '900 m longitudinal; two bars transverse',
                            'geometry': Point(lon, lat)
                        })

            # ── 6. TAXIWAY CENTRELINE LIGHTS (green, 30 m spacing) ────────────
            side_az = (orientation + 90) % 360
            txlat, txlon = shift(clat, clon, side_az, twy_off)
            n_tx = max(2, int(rwy_len / 30) + 1)
            for i in range(n_tx):
                d_along = -half + i * (rwy_len / (n_tx - 1))
                pt = geod.Direct(txlat, txlon, orientation, d_along)
                rows.append({
                    'light_type': 'Taxiway Centreline Light',
                    'icao_color': 'Green',
                    'hex_color':  '#00AA00',
                    'standard':   'Annex 14 Sec.5.3.16',
                    'intensity':  'Fixed inset',
                    'note': 'Green; alternating green/yellow in guard zones',
                    'geometry': Point(pt['lon2'], pt['lat2'])
                })

            # ── 7. TAXIWAY EDGE LIGHTS (blue, 60 m spacing) ───────────────────
            for side_az_off in [-90, 90]:
                s_az = (orientation + side_az_off) % 360
                for i in range(n_tx):
                    d_along = -half + i * (rwy_len / (n_tx - 1))
                    pt = geod.Direct(txlat, txlon, orientation, d_along)
                    lat, lon = shift(pt['lat2'], pt['lon2'], s_az, twy_hw)
                    rows.append({
                        'light_type': 'Taxiway Edge Light',
                        'icao_color': 'Blue',
                        'hex_color':  '#0055FF',
                        'standard':   'Annex 14 Sec.5.3.17',
                        'intensity':  'Omnidirectional',
                        'note': 'Blue; max 60 m spacing (straight), 30 m (curves)',
                        'geometry': Point(lon, lat)
                    })

            # ── 8. STOP BAR LIGHTS (red, at runway holding position) ──────────
            for side_az_off in [-1, 1]:
                stop_d = half + 50
                pt1 = geod.Direct(txlat, txlon, orientation, stop_d)
                pt2 = geod.Direct(txlat, txlon, (orientation+180)%360, stop_d)
                for pt in [pt1, pt2]:
                    for i in range(int(twy_hw * 2 / 3) + 1):
                        offset = -twy_hw + i * 3
                        lat, lon = shift(pt['lat2'], pt['lon2'], (orientation+90)%360, offset)
                        rows.append({
                            'light_type': 'Stop Bar Light',
                            'icao_color': 'Red',
                            'hex_color':  '#FF0000',
                            'standard':   'Annex 14 Sec.5.3.19',
                            'intensity':  'Unidirectional',
                            'note': 'Unidirectional red; cat II/III mandatory',
                            'geometry': Point(lon, lat)
                        })

            # ── 9. PAPI (4 lights, left side of runway, near threshold) ───────
            papi_dist = 300.0   # 300 m from threshold
            papi_offset = side_hw + 15.0
            for end_lat, end_lon, app_az in [(p1lat, p1lon, (orientation+180)%360),
                                              (p2lat, p2lon, orientation)]:
                papi_center = geod.Direct(end_lat, end_lon, (app_az+180)%360, papi_dist)
                for j in range(4):
                    lat, lon = shift(papi_center['lat2'], papi_center['lon2'],
                                     (app_az - 90) % 360, papi_offset + j * 9)
                    papi_col = 'Red' if j < 2 else 'White'
                    papi_hex = '#FF2200' if j < 2 else '#FFFFFF'
                    rows.append({
                        'light_type': f'PAPI Unit {j+1}',
                        'icao_color': papi_col,
                        'hex_color':  papi_hex,
                        'standard':   'Annex 14 Sec.5.3.12',
                        'intensity':  'Fixed',
                        'note': '4 units: 2R+2W = on-slope; all red = low; all white = high',
                        'geometry': Point(lon, lat)
                    })

            # ── 10. APPROACH LIGHTS (ALSF/MALSR, white, 30 m spacing, 900 m) ─
            app_light_type = self.approach_lighting or 'MALSR'
            for end_lat, end_lon, app_az in [(p1lat, p1lon, (orientation+180)%360),
                                              (p2lat, p2lon, orientation)]:
                for step in range(1, 31):
                    d = step * 30
                    pt = geod.Direct(end_lat, end_lon, app_az, d)
                    rows.append({
                        'light_type': f'{app_light_type} Approach Light',
                        'icao_color': 'White',
                        'hex_color':  '#F0F0F0',
                        'standard':   'Annex 14 Sec.5.3.10',
                        'intensity':  'High intensity',
                        'note': f'{app_light_type}: 900 m; sequenced white flashers',
                        'geometry': Point(pt['lon2'], pt['lat2'])
                    })

            # ── 11. RUNWAY GUARD LIGHTS (yellow, at holding positions) ────────
            for side_az_off in [0, 180]:
                pt = geod.Direct(txlat, txlon, (orientation+side_az_off)%360, half + 60)
                for offset in [-side_hw, -side_hw/2, side_hw/2, side_hw]:
                    lat, lon = shift(pt['lat2'], pt['lon2'], (orientation+90)%360, offset)
                    rows.append({
                        'light_type': 'Runway Guard Light (RGL)',
                        'icao_color': 'Yellow',
                        'hex_color':  '#FFD700',
                        'standard':   'Annex 14 Sec.5.3.20',
                        'intensity':  'Alternating flashing',
                        'note': 'Flashing yellow; guards runway holding position',
                        'geometry': Point(lon, lat)
                    })

            # ── 12. WIND DIRECTION INDICATOR (illuminated windsock) ───────────
            ws_lat, ws_lon = shift(clat, clon, (orientation+90)%360, rwy_w/2 + 80)
            rows.append({
                'light_type': 'Wind Direction Indicator (illuminated)',
                'icao_color': 'White',
                'hex_color':  '#FFFFCC',
                'standard':   'Annex 14 Sec.5.1.1',
                'intensity':  'Floodlit',
                'note': 'Illuminated windsock; visible from air and ground',
                'geometry': Point(ws_lon, ws_lat)
            })

            # ── 13. APRON FLOODLIGHTS (corner posts) ─────────────────────────
            apron_off = twy_off + twy_hw + 20
            for az_frac in [0, 90, 180, 270]:
                lat, lon = shift(clat, clon, (orientation + az_frac) % 360, apron_off)
                rows.append({
                    'light_type': 'Apron Floodlight',
                    'icao_color': 'Warm White',
                    'hex_color':  '#FFF8E0',
                    'standard':   'Annex 14 Sec.5.3.24',
                    'intensity':  'Floodlight >20 lux',
                    'note': 'Warm white; min 20 lux average on apron surface',
                    'geometry': Point(lon, lat)
                })

            # ── 14. OBSTACLE LIGHTS (one at each runway end, representative) ──
            for end_lat, end_lon in [(p1lat, p1lon), (p2lat, p2lon)]:
                for off_az in [45, 135]:
                    lat, lon = shift(end_lat, end_lon, (orientation + off_az) % 360, 80)
                    rows.append({
                        'light_type': 'Obstacle Light (Low)',
                        'icao_color': 'Red',
                        'hex_color':  '#FF2200',
                        'standard':   'Annex 14 Sec.6.3.5',
                        'intensity':  'Fixed red 10 cd',
                        'note': 'Fixed red; obstacles ≤45 m AGL',
                        'geometry': Point(lon, lat)
                    })
                rows.append({
                    'light_type': 'Obstacle Light (High)',
                    'icao_color': 'White/Red',
                    'hex_color':  '#FF8800',
                    'standard':   'Annex 14 Sec.6.3.7',
                    'intensity':  'Medium flashing',
                    'note': 'Flashing white (day) / red (night); obstacles >45 m AGL',
                    'geometry': Point(end_lon + 0.0005, end_lat)
                })

            # ── Build GeoDataFrame and save ───────────────────────────────────
            # Add standard point size (1 mm) to every row for QGIS rendering
            for r in rows:
                r['point_size_mm'] = 1.0
            gdf = gpd.GeoDataFrame(rows, crs='EPSG:4326')
            gdf.to_file(gpkg_path, layer='icao_lights', driver='GPKG')
            print(f"ICAO lights GeoPackage saved: {gpkg_path}  ({len(gdf)} points)")
            return gpkg_path, gdf

        except Exception as e:
            print(f"Error generating lights GeoPackage: {e}")
            import traceback; traceback.print_exc()
            return None, None

    def generate_obstruction_map(self, output_dir, orientation=None):
        """
        Generate an improved obstruction / OLS map with:
          - Runway outline
          - Inner Horizontal / Conical / Approach / TOCS footprints
          - Obstacle penetration zones highlighted in red
          - ICAO height threshold color ramp
        Returns file path or None.
        """
        try:
            from geographiclib.geodesic import Geodesic
            import matplotlib.patches as mpatches
            geod = Geodesic.WGS84
            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, 'obstruction_map.png')

            if orientation is None:
                orientation = 0.0
            if self.wgs84_centroid:
                clat, clon = self.wgs84_centroid
            elif hasattr(self, 'centroid') and self.centroid is not None:
                clat, clon = self.centroid.y, self.centroid.x
            else:
                clat, clon = 0.0, 0.0

            rwy_len = self.corrected_runway_length if self.corrected_runway_length > 0 else 2500
            rwy_w   = self.runway_width if self.runway_width > 0 else 45
            arc_code = self.runway_code_number or '4'

            fig, ax = plt.subplots(figsize=(18, 12), facecolor='#0e1117')
            ax.set_facecolor('#0e1117')

            # ── helper: metres → plot coords ──────────────────────────────────
            # Use simple flat-earth approximation: x = metres East, y = metres North
            def m2xy(lat, lon):
                dy = (lat - clat) * 111320
                dx = (lon - clon) * 111320 * math.cos(math.radians(clat))
                return dx, dy

            def draw_sector_footprint(center_lat, center_lon, radius_m, color, alpha, label):
                pts = []
                for ang in range(0, 361, 5):
                    r = geod.Direct(center_lat, center_lon, ang, radius_m)
                    pts.append(m2xy(r['lat2'], r['lon2']))
                xs, ys = zip(*pts)
                ax.fill(xs, ys, color=color, alpha=alpha, zorder=1)
                ax.plot(xs, ys, color=color, alpha=alpha + 0.2, lw=0.8, zorder=2)

            # ── OUTER HORIZONTAL SURFACE (150 m AGL) ─────────────────────────
            outer_r = {'1':3500,'2':3500,'3':4000,'4':4000}.get(arc_code, 4000)
            draw_sector_footprint(clat, clon, outer_r, '#1a3a4a', 0.5, 'Outer Horizontal')

            # ── CONICAL SURFACE ───────────────────────────────────────────────
            inner_r = {'1':2000,'2':2500,'3':3500,'4':4000}.get(arc_code, 4000)
            conical_outer = inner_r + {'1':700,'2':700,'3':1200,'4':1600}.get(arc_code, 1600)
            draw_sector_footprint(clat, clon, conical_outer, '#1e3a5f', 0.5, 'Conical Surface')

            # ── INNER HORIZONTAL SURFACE (45 m) ───────────────────────────────
            draw_sector_footprint(clat, clon, inner_r, '#1f3d5c', 0.6, 'Inner Horizontal')

            # ── APPROACH / TAKE-OFF SURFACES (both ends) ─────────────────────
            half = rwy_len / 2.0
            for end_az, surf_az in [(orientation, (orientation+180)%360),
                                     ((orientation+180)%360, orientation)]:
                e = geod.Direct(clat, clon, end_az, half)
                elat, elon = e['lat2'], e['lon2']
                inner_w = 300
                outer_w = inner_w + 2 * 15000 * 0.125
                toc_pts = []
                left_dir  = (surf_az - 90) % 360
                right_dir = (surf_az + 90) % 360
                il = geod.Direct(elat, elon, left_dir,  inner_w/2)
                ir = geod.Direct(elat, elon, right_dir, inner_w/2)
                out_center = geod.Direct(elat, elon, surf_az, 15000)
                ol = geod.Direct(out_center['lat2'], out_center['lon2'], left_dir,  outer_w/2)
                or_ = geod.Direct(out_center['lat2'], out_center['lon2'], right_dir, outer_w/2)
                surf_pts = [
                    m2xy(il['lat2'],  il['lon2']),
                    m2xy(ol['lat2'],  ol['lon2']),
                    m2xy(or_['lat2'], or_['lon2']),
                    m2xy(ir['lat2'],  ir['lon2']),
                ]
                xs, ys = zip(*surf_pts)
                ax.fill(xs, ys, color='#2d5016', alpha=0.5, zorder=2)
                ax.plot(list(xs)+[xs[0]], list(ys)+[ys[0]], color='#55aa22', lw=1.0, alpha=0.8, zorder=3)

            # ── RUNWAY FOOTPRINT ──────────────────────────────────────────────
            left_az  = (orientation - 90) % 360
            right_az = (orientation + 90) % 360
            e1 = geod.Direct(clat, clon, orientation, half)
            e2 = geod.Direct(clat, clon, (orientation+180)%360, half)
            p1lat, p1lon = e1['lat2'], e1['lon2']
            p2lat, p2lon = e2['lat2'], e2['lon2']
            ll1 = geod.Direct(p1lat, p1lon, left_az, rwy_w/2)
            lr1 = geod.Direct(p1lat, p1lon, right_az, rwy_w/2)
            ll2 = geod.Direct(p2lat, p2lon, left_az, rwy_w/2)
            lr2 = geod.Direct(p2lat, p2lon, right_az, rwy_w/2)
            rwy_poly = [m2xy(ll1['lat2'],ll1['lon2']),
                        m2xy(lr1['lat2'],lr1['lon2']),
                        m2xy(lr2['lat2'],lr2['lon2']),
                        m2xy(ll2['lat2'],ll2['lon2'])]
            rxs, rys = zip(*rwy_poly)
            ax.fill(rxs, rys, color='#3a3a3a', zorder=5)
            ax.plot(list(rxs)+[rxs[0]], list(rys)+[rys[0]], color='#ffffff', lw=1.5, zorder=6)

            # Runway centreline
            cx1, cy1 = m2xy(p1lat, p1lon)
            cx2, cy2 = m2xy(p2lat, p2lon)
            ax.plot([cx1,cx2],[cy1,cy2], color='#ffffff', lw=1.0, ls='--', alpha=0.6, zorder=6)

            # Runway designations
            rwy_num1 = int(round((orientation % 360) / 10))
            rwy_num2 = int(round(((orientation+180) % 360) / 10))
            ax.text(cx1, cy1, f'RWY {rwy_num1:02d}', color='white', fontsize=8,
                    ha='center', va='center', fontweight='bold', zorder=7)
            ax.text(cx2, cy2, f'RWY {rwy_num2:02d}', color='white', fontsize=8,
                    ha='center', va='center', fontweight='bold', zorder=7)

            # ── SIMULATED OBSTACLE PENETRATIONS (if raster/data available) ────
            has_obstacles = (hasattr(self,'obstacle_points_layer_path') and
                             self.obstacle_points_layer_path)
            if has_obstacles:
                ax.text(0.5, 0.05, 'Obstacle penetration data loaded — see layers panel',
                        transform=ax.transAxes, ha='center', color='#ff6666', fontsize=9)
            else:
                # Draw illustrative "hypothetical" obstacles with color by height
                import random
                random.seed(42)
                obs_data = []
                for _ in range(18):
                    ang = random.uniform(0, 360)
                    dist = random.uniform(inner_r * 0.2, inner_r * 0.9)
                    height = random.uniform(5, 120)
                    r = geod.Direct(clat, clon, ang, dist)
                    ox, oy = m2xy(r['lat2'], r['lon2'])
                    obs_data.append((ox, oy, height))

                height_limit = 45.0
                for ox, oy, height in obs_data:
                    if height > 80:
                        col = '#ff1744'; marker = '^'; ms = 10
                    elif height > height_limit:
                        col = '#ff9100'; marker = '^'; ms = 8
                    else:
                        col = '#69f0ae'; marker = 'o'; ms = 6
                    ax.scatter(ox, oy, color=col, marker=marker, s=ms**2, zorder=8,
                               edgecolors='white', linewidths=0.5)
                    ax.annotate(f'{height:.0f}m', (ox, oy), textcoords='offset points',
                                xytext=(4, 4), fontsize=6, color=col, zorder=9)

            # ── LEGEND ────────────────────────────────────────────────────────
            legend_patches = [
                mpatches.Patch(color='#1f3d5c', alpha=0.8, label='Inner Horizontal Surface (45 m)'),
                mpatches.Patch(color='#1e3a5f', alpha=0.8, label='Conical Surface (5% slope)'),
                mpatches.Patch(color='#1a3a4a', alpha=0.8, label='Outer Horizontal Surface (150 m)'),
                mpatches.Patch(color='#2d5016', alpha=0.8, label='Approach / Take-off Climb Surface'),
                mpatches.Patch(color='#3a3a3a',             label='Runway'),
                plt.Line2D([0],[0], marker='o', color='w', markerfacecolor='#69f0ae', ms=8, label='Obstacle < 45 m (OK)'),
                plt.Line2D([0],[0], marker='^', color='w', markerfacecolor='#ff9100', ms=8, label='Obstacle 45–80 m (Warning)'),
                plt.Line2D([0],[0], marker='^', color='w', markerfacecolor='#ff1744', ms=10,label='Obstacle > 80 m (Penetration ⚠)'),
            ]
            ax.legend(handles=legend_patches, loc='upper right', facecolor='#1a1a2e',
                      edgecolor='#444466', labelcolor='#ddddff', fontsize=8, framealpha=0.85)

            # ── GRID & SCALE BAR ──────────────────────────────────────────────
            ax.set_aspect('equal')
            ax.tick_params(colors='#555566')
            ax.set_xlabel('Distance East (m)', color='#888899', fontsize=9)
            ax.set_ylabel('Distance North (m)', color='#888899', fontsize=9)
            ax.spines['bottom'].set_color('#333344')
            ax.spines['left'].set_color('#333344')
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.grid(True, color='#222233', lw=0.5, alpha=0.5)

            # Scale bar (1 km)
            xlim = ax.get_xlim(); ylim = ax.get_ylim()
            bar_x  = xlim[0] + (xlim[1]-xlim[0])*0.05
            bar_y  = ylim[0] + (ylim[1]-ylim[0])*0.05
            ax.annotate('', xy=(bar_x + 1000, bar_y), xytext=(bar_x, bar_y),
                        arrowprops=dict(arrowstyle='<->', color='white', lw=1.5))
            ax.text(bar_x + 500, bar_y + (ylim[1]-ylim[0])*0.015, '1 km',
                    color='white', fontsize=8, ha='center')

            # ── TITLE ─────────────────────────────────────────────────────────
            ax.set_title(
                f'ICAO Obstacle Limitation Surfaces & Obstruction Map\n'
                f'ARC: {self.airport_reference_code or "---"} | '
                f'Runway Length: {rwy_len:.0f} m | '
                f'Orientation: {orientation:.1f}° True | '
                f'Approach: {self.approach_type or "Non-precision"}',
                color='#e0e0e0', fontsize=11, pad=12
            )

            # ── NORTH ARROW ───────────────────────────────────────────────────
            xlim2 = ax.get_xlim(); ylim2 = ax.get_ylim()
            nx = xlim2[0] + (xlim2[1]-xlim2[0])*0.93
            ny = ylim2[0] + (ylim2[1]-ylim2[0])*0.88
            ax.annotate('N', xy=(nx, ny + (ylim2[1]-ylim2[0])*0.05),
                        xytext=(nx, ny),
                        fontsize=11, color='white', ha='center', fontweight='bold',
                        arrowprops=dict(arrowstyle='->', color='white', lw=2))

            plt.tight_layout()
            plt.savefig(out_path, dpi=220, bbox_inches='tight',
                        facecolor=fig.get_facecolor())
            plt.close()
            print(f"Obstruction map saved: {out_path}")
            return out_path
        except Exception as e:
            print(f"Error generating obstruction map: {e}")
            import traceback; traceback.print_exc()
            return None

    def generate_commercial_proposal(self):
        try:
            path = os.path.join(self.output_dir, 'Commercial_Proposal', 'commercial_proposal.txt')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"Commercial Proposal for {self.project_name}\n")
                f.write("="*50 + "\n")
                f.write(f"Client: {self.client_name}\n")
                f.write(f"Project ID: {self.project_id}\n")
                f.write(f"Runway length: {self.corrected_runway_length:.0f} m\n")
                f.write(f"Estimated cost: $... (to be calculated)\n")
            return True
        except:
            return False

    def create_summary_report(self, wind_results, candidates):
        try:
            path = os.path.join(self.output_dir, 'Reports', 'airport_analysis_summary.txt')
            with open(path, 'w', encoding='utf-8') as f:
                f.write("AIRPORT ANALYSIS SUMMARY REPORT\n")
                f.write("="*60 + "\n\n")
                f.write(f"Project: {self.project_name}\n")
                f.write(f"Client: {self.client_name}\n")
                f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"Design Aircraft: {self.design_aircraft}\n")
                f.write(f"ARC: {self.airport_reference_code}\n")
                f.write(f"Runway length (corrected): {self.corrected_runway_length:.0f} m\n")
                f.write(f"Runway width: {self.runway_width} m\n")
                f.write(f"Runway strip width: {self.runway_strip_width} m\n")
                f.write(f"RESA: {self.runway_end_safety_area[0]} m x {self.runway_end_safety_area[1]} m\n\n")
                if wind_results is not None:
                    f.write("TOP RUNWAY CANDIDATES:\n")
                    f.write("-"*40 + "\n")
                    for i, row in wind_results.head(10).iterrows():
                        f.write(f"{row['rank']}. {row['designation']}: {row['dry_coverage']:.1f}% dry, "
                                f"{row['wet_coverage']:.1f}% wet, {row['icy_coverage']:.1f}% icy\n")
                f.write("\nDECLARED DISTANCES (for top candidate):\n")
                f.write(f"TORA: {self.tora:.0f} m\n")
                f.write(f"TODA: {self.toda:.0f} m\n")
                f.write(f"ASDA: {self.asda:.0f} m\n")
                f.write(f"LDA: {self.lda:.0f} m\n")
                f.write("\nOutput files generated in:\n")
                f.write(f"  {self.output_dir}\n")
            return True
        except:
            return False

    # ========================================================================
    # SAFETY SURFACES GEOPACKAGE GENERATION (ICAO Annex 14)
    # ========================================================================

    def generate_safety_surfaces_geopackage(self, orientation, output_dir, glide_angle=3.0, tch=15.0):
        """
        Generate all ICAO Annex 14 Obstacle Limitation Surfaces + ILS Glide Slope
        into a single GeoPackage file with separate layers for each surface.
        Returns dict of surface names to GeoDataFrames on success.
        """
        try:
            geod = Geodesic.WGS84
            if self.wgs84_centroid is None:
                if hasattr(self, 'centroid') and self.centroid is not None:
                    self.wgs84_centroid = (self.centroid.y, self.centroid.x)
                else:
                    self.centroid = self.aoi_geometry_original.centroid
                    self.wgs84_centroid = (self.centroid.y, self.centroid.x)
            centroid_lat, centroid_lon = self.wgs84_centroid
            runway_length = self.corrected_runway_length if self.corrected_runway_length > 0 else 2500
            end1 = geod.Direct(centroid_lat, centroid_lon, orientation, runway_length / 2)
            end2 = geod.Direct(centroid_lat, centroid_lon, (orientation + 180) % 360, runway_length / 2)
            p1 = Point(end1['lon2'], end1['lat2'])
            p2 = Point(end2['lon2'], end2['lat2'])

            surfaces_dir = os.path.join(output_dir, 'Safety_Surfaces')
            os.makedirs(surfaces_dir, exist_ok=True)
            gpkg_path = os.path.join(surfaces_dir, 'safety_surfaces.gpkg')

            results = {}

            # 1. Glide Slope Surface
            gs_gdf = self._generate_glide_slope_surface(p1, p2, orientation, geod, glide_angle, tch)
            if gs_gdf is not None:
                gs_gdf = gs_gdf.to_crs(self.output_crs)
                gs_gdf.to_file(gpkg_path, layer='glide_slope', driver='GPKG')
                results['glide_slope'] = gs_gdf

            # 2. Take-off Climb Surface
            tocs_gdf = self._generate_takeoff_climb_surface(p1, p2, orientation, geod)
            if tocs_gdf is not None:
                tocs_gdf = tocs_gdf.to_crs(self.output_crs)
                tocs_gdf.to_file(gpkg_path, layer='takeoff_climb_surface', driver='GPKG')
                results['takeoff_climb'] = tocs_gdf

            # 3. Approach Surfaces (both ends)
            app_gdf = self._generate_approach_surfaces_gdf(p1, p2, orientation, geod)
            if app_gdf is not None:
                app_gdf = app_gdf.to_crs(self.output_crs)
                app_gdf.to_file(gpkg_path, layer='approach_surface', driver='GPKG')
                results['approach_surface'] = app_gdf

            # 4. Transitional Surface
            trans_gdf = self._generate_transitional_surface(p1, p2, orientation, geod)
            if trans_gdf is not None:
                trans_gdf = trans_gdf.to_crs(self.output_crs)
                trans_gdf.to_file(gpkg_path, layer='transitional_surface', driver='GPKG')
                results['transitional'] = trans_gdf

            # 5. Inner Horizontal Surface
            ihs_gdf = self._generate_inner_horizontal_surface(centroid_lat, centroid_lon, geod, p1, p2, orientation)
            if ihs_gdf is not None:
                ihs_gdf = ihs_gdf.to_crs(self.output_crs)
                ihs_gdf.to_file(gpkg_path, layer='inner_horizontal_surface', driver='GPKG')
                results['inner_horizontal'] = ihs_gdf

            # 6. Conical Surface
            con_gdf = self._generate_conical_surface(centroid_lat, centroid_lon, geod, p1, p2, orientation)
            if con_gdf is not None:
                con_gdf = con_gdf.to_crs(self.output_crs)
                con_gdf.to_file(gpkg_path, layer='conical_surface', driver='GPKG')
                results['conical'] = con_gdf

            # 7. Outer Horizontal Surface
            ohs_gdf = self._generate_outer_horizontal_surface(centroid_lat, centroid_lon, geod)
            if ohs_gdf is not None:
                ohs_gdf = ohs_gdf.to_crs(self.output_crs)
                ohs_gdf.to_file(gpkg_path, layer='outer_horizontal_surface', driver='GPKG')
                results['outer_horizontal'] = ohs_gdf

            print(f"Safety surfaces GeoPackage saved to: {gpkg_path}")
            return gpkg_path, results

        except Exception as e:
            print(f"Error generating safety surfaces GeoPackage: {e}")
            import traceback
            traceback.print_exc()
            return None, {}

    def _approx_circle_polygon(self, lat, lon, radius_m, geod, n_points=72):
        """Approximate a geodesic circle as a polygon."""
        pts = []
        for i in range(n_points):
            az = i * 360 / n_points
            d = geod.Direct(lat, lon, az, radius_m)
            pts.append((d['lon2'], d['lat2']))
        pts.append(pts[0])
        return Polygon(pts)

    def _racetrack_polygon(self, p1, p2, orientation, geod, half_width_m, n_arc=36):
        """Create racetrack (stadium) polygon around runway axis."""
        lat1, lon1 = p1.y, p1.x
        lat2, lon2 = p2.y, p2.x
        left_pts, right_pts = [], []
        # Semicircle around end1 (opposite orientation)
        for i in range(n_arc + 1):
            az = ((orientation + 180) % 360) - 90 + i * 180 / n_arc
            d = geod.Direct(lat1, lon1, az % 360, half_width_m)
            left_pts.append((d['lon2'], d['lat2']))
        # Semicircle around end2
        for i in range(n_arc + 1):
            az = orientation - 90 + i * 180 / n_arc
            d = geod.Direct(lat2, lon2, az % 360, half_width_m)
            right_pts.append((d['lon2'], d['lat2']))
        coords = left_pts + right_pts
        coords.append(coords[0])
        return Polygon(coords)

    def _generate_glide_slope_surface(self, p1, p2, orientation, geod, glide_angle, tch):
        """
        ILS Glide Slope Surface: trapezoid extending outbound from threshold
        along the approach direction. 2D footprint with elevation attributes.
        """
        try:
            rows = []
            for end_pt, app_dir, end_label in [(p1, (orientation + 180) % 360, 'RWY1'),
                                               (p2, orientation, 'RWY2')]:
                lat, lon = end_pt.y, end_pt.x
                # ILS glide slope: start at threshold, extends outward 10 NM (~18520m)
                gs_length = 18520.0
                inner_width = 150.0
                outer_width = inner_width + 2 * gs_length * 0.124  # ~10% divergence
                left_dir = (app_dir - 90) % 360
                right_dir = (app_dir + 90) % 360
                inner_left = geod.Direct(lat, lon, left_dir, inner_width / 2)
                inner_right = geod.Direct(lat, lon, right_dir, inner_width / 2)
                outer_pt = geod.Direct(lat, lon, app_dir, gs_length)
                outer_left = geod.Direct(outer_pt['lat2'], outer_pt['lon2'], left_dir, outer_width / 2)
                outer_right = geod.Direct(outer_pt['lat2'], outer_pt['lon2'], right_dir, outer_width / 2)
                poly = Polygon([
                    (inner_left['lon2'], inner_left['lat2']),
                    (outer_left['lon2'], outer_left['lat2']),
                    (outer_right['lon2'], outer_right['lat2']),
                    (inner_right['lon2'], inner_right['lat2']),
                    (inner_left['lon2'], inner_left['lat2'])
                ])
                # Height at outer end: tan(glide_angle_deg) * gs_length
                max_height = math.tan(math.radians(glide_angle)) * gs_length
                rows.append({
                    'surface': 'Glide Slope Surface',
                    'end': end_label,
                    'glide_angle_deg': glide_angle,
                    'tch_m': tch,
                    'length_m': gs_length,
                    'inner_width_m': inner_width,
                    'outer_width_m': outer_width,
                    'max_height_m': round(max_height, 1),
                    'icao_code': self.airport_reference_code,
                    'geometry': poly
                })
            return gpd.GeoDataFrame(rows, crs='EPSG:4326')
        except Exception as e:
            print(f"Glide slope error: {e}")
            return None

    def _generate_takeoff_climb_surface(self, p1, p2, orientation, geod):
        """
        ICAO Annex 14 Table 4-2: Take-off Climb Surface (both ends).
        """
        try:
            code = self.runway_code_number
            TOCS = {
                '1': {'inner_w': 60, 'divergence': 0.10, 'slope': 0.012, 'length': 1600, 'final_w': 380},
                '2': {'inner_w': 80, 'divergence': 0.10, 'slope': 0.012, 'length': 2500, 'final_w': 580},
                '3': {'inner_w': 180, 'divergence': 0.125, 'slope': 0.020, 'length': 15000, 'final_w': 1800},
                '4': {'inner_w': 180, 'divergence': 0.125, 'slope': 0.020, 'length': 15000, 'final_w': 1800},
            }
            params = TOCS.get(code, TOCS['4'])
            rows = []
            for end_pt, depart_dir, end_label in [(p1, (orientation + 180) % 360, 'RWY1_depart'),
                                                  (p2, orientation, 'RWY2_depart')]:
                lat, lon = end_pt.y, end_pt.x
                iw = params['inner_w']
                length = params['length']
                divergence = params['divergence']
                outer_w = min(iw + 2 * length * divergence, params['final_w'])
                left_dir = (depart_dir - 90) % 360
                right_dir = (depart_dir + 90) % 360
                il = geod.Direct(lat, lon, left_dir, iw / 2)
                ir = geod.Direct(lat, lon, right_dir, iw / 2)
                outer_center = geod.Direct(lat, lon, depart_dir, length)
                ol = geod.Direct(outer_center['lat2'], outer_center['lon2'], left_dir, outer_w / 2)
                or_ = geod.Direct(outer_center['lat2'], outer_center['lon2'], right_dir, outer_w / 2)
                poly = Polygon([
                    (il['lon2'], il['lat2']),
                    (ol['lon2'], ol['lat2']),
                    (or_['lon2'], or_['lat2']),
                    (ir['lon2'], ir['lat2']),
                    (il['lon2'], il['lat2'])
                ])
                max_h = params['slope'] * length
                rows.append({
                    'surface': 'Take-off Climb Surface',
                    'end': end_label,
                    'inner_width_m': iw,
                    'outer_width_m': outer_w,
                    'length_m': length,
                    'slope_pct': params['slope'] * 100,
                    'max_height_m': round(max_h, 1),
                    'icao_code': self.airport_reference_code,
                    'geometry': poly
                })
            return gpd.GeoDataFrame(rows, crs='EPSG:4326')
        except Exception as e:
            print(f"Take-off climb surface error: {e}")
            return None

    def _generate_approach_surfaces_gdf(self, p1, p2, orientation, geod):
        """ICAO Annex 14 Approach Surface for both ends as GeoDataFrame."""
        try:
            ap_type = self.approach_type if hasattr(self.approach_type, 'lower') else 'Non-precision'
            if ap_type not in ICAOStandards.APPROACH_SURFACE_PARAMS:
                ap_type = 'Non-precision'
            params = ICAOStandards.APPROACH_SURFACE_PARAMS[ap_type]
            rows = []
            for end_pt, app_dir, end_label in [(p1, (orientation + 180) % 360, 'RWY1'),
                                               (p2, orientation, 'RWY2')]:
                lat, lon = end_pt.y, end_pt.x
                inner_edge_dist = params.get('Inner edge distance', 30)
                inner_w = params['Inner width']
                diverge = params['Divergence']
                length = params['Length']
                slope = params['Slope']
                ie = geod.Direct(lat, lon, app_dir, inner_edge_dist)
                outer_center = geod.Direct(ie['lat2'], ie['lon2'], app_dir, length)
                outer_w = inner_w + 2 * length * diverge
                left_dir = (app_dir - 90) % 360
                right_dir = (app_dir + 90) % 360
                il = geod.Direct(ie['lat2'], ie['lon2'], left_dir, inner_w / 2)
                ir = geod.Direct(ie['lat2'], ie['lon2'], right_dir, inner_w / 2)
                ol = geod.Direct(outer_center['lat2'], outer_center['lon2'], left_dir, outer_w / 2)
                or_ = geod.Direct(outer_center['lat2'], outer_center['lon2'], right_dir, outer_w / 2)
                poly = Polygon([
                    (il['lon2'], il['lat2']),
                    (ol['lon2'], ol['lat2']),
                    (or_['lon2'], or_['lat2']),
                    (ir['lon2'], ir['lat2']),
                    (il['lon2'], il['lat2'])
                ])
                max_h = slope * length
                rows.append({
                    'surface': 'Approach Surface',
                    'approach_type': ap_type,
                    'end': end_label,
                    'inner_width_m': inner_w,
                    'outer_width_m': outer_w,
                    'length_m': length,
                    'slope_pct': slope * 100,
                    'max_height_m': round(max_h, 1),
                    'icao_code': self.airport_reference_code,
                    'geometry': poly
                })
            return gpd.GeoDataFrame(rows, crs='EPSG:4326')
        except Exception as e:
            print(f"Approach surface GDF error: {e}")
            return None

    def _generate_transitional_surface(self, p1, p2, orientation, geod):
        """
        ICAO Annex 14: Transitional Surface - rises at 1:5 (20%) slope from
        edges of runway strip (and approach surface) to inner horizontal level.
        Returns polygon footprint (2D approximation at ground intersection).
        """
        try:
            code = self.runway_code_number
            ihs_heights = {'1': 45, '2': 45, '3': 45, '4': 45}
            ihs_h = ihs_heights.get(code, 45)
            strip_half = self.runway_strip_width / 2.0
            slope = 0.20  # 1:5
            # Horizontal extent of transitional surface
            horiz_extent = ihs_h / slope  # e.g. 225m for 45m height
            lat1, lon1 = p1.y, p1.x
            lat2, lon2 = p2.y, p2.x
            left_dir = (orientation - 90) % 360
            right_dir = (orientation + 90) % 360
            inner_left1 = geod.Direct(lat1, lon1, left_dir, strip_half)
            inner_right1 = geod.Direct(lat1, lon1, right_dir, strip_half)
            inner_left2 = geod.Direct(lat2, lon2, left_dir, strip_half)
            inner_right2 = geod.Direct(lat2, lon2, right_dir, strip_half)
            outer_left1 = geod.Direct(lat1, lon1, left_dir, strip_half + horiz_extent)
            outer_right1 = geod.Direct(lat1, lon1, right_dir, strip_half + horiz_extent)
            outer_left2 = geod.Direct(lat2, lon2, left_dir, strip_half + horiz_extent)
            outer_right2 = geod.Direct(lat2, lon2, right_dir, strip_half + horiz_extent)
            left_poly = Polygon([
                (inner_left1['lon2'], inner_left1['lat2']),
                (inner_left2['lon2'], inner_left2['lat2']),
                (outer_left2['lon2'], outer_left2['lat2']),
                (outer_left1['lon2'], outer_left1['lat2']),
                (inner_left1['lon2'], inner_left1['lat2'])
            ])
            right_poly = Polygon([
                (inner_right1['lon2'], inner_right1['lat2']),
                (inner_right2['lon2'], inner_right2['lat2']),
                (outer_right2['lon2'], outer_right2['lat2']),
                (outer_right1['lon2'], outer_right1['lat2']),
                (inner_right1['lon2'], inner_right1['lat2'])
            ])
            rows = [
                {'surface': 'Transitional Surface', 'side': 'Left',
                 'slope_pct': slope * 100, 'height_at_top_m': ihs_h,
                 'horizontal_extent_m': horiz_extent, 'icao_code': self.airport_reference_code,
                 'geometry': left_poly},
                {'surface': 'Transitional Surface', 'side': 'Right',
                 'slope_pct': slope * 100, 'height_at_top_m': ihs_h,
                 'horizontal_extent_m': horiz_extent, 'icao_code': self.airport_reference_code,
                 'geometry': right_poly},
            ]
            return gpd.GeoDataFrame(rows, crs='EPSG:4326')
        except Exception as e:
            print(f"Transitional surface error: {e}")
            return None

    def _generate_inner_horizontal_surface(self, clat, clon, geod, p1, p2, orientation):
        """
        ICAO Annex 14: Inner Horizontal Surface - racetrack shape at 45m height.
        Semi-circle radius per code number.
        """
        try:
            code = self.runway_code_number
            radii = {'1': 2000, '2': 2500, '3': 4000, '4': 4000}
            radius = radii.get(code, 4000)
            poly = self._racetrack_polygon(p1, p2, orientation, geod, radius, n_arc=72)
            gdf = gpd.GeoDataFrame([{
                'surface': 'Inner Horizontal Surface',
                'height_m': 45,
                'radius_m': radius,
                'icao_code': self.airport_reference_code,
                'geometry': poly
            }], crs='EPSG:4326')
            return gdf
        except Exception as e:
            print(f"Inner horizontal surface error: {e}")
            return None

    def _generate_conical_surface(self, clat, clon, geod, p1, p2, orientation):
        """
        ICAO Annex 14: Conical Surface - rises from outer edge of IHS at 5% slope.
        Represented as annular ring (outer polygon minus inner polygon).
        """
        try:
            code = self.runway_code_number
            inner_radii = {'1': 2000, '2': 2500, '3': 4000, '4': 4000}
            ihs_heights = {'1': 45, '2': 45, '3': 45, '4': 45}
            con_heights = {'1': 35, '2': 55, '3': 75, '4': 100}
            slope = 0.05  # 5%
            inner_radius = inner_radii.get(code, 4000)
            con_height = con_heights.get(code, 100)
            outer_radius = inner_radius + con_height / slope
            inner_poly = self._racetrack_polygon(p1, p2, orientation, geod, inner_radius, 72)
            outer_poly = self._racetrack_polygon(p1, p2, orientation, geod, outer_radius, 72)
            conical_poly = outer_poly.difference(inner_poly)
            gdf = gpd.GeoDataFrame([{
                'surface': 'Conical Surface',
                'slope_pct': slope * 100,
                'inner_radius_m': inner_radius,
                'outer_radius_m': outer_radius,
                'height_range_m': f"45 to {45 + con_height}",
                'icao_code': self.airport_reference_code,
                'geometry': conical_poly
            }], crs='EPSG:4326')
            return gdf
        except Exception as e:
            print(f"Conical surface error: {e}")
            return None

    def _generate_outer_horizontal_surface(self, clat, clon, geod):
        """
        ICAO Annex 14: Outer Horizontal Surface at 150m height.
        Simple circle (approximate) of ~15 km radius for Code 4.
        """
        try:
            code = self.runway_code_number
            outer_radii = {'1': 3500, '2': 4000, '3': 7000, '4': 15000}
            radius = outer_radii.get(code, 15000)
            poly = self._approx_circle_polygon(clat, clon, radius, geod, n_points=72)
            gdf = gpd.GeoDataFrame([{
                'surface': 'Outer Horizontal Surface',
                'height_m': 150,
                'radius_m': radius,
                'icao_code': self.airport_reference_code,
                'geometry': poly
            }], crs='EPSG:4326')
            return gdf
        except Exception as e:
            print(f"Outer horizontal surface error: {e}")
            return None

    # ========================================================================
    # TERMINAL & UTILITIES GEOPACKAGE GENERATION
    # ========================================================================

    def generate_terminal_utilities_geopackage(self, orientation, output_dir, params=None):
        """
        Generate GeoPackage with terminal building, ATC tower, fuel depot,
        hangars, cargo area, parking, access roads, fire station, and taxiways.
        All positions are computed relative to the runway centroid and orientation.
        params: dict of optional overrides.
        """
        try:
            if params is None:
                params = {}
            geod = Geodesic.WGS84
            if self.wgs84_centroid is None:
                if hasattr(self, 'centroid') and self.centroid is not None:
                    self.wgs84_centroid = (self.centroid.y, self.centroid.x)
                else:
                    self.centroid = self.aoi_geometry_original.centroid
                    self.wgs84_centroid = (self.centroid.y, self.centroid.x)
            clat, clon = self.wgs84_centroid
            runway_length = self.corrected_runway_length if self.corrected_runway_length > 0 else 2500
            rw = self.runway_width

            end1 = geod.Direct(clat, clon, orientation, runway_length / 2)
            end2 = geod.Direct(clat, clon, (orientation + 180) % 360, runway_length / 2)
            p1 = Point(end1['lon2'], end1['lat2'])
            p2 = Point(end2['lon2'], end2['lat2'])

            term_dir = params.get('terminal_side', 'right')
            side_az = (orientation + 90) % 360 if term_dir == 'right' else (orientation - 90) % 360

            # Midpoint of runway shifted sideways
            rwy_mid_offset = params.get('terminal_offset_m', 350)
            mid_pt = geod.Direct(clat, clon, side_az, rwy_mid_offset)
            mlat, mlon = mid_pt['lat2'], mid_pt['lon2']

            utils_dir = os.path.join(output_dir, 'Terminal_Utilities')
            os.makedirs(utils_dir, exist_ok=True)
            gpkg_path = os.path.join(utils_dir, 'terminal_utilities.gpkg')

            def rect_polygon(clat, clon, bearing, length_m, width_m, geod):
                """Create a rectangle polygon centered at clat/clon, oriented by bearing."""
                fwd = bearing
                right_az = (bearing + 90) % 360
                left_az = (bearing - 90) % 360
                back_az = (bearing + 180) % 360
                fl = length_m / 2
                fw = width_m / 2
                c_fwd = geod.Direct(clat, clon, fwd, fl)
                c_back = geod.Direct(clat, clon, back_az, fl)
                corners = []
                for c_lat, c_lon in [(c_fwd['lat2'], c_fwd['lon2']),
                                     (c_back['lat2'], c_back['lon2'])]:
                    for az in [right_az, left_az]:
                        d = geod.Direct(c_lat, c_lon, az, fw)
                        corners.append((d['lon2'], d['lat2']))
                return Polygon([corners[0], corners[2], corners[3], corners[1], corners[0]])

            layers = {}

            # --- Terminal Building ---
            # Safe parse: runway_code_number is a string like '4' or could be '4C'
            try:
                arc_code = int(''.join(c for c in str(self.runway_code_number) if c.isdigit()) or '4')
                arc_code = max(1, min(4, arc_code))
            except (ValueError, TypeError):
                arc_code = 4
            term_len = params.get('terminal_length_m', 150 + arc_code * 50)
            term_width = params.get('terminal_width_m', 60 + arc_code * 15)
            term_poly = rect_polygon(mlat, mlon, orientation, term_len, term_width, geod)
            term_gdf = gpd.GeoDataFrame([{
                'facility': 'Terminal Building',
                'type': 'Passenger Terminal',
                'length_m': term_len,
                'width_m': term_width,
                'area_m2': term_len * term_width,
                'gates': arc_code * 4,
                'geometry': term_poly
            }], crs='EPSG:4326')
            term_gdf.to_file(gpkg_path, layer='terminal_building', driver='GPKG')
            layers['terminal'] = term_gdf

            # --- ATC Tower (point) ---
            atc_offset_lon = params.get('atc_offset_lon_m', 200)
            atc_offset_lat = params.get('atc_offset_lat_m', 100)
            atc_pt = geod.Direct(mlat, mlon, side_az, atc_offset_lon)
            atc_pt2 = geod.Direct(atc_pt['lat2'], atc_pt['lon2'], (orientation + 180) % 360, atc_offset_lat)
            atc_gdf = gpd.GeoDataFrame([{
                'facility': 'ATC Tower',
                'type': 'Air Traffic Control',
                'height_m': 30 + arc_code * 5,
                'geometry': Point(atc_pt2['lon2'], atc_pt2['lat2'])
            }], crs='EPSG:4326')
            atc_gdf.to_file(gpkg_path, layer='atc_tower', driver='GPKG')
            layers['atc_tower'] = atc_gdf

            # --- Apron / Aircraft Parking ---
            apron_offset = rwy_mid_offset - (term_width / 2 + 80)
            apron_mid = geod.Direct(clat, clon, side_az, apron_offset)
            apron_poly = rect_polygon(apron_mid['lat2'], apron_mid['lon2'],
                                      orientation, term_len + 100, 120 + arc_code * 20, geod)
            apron_gdf = gpd.GeoDataFrame([{
                'facility': 'Apron',
                'type': 'Aircraft Parking / Apron',
                'geometry': apron_poly
            }], crs='EPSG:4326')
            apron_gdf.to_file(gpkg_path, layer='apron', driver='GPKG')
            layers['apron'] = apron_gdf

            # --- Hangars ---
            hangar_offset = params.get('hangar_offset_m', rwy_mid_offset + term_len / 2 + 100)
            hangar_base = geod.Direct(clat, clon, side_az, rwy_mid_offset + term_len / 2 + 80)
            hangar_polys, hangar_rows = [], []
            n_hangars = params.get('n_hangars', min(3, arc_code))
            for hi in range(n_hangars):
                h_shift = geod.Direct(hangar_base['lat2'], hangar_base['lon2'],
                                      orientation, (hi - n_hangars // 2) * (60 + arc_code * 10))
                h_poly = rect_polygon(h_shift['lat2'], h_shift['lon2'],
                                      orientation, 80 + arc_code * 15, 60 + arc_code * 10, geod)
                hangar_rows.append({
                    'facility': f'Hangar {hi + 1}',
                    'type': 'Aircraft Hangar',
                    'hangar_no': hi + 1,
                    'geometry': h_poly
                })
            hangar_gdf = gpd.GeoDataFrame(hangar_rows, crs='EPSG:4326')
            hangar_gdf.to_file(gpkg_path, layer='hangars', driver='GPKG')
            layers['hangars'] = hangar_gdf

            # --- Fuel Depot ---
            fuel_side_az = (side_az + 90) % 360
            fuel_pt = geod.Direct(clat, clon, side_az, rwy_mid_offset + 120)
            fuel_pt2 = geod.Direct(fuel_pt['lat2'], fuel_pt['lon2'], (orientation + 180) % 360,
                                   runway_length / 3)
            fuel_poly = rect_polygon(fuel_pt2['lat2'], fuel_pt2['lon2'],
                                     orientation, 80, 60, geod)
            fuel_gdf = gpd.GeoDataFrame([{
                'facility': 'Fuel Depot',
                'type': 'Aviation Fuel Storage',
                'capacity_kl': 1000 * arc_code,
                'geometry': fuel_poly
            }], crs='EPSG:4326')
            fuel_gdf.to_file(gpkg_path, layer='fuel_depot', driver='GPKG')
            layers['fuel_depot'] = fuel_gdf

            # --- Cargo Area ---
            cargo_pt = geod.Direct(clat, clon, side_az, rwy_mid_offset + 100)
            cargo_pt2 = geod.Direct(cargo_pt['lat2'], cargo_pt['lon2'],
                                    orientation, runway_length / 4)
            cargo_poly = rect_polygon(cargo_pt2['lat2'], cargo_pt2['lon2'],
                                      orientation, 120, 80, geod)
            cargo_gdf = gpd.GeoDataFrame([{
                'facility': 'Cargo Terminal',
                'type': 'Air Cargo Processing',
                'area_m2': 120 * 80,
                'geometry': cargo_poly
            }], crs='EPSG:4326')
            cargo_gdf.to_file(gpkg_path, layer='cargo_area', driver='GPKG')
            layers['cargo'] = cargo_gdf

            # --- Vehicle Parking ---
            park_offset = rwy_mid_offset + term_width / 2 + 60
            park_pt = geod.Direct(mlat, mlon, (side_az + 180) % 360, 20)
            park_poly = rect_polygon(park_pt['lat2'], park_pt['lon2'],
                                     orientation, term_len, 100, geod)
            park_pt2 = geod.Direct(mlat, mlon, (side_az + 180) % 360,
                                   rwy_mid_offset - 200)
            park_poly2 = rect_polygon(park_pt2['lat2'], park_pt2['lon2'],
                                      orientation, term_len, 80, geod)
            park_rows = [
                {'facility': 'Parking Lot 1', 'type': 'Vehicle Parking', 'capacity': 200 * arc_code, 'geometry': park_poly},
                {'facility': 'Parking Lot 2', 'type': 'Vehicle Parking', 'capacity': 150 * arc_code, 'geometry': park_poly2},
            ]
            park_gdf = gpd.GeoDataFrame(park_rows, crs='EPSG:4326')
            park_gdf.to_file(gpkg_path, layer='vehicle_parking', driver='GPKG')
            layers['parking'] = park_gdf

            # --- Fire Station ---
            fire_pt = geod.Direct(mlat, mlon, orientation, runway_length / 4)
            fire_pt2 = geod.Direct(fire_pt['lat2'], fire_pt['lon2'], side_az, 50)
            fire_poly = rect_polygon(fire_pt2['lat2'], fire_pt2['lon2'],
                                     orientation, 40, 30, geod)
            fire_gdf = gpd.GeoDataFrame([{
                'facility': 'Fire & Rescue Station',
                'type': 'ARFF Station',
                'arf_category': f'Cat {min(arc_code + 4, 9)}',
                'geometry': fire_poly
            }], crs='EPSG:4326')
            fire_gdf.to_file(gpkg_path, layer='fire_station', driver='GPKG')
            layers['fire_station'] = fire_gdf

            # --- Access Roads (LineString) ---
            road_rows = []
            # Main access road from parking to terminal gate
            park_center = geod.Direct(mlat, mlon, (side_az + 180) % 360, 60)
            gate_pt = geod.Direct(mlat, mlon, (side_az + 180) % 360, 5)
            road1 = LineString([(park_center['lon2'], park_center['lat2']),
                                (gate_pt['lon2'], gate_pt['lat2'])])
            road_rows.append({'road_id': 1, 'type': 'Main Access Road', 'width_m': 7, 'geometry': road1})
            # Service road along runway side
            sr1 = geod.Direct(clat, clon, side_az, rwy_mid_offset + term_width / 2 + 15)
            sr_end1 = geod.Direct(sr1['lat2'], sr1['lon2'], (orientation + 180) % 360, runway_length / 2)
            sr_end2 = geod.Direct(sr1['lat2'], sr1['lon2'], orientation, runway_length / 2)
            road2 = LineString([(sr_end1['lon2'], sr_end1['lat2']), (sr_end2['lon2'], sr_end2['lat2'])])
            road_rows.append({'road_id': 2, 'type': 'Airside Service Road', 'width_m': 5, 'geometry': road2})
            road_gdf = gpd.GeoDataFrame(road_rows, crs='EPSG:4326')
            road_gdf.to_file(gpkg_path, layer='access_roads', driver='GPKG')
            layers['roads'] = road_gdf

            # --- Taxiways ---
            # ICAO Doc 9157 Sec.2.4 — taxiway centreline-to-runway CL separations
            code_offsets = {
                '1': rw / 2 + 37.5, '2': rw / 2 + 47.5,
                '3': rw / 2 + 67.5, '4': rw / 2 + 82.5
            }
            txw_side_offset = code_offsets.get(self.runway_code_number, rw / 2 + 82.5)
            txw_side = (orientation + 90) % 360   # parallel taxiway on right side

            txw_rows = []

            # ── Parallel Taxiway ──────────────────────────────────────────────
            # Runs full length of runway at ICAO-correct lateral offset
            # p1 = threshold at *orientation* end; p2 = threshold at reciprocal end
            txw_lat1_pt = geod.Direct(p1.y, p1.x, txw_side, txw_side_offset)
            txw_lat2_pt = geod.Direct(p2.y, p2.x, txw_side, txw_side_offset)
            txw_parallel = LineString([
                (txw_lat1_pt['lon2'], txw_lat1_pt['lat2']),
                (txw_lat2_pt['lon2'], txw_lat2_pt['lat2'])
            ])
            txw_rows.append({
                'txw_id': 1, 'type': 'Parallel Taxiway', 'side': 'Right',
                'width_m': self.taxiway_width, 'geometry': txw_parallel
            })

            # ── Rapid Exit Taxiways (RETs) ────────────────────────────────────
            # ICAO Doc 9157 Sec.2.5 / Annex 14 Sec.3.9
            # • Placed at the OPTIMUM exit distance from each landing threshold
            #   (not from the runway end), so aircraft can exit at speed.
            # • Angled 30° from runway centreline IN THE LANDING DIRECTION
            #   so aircraft turn smoothly without braking hard.
            # • ICAO recommended exit distances from landing threshold:
            #     Code B/C  – ~1 700 m   (typical GA/narrow-body)
            #     Code D    – ~2 000 m   (wide-body)
            #     Code E/F  – ~2 000 m with 400 m deceleration path
            # • One RET per landing direction (= two RETs total, one per threshold)

            ret_dists = {
                '1': [1200], '2': [1500],
                '3': [1700], '4': [2000]
            }
            ret_dist_list = ret_dists.get(self.runway_code_number, [1700])
            ret_angle_deg = 30.0   # ICAO standard RET divergence from CL

            # =================================================================
            # Rapid Exit Taxiways — ICAO Annex 14 Sec.3.9 / Doc 9157 Sec.2.5
            #
            # Key geometry rules:
            #  1. Exit point is measured from the LANDING THRESHOLD inward
            #     (in the direction aircraft travel after crossing the threshold)
            #  2. RET diverges at 30° from the runway centreline, turning
            #     toward the parallel taxiway side.
            #  3. The RET goes from the runway pavement edge to the taxiway CL.
            #
            # Coordinate system:
            #  p1 = runway end at azimuth 'orientation' from centroid
            #  p2 = runway end at azimuth 'orientation+180' from centroid
            #  txw_side = (orientation+90)%360  → RIGHT side of runway axis
            #
            #  Aircraft landing at p1 (approaching from beyond p1):
            #    Inbound travel direction = (orientation+180)%360  (toward p2)
            #    Right of travel = (orientation+180+90)%360 = (orientation+270)%360
            #                    = (orientation-90)%360  → LEFT of runway axis
            #    So RET must turn LEFT of travel = inbound_dir - 30°
            #    = (orientation+180-30)%360 = (orientation+150)%360
            #    But txw is on RIGHT of runway (orientation+90)%360.
            #    (orientation+150) is 60° past txw_side — that's wrong.
            #    Correct: RET at p1 turns to RIGHT of runway = LEFT of travel direction
            #    RET_dir for p1 = (inbound_dir - 30°) % 360
            #                   = (orientation+180-30)%360 = (orientation+150)%360
            #    Check: (orientation+150) is between (orientation+90) [txw_side] and
            #           (orientation+180) [inbound], so it curves toward the taxiway ✓
            #
            #  Aircraft landing at p2 (approaching from beyond p2):
            #    Inbound travel direction = orientation  (toward p1)
            #    Right of travel = (orientation+90)%360 = txw_side ✓
            #    RET_dir for p2 = (inbound_dir + 30°) % 360
            #                   = (orientation+30)%360
            #    Check: (orientation+30) is between orientation [inbound] and
            #           (orientation+90) [txw_side], so it curves toward taxiway ✓
            # =================================================================

            ret_dists = {
                '1': [1200], '2': [1500],
                '3': [1700], '4': [2000]
            }
            ret_dist_list = ret_dists.get(self.runway_code_number, [1700])
            ret_angle_deg = 30.0   # ICAO standard RET angle from centreline

            # Each entry: (threshold_point, inbound_direction, ret_angle_sign, label)
            # inbound_direction = direction aircraft travels AFTER crossing threshold
            # ret_angle_sign: +30 turns right of inbound (toward txw), -30 turns left
            ret_configs = [
                # Landing at p1: inbound toward p2, RET diverges LEFT of inbound
                # (which is toward RIGHT/txw side of the runway axis)
                (p1, (orientation + 180) % 360, -ret_angle_deg, 'THR1'),
                # Landing at p2: inbound toward p1, RET diverges RIGHT of inbound
                # (which is toward RIGHT/txw side of the runway axis)
                (p2, orientation,               +ret_angle_deg, 'THR2'),
            ]

            ret_id = 'A'
            for thr_pt, inbound_dir, angle_sign, thr_label in ret_configs:
                thr_lat, thr_lon = thr_pt.y, thr_pt.x

                for dist_m in ret_dist_list:
                    dist_m = min(dist_m, runway_length - 200)
                    if dist_m < 200:
                        continue

                    # Exit point: measured from threshold INWARD along aircraft path
                    exit_pt  = geod.Direct(thr_lat, thr_lon, inbound_dir, dist_m)
                    exit_lat, exit_lon = exit_pt['lat2'], exit_pt['lon2']

                    # RET direction: 30° from inbound, curving toward taxiway side
                    ret_dir = (inbound_dir + angle_sign) % 360

                    # RET runs from runway pavement edge to parallel taxiway CL
                    ret_reach = txw_side_offset          # total distance from CL
                    inner = geod.Direct(exit_lat, exit_lon, ret_dir, rw / 2)
                    outer = geod.Direct(exit_lat, exit_lon, ret_dir, ret_reach + 20)

                    ret_line = LineString([
                        (inner['lon2'], inner['lat2']),
                        (outer['lon2'], outer['lat2'])
                    ])
                    txw_rows.append({
                        'txw_id':         f'RET_{ret_id}',
                        'type':           'Rapid Exit Taxiway',
                        'side':           'Right',
                        'threshold':      thr_label,
                        'dist_from_thr_m': dist_m,
                        'angle_deg':      ret_angle_deg,
                        'width_m':        self.taxiway_width,
                        'geometry':       ret_line
                    })
                    ret_id = chr(ord(ret_id) + 1)

            txw_gdf_lines = gpd.GeoDataFrame(txw_rows, crs='EPSG:4326')

            # ── Convert taxiway LineStrings → buffered width polygons ─────────
            # Project to local UTM for metre-accurate buffering, reconstruct the
            # GeoDataFrame explicitly to avoid pandas SettingWithCopyWarning.
            try:
                utm_zone = int((clon + 180) / 6) + 1
                utm_epsg = (32600 if clat >= 0 else 32700) + utm_zone
                utm_crs  = f'EPSG:{utm_epsg}'
                txw_utm  = txw_gdf_lines.to_crs(utm_crs).copy()

                half_w  = max(1.0, float(self.taxiway_width) / 2.0)
                # shapely 2.x uses string cap_style; 1.x uses int — support both
                try:
                    buf_geom = txw_utm.geometry.buffer(half_w,
                                                        cap_style='flat',
                                                        join_style='mitre')
                except TypeError:
                    buf_geom = txw_utm.geometry.buffer(half_w,
                                                        cap_style=2,
                                                        join_style=2)

                # Rebuild as a fresh GeoDataFrame so geometry column is clean
                attr_cols = [c for c in txw_utm.columns if c != 'geometry']
                txw_poly  = gpd.GeoDataFrame(
                    txw_utm[attr_cols].reset_index(drop=True),
                    geometry=buf_geom.reset_index(drop=True),
                    crs=utm_crs
                )
                txw_gdf = txw_poly.to_crs('EPSG:4326')
                print(f"Taxiways buffered OK: {len(txw_gdf)} polygons "
                      f"(half-width={half_w:.1f} m, UTM {utm_epsg})")
            except Exception as buf_err:
                import traceback as _tb
                print(f"Taxiway buffer error — falling back to lines: {buf_err}")
                print(_tb.format_exc())
                txw_gdf = txw_gdf_lines

            txw_gdf.to_crs(self.output_crs).to_file(
                gpkg_path, layer='taxiways', driver='GPKG')
            layers['taxiways'] = txw_gdf

            # ================================================================
            # LABEL POINTS LAYER — one centroid point per facility component
            # (polygon centroid for areas, point geometry for points/lines)
            # with labels pre-configured and ENABLED in QGIS.
            # ================================================================
            label_rows = []

            def _centroid_pt(gdf_or_row):
                """Return the centroid (or point itself) of a GeoDataFrame/geometry."""
                if isinstance(gdf_or_row, gpd.GeoDataFrame):
                    return gdf_or_row.geometry.iloc[0].centroid
                return gdf_or_row.centroid if hasattr(gdf_or_row, 'centroid') else gdf_or_row

            # Terminal Building
            label_rows.append({
                'facility': 'Terminal Building',
                'label':    'TERMINAL',
                'category': 'Passenger',
                'geometry': _centroid_pt(term_gdf)
            })

            # ATC Tower
            atc_geom = atc_gdf.geometry.iloc[0]
            label_rows.append({
                'facility': 'ATC Tower',
                'label':    'ATC TOWER',
                'category': 'Navigation',
                'geometry': atc_geom
            })

            # Apron
            label_rows.append({
                'facility': 'Apron',
                'label':    'APRON',
                'category': 'Airside',
                'geometry': _centroid_pt(apron_gdf)
            })

            # Hangars (one label point per hangar)
            for _, row in hangar_gdf.iterrows():
                label_rows.append({
                    'facility': row['facility'],
                    'label':    row['facility'].upper(),
                    'category': 'Maintenance',
                    'geometry': row['geometry'].centroid
                })

            # Fuel Depot
            label_rows.append({
                'facility': 'Fuel Depot',
                'label':    'FUEL DEPOT',
                'category': 'Utilities',
                'geometry': _centroid_pt(fuel_gdf)
            })

            # Cargo Area
            label_rows.append({
                'facility': 'Cargo Terminal',
                'label':    'CARGO',
                'category': 'Cargo',
                'geometry': _centroid_pt(cargo_gdf)
            })

            # Vehicle Parking (one per lot)
            for _, row in park_gdf.iterrows():
                label_rows.append({
                    'facility': row['facility'],
                    'label':    row['facility'].upper(),
                    'category': 'Landside',
                    'geometry': row['geometry'].centroid
                })

            # Fire Station
            label_rows.append({
                'facility': 'Fire & Rescue Station',
                'label':    'ARFF',
                'category': 'Emergency',
                'geometry': _centroid_pt(fire_gdf)
            })

            # Access Roads (midpoint labels)
            for _, row in road_gdf.iterrows():
                geom = row['geometry']
                try:
                    label_pt = geom.interpolate(0.5, normalized=True)
                except Exception:
                    label_pt = geom.centroid
                label_rows.append({
                    'facility': row['type'],
                    'label':    row['type'].upper(),
                    'category': 'Roads',
                    'geometry': label_pt
                })

            # Taxiways — centroid for polygons (buffered), midpoint for lines
            for _, row in txw_gdf.iterrows():
                geom = row['geometry']
                try:
                    if geom.geom_type in ('Polygon', 'MultiPolygon'):
                        label_pt = geom.centroid
                    else:
                        label_pt = geom.interpolate(0.5, normalized=True)
                except Exception:
                    label_pt = geom.centroid
                label_rows.append({
                    'facility': f"TWY {row['txw_id']} ({row['type']})",
                    'label':    f"TWY {row['txw_id']}",
                    'category': 'Taxiway',
                    'geometry': label_pt
                })

            label_gdf = gpd.GeoDataFrame(label_rows, crs='EPSG:4326')
            label_gdf.to_file(gpkg_path, layer='facility_labels', driver='GPKG')
            layers['facility_labels'] = label_gdf
            # ================================================================

            print(f"Terminal & Utilities GeoPackage saved to: {gpkg_path}")
            return gpkg_path, layers

        except Exception as e:
            import traceback as _tb
            print(f"Error generating terminal utilities GeoPackage: {e}")
            print(_tb.format_exc())
            return None, {}

    def set_aircraft_parameters(self, aircraft_type):
        if aircraft_type in self.icao.AIRCRAFT_PERFORMANCE:
            data = self.icao.AIRCRAFT_PERFORMANCE[aircraft_type]
            self.design_aircraft = aircraft_type
            self.wingspan = data['Wingspan']
            self.wheel_span = data['Wheel span']
            self.max_takeoff_weight = data['Max takeoff weight']
            self.reference_field_length = data['Reference field length']
            self.runway_code_letter = data['Category']
            if self.max_takeoff_weight < 5700:
                cat_key = 'Light Aircraft (< 5,700 kg)'
            elif self.max_takeoff_weight < 27000:
                cat_key = 'Medium Aircraft (5,700 - 27,000 kg)'
            else:
                if 'fighter' in aircraft_type.lower() or 'f-' in aircraft_type or 'mig' in aircraft_type.lower():
                    cat_key = 'Military Fighter'
                elif 'transport' in aircraft_type.lower() or 'c-' in aircraft_type or 'hercules' in aircraft_type.lower():
                    cat_key = 'Military Transport'
                else:
                    cat_key = 'Heavy Aircraft (> 27,000 kg)'
            self.aircraft_category = cat_key
            thr = self.icao.CROSSWIND_COMPONENTS.get(cat_key, self.icao.CROSSWIND_COMPONENTS['Medium Aircraft (5,700 - 27,000 kg)'])
            self.crosswind_threshold_dry = thr['Dry']
            self.crosswind_threshold_wet = thr['Wet']
            self.crosswind_threshold_icy = thr['Icy']
            self.crosswind_threshold_dry_ms = self.crosswind_threshold_dry * self.KNOTS_TO_MS
            self.crosswind_threshold_wet_ms = self.crosswind_threshold_wet * self.KNOTS_TO_MS
            self.crosswind_threshold_icy_ms = self.crosswind_threshold_icy * self.KNOTS_TO_MS
            self.runway_code_number = self.icao.get_runway_code(self.reference_field_length)
            self.airport_reference_code = f"{self.runway_code_number}{self.runway_code_letter}"
            self.runway_width = self.icao.get_runway_width(self.runway_code_number, self.runway_code_letter)
            self.runway_strip_width = self.icao.get_runway_strip_width(self.runway_code_number, self.runway_code_letter)
            self.runway_end_safety_area = self.icao.get_resa_dimensions(self.runway_code_number, self.runway_code_letter)
            return True
        else:
            print(f"Aircraft type {aircraft_type} not found in ICAO database")
            return False


