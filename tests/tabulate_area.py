"""Processing: the plugin registers its provider, and Tabulate area (legend labels) counts a small synthetic
raster exactly: values sharing a label are added together, unlabelled values get "value <n>", NoData is left
out, and labels come from a .qml file or from the layer's own style. No network.

  python tests/tabulate_area.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
import numpy as np
from osgeo import gdal, osr
from qgis.core import (QgsApplication, QgsFeature, QgsGeometry, QgsPalettedRasterRenderer, QgsProject, QgsRasterLayer,
                       QgsVectorLayer)
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


tmp = tempfile.mkdtemp()
# 10 x 10 pixels of 10 m: left half 1, right half 63 (both "Lake"), top row 5 ("Public area"),
# the bottom-right pixel 999 (not in the legend), the bottom-left pixel NoData (0).
grid = np.where(np.arange(10) < 5, 1, 63)[None, :].repeat(10, axis=0).astype(np.uint16)
grid[0, :] = 5
grid[9, 9] = 999
grid[9, 0] = 0
path = os.path.join(tmp, "classes.tif")
ds = gdal.GetDriverByName("GTiff").Create(path, 10, 10, 1, gdal.GDT_UInt16)
ds.SetGeoTransform((700000, 10, 0, 6200100, 0, -10))
srs = osr.SpatialReference(); srs.ImportFromEPSG(25832); ds.SetProjection(srs.ExportToWkt())
ds.GetRasterBand(1).WriteArray(grid); ds.GetRasterBand(1).SetNoDataValue(0); ds = None
qml = os.path.join(tmp, "legend.qml")
with open(qml, "w", encoding="utf-8") as f:
    f.write('<qgis><pipe><rasterrenderer type="paletted"><colorPalette>'
            '<paletteEntry color="#4261d1" label="Lake" value="1" alpha="255"/>'
            '<paletteEntry color="#4261d1" label="Lake" value="63" alpha="255"/>'
            '<paletteEntry color="#d56a93" label="Public area" value="5" alpha="255"/>'
            '<paletteEntry color="#999999" label="Unused" value="7" alpha="255"/>'
            '</colorPalette></rasterrenderer></pipe></qgis>')

zones = QgsVectorLayer("Polygon?crs=EPSG:25832&field=name:string", "zones", "memory")
for name, wkt in (("all", "POLYGON((700000 6200000, 700100 6200000, 700100 6200100, 700000 6200100, 700000 6200000))"),
                  ("left", "POLYGON((700000 6200000, 700050 6200000, 700050 6200100, 700000 6200100, 700000 6200000))")):
    f = QgsFeature(zones.fields()); f.setAttributes([name]); f.setGeometry(QgsGeometry.fromWkt(wkt)); zones.dataProvider().addFeature(f)

import semanticgis
plugin = semanticgis.classFactory(type("Iface", (), {})())
plugin.initProcessing()
registry = QgsApplication.processingRegistry()
expect("provider registered", registry.providerById("semanticgis").name(), "SemanticGIS")
alg = registry.algorithmById("semanticgis:tabulatearealegend")
expect("tool in its group", (alg.displayName(), alg.group()), ("Tabulate area (legend labels)", "Raster analysis"))

raster = QgsRasterLayer(path, "classes")
QgsProject.instance().addMapLayer(raster)


def run(**params):
    out = processing.run("semanticgis:tabulatearealegend", {"RASTER": raster, "BAND": 1, "ZONES": zones,
                         "OUTPUT": os.path.join(tmp, f"out{len(os.listdir(tmp))}.gpkg"), **params})["OUTPUT"]
    layer = QgsVectorLayer(out, "out")
    return {f["name"]: {n: f[n] for n in layer.fields().names() if n not in ("fid", "name")} for f in layer.getFeatures()}


counts = run(QML=qml, UNIT=0)
expect("counts, whole raster", counts["all"], {"Lake": 88, "Public area": 10, "value 999": 1})
expect("counts, left half", counts["left"], {"Lake": 44, "Public area": 5, "value 999": 0})
area = run(QML=qml, UNIT=1)
expect("area in m²", area["all"]["Lake"], 8800.0)
share = run(QML=qml, UNIT=2)
expect("percent", round(share["all"]["Public area"], 3), round(1000 / 99, 3))
expect("empty classes kept on request", sorted(run(QML=qml, UNIT=0, DROP_EMPTY=False)["all"]),
       ["Lake", "Public area", "Unused", "value 999"])
raster.setRenderer(QgsPalettedRasterRenderer(raster.dataProvider(), 1, [
    QgsPalettedRasterRenderer.Class(1, QColor("blue"), "Sø"), QgsPalettedRasterRenderer.Class(63, QColor("blue"), "Sø"),
    QgsPalettedRasterRenderer.Class(5, QColor("red"), "Offentligt område")]))
expect("labels from the layer's style", run(UNIT=0)["all"], {"Sø": 88, "Offentligt område": 10, "value 999": 1})

registry.removeProvider(plugin.provider)
app.exitQgis()
sys.exit(1 if failures else 0)
