# -*- coding: utf-8 -*-
"""
Aerodrome Spatial Planner – QGIS Plugin
=========================================
Entry point required by the QGIS plugin loader.
``classFactory`` is the only symbol QGIS calls on startup.
"""
import sys, os

# Inject plugin directory into sys.path immediately.
# This ensures all sibling modules (engine, airport_planner, etc.) are
# importable as plain absolute modules on Windows QGIS 4 / Python 3.12.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)


def classFactory(iface):
    """
    Required by QGIS plugin loader.
    Returns an AirportPlannerSuite instance.
    """
    # Plain absolute import – works on all QGIS versions / OS combinations
    from airport_planner import AirportPlannerSuite  # noqa: E402
    return AirportPlannerSuite(iface)
