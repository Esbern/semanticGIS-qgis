"""Processing: Flatten snapshot series. Synthetic fields in three years: 2024 repeats the 2026 boundaries 0.8 m off
(digitising noise) and splits one field (real change); 2020 has a field that later disappeared. With a 2 m
tolerance the noise must vanish and the real change stay. No network.

  python tests/flatten_snapshots.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QDate

import semanticgis
plugin = semanticgis.classFactory(type("Iface", (), {})())
plugin.initProcessing()
from semanticgis.tools.flatten_snapshots import is_sliver, period_prefix, sort_periods

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def box(x0, y0, x1, y1):
    return QgsGeometry.fromWkt(f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))")


expect("prefix for a year", period_prefix(2026), "y2026_")
expect("prefix for a month", period_prefix("2026-03", "m"), "m2026_03_")
expect("prefix for a date", period_prefix(QDate(2026, 3, 1), "d"), "d2026_03_01_")
expect("prefix for a decimal year", period_prefix(2026.0, "y"), "y2026_")
expect("no prefix: never starts with a digit", period_prefix(2026, ""), "p2026_")
expect("newest first", sort_periods([2020, 2026, 2024]), [2026, 2024, 2020])
expect("weeks in time order", sort_periods(["week 5", "week 12", "week 9"]), ["week 12", "week 9", "week 5"])
expect("months in time order", sort_periods(["2026-11", "2026-03", "2025-12"]), ["2026-11", "2026-03", "2025-12"])
expect("dates in time order", sort_periods([QDate(2026, 3, 1), QDate(2026, 11, 1)]), [QDate(2026, 11, 1), QDate(2026, 3, 1)])
expect("a 1 m strip is a sliver at 2 m", is_sliver(box(0, 0, 100, 1), 2), True)
expect("a 5 m strip is not", is_sliver(box(0, 0, 100, 5), 2), False)

E = 690000, 6170000   # somewhere in UTM 32N
def at(x0, y0, x1, y1):
    return box(E[0] + x0, E[1] + y0, E[0] + x1, E[1] + y1)

d = 0.8
data = [
    (2026, "A", "Vinterhvede", at(0, 0, 100, 100)), (2026, "B", "Vårbyg", at(100, 0, 200, 100)),
    (2024, "A", "Vårbyg", at(d, d, 100 + d, 100 + d)),                       # same field, digitised 0.8 m off
    (2024, "B1", "Majs", at(100 + d, d, 150, 100 + d)), (2024, "B2", "Græs", at(150, d, 200 + d, 100 + d)),  # B was two fields
    (2020, "A", "Raps", at(-d, 0, 100 - d, 100)), (2020, "C", "Kartofler", at(300, 0, 400, 100)),          # C later gone
]
layer = QgsVectorLayer("Polygon?crs=EPSG:25832&field=snapshot_year:integer&field=Marknr:string&field=Afgroede:string&field=other:string",
                       "series", "memory")
for year, nr, crop, g in data:
    f = QgsFeature(layer.fields()); f.setAttributes([year, nr, crop, "x"]); f.setGeometry(g)
    layer.dataProvider().addFeature(f)
QgsProject.instance().addMapLayer(layer)

r = processing.run("semanticgis:flattensnapshots", {"INPUT": layer, "PERIOD_FIELD": "snapshot_year", "FIELDS": ["Marknr", "Afgroede"],
                                                    "TOLERANCE": 2, "OUTPUT": "TEMPORARY_OUTPUT"})
out = r["OUTPUT"]
names = [f.name() for f in out.fields()]
expect("columns per period, newest first", names, ["y2026_Marknr", "y2026_Afgroede", "y2024_Marknr", "y2024_Afgroede", "y2020_Marknr", "y2020_Afgroede"])
rows = sorted((f["y2026_Marknr"] or "-", f["y2024_Marknr"] or "-", f["y2020_Marknr"] or "-") for f in out.getFeatures())
expect("pieces: A through all years, B split in 2024, C only in 2020", rows,
       [("-", "-", "C"), ("A", "A", "A"), ("B", "B1", "-"), ("B", "B2", "-")])
expect("no slivers left", any(is_sliver(f.geometry(), 2) for f in out.getFeatures()), False)
area = {f["y2026_Marknr"] or f["y2020_Marknr"]: 0 for f in out.getFeatures()}
for f in out.getFeatures():
    area[f["y2026_Marknr"] or f["y2020_Marknr"]] += f.geometry().area()
expect("A keeps the 2026 area", round(area["A"]), 10000)
expect("B keeps the 2026 area", round(area["B"]), 10000)
a = next(f for f in out.getFeatures() if f["y2026_Marknr"] == "A")
expect("A's crops through time", (a["y2026_Afgroede"], a["y2024_Afgroede"], a["y2020_Afgroede"]), ("Vinterhvede", "Vårbyg", "Raps"))
expect("A follows the 2026 boundary", round(a.geometry().symDifference(at(0, 0, 100, 100)).area(), 3) == 0, True)

print("all good" if not failures else f"{failures} failure(s)")
sys.exit(failures)
