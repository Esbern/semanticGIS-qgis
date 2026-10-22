"""A project without a coordinate system gets the CRS of the first layer the plugin adds, so layers
in different CRSs (Københavns Kommune in EPSG:25832, NUTS in EPSG:3035) are drawn in one system;
a project that has a CRS keeps it.

  python tests/project_crs.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer
app = QgsApplication([], False); app.initQgis()
from semanticgis.layers import _add_to_group

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


project = QgsProject.instance()
project.setCrs(QgsCoordinateReferenceSystem())   # "No CRS", e.g. after opening a file without one
_add_to_group(QgsVectorLayer("Polygon?crs=EPSG:25832", "bydele", "memory"), "Reference Framework")
expect("no-CRS project takes the first layer's CRS", project.crs().authid(), "EPSG:25832")
_add_to_group(QgsVectorLayer("Polygon?crs=EPSG:3035", "nuts", "memory"), "Reference Framework")
expect("a later layer does not change it", project.crs().authid(), "EPSG:25832")
project.clear()
project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
_add_to_group(QgsVectorLayer("Polygon?crs=EPSG:25832", "bydele", "memory"), "Reference Framework")
expect("a project with a CRS keeps it", project.crs().authid(), "EPSG:3857")
app.exitQgis()
sys.exit(1 if failures else 0)
