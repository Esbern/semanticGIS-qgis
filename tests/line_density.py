"""Line density against a brute-force answer, and the algorithm on made-up and (AIS_LINES) real lines."""
import os, sys, tempfile, random, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import *
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"): QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
from osgeo import gdal
import numpy as np
from semanticgis.tools.line_density import LineDensity, cells_of
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
random.seed(4); bad = 0
for _ in range(400):                       # every cell the line really passes through, by exact geometry
    pts = [(random.uniform(-2, 12), random.uniform(-2, 12)) for _ in range(random.randint(2, 4))]; line = QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in pts])
    true = {r * 10 + c for r in range(10) for c in range(10) if line.intersects(QgsGeometry.fromRect(QgsRectangle(c, 9 - r, c + 1, 10 - r)))}
    bad += cells_of(pts, 0, 10, 1, 10, 10) != true
expect("same cells as exact intersection (400 random lines)", bad, 0)
expect("along a row, backwards, vertical, one point", [cells_of(p, 0, 3, 1, 3, 3) for p in ([(0.5, 2.5), (2.5, 2.5)], [(2.5, 2.5), (0.5, 2.5)], [(0.5, 2.5), (0.5, 0.5)], [(1.5, 1.5)])], [{0, 1, 2}, {0, 1, 2}, {0, 3, 6}, {4}])
l = QgsVectorLayer("LineString?crs=EPSG:25832&field=mmsi:integer&field=start_time:datetime", "l", "memory")
from qgis.PyQt.QtCore import QDateTime, QDate, QTime
def add(m, day, *p):
    f = QgsFeature(l.fields()); f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*x) for x in p])); f.setAttributes([m, QDateTime(QDate(2026, 7, day), QTime(1, 0))]); l.dataProvider().addFeature(f)
add(1, 1, (500050, 6200050), (500250, 6200050)); add(1, 1, (500050, 6200060), (500150, 6200060)); add(1, 2, (500050, 6200050), (500150, 6200050)); add(2, 1, (500050, 6200050), (500050, 6200250))
QgsProject.instance().addMapLayer(l); d = tempfile.mkdtemp(); alg = LineDensity(); alg.initAlgorithm(); ctx, fb = QgsProcessingContext(), QgsProcessingFeedback(); ctx.setProject(QgsProject.instance())
def run(n, **k):
    out = f"{d}/{n}.tif"; res, ok = alg.run({"INPUT": l, "CRS": QgsCoordinateReferenceSystem("EPSG:25832"), "CELL_SIZE": 100, "ID_FIELD": "mmsi", "TIME_FIELD": "start_time", "EMPTY_CELLS": 0, "OUTPUT": out, **k}, ctx, fb)
    return gdal.Open(out) if ok else None
a = run("v", COUNT=0); expect("different vessels", a.ReadAsArray().tolist(), [[1, 0, 0], [1, 0, 0], [2, 1, 1]]); expect("aligned grid", a.GetGeoTransform(), (500000.0, 100.0, 0.0, 6200300.0, 0.0, -100.0))
expect("per day", run("d", COUNT=1).ReadAsArray().tolist(), [[1, 0, 0], [1, 0, 0], [3, 2, 1]]); expect("lines", run("l", COUNT=2).ReadAsArray().tolist(), [[1, 0, 0], [1, 0, 0], [4, 3, 1]])
expect("no data", run("n", COUNT=0, EMPTY_CELLS=1).ReadAsArray().min(), -9999); expect("degrees refused", run("g", CRS=QgsCoordinateReferenceSystem("EPSG:4326")), None)
e = run("e", COUNT=0, EXTENT="500100,500200,6200000,6200100 [EPSG:25832]"); expect("only inside the area", e.ReadAsArray().tolist(), [[1]])
if os.environ.get("AIS_LINES"):
    import time; r = QgsVectorLayer(os.environ["AIS_LINES"], "r", "ogr"); QgsProject.instance().addMapLayer(r); t = time.time(); x = run("real", INPUT=r, CRS=QgsCoordinateReferenceSystem("EPSG:3035"), CELL_SIZE=1000, COUNT=0); print(round(time.time() - t, 1), "s", x.RasterXSize, x.RasterYSize, x.GetGeoTransform(), fb.textLog()[-120:])
    if os.environ.get("HELCOM"):
        h = gdal.Open(os.environ["HELCOM"]); hg, xg = h.GetGeoTransform(), x.GetGeoTransform(); expect("same grid as HELCOM", ((hg[0] - xg[0]) % 1000, (hg[3] - xg[3]) % 1000), (0.0, 0.0)); H = h.GetRasterBand(1).ReadAsArray(); A = x.ReadAsArray(); co, ro = int((xg[0] - hg[0]) / 1000), int((hg[3] - xg[3]) / 1000)
        pairs = [(A[i, j], H[i + ro, j + co]) for i in range(A.shape[0]) for j in range(A.shape[1]) if 0 <= i + ro < H.shape[0] and 0 <= j + co < H.shape[1] and H[i + ro, j + co] > 0]; p = np.array(pairs); rk = lambda v: np.argsort(np.argsort(v)); print("cells in both:", len(p), "rank correlation:", round(float(np.corrcoef(rk(p[:, 0]), rk(p[:, 1]))[0, 1]), 2))
print("failures:", fails); app.exitQgis(); sys.exit(bool(fails))
