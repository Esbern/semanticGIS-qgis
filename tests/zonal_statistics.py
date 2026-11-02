"""Processing: Zonal statistics by class (legend labels) on small synthetic rasters. Values sharing a label are
pooled into one row, unlabelled values get "value <n>", NoData in either raster is left out, a finer value raster
is counted on its own grid, and labels come from a .qml file or from the layer's own style. No network.

  python tests/zonal_statistics.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsApplication, QgsPalettedRasterRenderer, QgsProject, QgsRasterLayer, QgsVectorLayer
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from qgis.PyQt.QtGui import QColor

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def write(path, array, size, dtype, nodata):
    ds = gdal.GetDriverByName("GTiff").Create(path, array.shape[1], array.shape[0], 1, dtype)
    ds.SetGeoTransform((700000, size, 0, 6200100, 0, -size))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(25832); ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(array); ds.GetRasterBand(1).SetNoDataValue(nodata); ds = None


tmp = tempfile.mkdtemp()
# Classes, 10 x 10 pixels of 10 m: left half 1, right half 63 (both "Lake"), top row 5 ("Public area"),
# the bottom-right pixel 999 (not in the legend), the bottom-left pixel NoData (0).
classes = np.where(np.arange(10) < 5, 1, 63)[None, :].repeat(10, axis=0).astype(np.uint16)
classes[0, :] = 5
classes[9, 9] = 999
classes[9, 0] = 0
write(os.path.join(tmp, "classes.tif"), classes, 10, gdal.GDT_UInt16, 0)
# Values on the same grid: 10 * row + column (0 … 99), NoData (-9999) at row 1, column 0.
values = (10 * np.arange(10)[:, None] + np.arange(10)[None, :]).astype(np.float32)
values[1, 0] = -9999
write(os.path.join(tmp, "values.tif"), values, 10, gdal.GDT_Float32, -9999)
# The same values on a 5 m grid: each 10 m pixel split in four.
write(os.path.join(tmp, "values5.tif"), values.repeat(2, axis=0).repeat(2, axis=1), 5, gdal.GDT_Float32, -9999)
qml = os.path.join(tmp, "legend.qml")
with open(qml, "w", encoding="utf-8") as f:
    f.write('<qgis><pipe><rasterrenderer type="paletted"><colorPalette>'
            '<paletteEntry color="#4261d1" label="Lake" value="1" alpha="255"/>'
            '<paletteEntry color="#4261d1" label="Lake" value="63" alpha="255"/>'
            '<paletteEntry color="#d56a93" label="Public area" value="5" alpha="255"/>'
            '</colorPalette></rasterrenderer></pipe></qgis>')

import semanticgis
plugin = semanticgis.classFactory(type("Iface", (), {})())
plugin.initProcessing()
registry = QgsApplication.processingRegistry()
alg = registry.algorithmById("semanticgis:zonalstatisticslegend")
expect("tool in its group", (alg.displayName(), alg.group()), ("Zonal statistics by class (legend labels)", "Raster analysis"))

layers = {name: QgsRasterLayer(os.path.join(tmp, f"{name}.tif"), name) for name in ("classes", "values", "values5")}
for layer in layers.values():
    QgsProject.instance().addMapLayer(layer)


def run(values="values", **params):
    out = processing.run("semanticgis:zonalstatisticslegend", {"CLASSES": layers["classes"], "CLASSES_BAND": 1,
                         "VALUES": layers[values], "VALUES_BAND": 1,
                         "OUTPUT": os.path.join(tmp, f"out{len(os.listdir(tmp))}.gpkg"), **params})["OUTPUT"]
    layer = QgsVectorLayer(out, "out")
    return [{n: f[n] for n in layer.fields().names() if n != "fid"} for f in layer.getFeatures()]


valid = values != -9999
lake = values[(np.isin(classes, [1, 63])) & valid].astype(np.float64)
rows = run(QML=qml)
expect("rows in legend order", [(r["label"], r["class_values"], r["count"]) for r in rows],
       [("Lake", "1, 63", 87), ("Public area", "5", 10), ("value 999", "999", 1)])
r = rows[0]
expect("Lake statistics", [round(r[k], 6) for k in ("area", "sum", "mean", "median", "min", "max", "q1", "q3", "iqr")],
       [8700.0, float(lake.sum()), round(float(lake.mean()), 6), float(np.median(lake)), 11.0, 98.0,
        float(np.percentile(lake, 25)), float(np.percentile(lake, 75)), float(np.percentile(lake, 75) - np.percentile(lake, 25))])
expect("Lake stddev (population)", round(r["stddev"], 6), round(float(lake.std()), 6))
expect("Public area range", (rows[1]["min"], rows[1]["max"], rows[1]["range"]), (0.0, 9.0, 9.0))
expect("one row per raster value", [(r["class_value"], r["label"], r["count"]) for r in run(QML=qml, POOL=False)],
       [(1.0, "Lake", 43), (63.0, "Lake", 44), (5.0, "Public area", 10), (999.0, "value 999", 1)])
fine = run("values5", QML=qml)
expect("5 m values on their own grid: four times the pixels, same area and mean",
       [(r["label"], r["count"], r["area"], round(r["mean"], 6)) for r in fine],
       [(r["label"], 4 * r["count"], r["area"], round(r["mean"], 6)) for r in rows])
coarse = run("values5", QML=qml, GRID=1)
expect("5 m values on the class grid: one value per class pixel",
       [(r["label"], r["count"], r["area"]) for r in coarse], [(r["label"], r["count"], r["area"]) for r in rows])
layers["classes"].setRenderer(QgsPalettedRasterRenderer(layers["classes"].dataProvider(), 1, [
    QgsPalettedRasterRenderer.Class(1, QColor("blue"), "Sø"), QgsPalettedRasterRenderer.Class(63, QColor("blue"), "Sø"),
    QgsPalettedRasterRenderer.Class(5, QColor("red"), "Offentligt område")]))
expect("labels from the layer's style", [r["label"] for r in run()], ["Sø", "Offentligt område", "value 999"])

registry.removeProvider(plugin.provider)
app.exitQgis()
sys.exit(1 if failures else 0)
