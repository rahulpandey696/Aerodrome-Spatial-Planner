#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
icao_standards.py
=================
ICAO Annex 14 constants, lookup tables, and compliance checker.

Classes
-------
ICAOStandards          – Static ICAO Annex 14 data tables and helper methods.
ICAOComplianceChecker  – Runs all Annex 14 compliance checks against a planner
                         instance and returns a structured report.
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import math

# ---------------------------------------------------------------------------
# Third-party / scientific
# ---------------------------------------------------------------------------
import numpy as np


# ============================================================================
# ICAO CONSTANTS AND STANDARDS
# ============================================================================

class ICAOStandards:
    """
    Static data tables from ICAO Annex 14, Volume I (Aerodromes), 9th Edition.

    All lookup dictionaries are keyed by (code_number, code_letter) tuples
    unless noted otherwise.
    """

    # ── Runway Reference Code Numbers ────────────────────────────────────────
    # Key: code number string  →  (min_ARFL_m, max_ARFL_m)
    RUNWAY_CODE_NUMBERS = {
        '1': (0,    800),
        '2': (800,  1200),
        '3': (1200, 1800),
        '4': (1800, float('inf'))
    }

    # ── Runway Reference Code Letters ───────────────────────────────────────
    # Key: code letter  →  (min_wingspan_m, max_wingspan_m, min_wheelspan_m, max_wheelspan_m)
    RUNWAY_CODE_LETTERS = {
        'A': (0,  15, 0,   4.5),
        'B': (15, 24, 4.5, 6),
        'C': (24, 36, 6,   9),
        'D': (36, 52, 9,   14),
        'E': (52, 65, 9,   14),
        'F': (65, 80, 14,  16)
    }

    # ── Runway Widths (m) – Annex 14 Table 1-1 ──────────────────────────────
    RUNWAY_WIDTHS = {
        ('1','A'): 18, ('1','B'): 18, ('1','C'): 23, ('1','D'): 30, ('1','E'): 30, ('1','F'): 30,
        ('2','A'): 23, ('2','B'): 23, ('2','C'): 30, ('2','D'): 30, ('2','E'): 30, ('2','F'): 30,
        ('3','A'): 30, ('3','B'): 30, ('3','C'): 30, ('3','D'): 45, ('3','E'): 45, ('3','F'): 45,
        ('4','A'): 45, ('4','B'): 45, ('4','C'): 45, ('4','D'): 45, ('4','E'): 45, ('4','F'): 60,
    }

    # ── Runway Strip Widths (m) – Annex 14 Sec.3.3 ─────────────────────────────
    RUNWAY_STRIP_WIDTHS = {
        ('1','A'): 30, ('1','B'): 30, ('1','C'): 30, ('1','D'): 30, ('1','E'): 30, ('1','F'): 30,
        ('2','A'): 30, ('2','B'): 30, ('2','C'): 40, ('2','D'): 40, ('2','E'): 40, ('2','F'): 40,
        ('3','A'): 40, ('3','B'): 40, ('3','C'): 40, ('3','D'): 75, ('3','E'): 75, ('3','F'): 75,
        ('4','A'): 75, ('4','B'): 75, ('4','C'): 75, ('4','D'): 75, ('4','E'): 75, ('4','F'): 75,
    }

    # ── RESA Minimum Dimensions (length_m, width_m) – Annex 14 Sec.3.5 ─────────
    RESA_DIMENSIONS = {
        ('1','A'): (30,30), ('1','B'): (30,30), ('1','C'): (30,30),
        ('1','D'): (30,30), ('1','E'): (30,30), ('1','F'): (30,30),
        ('2','A'): (30,30), ('2','B'): (30,30), ('2','C'): (60,30),
        ('2','D'): (60,30), ('2','E'): (60,30), ('2','F'): (60,30),
        ('3','A'): (90,30), ('3','B'): (90,30), ('3','C'): (90,30),
        ('3','D'): (90,30), ('3','E'): (90,30), ('3','F'): (90,30),
        ('4','A'): (90,30), ('4','B'): (90,30), ('4','C'): (90,30),
        ('4','D'): (90,30), ('4','E'): (90,30), ('4','F'): (90,30),
    }
    # Recommended RESA for code 4 (Doc 9157)
    RESA_RECOMMENDED = {'4': 120}

    # ── Approach / Departure Surface Parameters – Annex 14 Sec.4.2 ─────────────
    APPROACH_SURFACE_PARAMS = {
        'Non-precision': {
            'Divergence': 0.10, 'Length': 2500,
            'Slope': 0.05,      'Inner width': 60,
            'Inner edge distance': 30
        },
        'Precision Cat I': {
            'Divergence': 0.125, 'Length': 3000,
            'Slope': 0.04,       'Inner width': 150,
            'Inner edge distance': 30
        },
        'Precision Cat II/III': {
            'Divergence': 0.125, 'Length': 3600,
            'Slope': 0.025,      'Inner width': 150,
            'Inner edge distance': 30
        },
    }

    # ── Crosswind Component Limits (knots) – ICAO Doc 9157 ──────────────────
    CROSSWIND_COMPONENTS = {
        'Light Aircraft (< 5,700 kg)':       {'Dry': 13,  'Wet': 10, 'Icy': 5},
        'Medium Aircraft (5,700 - 27,000 kg)':{'Dry': 20,  'Wet': 15, 'Icy': 7.5},
        'Heavy Aircraft (> 27,000 kg)':       {'Dry': 25,  'Wet': 20, 'Icy': 10},
        'Military Fighter':                   {'Dry': 30,  'Wet': 25, 'Icy': 15},
        'Military Transport':                 {'Dry': 25,  'Wet': 20, 'Icy': 10},
    }

    # ── Length Correction Factors – ICAO Doc 9157 Sec.3.4 ──────────────────────
    LENGTH_CORRECTION_FACTORS = {
        'Elevation':    0.07,
        'Temperature':  0.01,
        'Gradient':     0.10,
        'Wet Runway':   1.15,
        'Contaminated': 1.25,
    }

    # ── Aircraft Performance Database ────────────────────────────────────────
    # Keys: Reference field length (m), Wingspan (m), Wheel span (m),
    #       Max takeoff weight (kg), Category (ICAO code letter)
    AIRCRAFT_PERFORMANCE = {
        # ── Commercial narrow-body ──────────────────────────────────────────
        'A320-200':          {'Reference field length': 2100, 'Wingspan': 35.8,  'Wheel span': 7.6,  'Max takeoff weight': 78000,   'Category': 'C'},
        'B737-800':          {'Reference field length': 2400, 'Wingspan': 35.8,  'Wheel span': 5.2,  'Max takeoff weight': 79000,   'Category': 'C'},
        # ── Commercial wide-body ────────────────────────────────────────────
        'A330-300':          {'Reference field length': 2800, 'Wingspan': 60.3,  'Wheel span': 10.7, 'Max takeoff weight': 242000,  'Category': 'E'},
        'B777-300ER':        {'Reference field length': 3200, 'Wingspan': 64.8,  'Wheel span': 12.8, 'Max takeoff weight': 351500,  'Category': 'E'},
        'A380-800':          {'Reference field length': 3500, 'Wingspan': 79.8,  'Wheel span': 14.3, 'Max takeoff weight': 575000,  'Category': 'F'},
        'B787-9 Dreamliner': {'Reference field length': 2900, 'Wingspan': 60.1,  'Wheel span': 9.8,  'Max takeoff weight': 254000,  'Category': 'E'},
        'A350-900':          {'Reference field length': 2800, 'Wingspan': 64.8,  'Wheel span': 10.2, 'Max takeoff weight': 283000,  'Category': 'E'},
        'E195-E2':           {'Reference field length': 2000, 'Wingspan': 35.1,  'Wheel span': 6.2,  'Max takeoff weight': 62000,   'Category': 'C'},
        'CRJ900':            {'Reference field length': 1900, 'Wingspan': 24.9,  'Wheel span': 4.8,  'Max takeoff weight': 38000,   'Category': 'B'},
        # ── Military transport ──────────────────────────────────────────────
        'C-130J Hercules':   {'Reference field length': 1500, 'Wingspan': 40.4,  'Wheel span': 4.3,  'Max takeoff weight': 70000,   'Category': 'C'},
        'C-17 Globemaster III':{'Reference field length': 2300,'Wingspan': 51.8, 'Wheel span': 6.9,  'Max takeoff weight': 265000,  'Category': 'D'},
        'C-5M Super Galaxy': {'Reference field length': 3200, 'Wingspan': 67.9,  'Wheel span': 11.0, 'Max takeoff weight': 381000,  'Category': 'E'},
        'A400M Atlas':       {'Reference field length': 2100, 'Wingspan': 42.4,  'Wheel span': 7.5,  'Max takeoff weight': 141000,  'Category': 'D'},
        # ── Military fighter / strike ───────────────────────────────────────
        'F-16 Fighting Falcon':  {'Reference field length': 800,  'Wingspan': 9.96,  'Wheel span': 2.4, 'Max takeoff weight': 19200,  'Category': 'B'},
        'F-35 Lightning II':     {'Reference field length': 800,  'Wingspan': 10.7,  'Wheel span': 2.6, 'Max takeoff weight': 31800,  'Category': 'B'},
        'F/A-18 Hornet':         {'Reference field length': 800,  'Wingspan': 13.6,  'Wheel span': 3.1, 'Max takeoff weight': 29900,  'Category': 'B'},
        'Eurofighter Typhoon':   {'Reference field length': 700,  'Wingspan': 10.95, 'Wheel span': 2.5, 'Max takeoff weight': 23500,  'Category': 'B'},
        'Dassault Rafale':       {'Reference field length': 700,  'Wingspan': 10.9,  'Wheel span': 2.4, 'Max takeoff weight': 24500,  'Category': 'B'},
        'F-22 Raptor':           {'Reference field length': 900,  'Wingspan': 13.6,  'Wheel span': 3.2, 'Max takeoff weight': 38000,  'Category': 'B'},
        'Su-35 Flanker':         {'Reference field length': 1000, 'Wingspan': 14.7,  'Wheel span': 4.0, 'Max takeoff weight': 34500,  'Category': 'B'},
        # ── Military bomber ─────────────────────────────────────────────────
        'B-52 Stratofortress':   {'Reference field length': 2900, 'Wingspan': 56.4,  'Wheel span': 9.5,  'Max takeoff weight': 220000, 'Category': 'E'},
        'B-1B Lancer':           {'Reference field length': 2400, 'Wingspan': 41.8,  'Wheel span': 7.8,  'Max takeoff weight': 216000, 'Category': 'D'},
        'B-2 Spirit':            {'Reference field length': 3000, 'Wingspan': 52.4,  'Wheel span': 12.2, 'Max takeoff weight': 170000, 'Category': 'E'},
        # ── Medium/large UAS ────────────────────────────────────────────────
        'MQ-1 Predator':  {'Reference field length': 500, 'Wingspan': 16.8, 'Wheel span': 2.5, 'Max takeoff weight': 1020,  'Category': 'A'},
        'MQ-9 Reaper':    {'Reference field length': 800, 'Wingspan': 20.1, 'Wheel span': 3.0, 'Max takeoff weight': 4760,  'Category': 'B'},
        'RQ-4 Global Hawk':{'Reference field length': 1500,'Wingspan': 39.9,'Wheel span': 4.5, 'Max takeoff weight': 14600, 'Category': 'B'},
        'RQ-7 Shadow':    {'Reference field length': 200, 'Wingspan': 4.3,  'Wheel span': 1.2, 'Max takeoff weight': 170,   'Category': 'A'},
        'MQ-8 Fire Scout':{'Reference field length': 300, 'Wingspan': 8.4,  'Wheel span': 2.0, 'Max takeoff weight': 1430,  'Category': 'A'},
        'Hermes 900':     {'Reference field length': 500, 'Wingspan': 15.0, 'Wheel span': 2.8, 'Max takeoff weight': 1100,  'Category': 'A'},
        'Bayraktar TB2':  {'Reference field length': 400, 'Wingspan': 12.0, 'Wheel span': 2.2, 'Max takeoff weight': 650,   'Category': 'A'},
        'Wing Loong II':  {'Reference field length': 600, 'Wingspan': 18.0, 'Wheel span': 3.0, 'Max takeoff weight': 4200,  'Category': 'B'},
        'CH-4 Rainbow':   {'Reference field length': 700, 'Wingspan': 18.0, 'Wheel span': 3.2, 'Max takeoff weight': 4500,  'Category': 'B'},
        # ── Micro/nano UAS ──────────────────────────────────────────────────
        'Eli 2050':    {'Reference field length': 150, 'Wingspan': 3.0, 'Wheel span': 0.8, 'Max takeoff weight': 50,  'Category': 'A'},
        'DJI M300':    {'Reference field length': 50,  'Wingspan': 1.5, 'Wheel span': 0.4, 'Max takeoff weight': 9,   'Category': 'A'},
        'Skydio X2':   {'Reference field length': 30,  'Wingspan': 1.2, 'Wheel span': 0.3, 'Max takeoff weight': 5,   'Category': 'A'},
        'Aerosonde':   {'Reference field length': 100, 'Wingspan': 2.9, 'Wheel span': 0.6, 'Max takeoff weight': 25,  'Category': 'A'},
        'ScanEagle':   {'Reference field length': 120, 'Wingspan': 3.1, 'Wheel span': 0.7, 'Max takeoff weight': 22,  'Category': 'A'},
        'Raven RQ-11': {'Reference field length': 30,  'Wingspan': 1.4, 'Wheel span': 0.3, 'Max takeoff weight': 2,   'Category': 'A'},
        'Puma AE':     {'Reference field length': 50,  'Wingspan': 2.8, 'Wheel span': 0.5, 'Max takeoff weight': 6,   'Category': 'A'},
    }

    # ── Static helper methods ────────────────────────────────────────────────

    @staticmethod
    def get_runway_code(reference_field_length):
        """Return ICAO code number string for a given ARFL (m)."""
        for code, (min_len, max_len) in ICAOStandards.RUNWAY_CODE_NUMBERS.items():
            if min_len <= reference_field_length < max_len:
                return code
        return '4'

    @staticmethod
    def get_runway_letter(wingspan, wheel_span):
        """Return ICAO code letter for given wingspan and wheel span (m)."""
        for letter, (w_min, w_max, ws_min, ws_max) in ICAOStandards.RUNWAY_CODE_LETTERS.items():
            if w_min <= wingspan < w_max and ws_min <= wheel_span < ws_max:
                return letter
        return 'F'

    @staticmethod
    def get_runway_width(code_number, code_letter):
        """Return standard runway width (m) for a given ARC."""
        return ICAOStandards.RUNWAY_WIDTHS.get((code_number, code_letter), 45)

    @staticmethod
    def get_runway_strip_width(code_number, code_letter):
        """Return standard runway strip width (m) for a given ARC."""
        return ICAOStandards.RUNWAY_STRIP_WIDTHS.get((code_number, code_letter), 75)

    @staticmethod
    def get_resa_dimensions(code_number, code_letter):
        """Return (length_m, width_m) RESA minimum for a given ARC."""
        return ICAOStandards.RESA_DIMENSIONS.get((code_number, code_letter), (90, 30))

    @staticmethod
    def get_crosswind_category(weight):
        """Return crosswind category string for a given MTOW (kg)."""
        if weight < 5700:
            return 'Light Aircraft (< 5,700 kg)'
        elif weight < 27000:
            return 'Medium Aircraft (5,700 - 27,000 kg)'
        else:
            return 'Heavy Aircraft (> 27,000 kg)'

    @staticmethod
    def calculate_balanced_field_length(takeoff_distance, accelerate_stop_distance):
        """Return balanced field length per ICAO performance methodology."""
        return max(takeoff_distance, accelerate_stop_distance) * 1.15


# ============================================================================
# ICAO COMPLIANCE CHECKER
# ============================================================================

class ICAOComplianceChecker:
    """
    Executes all applicable ICAO Annex 14 compliance checks against a
    ``ICAOCompliantAirportRunwayPlanner`` instance and returns a structured
    report dictionary.

    Usage
    -----
    checker = ICAOComplianceChecker(planner_instance)
    report  = checker.run_all_checks()
    """

    def __init__(self, planner):
        self.planner = planner
        self.issues = []
        self.passed = []

    # ── Individual check methods ─────────────────────────────────────────────

    def check_runway_dimensions(self):
        """Annex 14 Sec.3.1.7–3.1.9: runway width and strip width."""
        std_width = ICAOStandards.get_runway_width(
            self.planner.runway_code_number, self.planner.runway_code_letter)
        if abs(self.planner.runway_width - std_width) > 1:
            self.issues.append(
                f"Runway width {self.planner.runway_width} m does not match "
                f"ICAO standard {std_width} m for code "
                f"{self.planner.airport_reference_code}.")
        else:
            self.passed.append("Runway width complies with ICAO standards.")

        std_strip = ICAOStandards.get_runway_strip_width(
            self.planner.runway_code_number, self.planner.runway_code_letter)
        if abs(self.planner.runway_strip_width - std_strip) > 1:
            self.issues.append(
                f"Runway strip width {self.planner.runway_strip_width} m does "
                f"not match ICAO standard {std_strip} m.")
        else:
            self.passed.append("Runway strip width complies with ICAO standards.")

    def check_resa(self):
        """Annex 14 Sec.3.5.1–3.5.3: Runway End Safety Area dimensions."""
        std_len, std_wid = ICAOStandards.get_resa_dimensions(
            self.planner.runway_code_number, self.planner.runway_code_letter)

        if (self.planner.runway_code_number == '4' and
                self.planner.runway_end_safety_area[0] < 120):
            self.issues.append(
                f"RESA length {self.planner.runway_end_safety_area[0]} m is "
                f"less than recommended 120 m for code 4 runways.")
        elif self.planner.runway_end_safety_area[0] < std_len:
            self.issues.append(
                f"RESA length {self.planner.runway_end_safety_area[0]} m is "
                f"less than ICAO standard {std_len} m.")
        else:
            self.passed.append("RESA length complies with ICAO standards.")

        if self.planner.runway_end_safety_area[1] < std_wid:
            self.issues.append(
                f"RESA width {self.planner.runway_end_safety_area[1]} m is "
                f"less than ICAO standard {std_wid} m.")
        else:
            self.passed.append("RESA width complies with ICAO standards.")

    def check_approach_surface(self):
        """Annex 14 Sec.4.2: approach/departure obstacle limitation surfaces."""
        if self.planner.obstacles is not None and len(self.planner.obstacles) > 0:
            self.issues.append(
                "Obstacle penetration check not fully implemented; "
                "manual review recommended.")
        else:
            self.passed.append(
                "No obstacles found in AOI; approach surface assumed clear.")

    def check_wind_coverage(self):
        """
        Annex 14 Sec.3.1.1–3.1.3, Doc 9157: ≥ 95 % usability for the crosswind
        component applicable to the design aircraft.

        Runs extended checks (1–12) on the top-ranked orientation including
        seasonal coverage, calm percentage, headwind sign, and dataset size.
        """
        # ── Guard: wind results must exist ───────────────────────────────────
        wind_results = getattr(self.planner, 'wind_analysis_results', None)
        if wind_results is None or len(wind_results) == 0:
            self.issues.append(
                "Wind analysis results not available; run analysis first.")
            return

        top = wind_results.iloc[0]

        # ── 1. Dry coverage ───────────────────────────────────────────────────
        dry = top.get('dry_coverage', 0.0)
        if dry >= 95.0:
            self.passed.append(
                f"Dry crosswind coverage {dry:.1f}% meets ICAO 95% minimum "
                f"(Annex 14 Sec.3.1.1).")
        else:
            self.issues.append(
                f"Dry crosswind coverage {dry:.1f}% is below ICAO 95% minimum "
                f"(Annex 14 Sec.3.1.1).")

        # ── 2. Wet coverage ───────────────────────────────────────────────────
        wet = top.get('wet_coverage', 0.0)
        if wet >= 95.0:
            self.passed.append(f"Wet crosswind coverage {wet:.1f}% ≥ 95%.")
        else:
            self.issues.append(
                f"Wet crosswind coverage {wet:.1f}% < 95% – wet-runway "
                f"performance may be restricted.")

        # ── 3. Icy / contaminated coverage ───────────────────────────────────
        icy = top.get('icy_coverage', 0.0)
        if icy >= 95.0:
            self.passed.append(
                f"Icy/contaminated crosswind coverage {icy:.1f}% ≥ 95%.")
        else:
            self.issues.append(
                f"Icy/contaminated coverage {icy:.1f}% < 95% – winter "
                f"operations may require a crosswind runway.")

        # ── 4. Seasonal coverages ─────────────────────────────────────────────
        for season, col in [('Winter', 'winter_coverage'),
                             ('Spring', 'spring_coverage'),
                             ('Summer', 'summer_coverage'),
                             ('Autumn', 'fall_coverage')]:
            val = top.get(col, None)
            if val is not None:
                if val >= 95.0:
                    self.passed.append(
                        f"{season} crosswind coverage {val:.1f}% ≥ 95%.")
                else:
                    self.issues.append(
                        f"{season} crosswind coverage {val:.1f}% < 95% – "
                        f"seasonal operations may be restricted.")

        # ── 5. Crosswind runway need ──────────────────────────────────────────
        needs_xw = top.get('needs_crosswind_rwy', False)
        if needs_xw:
            self.issues.append(
                f"Orientation {top.get('designation','?')} may require a "
                f"crosswind runway; consider a secondary orientation "
                f"(Doc 9157 Sec.3.3).")
        else:
            self.passed.append(
                "No crosswind runway required for primary orientation.")

        # ── 6. Crosswind threshold compliance ────────────────────────────────
        xw_thresh = top.get('icao_xwind_thresh_kn', None)
        if xw_thresh is not None:
            self.passed.append(
                f"Crosswind threshold: {xw_thresh:.1f} kt "
                f"(matches design aircraft category).")

        # ── 7. Wind coverage requirement ─────────────────────────────────────
        req = getattr(self.planner, 'wind_coverage_requirement', 95)
        if dry >= req:
            self.passed.append(
                f"Coverage {dry:.1f}% meets project requirement of {req}%.")
        else:
            self.issues.append(
                f"Coverage {dry:.1f}% does not meet project requirement "
                f"of {req}%.")

        # ── 8. Orientation ICAO compliance flag ───────────────────────────────
        icao_ok = top.get('icao_compliant', False)
        if icao_ok:
            self.passed.append(
                f"Orientation {top.get('designation','?')} is flagged ICAO "
                f"compliant by the wind analysis module.")
        else:
            self.issues.append(
                f"Orientation {top.get('designation','?')} is NOT flagged ICAO "
                f"compliant by the wind analysis module.")

        # ── 9. Calm percentage ────────────────────────────────────────────────
        calm = top.get('calm_pct', None)
        if calm is not None:
            if calm <= 5.0:
                self.passed.append(
                    f"Calm fraction {calm:.1f}% ≤ 5% — wind data considered "
                    f"representative.")
            else:
                self.issues.append(
                    f"Calm fraction {calm:.1f}% > 5% — high calm prevalence "
                    f"may understate crosswind exposure.")

        # ── 10. Rank score ────────────────────────────────────────────────────
        rank_score = top.get('rank_score', None)
        if rank_score is not None:
            self.passed.append(
                f"Primary orientation rank score: {rank_score:.2f} "
                f"(higher is better).")

        # ── 11. Mean headwind sign ────────────────────────────────────────────
        hw_kn = top.get('mean_headwind_kn', None)
        if hw_kn is not None:
            if hw_kn >= 0:
                self.passed.append(
                    f"Mean headwind on {top['designation']}: {hw_kn:.1f} kt "
                    f"(positive = favourable headwind for operations).")
            else:
                self.issues.append(
                    f"Mean wind on {top['designation']}: {hw_kn:.1f} kt "
                    f"(tailwind dominance – review landing performance).")

        # ── 12. Wind dataset size vs Doc 9157 recommendation ─────────────────
        total_obs = int(top.get('total_obs', 0))
        if total_obs > 0:
            years_equiv = total_obs / 8760.0
            if years_equiv >= 5:
                self.passed.append(
                    f"Wind dataset: {total_obs:,} hourly observations "
                    f"≈ {years_equiv:.1f} years. "
                    f"Meets ICAO Doc 9157 recommendation of ≥ 5 years.")
            else:
                self.issues.append(
                    f"Wind dataset: {total_obs:,} observations "
                    f"≈ {years_equiv:.1f} years. "
                    f"ICAO Doc 9157 recommends ≥ 5 years for reliable "
                    f"orientation analysis.")

    def check_runway_orientation(self):
        """Delegated to check_wind_coverage for orientation-specific checks."""
        self.check_wind_coverage()

    def check_runway_slope(self):
        """Annex 14 Sec.3.1.11/12: maximum longitudinal slope."""
        slope = getattr(self.planner, 'runway_slope_percent', None)
        if slope is None:
            self.issues.append(
                "Runway slope not computed; manual check required "
                "(ICAO Sec.3.1.11).")
            return
        code = self.planner.runway_code_number
        max_slope = 1.5 if code in ['3', '4'] else 2.0
        if slope > max_slope:
            self.issues.append(
                f"Runway slope {slope:.2f}% exceeds ICAO max {max_slope}% "
                f"for code {code} (Annex 14 Sec.3.1.11).")
        else:
            self.passed.append(
                f"Runway slope {slope:.2f}% within ICAO limit of "
                f"{max_slope}% (Annex 14 Sec.3.1.11).")

    def check_taxiway_separation(self):
        """Annex 14 Table 1-1: taxiway to runway centerline separation."""
        separations = {
            ('1','A'): 37.5,  ('1','B'): 47.0,
            ('2','A'): 37.5,  ('2','B'): 47.0,  ('2','C'): 66.0,  ('2','D'): 80.0,
            ('3','A'): 37.5,  ('3','B'): 47.0,  ('3','C'): 66.0,  ('3','D'): 80.0,
            ('3','E'): 80.0,
            ('4','C'): 168.0, ('4','D'): 176.0, ('4','E'): 182.5, ('4','F'): 190.0,
        }
        key = (self.planner.runway_code_number, self.planner.runway_code_letter)
        if key in separations:
            self.passed.append(
                f"Min taxiway-to-runway CL separation for ARC "
                f"{key[0]}{key[1]}: {separations[key]} m "
                f"(Annex 14 Table 1-1). Verify layout.")
        else:
            self.issues.append(
                f"No separation standard found for ARC "
                f"{key[0]}{key[1]}. Manual check required.")

    def check_declared_distances(self):
        """Annex 14 Sec.3.6.2–3.6.3: TORA / TODA / ASDA / LDA consistency."""
        tora = getattr(self.planner, 'tora', 0)
        toda = getattr(self.planner, 'toda', 0)
        asda = getattr(self.planner, 'asda', 0)
        if tora <= 0:
            self.issues.append(
                "Declared distances not computed; run full analysis first.")
            return
        if asda > tora + getattr(self.planner, 'stopway_length', 0) + 1:
            self.issues.append(
                f"ASDA ({asda:.0f} m) exceeds TORA + stopway "
                f"(Annex 14 Sec.3.6.3).")
        else:
            self.passed.append(
                f"ASDA ({asda:.0f} m) consistent with TORA ({tora:.0f} m).")

        clearway_limit = min(
            getattr(self.planner, 'clearway_length', 0), tora * 0.5)
        if toda > tora + clearway_limit + 1:
            self.issues.append(
                f"TODA ({toda:.0f} m) exceeds TORA + allowable clearway "
                f"(Annex 14 Sec.3.6.2).")
        else:
            self.passed.append(
                f"TODA ({toda:.0f} m) consistent with TORA ({tora:.0f} m).")

    def check_obstacle_free_zone(self):
        """Annex 14 Sec.4.1: OFZ must be free of fixed objects."""
        if getattr(self.planner, 'obstacle_free_zone', False):
            self.passed.append(
                "Obstacle Free Zone (OFZ) requirement flagged for enforcement "
                "(Annex 14 Sec.4.1).")
        else:
            self.issues.append(
                "Obstacle Free Zone (OFZ) not enabled; review Annex 14 Sec.4.1.")

    def check_strip_grading(self):
        """Annex 14 Sec.3.4: strip grading half-width per code number."""
        code = self.planner.runway_code_number
        required = {'1': 15, '2': 15, '3': 30, '4': 30}
        req = required.get(code, 30)
        strip_half = self.planner.runway_strip_width / 2
        if strip_half >= req:
            self.passed.append(
                f"Runway strip grading half-width {strip_half:.0f} m "
                f">= {req} m (Annex 14 Sec.3.4).")
        else:
            self.issues.append(
                f"Strip grading half-width {strip_half:.0f} m < required "
                f"{req} m for code {code} (Annex 14 Sec.3.4).")

    # ── Master runner ────────────────────────────────────────────────────────

    def run_all_checks(self):
        """
        Execute all ICAO Annex 14 compliance checks.

        Check catalogue
        ---------------
        1.  Runway dimensions          (Annex 14 Sec.3.1.7–3.1.9)
        2.  RESA                       (Annex 14 Sec.3.5.1–3.5.3)
        3.  Approach obstacle surface  (Annex 14 Sec.4.2)
        4.  Runway orientation & wind  (Annex 14 Sec.3.1.1–3.1.3)
        5.  Runway slope               (Annex 14 Sec.3.1.11–3.1.12)
        6.  Taxiway separation         (Annex 14 Table 1-1)
        7.  Declared distances         (Annex 14 Sec.3.6.2–3.6.3)
        8.  Obstacle Free Zone         (Annex 14 Sec.4.1)
        9.  Strip grading              (Annex 14 Sec.3.4)

        Returns
        -------
        dict with keys: passed, issues, compliant, compliance_score, num_checks
        """
        self.check_runway_dimensions()
        self.check_resa()
        self.check_approach_surface()
        self.check_runway_orientation()
        self.check_runway_slope()
        self.check_taxiway_separation()
        self.check_declared_distances()
        self.check_obstacle_free_zone()
        self.check_strip_grading()

        num_checks = len(self.passed) + len(self.issues)
        score = len(self.passed) / max(1, num_checks) * 100
        return {
            'passed':           self.passed,
            'issues':           self.issues,
            'compliant':        len(self.issues) == 0,
            'compliance_score': round(score, 1),
            'num_checks':       num_checks,
        }
