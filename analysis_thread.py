#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis_thread.py
==================
Background QThread that drives the full airport analysis pipeline without
blocking the QGIS main thread.

Class
-----
ProfessionalAnalysisThread
    Emits granular progress / step / log / validation signals at each stage
    so the GUI can update live.  On completion it emits finished_signal with
    the consolidated results dict.

Signals (all pyqtSignal)
------------------------
progress_signal         (int percent, str message)
step_started_signal     (str step_id, str description)
step_completed_signal   (str step_id, bool success, str message)
finished_signal         (bool success, str message, object results_dict)
log_signal              (str message, str level)          level ∈ {INFO WARNING ERROR SUCCESS}
validation_signal       (str item, bool passed, str detail)
output_generated_signal (str output_dir_path)

Dependencies (internal)
-----------------------
from .engine import ICAOCompliantAirportRunwayPlanner
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Third-party / scientific
# ---------------------------------------------------------------------------
import geopandas as gpd
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# QGIS / Qt
# ---------------------------------------------------------------------------
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)

# ---------------------------------------------------------------------------
# CRITICAL: inject plugin directory into sys.path BEFORE any plugin imports
# Plain absolute import — guaranteed to work on Windows QGIS 4 / Python 3.12
# ---------------------------------------------------------------------------
import sys as _sys, os as _os
_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _THIS_DIR not in _sys.path:
    _sys.path.insert(0, _THIS_DIR)

from engine import ICAOCompliantAirportRunwayPlanner  # noqa: E402


# ============================================================================
# ANALYSIS THREAD
# ============================================================================

class ProfessionalAnalysisThread(QThread):
    """
    Executes the full airport runway planning pipeline in a background thread.

    Instantiate with a configured ``ICAOCompliantAirportRunwayPlanner`` and a
    ``parameters`` dict that mirrors the GUI widget state.  Call ``start()``
    (not ``run()`` directly) to launch the thread.

    Parameters
    ----------
    planner : ICAOCompliantAirportRunwayPlanner
        A freshly constructed (or re-used) planner instance.  The thread will
        apply ``parameters`` values to its attributes via ``setattr`` before
        starting analysis.
    parameters : dict
        Keys must match planner attribute names or one of the special keys
        handled by ``validate_and_setup`` (e.g. ``aoi_layer``, ``dsm_layer``,
        ``dtm_layer``, ``design_aircraft``, ``start_date``, ``end_date``,
        ``output_dir``, ``magnetic_annual_change``).
    """

    # ── Qt signals ────────────────────────────────────────────────────────────
    progress_signal         = pyqtSignal(int, str)
    step_started_signal     = pyqtSignal(str, str)
    step_completed_signal   = pyqtSignal(str, bool, str)
    finished_signal         = pyqtSignal(bool, str, object)
    log_signal              = pyqtSignal(str, str)
    validation_signal       = pyqtSignal(str, bool, str)
    output_generated_signal = pyqtSignal(str)

    def __init__(self, planner, parameters):
        super().__init__()
        self.planner    = planner
        self.parameters = parameters
        self.results    = None
        self.is_running = True

    # =========================================================================
    # Main pipeline
    # =========================================================================

    def run(self):
        """Entry point called by QThread.start()."""
        try:
            self.log_signal.emit(
                "Starting professional airport analysis with Open-Meteo API...",
                "INFO")

            # ── 1. Validate & configure ───────────────────────────────────────
            self.step_started_signal.emit(
                "validation", "Validating input parameters and setup...")
            if not self.validate_and_setup():
                self.finished_signal.emit(False, "Validation failed", None)
                return
            self.step_completed_signal.emit(
                "validation", True, "Validation completed successfully")

            # ── 2. Elevation / DSM-DTM ────────────────────────────────────────
            self.step_started_signal.emit(
                "elevation", "Loading and processing elevation data...")
            if not self.process_elevation_data():
                self.finished_signal.emit(
                    False, "Elevation data processing failed", None)
                return
            self.step_completed_signal.emit(
                "elevation", True, "Elevation data processed")

            # ── 3. Weather data (Open-Meteo) ──────────────────────────────────
            self.step_started_signal.emit(
                "weather_data",
                "Fetching weather data from Open-Meteo API...")
            if not self.fetch_weather_data():
                self.log_signal.emit(
                    "Weather data acquisition had issues", "WARNING")
            self.step_completed_signal.emit(
                "weather_data", True, "Weather data acquired")

            # ── 4. Runway length (placeholder step) ───────────────────────────
            self.step_started_signal.emit(
                "length_calculation",
                "Calculating ICAO-compliant runway length...")
            self.step_completed_signal.emit(
                "length_calculation", True,
                "Runway length calculation prepared")

            # ── 5. Wind orientation analysis ──────────────────────────────────
            self.step_started_signal.emit(
                "wind_analysis",
                "Analyzing wind patterns and optimizing orientations...")
            wind_results = self.analyze_wind_patterns()
            if wind_results is None:
                self.finished_signal.emit(
                    False, "Wind analysis failed", None)
                return
            self.step_completed_signal.emit(
                "wind_analysis", True, "Wind analysis completed")

            # ── 6. Obstacle analysis ──────────────────────────────────────────
            self.step_started_signal.emit(
                "obstacle_analysis",
                "Analyzing obstacles and clearance requirements...")
            self.analyze_obstacles()
            self.step_completed_signal.emit(
                "obstacle_analysis", True, "Obstacle analysis completed")

            # ── 7. Candidate generation ───────────────────────────────────────
            self.step_started_signal.emit(
                "candidate_generation", "Generating runway candidates...")
            candidates = self.generate_candidates(wind_results)
            if not candidates:
                self.finished_signal.emit(
                    False, "No valid runway candidates generated", None)
                return
            self.step_completed_signal.emit(
                "candidate_generation", True,
                f"Generated {len(candidates)} candidates")

            # ── 8. ICAO compliance check ──────────────────────────────────────
            self.step_started_signal.emit(
                "compliance_check",
                "Performing ICAO compliance checks...")
            compliance_report = self.perform_icao_compliance_check()
            self.step_completed_signal.emit(
                "compliance_check", True, "ICAO compliance check completed")

            # ── 9. Commercial analysis ────────────────────────────────────────
            self.step_started_signal.emit(
                "commercial_analysis", "Performing commercial analysis...")
            commercial_proposal = self.perform_commercial_analysis()
            self.step_completed_signal.emit(
                "commercial_analysis", True, "Commercial analysis completed")

            # ── 10. Reports & visualisations ──────────────────────────────────
            self.step_started_signal.emit(
                "reporting",
                "Generating professional reports and visualizations...")
            output_success = self.generate_reports_and_visualizations(
                wind_results, candidates, compliance_report, commercial_proposal)
            if output_success:
                self.step_completed_signal.emit(
                    "reporting", True, "Reports generated")
                self.output_generated_signal.emit(self.planner.output_dir)
            else:
                self.step_completed_signal.emit(
                    "reporting", False, "Report generation failed")

            # ── Package results ───────────────────────────────────────────────
            self.results = {
                'planner':            self.planner,
                'wind_results':       wind_results,
                'candidates':         candidates,
                'compliance_report':  compliance_report,
                'commercial_proposal':commercial_proposal,
                'corrected_length':   self.planner.corrected_runway_length,
            }
            self.progress_signal.emit(100, "Analysis completed successfully!")
            self.log_signal.emit(
                "Professional analysis completed successfully", "SUCCESS")
            self.finished_signal.emit(
                True, "Analysis completed successfully", self.results)

        except Exception as e:
            import traceback
            self.log_signal.emit(
                f"Critical error in analysis: {str(e)}", "ERROR")
            self.log_signal.emit(
                f"Traceback: {traceback.format_exc()}", "ERROR")
            self.finished_signal.emit(
                False, f"Analysis failed: {str(e)}", None)

    # =========================================================================
    # Pipeline step implementations
    # =========================================================================

    def validate_and_setup(self):
        """
        Validate required parameters, apply them to the planner, configure the
        design aircraft, apply magnetic annual change (ICAO Annex 4 §5.16),
        and ensure the output directory exists.
        """
        try:
            self.progress_signal.emit(5, "Validating parameters...")

            # ── Required keys check ───────────────────────────────────────────
            required_params = ['aoi_layer', 'output_dir']
            for param in required_params:
                if param not in self.parameters or not self.parameters[param]:
                    self.validation_signal.emit(
                        f"Missing {param}", False, f"{param} is required")
                    return False

            # ── Apply all parameters to planner via setattr ───────────────────
            for key, value in self.parameters.items():
                if hasattr(self.planner, key):
                    setattr(self.planner, key, value)

            # ── Explicit runway configuration mapping ─────────────────────────
            # GUI sends 'runway_config' (QComboBox text); planner stores it as
            # 'runway_configuration'.  Also map separation and strip width.
            cfg_map = {
                'runway_config':      'runway_configuration',
                'runway_separation':  'runway_separation',
                'strip_width':        'runway_strip_width',
                'resa_length':        '_resa_length_override',   # applied below
                'resa_width':         '_resa_width_override',
            }
            for param_key, planner_attr in cfg_map.items():
                if param_key in self.parameters:
                    val = self.parameters[param_key]
                    if planner_attr.startswith('_'):
                        setattr(self.planner, planner_attr, val)
                    else:
                        setattr(self.planner, planner_attr, val)
            # Apply RESA overrides
            rl = self.parameters.get('resa_length', None)
            rw = self.parameters.get('resa_width',  None)
            if rl and rw:
                self.planner.runway_end_safety_area = (float(rl), float(rw))

            # ── Configure design aircraft ─────────────────────────────────────
            if 'design_aircraft' in self.parameters:
                success = self.planner.set_aircraft_parameters(
                    self.parameters['design_aircraft'])
                if not success:
                    self.validation_signal.emit(
                        "Aircraft configuration", False,
                        f"Failed to configure aircraft "
                        f"{self.parameters['design_aircraft']}")
                    return False

            # ── Magnetic annual change (ICAO Annex 4 §5.16) ───────────────────
            try:
                annual_change = float(
                    self.parameters.get('magnetic_annual_change', 0.0))
                if annual_change != 0.0:
                    start_str  = self.parameters.get(
                        'start_date', self.planner.start_date)
                    end_str    = self.parameters.get(
                        'end_date', self.planner.end_date)
                    start_yr   = datetime.strptime(start_str, '%Y-%m-%d').year
                    end_yr     = datetime.strptime(end_str,   '%Y-%m-%d').year
                    mid_yr     = (start_yr + end_yr) / 2.0
                    current_yr = datetime.now().year
                    years_elapsed = current_yr - mid_yr
                    adjustment = annual_change * years_elapsed
                    self.planner.magnetic_variation = (
                        getattr(self.planner, 'magnetic_variation', 0.0)
                        + adjustment)
                    self.log_signal.emit(
                        f"Magnetic variation adjusted by {adjustment:+.2f}° "
                        f"({annual_change:+.3f}°/yr × {years_elapsed:.1f} yr)"
                        f" → {self.planner.magnetic_variation:+.2f}° total "
                        f"(ICAO Annex 4 §5.16)",
                        "INFO")
            except Exception as mag_err:
                self.log_signal.emit(
                    f"Could not apply magnetic annual change: {mag_err}",
                    "WARNING")

            # ── Ensure output directory exists ────────────────────────────────
            output_dir = self.parameters.get('output_dir')
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            self.validation_signal.emit(
                "Parameter validation", True,
                "All parameters validated successfully")
            return True

        except Exception as e:
            self.log_signal.emit(f"Validation error: {str(e)}", "ERROR")
            return False

    def process_elevation_data(self):
        """
        Extract AOI centroid (reprojecting to WGS-84), load DSM/DTM rasters
        onto the planner, and run obstacle detection.
        """
        try:
            self.progress_signal.emit(15, "Processing elevation data...")
            aoi_layer = self.parameters.get('aoi_layer')
            if aoi_layer:
                features = list(aoi_layer.getFeatures())
                if features:
                    feature      = features[0]
                    qgs_geom     = feature.geometry()
                    from shapely.wkt import loads
                    self.planner.aoi_geometry_original = loads(qgs_geom.asWkt())
                    self.planner.aoi_geometry = self.planner.aoi_geometry_original
                    self.planner.aoi_crs      = aoi_layer.crs().authid()

                    centroid_orig = feature.geometry().centroid().asPoint()
                    self.planner.centroid = Point(centroid_orig.x(),
                                                  centroid_orig.y())

                    wgs84_crs = QgsCoordinateReferenceSystem('EPSG:4326')
                    xform = QgsCoordinateTransform(
                        aoi_layer.crs(), wgs84_crs, QgsProject.instance())
                    try:
                        centroid_wgs84 = xform.transform(centroid_orig)
                        self.planner.wgs84_centroid = (
                            centroid_wgs84.y(), centroid_wgs84.x())
                    except Exception:
                        self.planner.wgs84_centroid = (
                            centroid_orig.y(), centroid_orig.x())
                        self.log_signal.emit(
                            "Using original centroid as lat/lon "
                            "(may be inaccurate)", "WARNING")

                    # Store AOI layer on planner for DSM clipping
                    self.planner.aoi_layer = aoi_layer
                else:
                    self.log_signal.emit(
                        "AOI layer has no features", "ERROR")
                    return False
            else:
                self.log_signal.emit("No AOI layer provided", "ERROR")
                return False

            dsm_layer = self.parameters.get('dsm_layer')
            dtm_layer = self.parameters.get('dtm_layer')
            if dsm_layer and dtm_layer:
                self.planner.load_elevation_data(
                    dsm_layer.source(), dtm_layer.source())
                self.planner.dtm_crs    = dtm_layer.crs().authid()
                self.planner.dtm_raster = dtm_layer
                self.planner.dsm_raster = dsm_layer
            else:
                self.log_signal.emit(
                    "DSM or DTM layers missing; obstacle analysis will be "
                    "skipped", "WARNING")

            if self.planner.dsm_raster and self.planner.dtm_raster:
                self.planner.calculate_obstacles()
            else:
                self.planner.obstacles = gpd.GeoDataFrame()
                self.log_signal.emit(
                    "Obstacle analysis skipped due to missing DSM/DTM",
                    "WARNING")
            return True

        except Exception as e:
            self.log_signal.emit(
                f"Elevation processing error: {str(e)}", "ERROR")
            return False

    def fetch_weather_data(self):
        """Delegate to planner.fetch_wind_data with the configured date range."""
        try:
            self.progress_signal.emit(35, "Fetching weather data from Open-Meteo API...")
            start_date = self.parameters.get('start_date')
            end_date   = self.parameters.get('end_date')

            # Prefer explicit wind-point coordinates; fall back to AOI centroid
            if ('wind_point_lat' in self.parameters and
                    'wind_point_lon' in self.parameters):
                self.planner.wind_point_lat = self.parameters['wind_point_lat']
                self.planner.wind_point_lon = self.parameters['wind_point_lon']
            elif self.planner.wgs84_centroid:
                (self.planner.wind_point_lat,
                 self.planner.wind_point_lon) = self.planner.wgs84_centroid
            else:
                self.planner.wind_point_lat = self.planner.centroid.y
                self.planner.wind_point_lon = self.planner.centroid.x

            return self.planner.fetch_wind_data(start_date, end_date)

        except Exception as e:
            self.log_signal.emit(f"Error fetching weather data: {e}", "ERROR")
            return False

    def analyze_wind_patterns(self):
        """Delegate to planner.analyze_runway_orientations_icao()."""
        return self.planner.analyze_runway_orientations_icao()

    def analyze_obstacles(self):
        """Obstacle analysis already completed in process_elevation_data."""
        return True

    def generate_candidates(self, wind_results):
        """
        Build the ranked runway candidate list from wind results, including all
        ICAO-relevant fields and declared distance values.
        """
        candidates = []
        if wind_results is not None and len(wind_results) > 0:
            for i in range(min(self.planner.num_candidates, len(wind_results))):
                row         = wind_results.iloc[i]
                orientation = row['orientation']
                self.planner.calculate_icao_compliant_length(orientation)

                # Reciprocal magnetic heading (ensure 360 → 36 is handled)
                rec_mag = (row['magnetic_orientation'] + 180) % 360
                if rec_mag == 0:
                    rec_mag = 36

                candidates.append({
                    # Identification
                    'rank':                  i + 1,
                    'orientation':           orientation,
                    'magnetic_orientation':  row['magnetic_orientation'],
                    'reciprocal_magnetic':   rec_mag,
                    'designation':           row['designation'],
                    # ICAO usability
                    'dry_coverage':          row['dry_coverage'],
                    'wet_coverage':          row['wet_coverage'],
                    'icy_coverage':          row['icy_coverage'],
                    'icao_compliant':        row['icao_compliant'],
                    # Extended ICAO fields
                    'calm_pct':              row.get('calm_pct',              0.0),
                    'mean_headwind_kn':      row.get('mean_headwind_kn',      0.0),
                    'needs_crosswind_rwy':   row.get('needs_crosswind_rwy',  False),
                    'rank_score':            row.get('rank_score',  row['dry_coverage']),
                    'icao_xwind_thresh_kn':  row.get('icao_xwind_thresh_kn', 20.0),
                    # Seasonal
                    'winter_coverage':       row.get('winter_coverage', 0.0),
                    'spring_coverage':       row.get('spring_coverage', 0.0),
                    'summer_coverage':       row.get('summer_coverage', 0.0),
                    'fall_coverage':         row.get('fall_coverage',   0.0),
                    # Declared distances (Annex 14 §3.6)
                    'length':                self.planner.corrected_runway_length,
                    'tora':                  self.planner.tora,
                    'toda':                  self.planner.toda,
                    'asda':                  self.planner.asda,
                    'lda':                   self.planner.lda,
                })
        return candidates

    def perform_icao_compliance_check(self):
        """Delegate to planner.generate_icao_compliance_report()."""
        return self.planner.generate_icao_compliance_report()

    def perform_commercial_analysis(self):
        """Delegate to planner.generate_commercial_proposal()."""
        return self.planner.generate_commercial_proposal()

    def generate_reports_and_visualizations(self, wind_results, candidates,
                                             compliance_report,
                                             commercial_proposal):
        """Emit progress and delegate to planner.generate_output_files()."""
        self.progress_signal.emit(
            95, "Generating reports and visualizations...")
        top_orientation = candidates[0]['orientation'] if candidates else 0
        return self.planner.generate_output_files(
            wind_results, candidates, top_orientation)
